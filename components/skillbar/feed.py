"""feed.py - the skill bar's side of live.json: HP/MP, the regen clock, what the HP/MP went on, casts left.

Fed by the skillbar component (components/skillbar/component.py) with the same facts as character.Profile (no pixels here):
  'vitals' {hp: [cur, max], mp: [cur, max]}  -> mpuse.Usage: explains each change (regen tick,
                                                casts, potions, hits...) and feeds meter.Meter
                                                per meter (the regen clock: next tick)
  'away'  (screen went dark: portal / channel change) -> the regen clocks start over
  the current character's skill levels (Profile) + skills_classic.json per-level MP costs
                                             -> casts left with the MP you have now

The sections it publishes to live.json (hub.publish, written by hub/live.py; the key list is there):
  hp / mp     {cur, max, tick, period, next (epoch s), sure_s, ticks_seen, clock, check_every,
              checks, restarts}   (meter.Meter.state)
  usage       {since, hp/mp {kind: {name: {n, amount}}}, recent [events], levels_seen}
              what the HP/MP went on since the tracker started (mpuse.Usage.summary)
  regen       {mp: {skill, level, says}}   what the skill data promises (Improved MP Recovery)
  skills      [{id, name, level, max, mp, casts, icon}]   skills with an MP cost, level >= 1;
              icon = templates/skill_icons/<id>.png when it was cut from the Skill window
The training and party sections come from their own components; the skills list asks the training
component for prices (hub.ask "mp_price" / "price_of").
The skill bar (skillbar_ui.py) animates the wheels itself between updates.
"""
import csv
import json
import re
import time
from collections import deque
from datetime import datetime

from base import perf
from base.paths import DATA, asset
from components.skillbar import maxwatch, meter, mpuse, regen
from gamedata import formulas, translate
from hub import history as H

COMBAT_S = 5            # after a cast, look at HP/MP COMBAT_FPS times a second for this long (the bar's
COMBAT_FPS = 5          # pulse comes ~0.2 s after the cast instead of up to ~2.4 s; player, 2026-10-31)
HOLD_S = 1.0            # an ambiguous cast (attack vs buff of the same MP cost) waits this long for the buff icon to flip
SETTLE_S = 0.4          # ... plus this for a flip the source confirms on a second look (it keeps the first look's time)
FLIP_BEFORE_S = 0.3     # ... a flip seen this much before the MP drop counts too (the two reads come from separate looks)
CLOCK_SAVE_S = 10       # the regen clock's position goes into the character's record this often
CLOCK_KEEP_S = 90       # ... and is picked up again after a restart / update within this long
# passive recovery skills that tick every 10 s; the bar shows a meter's regen slot only when the
# character has one of these at level >= 1 (chance-based ones like Critical Recovery don't count)
REGEN_SKILLS = {"hp": ["1000000"],                          # Improved HP Recovery (Warrior)
                "mp": ["2000000", "1110000", "1210000"]}    # Improved MP Recovery (Magician,
                                                            # Crusader, White Knight)


def cast_extras(text, char_level=None):
    """What a cast takes besides MP, from the skill's level text (skills_classic.json):
    items ("Magic Rock -1", "Summoning Rock -1", "Magic Stone -1"), mesos ("Throws 200 Mesos",
    "Uses 30 times level in Mesos"), or mesos that depend on the fight ("Consumes Meso ...")."""
    items = [(m.group(1), int(m.group(2))) for m in
             re.finditer(r"((?:The )?Magic Rock|Summoning Rock|Magic Stone)\s*-\s*(\d+)", text or "")]
    items = [("The Magic Rock" if n == "Magic Rock" else n, k) for n, k in items]
    mesos = None
    m = re.search(r"Throws (\d+) Mesos", text or "")
    if m:
        mesos = int(m.group(1))
    m = re.search(r"Uses (\d+) times level in Mesos", text or "")
    if m and char_level:
        mesos = int(m.group(1)) * char_level
    varies = bool(re.search(r"Consumes Meso", text or ""))
    return {"items": items, "mesos": mesos, "varies": varies}


def _skills_data():
    try:
        return json.loads((asset("gamedata/skills_classic.json")).read_text(encoding="utf-8"))["skills"]
    except (OSError, ValueError, KeyError):
        return {}


class Feed:
    def __init__(self, hub=None):
        self.hub = hub                      # the source in (burst looks)
        self.model = None                   # regen.RegenModel of the current character
        self.clock_saved = 0.0
        self.data = _skills_data()
        self.who = None
        self.party_n = 0                    # rows of the party box at the last look (party.py)
        self._new_meters()

    def _new_meters(self, t=None):
        self.meters = {"hp": meter.Meter(), "mp": meter.Meter()}
        self.watch = {k: maxwatch.MaxWatch(k, self.data) for k in ("hp", "mp")}
        self.last_cast_t = -1e9                 # when the last cast was booked (combat looks)
        self.hud_seen = False               # a vitals look since the character was set
        self.usage = mpuse.Usage(self.meters)
        self.usage.reset_totals(t)
        self.buff_on = {}                       # {buff skill id: bool} from 'buffs' facts (absent: unknown)
        self.flips = deque(maxlen=20)           # [t, skill id, now on, used] icon changes
        self.pending = []                       # casts booked as an attack that a buff may have made: {ev, t, alts}
        self.watch_sent = None                  # the ids last handed to source.watch_buffs

    def wants_fast(self, now):
        """The regen clock wants looks at burst rate now (meter.Meter.wants_fast)."""
        return any(m.wants_fast(now) for m in self.meters.values())

    def in_combat(self, now):
        """A cast was booked in the last COMBAT_S seconds: keep looking fast for the bar's pulse."""
        return now - self.last_cast_t < COMBAT_S

    def burst_in(self, now):
        """Seconds until the next burst of looks (0 = now, None = none planned)."""
        if self.in_combat(now):
            return 0
        due = [d for d in (m.burst_in(now) for m in self.meters.values()) if d is not None]
        return min(due) if due else None

    def burst_looks(self, until):
        """Between quick looks: the regen clock's bursts (meter.Meter). HP/MP strip only at
        burst_fps while it asks (finding the clock after a start / map change, or a check around
        a predicted tick); otherwise sleep until the next burst or `until`."""
        hub = self.hub
        source = hub.source
        rs = meter.regen_settings()
        step = 1.0 / rs["burst_fps"]
        while (n := time.time()) < until - 0.02:
            regen_fast = self.wants_fast(n)
            if regen_fast or self.in_combat(n):
                fps = rs["burst_fps"] if regen_fast else COMBAT_FPS
                step = 1.0 / fps
                source.boost(fps, 1.5)
                try:
                    with perf.timed("burst_look"):
                        ok = source.quick_vitals(n)
                except Exception:
                    ok = False
                hub.take_facts()
                if not ok:
                    return
                time.sleep(max(0.0, min(step - (time.time() - n), until - time.time())))
            else:
                d = self.burst_in(n)
                if d is None or n + d >= until:
                    return
                if d <= 0.8:                  # let Windows speed up its frames just before (a new
                    # capture session at the new rate takes ~0.5 s to deliver its first frame)
                    source.boost(rs["burst_fps"], 1.5)
                    time.sleep(max(0.01, min(d, until - n)))
                else:                         # wake at the boost point
                    time.sleep(max(0.01, min(d - 0.8, until - n)))

    def shown_counts(self):
        """Casts per skill without the ones still waiting for their buff-icon decision."""
        n = dict(self.usage.cast_count)
        for p in self.pending:
            sid = p["ev"]["skill"]
            n[sid] = n.get(sid, 0) - 1
        return {k: v for k, v in n.items() if v > 0}

    # -- buff icons: what an ambiguous cast was (Energy Bolt and Magic Guard both cost 8 MP) ------
    def _watch_ids(self, prof):
        """Owned buff skills whose icon file exists (the source can only look for those)."""
        c = (prof.char() if prof is not None else None) or {}
        out = []
        for sid, s in (c.get("skills") or {}).items():
            if (s.get("value") or 0) > 0 and (translate.skill_kind(sid) or {}).get("kind") == "buff"                     and (DATA / "templates" / "skill_icons" / f"{sid}.png").exists():
                out.append(sid)
        return sorted(out)

    def sync_watch(self, prof):
        """Tell the source which buffs to look for (when the list changed; icons appear as the Skill window is read)."""
        src = getattr(self.hub, "source", None)
        if src is None or prof is None or not prof.who:
            return
        ids = self._watch_ids(prof)
        if ids != self.watch_sent:
            self.watch_sent = ids
            self.buff_on = {k: v for k, v in self.buff_on.items() if k in ids}
            try:
                src.watch_buffs(ids)
            except Exception:
                pass

    def _buffs(self, t, v):
        """'buffs' fact {on: [ids]}: remember each watched icon's state; a change is a flip."""
        on = set((v or {}).get("on") or [])
        for sid in (self.watch_sent or []):
            now_on = sid in on
            old = self.buff_on.get(sid)
            if old is not None and old != now_on:
                self.flips.append([t, sid, now_on, False])
            self.buff_on[sid] = now_on

    def _hold(self, ev):
        """Cast events that may be a buff: parked in `pending` (not logged, no pulse) until decided."""
        out = []
        for e in ev:
            if e["kind"] == "cast" and (translate.skill_kind(e.get("skill")) or {}).get("kind") == "attack":
                alts = [a for a in self.usage.rivals(e)
                        if a in self.buff_on and (translate.skill_kind(a) or {}).get("kind") == "buff"]
                if alts:
                    self.pending.append({"ev": e, "t": e["t"], "alts": alts})
                    continue
            out.append(e)
        return out

    def _decide(self, now):
        """Settle parked casts: the watched buff's icon flipped (either way) from FLIP_BEFORE_S before the MP
        drop to HOLD_S after it -> that buff's cast; HOLD_S passed without -> the attack. Returns the events
        that are final now."""
        done, keep = [], []
        for p in self.pending:
            hit = next((f for f in self.flips if not f[3] and f[1] in p["alts"]
                        and p["t"] - FLIP_BEFORE_S <= f[0] <= p["t"] + HOLD_S), None)
            if hit:
                hit[3] = True
                self.usage.rebook(p["ev"], hit[1])
                done.append(p["ev"])
            elif now - p["t"] >= HOLD_S + SETTLE_S:
                done.append(p["ev"])
            else:
                keep.append(p)
        self.pending = keep
        return done

    def settle(self, now):
        """Decide what can be decided at `now` and book it (also called from the live.json beat)."""
        if not self.pending:
            return
        self._finish(self._decide(now))

    def _finish(self, done):
        if done:
            self._log_events(done)
            self._write_casts()

    def _write_casts(self):
        """live_casts.json: casts per skill, written the moment a cast is booked (live.json waits
        for its ~1 s beat); the bar checks it 10 times a second to start a pulse."""
        try:
            (H.ROOT / "live_casts.json").write_text(
                json.dumps({"t": round(time.time(), 2), "n": self.shown_counts()}),
                encoding="utf-8")
        except OSError:
            pass

    def _log_events(self, ev):
        """history/usage/<date>.csv: one row per explained HP/MP change (not regen ticks)."""
        if not ev:
            return
        try:
            d = H.ROOT / "history" / "usage"
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{datetime.now():%Y-%m-%d}.csv"
            new = not f.exists()
            with open(f, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["time", "meter", "kind", "name", "amount", "skill", "level"])
                for e in ev:
                    w.writerow([datetime.fromtimestamp(e["t"]).isoformat(timespec="milliseconds"),
                                e["meter"], e["kind"], e["name"], e["amount"],
                                e.get("skill", ""), e.get("level", "")])
        except OSError:
            pass

    # -- facts (the component calls these) ----------------------------------------------
    def begin(self, prof=None, now=None):
        """Start of a facts batch: another character starts everything over; the party flag is the
        one the last batch left."""
        now = now or time.time()
        if self.usage.totals["since"] is None:
            self.usage.totals["since"] = round(now, 1)
        if prof is not None and prof.who != self.who:      # another character: start over
            self.who = prof.who
            self._new_meters(now)
            fresh = True
        else:
            fresh = False
        if prof is not None:
            c = prof.char() or {}
            skills = c.get("skills") or {}
            self.usage.set_context(
                levels={sid: (s.get("value") or 0) for sid, s in skills.items()},
                party=self.party_n > 1, rise=self._may_rise(c, skills))
        if fresh:
            self._load_regen(prof, now)
            self._predict(prof)
        self.sync_watch(prof)

    @staticmethod
    def _may_rise(c, skills):
        """Skills whose level may be above the stored one: SP comes only with a level-up, so a skill
        last seen in the Skill window after the latest level-up can't have gone up. Unknown times:
        every skill may rise (None)."""
        since = ((c.get("stats") or {}).get("level") or {}).get("since")
        if not since:
            return None
        job = c.get("job")
        job = job.get("value") if isinstance(job, dict) else job
        tier = translate.TIER.get(job)
        out = set()
        for sid, s in skills.items():
            if s.get("max") and (s.get("value") or 0) >= s["max"]:
                continue                        # maxed: nothing to guess
            st = translate.TIER.get(s.get("job"))
            if tier is not None and st is not None and st < tier:
                continue                        # an earlier job's skill (Beginner for a Magician):
                                                # rarely changes, only the Skill window updates it
            if not s.get("seen") or s["seen"] < since:
                out.add(sid)
        return out

    def fact(self, t, kind, v, prof=None):
        if kind == "vitals":
            self._expect(v)
            self.hud_seen = True
            before = {k: len(m.rises) for k, m in self.meters.items()}
            ev = self.usage.look(t, v.get("hp"), v.get("mp"))
            if any(e["kind"] == "cast" for e in ev):
                self.last_cast_t = time.time()
            booked = self._hold(ev) if self.buff_on else ev       # casts a buff may have made wait for the icon
            if any(e["kind"] == "cast" for e in booked):
                self._write_casts()
            self._log_events(booked)
            self._finish(self._decide(t))
            self._watch_max(t, v, ev, prof)
            self._reconcile(before, prof)
        elif kind == "away":                # portal / channel change: new regen clock
            for m in self.meters.values():
                m.reset_clock(t)
            self.clock_saved = 0.0           # save the cleared clock (no stale restore)
        elif kind == "party":
            self.party_n = len(v)
        elif kind == "buffs":
            self._buffs(t, v)
            self._finish(self._decide(t))

    # -- the character's regen numbers (regen.py, stored in characters.json "regen") ---------
    def _texts(self):
        """Improved MP Recovery's text at the character's level (as many as it has)."""
        out = []
        for sid in regen.MP_SKILLS:
            lv = self.usage.level(sid)
            texts = (self.data.get(sid) or {}).get("levels") or []
            if 0 < lv <= len(texts):
                out.append(texts[lv - 1])
        return out

    def _load_regen(self, prof, now):
        d = prof.regen() if prof is not None and prof.who else {}
        self.model = regen.RegenModel(d, log=lambda s: getattr(prof, "log", print)(s))
        clock = (d or {}).get("clock") or {}
        if clock.get("t") and now - clock["t"] < CLOCK_KEEP_S:
            for k, m in self.meters.items():           # restart / update: same clock as before
                c = clock.get(k)
                if c and c.get("anchor"):
                    m.anchor, m.anchor_w = c["anchor"], c.get("w", 0.5)
        self.clock_saved = now

    def _expect(self, v):
        """Tell the meters which tick sizes the character's numbers predict (before any tick)."""
        if self.model is None:
            return
        mp = v.get("mp")
        if mp:
            self.meters["mp"].known = self.model.mp_ticks(mp[1], self._texts())
        self.meters["hp"].known = self.model.hp_ticks()

    def _predict(self, prof):
        """Predict first: as soon as the character is known, the tick sizes follow from the stored
        base max MP and the stored skill levels (a HUD read then reconciles them in _expect)."""
        if self.model is None or prof is None or not prof.who:
            return
        base = prof.base_max("mp")
        if base:
            self.meters["mp"].known = self.model.mp_ticks(base, self._texts())
        self.meters["hp"].known = self.model.hp_ticks()

    def _watch_max(self, t, v, ev, prof):
        """HUD max vs the stored base (maxwatch.py): a max buff is not a new base; a lasting
        difference is, and goes into the character's record."""
        if prof is None or not prof.who:
            return
        lvl = any(e["kind"] == "levelup" for e in ev)
        with perf.timed("max_watch"):
            for k in ("hp", "mp"):
                if v.get(k):
                    new = self.watch[k].look(t, v[k][1], prof.base_max(k), levelup=lvl,
                                             levels=self.usage.levels)
                    if new:
                        prof.learn_base_max(k, new, t, "hud")

    def _max_info(self, k, prof):
        w = self.watch[k]
        return {"base_max": prof.base_max(k) if prof is not None and prof.who else None,
                "buff": w.state()}

    def _predicted(self, k):
        """True while the tick size shown comes from the math (stored or HUD max), not a tick seen."""
        m = self.meters[k]
        if m.amount or not m.known:
            return False
        return "stored" if not self.hud_seen else "hud"

    def _reconcile(self, before, prof):
        """Ticks just seen vs the math; skill levels learned from cast costs; the clock."""
        if self.model is None or prof is None or not prof.who:
            return
        changed = False
        for k, m in self.meters.items():
            if len(m.rises) > before[k] and m.prev:
                changed |= self.model.observe(k, m.rises[-1][2], m.prev[2], self._texts())
        for sid, lv in self.usage.levels_seen.items():
            name = (self.data.get(sid) or {}).get("name")
            if prof.learn_skill_level(sid, lv, time.time(), name):
                changed = True
        now = time.time()
        if changed or now - self.clock_saved >= CLOCK_SAVE_S:
            self.clock_saved = now
            clock = {"t": round(now, 1)}
            for k, m in self.meters.items():
                if m.anchor is not None:
                    clock[k] = {"anchor": round(m.anchor, 3), "w": round(m.anchor_w, 3)}
            prof.save_regen({**self.model.data, "clock": clock})

    # -- live.json sections: fn(now, prof), evaluated in the order they are published ---------
    # (mp, skills, regen, hp, usage: the regen section sets a meter's prior from the skill text, which
    # hp's state then shows but mp's did not on the first write; the order keeps that as it was)
    def _section(self, k, now, prof):
        p = self._predicted(k)
        return {**self.meters[k].state(now), **self._max_info(k, prof),
                "predicted": bool(p), "predicted_from": p or None}

    def section_mp(self, now, prof):
        return self._section("mp", now, prof)

    def section_hp(self, now, prof):
        return self._section("hp", now, prof)

    def section_usage(self, now, prof):
        return self.usage.summary()

    def section_skills(self, now, prof):
        self.sync_watch(prof)
        self.settle(time.time())
        c = (prof.char() if prof is not None else None) or {}
        mp = self.meters["mp"].state(now)
        skills = []
        seen_lv = self.usage.levels_seen
        counts = self.shown_counts()
        # what one MP point costs you, and what a cast's items cost (the training component)
        mp_item, mp_meso, _ = self.hub.ask("mp_price") or (None, None, None)
        price_of = self.hub.ask("price_of") or (lambda name: None)
        level = ((c.get("stats") or {}).get("level") or {}).get("value")
        for sid, s in (c.get("skills") or {}).items():
            lv = max(s.get("value") or 0, seen_lv.get(sid) or 0)   # casts show a newer level
            d = self.data.get(sid) or {}
            costs = d.get("mp") or []
            cost = costs[lv - 1] if 0 < lv <= len(costs) else None
            if not lv or not cost:
                continue
            icon = DATA / "templates" / "skill_icons" / f"{sid}.png"
            texts = d.get("levels") or []
            extra = cast_extras(texts[lv - 1] if lv <= len(texts) else "", level)
            mesos = cost * mp_meso if mp_meso is not None else None
            if mesos is not None:
                for name, n in extra["items"]:
                    price = price_of(name)
                    mesos = mesos + price * n if price is not None else mesos
                mesos += extra["mesos"] or 0
            guard = None
            if sid == mpuse.GUARD_ID:           # Magic Guard: casts don't matter, what it covers does
                pct = formulas.magic_guard_pct(texts[lv - 1] if lv <= len(texts) else "")
                hp = self.meters["hp"].state(now).get("cur")
                if pct and hp is not None and mp.get("cur") is not None:
                    absorb, ehp = formulas.magic_guard_reach(hp, mp["cur"], pct)
                    last = self.usage.last_guard
                    guard = {"pct": pct, "absorb": absorb, "ehp": ehp,
                             "last": last[1] if last else None, "last_t": last[0] if last else None}
            text = texts[lv - 1] if lv <= len(texts) else ""
            hp_cost = re.search(r"HP -(\d+)", text)
            skills.append({"id": sid, "name": s.get("name") or d.get("name"), "level": lv, "guard": guard,
                           "kind": (translate.skill_kind(sid) or {}).get("kind"),
                           "hp_cost": int(hp_cost.group(1)) if hp_cost else None,
                           "cast_n": counts.get(sid, 0),
                           "active": self.buff_on.get(sid),     # buff icon showing (None: not watched / not seen yet)
                           "max": d.get("max"), "mp": cost,
                           "casts": (mp["cur"] // cost) if mp.get("cur") is not None else None,
                           "mesos": round(mesos, 1) if mesos is not None else None,
                           "mesos_with": mp_item,           # the potion that prices the MP
                           "items": [[n, k, price_of(n)] for n, k in extra["items"]],
                           "meso_varies": extra["varies"],
                           "icon": str(icon) if icon.exists() else None})
        return skills

    def section_regen(self, now, prof):
        c = (prof.char() if prof is not None else None) or {}
        regen = {}                  # only meters with a recovery skill (the bar's regen slots)
        for k, ids in REGEN_SKILLS.items():
            for sid in ids:
                lv = ((c.get("skills") or {}).get(sid) or {}).get("value") or 0
                texts = (self.data.get(sid) or {}).get("levels") or []
                if lv:
                    # the skill's own rhythm ("every 10 seconds") until ticks are measured
                    said = texts[lv - 1] if lv <= len(texts) else ""
                    m = re.search(r"every (\d+) sec", said or "")
                    if m and self.meters[k].prior is None:
                        self.meters[k].prior = float(m.group(1))
                    icon = DATA / "templates" / "skill_icons" / f"{sid}.png"
                    regen[k] = {"skill": (self.data.get(sid) or {}).get("name"), "level": lv,
                                "says": texts[lv - 1] if lv <= len(texts) else None,
                                "icon": str(icon) if icon.exists() else None}
        return regen
