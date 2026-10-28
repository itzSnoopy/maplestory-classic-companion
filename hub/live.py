"""live.py - the skill bar's feed: live.json, rewritten about once a second while playing.

Fed by tracker.run with the same facts as character.Profile (no pixels here):
  'vitals' {hp: [cur, max], mp: [cur, max]}  -> mpuse.Usage: explains each change (regen tick,
                                                casts, potions, hits...) and feeds vitals.Meter
                                                per meter (the regen clock: next tick)
  'away'  (screen went dark: portal / channel change) -> the regen clocks start over
  the current character's skill levels (Profile) + skills_classic.json per-level MP costs
                                             -> casts left with the MP you have now

live.json:
  t, character
  hp / mp     {cur, max, tick, period, next (epoch s), sure_s, ticks_seen, clock, check_every,
              checks, restarts}   (vitals.Meter.state)
  usage       {since, hp/mp {kind: {name: {n, amount}}}, recent [events], levels_seen}
              what the HP/MP went on since the tracker started (mpuse.Usage.summary)
  training    this map visit: kills, casts, potions, mesos, per kill / hour / to level
              (training.Training.summary; all-time per map in history/training.json)
  regen       {mp: {skill, level, says}}   what the skill data promises (Improved MP Recovery)
  skills      [{id, name, level, max, mp, casts, icon}]   skills with an MP cost, level >= 1;
              icon = templates/skill_icons/<id>.png when it was cut from the Skill window
  party       {t, rows [{name, me, on_map, hp (% from the box, None off your map), leader}]}
              the party box under the minimap (party.py), last look; rows [] = no party box
The skill bar (skillbar_ui.py) animates the wheels itself between updates.
"""
import json
import re
import time

import csv
from datetime import datetime

from hub import history as H
from components.skillbar import mpuse
from components.training import training
from engine import party
from components.skillbar import regen
from engine import vitals
from base.paths import DATA, asset

WRITE_EVERY_S = 0.9
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


class Live:
    def __init__(self, path=None):
        self.path = path
        self.model = None                   # regen.RegenModel of the current character
        self.clock_saved = 0.0
        self._new_meters()
        self.data = _skills_data()
        self.last_write = 0.0
        self.who = None
        self.names = party.Names()          # party box rows -> names (Tesseract once per row)
        self.party = {"t": None, "rows": []}

    def _new_meters(self, t=None):
        self.meters = {"hp": vitals.Meter(), "mp": vitals.Meter()}
        self.usage = mpuse.Usage(self.meters)
        self.usage.reset_totals(t)
        if getattr(self, "training", None):
            self.training.close(t or time.time())
        self.training = training.Training(self.usage)
        # every EXP rise also goes to the monster album (tracker.py sets rise_hook), with the party
        # state *now*: EXP is only shared with members on your map (the party box row includes
        # you), not "partied at some point of this visit" (visit.party, sticky: 2026-10-25 a whole
        # solo stretch on Caution Falling Down was skipped because of it)
        self.training.on_rise = lambda t, m, d, _visit_party: (
            getattr(self, "rise_hook", None)
            and self.rise_hook(t, m, d, self.party_on_map(), self.training.exp_gap))

    def _path(self):
        return self.path or (H.ROOT / "live.json")

    def wants_fast(self, now):
        """The regen clock wants looks at burst rate now (vitals.Meter.wants_fast)."""
        return any(m.wants_fast(now) for m in self.meters.values())

    def burst_in(self, now):
        """Seconds until the next burst of looks (0 = now, None = none planned)."""
        due = [d for d in (m.burst_in(now) for m in self.meters.values()) if d is not None]
        return min(due) if due else None

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

    def update(self, facts, prof=None, now=None):
        now = now or time.time()
        seen = False
        if self.usage.totals["since"] is None:
            self.usage.totals["since"] = round(now, 1)
        if prof is not None and prof.who != self.who:      # another character: start over
            self.who = prof.who
            self._new_meters(now)
            self._load_regen(prof, now)
        if prof is not None:
            c = prof.char() or {}
            self.usage.set_context(
                levels={sid: (s.get("value") or 0) for sid, s in (c.get("skills") or {}).items()},
                party=len(self.party["rows"]) > 1)
        for t, kind, v in facts:
            if kind == "vitals":
                self._expect(v)
                before = {k: len(m.rises) for k, m in self.meters.items()}
                self._log_events(self.usage.look(t, v.get("hp"), v.get("mp")))
                self._reconcile(before, prof)
                seen = True
            elif kind == "away":                # portal / channel change: new regen clock
                for m in self.meters.values():
                    m.reset_clock(t)
                self.clock_saved = 0.0           # save the cleared clock (no stale restore)
            elif kind == "map":                 # a new map visit for the training costs
                self.training.on_map(t, v)
            elif kind == "exp":                 # EXP bar on a quick look: kills
                self.training.on_exp(t, v["cur"], v["pct"])
            elif kind == "party":
                self._party(t, v, prof)
                if len(self.party["rows"]) > 1 and self.training.visit is not None:
                    self.training.visit.party = True     # EXP here includes party shares
        if seen and now - self.last_write >= WRITE_EVERY_S:
            self.last_write = now
            H.write_json(self._path(), self.snapshot(prof, now), indent=None)

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

    def exp_rate(self, now=None):
        """EXP/hr on this map visit (exprate.py summary), or None outside a visit."""
        v = self.training.visit if self.training is not None else None
        return v.rate.summary(now or time.time()) if v is not None else None

    def party_on_map(self, now=None, max_age=10.0):
        """Another party member is on your map right now (so your EXP rises are shares)."""
        now = now or time.time()
        p = self.party
        if not p.get("t") or now - p["t"] > max_age:
            return False
        return sum(1 for r in p["rows"] if r.get("on_map")) > 1

    def _party(self, t, rows, prof):
        c = (prof.char() if prof is not None else None) or {}
        me = c.get("name") or c.get("name_guess")
        mates = [m.get("name") for m in (H.read_json(H.ROOT / "party.json", {}).get("members")
                                         or [])]
        known = [n for n in [me] + mates if n]
        if len(rows) != len(self.party["rows"]):
            self.names.reset()
        out = []
        for i, r in enumerate(rows):
            name = self.names.name(i, r.get("mask"), known, t)
            out.append({"name": name, "me": bool(me and name == me), "on_map": r["on_map"],
                        "hp": r["hp"], "leader": r["leader"]})
        # one unreadable row and one partner not matched to any row: that's them
        missing = [n for n in mates if n and n not in {o["name"] for o in out}]
        blank = [o for o in out if not o["name"] or o["name"] not in known]
        if len(missing) == 1 and len(blank) == 1:
            blank[0]["name"] = missing[0]
        self.party = {"t": round(t, 2), "rows": out}

    def snapshot(self, prof, now):
        c = (prof.char() if prof is not None else None) or {}
        mp = self.meters["mp"].state(now)
        skills = []
        seen_lv = self.usage.levels_seen
        mp_item, mp_meso, _ = self.training.mp_price()      # what one MP point costs you
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
                    price = self.training._price(name)
                    mesos = mesos + price * n if price is not None else mesos
                mesos += extra["mesos"] or 0
            skills.append({"id": sid, "name": s.get("name") or d.get("name"), "level": lv,
                           "max": d.get("max"), "mp": cost,
                           "casts": (mp["cur"] // cost) if mp.get("cur") is not None else None,
                           "mesos": round(mesos, 1) if mesos is not None else None,
                           "mesos_with": mp_item,           # the potion that prices the MP
                           "items": [[n, k, self.training._price(n)] for n, k in extra["items"]],
                           "meso_varies": extra["varies"],
                           "icon": str(icon) if icon.exists() else None})
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
        return {"t": round(now, 2), "character": c.get("name") or c.get("name_guess"),
                "hp": self.meters["hp"].state(now), "mp": mp, "regen": regen, "skills": skills,
                "usage": self.usage.summary(), "training": self.training.summary(now),
                "party": self.party}
