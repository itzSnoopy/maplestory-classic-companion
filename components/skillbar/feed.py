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
from components.skillbar import maxwatch, meter, mpuse, partyfx, regen, skillicons
from gamedata import formulas, translate
from hub import history as H

COMBAT_S = 5            # after a cast, look at HP/MP COMBAT_FPS times a second for this long (the bar's
COMBAT_FPS = 5          # pulse comes ~0.2 s after the cast instead of up to ~2.4 s; player, 2026-10-31)
HOLD_S = 1.0            # an ambiguous cast (attack vs buff of the same MP cost) waits this long for the buff icon to flip
SETTLE_S = 0.4          # ... plus this for a flip the source confirms on a second look (it keeps the first look's time)
FLIP_BEFORE_S = 0.3     # ... a flip seen this much before the MP drop counts too (the two reads come from separate looks)
CLOCK_SAVE_S = 10       # the regen clock's position goes into the character's record this often
CLOCK_KEEP_S = 90       # ... and is picked up again after a restart / update within this long
HEAL_RECENT = 30        # own Heal amounts kept per level (the recent average)
PARTY_HEAL_SEQ_MAX = 10 ** 9   # the relay's limit for the "healed" seq
# passive recovery skills that tick every 10 s; the bar shows a meter's regen slot only when the
# character has one of these at level >= 1 (chance-based ones like Critical Recovery don't count)
PARTY_RECAST_S = 3.0        # a party buff whose digits are missing for PARTY_MISS facts in a row with more than this many s left = cast again
PARTY_MISS = 4
PARTY_OFF_S = 3.0           # a party buff icon must have been off this long before an ON counts as a fresh cast (level learning)
PARTY_LEVEL_TOL = 4.0       # a measured Haste duration must be this near a table duration (s) to give a level
PARTY_M0_S = 3.0            # the minutes digit counts as read "at the cast" up to this long after it
BUFF_CAST_S = 3.0           # a buff icon coming on more than this long after a booked cast of it = a cast of its own
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


EATER_BY_LINE = {"F/P": "2100000", "I/L": "2200000", "Cleric": "2300000", "Priest": "2300000"}


def _eater_for(job):
    """The MP Eater id of the character's job line (None: not a 2nd-job mage, or unknown)."""
    j = str(job or "")
    return next((sid for k, sid in EATER_BY_LINE.items() if j.startswith(k)), None)


class Feed:
    def __init__(self, hub=None):
        self.hub = hub                      # the source in (burst looks)
        self.model = None                   # regen.RegenModel of the current character
        self.clock_saved = 0.0
        self.data = _skills_data()
        self.who = None
        self.party_n = 0                    # rows of the party box at the last look (party.py)
        self.heal_dirty = False             # Heal stats changed since the last save
        self.party_heal_post = None         # (seq, last, avg) for the relay post (a tuple, replaced whole: the link thread reads it)
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
        self.watch_sent = None                  # the owned buff ids last handed to source.watch_buffs
        self.party_watch = []                   # party buff ids (partyfx icon entries) watched while in a party
        self.watch_all_sent = None              # (owned ids, party ids) last handed to source.watch_buffs
        self.party_buff = {}                    # {party buff id: {since, ends|None, ...}} while its icon shows (live.json usage.party_buffs)
        self.party_off_t = {}                   # party buff id -> when its icon was seen OFF (since the watch began); an ON at least
                                                # PARTY_OFF_S later = a fresh cast (a shorter gap = a missed look, not a cast)

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

    def _party_ids(self):
        """Party buffs whose icon to look for in the buff list: the partyfx entries read from it, only while the party
        box has other members (their pictures are fetched from MeowDB when missing: skillicons.path)."""
        if self.party_n <= 1:
            return []
        ids = sorted(sid for sid, e in partyfx.PARTY_SKILLS.items()
                     if e["kind"] == "buff" and e.get("icon") and not e.get("same_as"))      # one id per picture
        for sid in ids:
            skillicons.path(sid)                # starts the download when missing and allowed
        return ids

    def sync_watch(self, prof):
        """Tell the source which buffs to look for (when the list changed; icons appear as the Skill window is read).
        Owned buffs come first (their cuts from the Skill window), then the party's (pictures in skillicons.folder())."""
        src = getattr(self.hub, "source", None)
        if src is None or prof is None or not prof.who:
            return
        ids = self._watch_ids(prof)
        party = self._party_ids()
        if ids != self.watch_sent:
            self.watch_sent = ids
            self.buff_on = {k: v for k, v in self.buff_on.items() if k in ids}
        if party != self.party_watch:
            self.party_watch = party
            self.party_buff = {k: v for k, v in self.party_buff.items() if k in party}
            self.party_off_t = {}
        if (ids, party) != self.watch_all_sent:
            self.watch_all_sent = (ids, party)
            try:
                src.watch_buffs(ids + [i for i in party if i not in ids], folder=skillicons.folder())
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
                if now_on and sid == mpuse.NIMBLE_ID and t - self.usage.last_cast.get(sid, -1e9) > BUFF_CAST_S:
                    self.usage.last_cast[sid] = round(t, 2)     # icon came on with no cast booked: that's the cast
            self.buff_on[sid] = now_on
        left, mins = (v or {}).get("left") or {}, (v or {}).get("mins") or {}
        for sid in self.party_watch:           # party buffs: lit while the icon shows; the end from the best source
            if sid not in on:
                self.party_buff.pop(sid, None)
                self.party_off_t.setdefault(sid, t)
                continue
            self._party_buff(sid, t, left.get(sid), mins.get(sid))

    # -- party buffs (Haste): the end, and the caster's skill level learned from the timing ---------------
    def _party_levels(self):
        return self.model.data.setdefault("party_levels", {}) if self.model is not None else {}

    def _caster_key(self, sid):
        """The caster's name when party.json has exactly one caster of the skill's jobs, else "?"."""
        try:
            party = json.loads((DATA / "party.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            party = {}
        names = partyfx.casters(party, sid, time.time())
        return names[0] if len(names) == 1 else "?"

    def _party_prior(self, sid):
        """(level, src) to assume for a new cast: the level learned for this caster (or for "?"), else the entry's."""
        rec = self._party_levels().get(sid) or {}
        for key in dict.fromkeys((self._caster_key(sid), "?")):
            lv = (rec.get(key) or {}).get("level")
            if isinstance(lv, int) and partyfx.duration(sid, lv):
                return lv, "learned"
        return (partyfx.PARTY_SKILLS.get(sid) or {}).get("level") or 1, "assumed"

    def _party_cast(self, sid, d, t, fresh):
        """A cast starts at t: `fresh` = seen happen (the icon came on after being seen off, or a recast the minutes
        digit / the digit swap showed), so t0 is its start and the duration can be learned. Not fresh = no t0."""
        d.update(fresh=fresh, t0=t if fresh else None, m0=None, learned=False)
        d["level"], d["src"] = self._party_prior(sid)
        dur = partyfx.duration(sid, d["level"])
        d["est_end"] = d["since"] + dur if dur else None

    def _party_range(self, sid, d):
        """The minutes digit at a fresh cast (m0) says duration is in [60 m0, 60 m0 + 59]: a level whose duration is
        outside is replaced by the middle candidate."""
        lo, hi = 60 * d["m0"], 60 * d["m0"] + 59
        dur = partyfx.duration(sid, d["level"])
        if dur is not None and lo <= dur <= hi:
            return
        cands = partyfx.levels_in(sid, lo, hi)
        if cands:
            d["level"], d["src"] = cands[len(cands) // 2], "range"
            d["est_end"] = d["t0"] + partyfx.duration(sid, d["level"])

    def _party_learn(self, sid, d, t):
        """The exact end is known: duration = end - t0, snapped to the table (+-PARTY_LEVEL_TOL s, and inside the
        minutes digit's range when it was read) = this caster's Haste level. Fresh casts only; once per cast."""
        if not d.get("fresh") or d.get("learned") or d.get("t0") is None or not d.get("ends") or d.get("est"):
            return
        d["learned"] = True
        lv = partyfx.level_for_duration(sid, d["ends"] - d["t0"], PARTY_LEVEL_TOL)
        if lv is None:
            return
        dur = partyfx.duration(sid, lv)
        if d.get("m0") and not 60 * d["m0"] <= dur <= 60 * d["m0"] + 59:
            return
        d["level"], d["src"] = lv, "learned"
        self._party_levels().setdefault(sid, {})[self._caster_key(sid)] = {"level": lv, "t": round(t, 1)}
        self.heal_dirty = True                  # saved with the regen record

    def _party_buff(self, sid, t, left, mins):
        """One party buff whose icon shows at fact time t. `left` = seconds from the big digits (last 60 s), `mins` =
        whole minutes from the small digit (60 s or more left: left in [60n, 60n+59]). Sources of the end, best first:
        1. big digits: exact;
        2. the minutes digit going n+1 -> n between two facts = 60(n+1) s left right there: within a second;
        3. an estimate: cast time + duration at the level (learned for this caster, else assumed 7 = 160 s, else the
           middle candidate of the minutes digit read at the cast), kept inside the minutes digit's range
           (a buff first seen mid-way: re-anchored to the middle of the range on the first mismatch).
        A recast shows as the minutes digit going up, or a minutes digit while the last read said < 60 s left (both
        are a fresh cast: t0, and the level is learned when the end becomes exact), or (digits unreadable)
        PARTY_MISS facts without digits with time left (not fresh: when it happened is unknown)."""
        d = self.party_buff.get(sid)
        if d is None:
            d = self.party_buff[sid] = {"since": round(t, 2), "ends": None, "est": True, "mins": None, "t": t, "miss": 0,
                                        "est_end": None, "anch": False}
            off = self.party_off_t.pop(sid, None)
            self._party_cast(sid, d, t, off is not None and t - off >= PARTY_OFF_S)
        recast = fresh = exact = False
        if left:
            d["ends"], d["est"], d["miss"], exact = round(t + left, 2), False, 0, True
        elif mins:
            if d["mins"] is not None and mins > d["mins"]:
                recast = fresh = True
            elif d["ends"] and not d["est"] and d["ends"] - t < 55:
                recast = fresh = True               # a minutes digit after "under a minute": cast again
            elif d["mins"] is not None and mins < d["mins"] and d["mins"] == mins + 1:
                # the 60(n+1)-second boundary was crossed between the last fact and this one
                d["ends"], d["est"], exact = round((d["t"] + t) / 2 + 60 * (mins + 1), 2), False, True
            d["miss"] = 0
        elif d["ends"] and d["ends"] - t > PARTY_RECAST_S:
            d["miss"] += 1                          # digits gone with time left: cast again (> 60 s left, no digits yet)
            if d["miss"] >= PARTY_MISS:
                recast = True
        if recast:
            d.update(since=round(t, 2), ends=None, est=True, miss=0, anch=False, mins=None)
            self._party_cast(sid, d, t, fresh)
            exact = False
        if mins:
            d["mins"] = mins
            if d.get("fresh") and d["m0"] is None and t - d["t0"] <= PARTY_M0_S:
                d["m0"] = mins                      # the digit as the cast began: narrows the level at once
                self._party_range(sid, d)
            if d["ends"] is None and d["est_end"] is not None:      # keep the estimate inside the digit's range
                left_est = d["est_end"] - t
                if left_est >= 60 * mins + 60 or left_est < 60 * mins:
                    if not d["anch"]:
                        d["est_end"], d["anch"] = t + 60 * mins + 30, True
                    else:
                        d["est_end"] = t + min(max(left_est, 60 * mins), 60 * mins + 59)
        if exact:
            self._party_learn(sid, d, t)
        d["t"] = t

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
            self._heal_stats(booked)
            self._party_heal_stats(booked)
            self._eater_last()
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
        self._seed_eater()
        self._post_tuple()                      # the stored party Heal record goes out from the first post

    def _heal_stats(self, ev):
        """Own Heal rises that did not end at max HP (a capped one is cut short) go into the
        character's record under "heal": {level: {n, sum, min, max, recent, last_t}}."""
        if self.model is None:
            return
        for e in ev:
            if e["kind"] != "heal" or e["name"] != "Heal" or e.get("to_full") or e["amount"] <= 0:
                continue
            lv = str(self.usage.level(mpuse.HEAL_ID) or 0)
            st = self.model.data.setdefault("heal", {}).setdefault(lv, {})
            a = int(e["amount"])
            st["n"] = int(st.get("n") or 0) + 1
            st["sum"] = int(st.get("sum") or 0) + a
            st["min"] = min(a, st.get("min") or a)
            st["max"] = max(a, st.get("max") or a)
            st["recent"] = ((st.get("recent") or []) + [a])[-HEAL_RECENT:]
            st["last_t"] = round(e["t"], 1)
            self.heal_dirty = True

    def _party_heal_stats(self, ev):
        """A party member's Heal on me ("Heal (party?)") goes into the character's regen record under
        "party_heal": {seq, n, sum, min, max, recent[30], last, last_t}. seq counts every one (capped too: the
        caster's Heal credit goes by it); n / sum / min / max / recent only uncapped rises (a capped one is cut
        short). Not per level: the caster's level is unknown."""
        if self.model is None:
            return
        for e in ev:
            if e["kind"] != "heal" or e["name"] != "Heal (party?)" or e["amount"] <= 0:
                continue
            st = self.model.data.setdefault("party_heal", {})
            a = int(e["amount"])
            st["seq"] = min(PARTY_HEAL_SEQ_MAX, int(st.get("seq") or 0) + 1)
            st["last"], st["last_t"] = a, round(e["t"], 1)
            if not e.get("to_full"):
                st["n"] = int(st.get("n") or 0) + 1
                st["sum"] = int(st.get("sum") or 0) + a
                st["min"] = min(a, st.get("min") or a)
                st["max"] = max(a, st.get("max") or a)
                st["recent"] = ((st.get("recent") or []) + [a])[-HEAL_RECENT:]
            self.heal_dirty = True
        self._post_tuple()

    def _post_tuple(self):
        """(seq, last, avg) as ints for the party post, or None while no party Heal was received."""
        st = (self.model.data.get("party_heal") if self.model else None) or {}
        seq = int(st.get("seq") or 0)
        if seq <= 0:
            self.party_heal_post = None
            return
        n = int(st.get("n") or 0)
        avg = int(round(st["sum"] / n)) if n and st.get("sum") else 0
        self.party_heal_post = (seq, max(0, min(10 ** 6, int(st.get("last") or 0))), max(0, min(10 ** 6, avg)))

    def party_heal_info(self):
        """live.json usage.party_heal: {avg, n, last, last_t, seq} or None (no party Heal received yet)."""
        st = (self.model.data.get("party_heal") if self.model else None) or {}
        seq = int(st.get("seq") or 0)
        if seq <= 0:
            return None
        n = int(st.get("n") or 0)
        return {"avg": int(round(st["sum"] / n)) if n and st.get("sum") else None, "n": n,
                "last": st.get("last"), "last_t": st.get("last_t"), "seq": seq}

    def _seed_eater(self):
        """The stored last absorb tells the usual size from the first look of a session."""
        e = (self.model.data.get("eater") if self.model else None) or {}
        if e.get("last") and not self.usage.eater_recent:
            self.usage.eater_recent.append(int(e["last"]))

    def _eater_last(self):
        """The last MP Eater absorb, kept in the character's regen record ("eater": {last, t}) so the
        bar shows it after a restart too. Only the last one: the amount depends on the mob (player)."""
        le = self.usage.last_eater
        if self.model is None or not le:
            return
        cur = self.model.data.get("eater") or {}
        if cur.get("t") != le[0]:
            self.model.data["eater"] = {"last": int(le[1]), "t": le[0]}
            self.heal_dirty = True              # same save path as the Heal stats

    def eater_info(self):
        """live.json skills entry of MP Eater: {last, t, cd} or None (no absorb seen yet); cd = its
        cooldown in s (the bar sweeps it from t)."""
        e = (self.model.data.get("eater") if self.model else None) or {}
        return {"last": e["last"], "t": e.get("t"), "cd": mpuse.EATER_CD_S} if e.get("last") else None

    def recovery_info(self, now):
        """live.json skills entry of Recovery: {tick, t, ticks, first, every, n, cd}. tick = HP per
        tick at the character's level (0: unknown); cd = its cooldown in s from the cast (skill data);
        t = the last cast while its ticks or cooldown still run (else None), ticks = Recovery ticks
        booked since (the bar re-syncs its sweep on them)."""
        u = self.usage
        tick = formulas.recovery_tick(u._skill_text(mpuse.RECOVERY_ID))
        lv = u.level(mpuse.RECOVERY_ID)
        cds = (self.data.get(mpuse.RECOVERY_ID) or {}).get("cd") or []
        cd = cds[lv - 1] if 0 < lv <= len(cds) else (cds[-1] if cds else 0)
        live = u.recovery_cast is not None and now - u.recovery_cast <= max(mpuse.RECOVERY_S, cd)
        return {"tick": tick, "t": u.recovery_cast if live else None,
                "ticks": list(u.recovery_ticks) if live else [],
                "first": mpuse.RECOVERY_FIRST_S, "every": mpuse.RECOVERY_EVERY_S,
                "n": formulas.RECOVERY_TICKS, "cd": cd}

    def nimble_info(self, now):
        """live.json skills entry of Nimble Feet: {t, dur, cd, speed}. t = the last cast while the buff
        or its cooldown runs (else None); dur / cd in s at the character's level (skill data); speed =
        the bonus its text states ("speed +10")."""
        sid = mpuse.NIMBLE_ID
        d = self.data.get(sid) or {}
        lv = self.usage.level(sid)

        def at(key):
            v = d.get(key) or []
            return v[lv - 1] if 0 < lv <= len(v) else (v[-1] if v else 0)
        dur, cd = at("time"), at("cd")
        m = re.search(r"speed \+(\d+)", at("levels") or "")
        t = self.usage.last_cast.get(sid)
        live = t is not None and now - t <= max(dur, cd)
        return {"t": t if live else None, "dur": dur, "cd": cd, "speed": int(m.group(1)) if m else None}

    def heal_info(self):
        """live.json skills entry of Heal: {avg, recent, n, level} or None (no uncapped Heal yet)."""
        lv = self.usage.level(mpuse.HEAL_ID)
        st = ((self.model.data.get("heal") or {}).get(str(lv)) if self.model else None) or {}
        n = int(st.get("n") or 0)
        if not n:
            return None
        rec = st.get("recent") or []
        return {"avg": int(round(st["sum"] / n)),
                "recent": int(round(sum(rec) / len(rec))) if rec else None, "n": n, "level": lv}

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
        if self.heal_dirty:                 # new Heal stats: save them now
            changed, self.heal_dirty = True, False
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
        return {**self.usage.summary(), "party_heal": self.party_heal_info(),
                "party_buffs": {k: {"since": v["since"], "ends": v["ends"] or v["est_end"], "est": v["ends"] is None, "mins": v["mins"],
                                    "level": v["level"], "src": v["src"]}
                                for k, v in self.party_buff.items()}}

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
        own_eater = _eater_for(c.get("job"))  # one MP Eater slot: the job's (the Skill-window read can
        eater_shown = False                     # store all three ids for the one name, 2026-11-06)
        for sid, s in (c.get("skills") or {}).items():
            lv = max(s.get("value") or 0, seen_lv.get(sid) or 0)   # casts show a newer level
            d = self.data.get(sid) or {}
            costs = d.get("mp") or []
            cost = costs[lv - 1] if 0 < lv <= len(costs) else None
            eater = sid in mpuse.EATER_IDS      # MP Eater: passive, no cost, but its slot shows the last absorb
            if not lv or (not cost and not eater):
                continue
            if eater:
                if (own_eater and sid != own_eater) or (not own_eater and eater_shown):
                    continue
                eater_shown = True
            icon = DATA / "templates" / "skill_icons" / f"{sid}.png"
            texts = d.get("levels") or []
            extra = cast_extras(texts[lv - 1] if lv <= len(texts) else "", level)
            mesos = cost * mp_meso if (mp_meso is not None and cost) else None
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
                           "attacks": (translate.skill_kind(sid) or {}).get("attacks"),   # Heal: "undead"
                           "hp_cost": int(hp_cost.group(1)) if hp_cost else None,
                           "heal": self.heal_info() if sid == mpuse.HEAL_ID else None,
                           "eater": self.eater_info() if eater else None,
                           "recovery": self.recovery_info(now) if sid == mpuse.RECOVERY_ID else None,
                           "nimble": self.nimble_info(now) if sid == mpuse.NIMBLE_ID else None,
                           "cast_n": counts.get(sid, 0),
                           "active": self.buff_on.get(sid),     # buff icon showing (None: not watched / not seen yet)
                           "max": d.get("max"), "mp": cost,
                           "casts": (mp["cur"] // cost) if (cost and mp.get("cur") is not None) else None,
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
