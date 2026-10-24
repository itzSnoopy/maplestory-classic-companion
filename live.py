"""live.py - the skill bar's feed: live.json, rewritten about once a second while playing.

Fed by tracker.run with the same facts as character.Profile (no pixels here):
  'vitals' {hp: [cur, max], mp: [cur, max]}  -> vitals.Meter per meter (regen tick size, period,
                                                next tick)
  the current character's skill levels (Profile) + skills_classic.json per-level MP costs
                                             -> casts left with the MP you have now

live.json:
  t, character
  hp / mp     {cur, max, tick, period, next (epoch s), sure_s, ticks_seen}
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

import history as H
import party
import vitals
from paths import APP, DATA

WRITE_EVERY_S = 0.9
# passive recovery skills that tick every 10 s; the bar shows a meter's regen slot only when the
# character has one of these at level >= 1 (chance-based ones like Critical Recovery don't count)
REGEN_SKILLS = {"hp": ["1000000"],                          # Improved HP Recovery (Warrior)
                "mp": ["2000000", "1110000", "1210000"]}    # Improved MP Recovery (Magician,
                                                            # Crusader, White Knight)


def _skills_data():
    try:
        return json.loads((APP / "skills_classic.json").read_text(encoding="utf-8"))["skills"]
    except (OSError, ValueError, KeyError):
        return {}


class Live:
    def __init__(self, path=None):
        self.path = path
        self.meters = {"hp": vitals.Meter(), "mp": vitals.Meter()}
        self.data = _skills_data()
        self.last_write = 0.0
        self.who = None
        self.names = party.Names()          # party box rows -> names (Tesseract once per row)
        self.party = {"t": None, "rows": []}

    def _path(self):
        return self.path or (H.ROOT / "live.json")

    def update(self, facts, prof=None, now=None):
        now = now or time.time()
        seen = False
        for t, kind, v in facts:
            if kind == "vitals":
                for k in ("hp", "mp"):
                    if v.get(k):
                        self.meters[k].feed(t, *v[k])
                seen = True
            elif kind == "party":
                self._party(t, v, prof)
        if prof is not None and prof.who != self.who:      # another character: start over
            self.who = prof.who
            self.meters = {"hp": vitals.Meter(), "mp": vitals.Meter()}
        if seen and now - self.last_write >= WRITE_EVERY_S:
            self.last_write = now
            H.write_json(self._path(), self.snapshot(prof, now), indent=None)

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
        for sid, s in (c.get("skills") or {}).items():
            lv = s.get("value") or 0
            d = self.data.get(sid) or {}
            costs = d.get("mp") or []
            cost = costs[lv - 1] if 0 < lv <= len(costs) else None
            if not lv or not cost:
                continue
            icon = DATA / "templates" / "skill_icons" / f"{sid}.png"
            skills.append({"id": sid, "name": s.get("name") or d.get("name"), "level": lv,
                           "max": d.get("max"), "mp": cost,
                           "casts": (mp["cur"] // cost) if mp.get("cur") is not None else None,
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
                "party": self.party}
