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
The skill bar (skillbar_ui.py) animates the wheels itself between updates.
"""
import json
import time

import history as H
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
        if prof is not None and prof.who != self.who:      # another character: start over
            self.who = prof.who
            self.meters = {"hp": vitals.Meter(), "mp": vitals.Meter()}
        if seen and now - self.last_write >= WRITE_EVERY_S:
            self.last_write = now
            H.write_json(self._path(), self.snapshot(prof, now), indent=None)

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
                    icon = DATA / "templates" / "skill_icons" / f"{sid}.png"
                    regen[k] = {"skill": (self.data.get(sid) or {}).get("name"), "level": lv,
                                "says": texts[lv - 1] if lv <= len(texts) else None,
                                "icon": str(icon) if icon.exists() else None}
        return {"t": round(now, 2), "character": c.get("name") or c.get("name_guess"),
                "hp": self.meters["hp"].state(now), "mp": mp, "regen": regen, "skills": skills}
