"""mpuse.py - what every HP / MP change was: regen tick, skill casts, potions, hits, ...

Core side (no pixels). Fed every HP/MP look (live.py, from the 'vitals' facts). Between two looks
the change in MP (and HP) is explained as a sum of exact parts:

  MP  = regen tick (vitals.Meter's clock says whether one fell in the window)
        - skill casts (MP cost per level, skills_classic.json; one cast per ~0.6 s of window)
        + a potion / food (potions_classic.json, MeowDB; times the recovery skill's item bonus:
          Improved MP Recovery Lv 5 = +14%, so a Blue Potion's 200 shows as 228, measured
          2026-10-24). Capped at max: a rise to full fits any item at least that big
        - Magic Guard (part of a hit taken from MP; only when HP dropped in the same window)
        + MP Eater (2nd job mages: absorbs a share of the mob's MP on attacks, any size)
  HP  = regen tick - hits + potion / food (Improved HP Recovery's bonus for warriors).
        Sitting (and a chair: +30 HP / 10 s, user 2026-10-24) only makes the ticks bigger:
        MP +10 -> +13 sitting, HP +50 on a chair, each meter on its own 10 s beat
        + Heal (cleric: your own cast; a party member's while in a party: any size)
        + Recovery (beginner skill, 30 s after casting it)
  A max change is a level-up (or gear) refill; HP 0 is a death; a gap over 3 s isn't explained.

Every way to explain a change is scored and the simplest wins: fewest events, the regen clock
agreeing (a tick where it expects one, none where it doesn't), known skill levels, shop items
before event items. A change with no exact explanation is "other" (kept with its size, so new
cases can be studied). The tick found is handed back to the Meter (feed(tick=...)), so a cast in
the same look no longer hides it (-17 + 10 = -7).

Skill levels come from the Skill window (character.py) and can be stale: a level-up spends SP
long before the window is opened again. So a cast may cost any level's cost from the known level
up; once the same higher cost is seen MIN_LEVEL_SEEN times, that level is believed
(`levels_seen`, used for casts left).

  python mpuse.py LOG.csv [skill_id=level ...]   replay a dev/vitals_log.py recording
"""
import json
import re
from collections import Counter, deque
from itertools import combinations_with_replacement

from paths import APP

MAX_CASTS = 3           # casts that can fall in one window at most
CAST_S = 0.6            # about the fastest one cast follows another (attack animation)
MAX_GAP_S = 3.0         # a longer gap between looks: the change isn't explained
MIN_LEVEL_SEEN = 3      # casts at a higher level's cost before that level is believed
RECOVERY_S = 30         # Recovery (beginner skill): "Recover HP 24 in 30 sec"
EATER_IDS = ("2100000", "2200000", "2300000")         # MP Eater (F/P, I/L, Cleric)
GUARD_ID = "2001000"                                  # Magic Guard
HEAL_ID = "2301001"                                   # Heal (Cleric)
RECOVERY_ID = "0001001"                               # Recovery (Beginner)
ITEM_BONUS = {"mp": ("2000000",), "hp": ("1000000",)}  # Improved MP / HP Recovery
ITEM_RANK = {"Potion": 0, "Food": 1}                  # others (event / quest items) after
OTHER = 99.0            # score of "unexplained"


def _load(name, key):
    try:
        return json.loads((APP / name).read_text(encoding="utf-8"))[key]
    except (OSError, ValueError, KeyError):
        return {}


def _pct(text, pattern):
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else 0


class Usage:
    def __init__(self, meters, skills=None, potions=None):
        self.meters = meters                    # {"hp": vitals.Meter, "mp": vitals.Meter}
        self.data = skills if skills is not None else _load("skills_classic.json", "skills")
        self.items = potions if potions is not None else _load("potions_classic.json", "items")
        self.levels = {}                        # {skill id: level} (Skill window)
        self.party = False
        self.prev = None                        # (t, hp, mp)
        self.recent = deque(maxlen=12)          # last events, for live.json
        self.cost_seen = Counter()              # (skill id, cost) -> casts at that cost
        self.levels_seen = {}                   # {skill id: level} believed from casts
        self.last_item = None                   # the last item used (rises capped at max)
        self.last_cast_t = -1e9
        self.recovery_until = -1e9
        self.reset_totals()
        self._costs = None
        self._sums = {}

    # -- context -------------------------------------------------------------------
    def set_context(self, levels=None, party=None):
        """levels: {skill id: level} of the character (character.Profile); party: in a party."""
        if levels is not None and levels != self.levels:
            self.levels = dict(levels)
            self._costs = None
        if party is not None:
            self.party = bool(party)

    def level(self, sid):
        return max(self.levels.get(sid) or 0, self.levels_seen.get(sid) or 0)

    def _skill_text(self, sid):
        lv = self.level(sid)
        texts = (self.data.get(sid) or {}).get("levels") or []
        return texts[lv - 1] if 0 < lv <= len(texts) else ""

    def item_bonus(self, k):
        """Recovery from items +N% (Improved MP / HP Recovery's text at its level)."""
        return max([_pct(self._skill_text(s), r"from items by (\d+)%") for s in ITEM_BONUS[k]]
                   + [0])

    def costs(self):
        """[(cost, skill id, level, is the known level)]: every MP cost a cast could have now."""
        if self._costs is None:
            out = []
            for sid in self.levels:
                lv = self.level(sid)
                mp = (self.data.get(sid) or {}).get("mp") or []
                if not lv or lv > len(mp):
                    continue
                for L in range(lv, len(mp) + 1):        # the level can only have gone up
                    if mp[L - 1]:
                        out.append((mp[L - 1], sid, L, L == lv))
            self._costs = out
            self._sums = self._cast_sums(out)
        return self._costs

    @staticmethod
    def _combo_score(combo):
        # unknown levels cost half an event each; one skill at one level beats a mix
        return (len(combo) + 0.5 * sum(not c[3] for c in combo)
                + 0.1 * (len({(c[1], c[2]) for c in combo}) - 1 if combo else 0))

    def _cast_sums(self, costs):
        """{n: {total MP: (score, casts)}}: the best way to spend `total` in at most n casts."""
        uniq = sorted(set(costs))
        out, best = {}, {0: (0.0, ())}
        for n in range(1, MAX_CASTS + 1):
            for combo in combinations_with_replacement(uniq, n):
                total = sum(c[0] for c in combo)
                sc = self._combo_score(combo)
                if total not in best or sc < best[total][0]:
                    best[total] = (sc, combo)
            out[n] = dict(best)
        out[0] = {0: (0.0, ())}
        return out

    @staticmethod
    def _rank_cost(rank):
        """Shop potions and food: tiny tie-breaks; event / quest items (rank 2): a real cost,
        so a regen tick of the same size wins ("Red Snail Invasion" heals 50, like HP regen)."""
        return 0.5 + 0.01 * rank if rank >= 2 else 0.01 * rank

    def gains(self, k, mx):
        """[(gain, rank, item)] for meter k: what each item adds now (bonus applied)."""
        b = self.item_bonus(k)
        out = []
        for it in self.items.values():
            rank = ITEM_RANK.get(it.get("subtype"), 2) + 0.001 * (it.get("price") or 0) ** 0.5
            flat, pct = it.get(k), it.get(k + "_pct")
            if flat:
                out.append((int(flat * (100 + b) / 100 + 1e-6), rank, it))
            if pct and mx:
                out.append((int(mx * pct / 100 * (100 + b) / 100 + 1e-6), rank, it))
                if b:                           # does the bonus apply to % items? not measured
                    out.append((int(mx * pct / 100 + 1e-6), rank + 0.01, it))
        return out

    # -- one look --------------------------------------------------------------------
    def look(self, t, hp, mp):
        """hp / mp: [cur, max] or None. Explains the change since the last look, feeds the
        Meters (with the tick found) and returns the new events."""
        hp = list(hp) if hp else None
        mp = list(mp) if mp else None
        p, self.prev = self.prev, (t, hp, mp)
        if p is None or t - p[0] > MAX_GAP_S:
            for k, v in (("hp", hp), ("mp", mp)):
                if v:
                    self.meters[k].feed(t, *v)
            return []
        lo, php, pmp = p
        ev = []
        lvl = [k for k, a, b in (("hp", php, hp), ("mp", pmp, mp)) if a and b and a[1] != b[1]]
        if lvl:                                 # max changed: level-up / gear; refill isn't use
            ev.append(self._ev(t, lvl[0], "levelup", "max changed", 0))
            for k, v in (("hp", hp), ("mp", mp)):
                if v:
                    self.meters[k].feed(t, *v)
            return self._keep(ev)
        dmp = (mp[0] - pmp[0]) if mp and pmp else 0
        dhp = (hp[0] - php[0]) if hp and php else 0
        if hp and hp[0] == 0 and dhp < 0:
            ev.append(self._ev(t, "hp", "died", "HP 0", dhp))
            dhp = 0
        elif php and php[0] == 0 and dhp > 0:   # revived
            ev.append(self._ev(t, "hp", "revive", "back from 0", dhp))
            dhp = 0
        mp_tick = hp_tick = 0
        mp_ev = []
        if mp and pmp:
            mp_ev, mp_tick = self._explain_mp(lo, t, dmp, mp, dhp)
            ev += mp_ev
        if hp and php:
            hp_ev, hp_tick = self._explain_hp(lo, t, dhp, hp, mp_ev)
            ev += hp_ev
        if mp:
            self.meters["mp"].feed(t, *mp, tick=mp_tick)
        if hp:
            self.meters["hp"].feed(t, *hp, tick=hp_tick)
        for k, tk in (("mp", mp_tick), ("hp", hp_tick)):
            if tk:                              # regen: in the totals, not the event log
                self._count(k, "regen", "regen", tk)
        return self._keep(ev)

    def _tick_options(self, k, lo, hi, d, v):
        """[(tick size, penalty)] to try in this window."""
        m = self.meters[k]
        sizes = sorted(set(m.sizes), key=lambda s: -list(m.sizes).count(s))
        sizes += [s for s in m.known if s not in sizes]      # the character's numbers (regen.py)
        guess = list(sizes)
        # the tick's size changes with what you do (+10 moving, +13 sitting, chairs add more):
        # a rise near the known sizes is a tick of that size; a rise to full can be a cut tick
        if d > 0 and d not in guess and (m.is_tick_size(d, v[1]) or
                                         (v[0] >= v[1] and d <= max(sizes or [0]))):
            guess.insert(0, d)
        if v[0] >= v[1] and d <= 0:             # at max: no tick can show
            return [(0, 0.0)]
        if m.anchor is None:                    # no clock yet: a tick-sized rise may be one
            return [(0, 0.0)] + [(s, 0.4) for s in guess]
        tp = m.expected(lo, hi)
        if tp is not None and m.last and abs(sum(m.last) / 2 - tp) < 0.5 * m.per:
            tp = None                           # this tick was already seen
        if tp is not None:                      # the clock says a tick fell in (or near) here
            inside = lo <= tp <= hi
            return [(s, 0.0) for s in guess] + [(0, 0.6 if inside else 0.1)]
        # off the clock: less likely, but sitting down starts a new beat (the first chair tick
        # came ~10 s after sitting, 2026-10-24 21:59), so it still beats an event item (1.5);
        # an exact shop potion (1.0) wins
        return [(0, 0.0)] + [(s, 1.2) for s in guess]

    def _explain_mp(self, lo, t, d, mp, dhp):
        self.costs()
        cur, mx = mp
        n = max(1, min(MAX_CASTS, 1 + int((t - lo) / CAST_S)))
        sums = self._sums.get(n) or {0: (0.0, ())}
        guard = self.level(GUARD_ID) > 0
        eater = any(self.level(s) for s in EATER_IDS)
        best = (OTHER, None, 0)                 # (score, [events], tick)
        for tick, pen in self._tick_options("mp", lo, t, d, mp):
            r = d - tick
            if pen >= best[0]:
                continue
            if r <= 0 and -r in sums:           # casts only (or nothing)
                sc, combo = sums[-r]
                if pen + sc < best[0]:
                    best = (pen + sc, ("casts", combo), tick)
            # an item (+ up to 2 casts in the same look)
            for gain, rank, it in self.gains("mp", mx):
                for total, (sc, combo) in sums.items():
                    if len(combo) > 2:
                        continue
                    net = gain - total
                    capped = cur >= mx and net > r > 0 - total and rank < 2
                    if net == r or capped:
                        s = pen + 1 + sc + self._rank_cost(rank) + (0.6 if capped else 0) \
                            - (0.05 if it["name"] == self.last_item else 0)
                        if s < best[0]:
                            amt = r + total if capped else gain
                            best = (s, ("item", it, amt, capped, combo), tick)
            if r < 0 and dhp < 0 and guard:     # Magic Guard took part of a hit from MP
                for total, (sc, combo) in sums.items():
                    rest = -r - total
                    if 0 < rest <= 4 * -dhp + 5 and pen + 1.2 + sc < best[0]:
                        best = (pen + 1.2 + sc, ("guard", rest, combo), tick)
            if eater:                           # MP Eater (+ casts in the same look)
                for total, (sc, combo) in sums.items():
                    if r + total > 0 and len(combo) <= 2 and pen + 1.5 + sc < best[0]:
                        best = (pen + 1.5 + sc, ("eater", r + total, combo), tick)
        sc, how, tick = best
        if how is None:
            return ([self._ev(t, "mp", "other", "unexplained", d)] if d else []), 0
        if how[0] == "casts":
            return self._casts(t, how[1]), tick
        if how[0] == "item":
            _, it, amt, capped, combo = how
            self.last_item = it["name"]
            return ([self._ev(t, "mp", "potion", it["name"], amt, to_full=capped)]
                    + self._casts(t, combo)), tick
        if how[0] == "guard":
            return (self._casts(t, how[2])
                    + [self._ev(t, "mp", "magic_guard", "Magic Guard", -how[1])]), tick
        return (self._casts(t, how[2]) + [self._ev(t, "mp", "mp_eater", "MP Eater", how[1])],
                tick)

    def _casts(self, t, combo):
        out = []
        for cost, sid, L, known in combo:
            name = (self.data.get(sid) or {}).get("name") or sid
            self.cost_seen[(sid, cost)] += 1
            if not known and self.cost_seen[(sid, cost)] >= MIN_LEVEL_SEEN:
                if L > (self.levels_seen.get(sid) or 0):
                    self.levels_seen[sid] = L
                    self._costs = None
            if sid == RECOVERY_ID:
                self.recovery_until = t + RECOVERY_S
            self.last_cast_t = t
            out.append(self._ev(t, "mp", "cast", name, -cost, skill=sid, level=L))
        return out

    def _explain_hp(self, lo, t, d, hp, mp_ev):
        cur, mx = hp
        best = (OTHER, None, 0)
        for tick, pen in self._tick_options("hp", lo, t, d, hp):
            r = d - tick
            if r == 0:
                cand = [(pen, None)]
            elif r < 0:
                cand = [(pen + 1, ("hit", "hit", r))]
            else:
                cand = []
                for gain, rank, it in self.gains("hp", mx):
                    capped = cur >= mx and gain > r and rank < 2
                    if gain == r or capped:
                        cand.append((pen + 1 + self._rank_cost(rank) + (0.6 if capped else 0)
                                     - (0.05 if it["name"] == self.last_item else 0),
                                     ("potion", it["name"], r, capped)))
                if any(e.get("skill") == HEAL_ID for e in mp_ev):
                    cand.append((pen + 1.5, ("heal", "Heal", r)))
                if t <= self.recovery_until:
                    cand.append((pen + 1.5, ("recovery", "Recovery", r)))
                if self.party:
                    cand.append((pen + 2, ("heal", "Heal (party?)", r)))
            for sc, how in cand:
                if sc < best[0]:
                    best = (sc, how, tick)
        sc, how, tick = best
        if sc >= OTHER:
            return [self._ev(t, "hp", "other", "unexplained", d)], 0
        if how is None:
            return [], tick
        if how[0] == "potion":
            self.last_item = how[1]
            return [self._ev(t, "hp", "potion", how[1], how[2], to_full=how[3])], tick
        return [self._ev(t, "hp", how[0], how[1], how[2])], tick

    # -- results -----------------------------------------------------------------------
    @staticmethod
    def _ev(t, k, kind, name, amount, **extra):
        return {"t": round(t, 2), "meter": k, "kind": kind, "name": name, "amount": amount,
                **{a: b for a, b in extra.items() if b}}

    def _keep(self, ev):
        for e in ev:
            self.recent.append(e)
            k, kind, name, a = e["meter"], e["kind"], e["name"], e["amount"]
            if kind in ("cast", "potion", "magic_guard", "mp_eater", "heal", "recovery",
                        "hit", "other"):
                self._count(k, kind, name, a)
        return ev

    def _count(self, k, kind, name, a):
        row = self.totals[k].setdefault(kind, {}).setdefault(name, {"n": 0, "amount": 0})
        row["n"] += 1
        row["amount"] += a

    def reset_totals(self, t=None):
        self.totals = {"since": t, "hp": {}, "mp": {}}

    def summary(self):
        """live.json "usage": totals since the tracker started (per meter, per kind, per name),
        the last few events, and skill levels believed from cast costs."""
        lv = {sid: L for sid, L in self.levels_seen.items() if L > (self.levels.get(sid) or 0)}
        return {**self.totals, "recent": list(self.recent), "levels_seen": lv}


def _replay(path, levels, quiet=False):
    import csv
    import vitals
    fixed = lambda: dict(vitals.REGEN_DEFAULTS)     # noqa: E731
    meters = {"hp": vitals.Meter(prior=10.0, settings_fn=fixed),
              "mp": vitals.Meter(prior=10.0, settings_fn=fixed)}
    u = Usage(meters)
    u.set_context(levels=levels)
    for r in csv.DictReader(open(path)):
        hp = [int(r["hp"]), int(r["hp_max"])] if r["hp"] else None
        mp = [int(r["mp"]), int(r["mp_max"])] if r["mp"] else None
        for e in u.look(float(r["t"]), hp, mp):
            if not quiet and e["kind"] not in ("cast", "hit"):
                print(f"  {e['t']:.2f} {e['meter']} {e['kind']:<11} {e['name']:<22} "
                      f"{e['amount']:+d}")
    s = u.summary()
    print("MP:", json.dumps(s["mp"]))
    print("HP:", json.dumps(s["hp"]))
    print("levels seen:", s["levels_seen"], "| mp clock:",
          {k: v for k, v in meters["mp"].state(0).items()
           if k in ("clock", "ticks_seen", "checks", "restarts", "tick")})
    return u


if __name__ == "__main__":
    import sys
    lv = dict(a.split("=") for a in sys.argv[2:])
    _replay(sys.argv[1], {k: int(v) for k, v in lv.items()})
