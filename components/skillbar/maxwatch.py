"""maxwatch.py - the HUD's max HP / MP against the character's stored base: a buff or a real change?

Max HP/MP is long-lived (Profile keeps it from the Stat window); a max buff such as Hyper Body
(Max HP +10..30% for 100..300 s) raises the shown max for a while. Each look says:
  - HUD max == base                      -> no buff, nothing to learn
  - HUD max == floor(base x (1 + p/100)) (+-1) for a level p of a buff skill (gamedata/formulas.py
    buff_pct over every level of the skill, read from skills_classic.json)   -> a buff {id, name,
    pct, since, ends}; the base stays
  - anything else that lasts (the same new max for N_SAME looks in a row, or right after a level-up)
    -> a real base change: look() returns it, the feed stores it (Profile.learn_base_max)
A buff is cleared when the HUD max is back at the base, or after `ends` + SLACK_S. If the HUD still
shows the buffed value after that, it is not re-detected as a buff: it counts towards a base change.
Pure model (no pixels, no files); formulas.STATUS says which parts are guesses.
"""
from gamedata import formulas

BUFF_SKILLS = ["1311005"]       # Hyper Body (3rd job Dragon Knight); more max buffs: add ids
N_SAME = 5                      # looks of the same new max before it is believed to be a new base
SLACK_S = 15                    # a buff may run this long past its time before it is dropped
TOL = 1                         # the shown max may differ from floor(base x (1 + p/100)) by this


class MaxWatch:
    def __init__(self, kind, skills, buff_ids=None):
        self.kind = kind                    # "hp" | "mp"
        self.options = []                   # (skill id, name, level, pct, seconds)
        for sid in (buff_ids if buff_ids is not None else BUFF_SKILLS):
            rec = (skills or {}).get(sid) or {}
            for lv in range(1, len(rec.get("levels") or []) + 1):
                pct = formulas.buff_pct(rec, lv, kind)
                if pct:
                    self.options.append((sid, rec.get("name") or sid, lv, pct,
                                         formulas.buff_seconds(rec, lv)))
        self.buff = None                    # {id, name, level, pct, since, ends}
        self.cand = None                    # [value, looks in a row]
        self.last = None                    # the previous look's HUD max
        self.spent = None                   # HUD max of a buff that ran out but still shows

    def reset(self):
        self.buff = self.cand = self.last = self.spent = None

    def state(self):
        b = self.buff
        return ({"id": b["id"], "name": b["name"], "pct": b["pct"], "ends": round(b["ends"], 1)}
                if b else None)

    def _explain(self, t, hud, base, levels):
        best = None
        for sid, name, lv, pct, secs in self.options:
            diff = abs(hud - formulas.buffed_max(base, pct))
            if diff <= TOL and hud > base:
                key = (diff, 0 if (levels or {}).get(sid) == lv else 1, lv)
                if best is None or key < best[0]:
                    best = (key, sid, name, lv, pct, secs)
        if best is None:
            return None
        _, sid, name, lv, pct, secs = best
        return {"id": sid, "name": name, "level": lv, "pct": pct, "since": t, "ends": t + secs}

    def look(self, t, hud, base, levelup=False, levels=None):
        """One HUD look. hud: the max shown; base: the stored base (None = unknown); levelup: a
        level-up was just seen; levels: {skill id: level} of the character (tie-break only).
        Returns the new base to store, or None."""
        if not hud:
            return None
        jump = self.last is not None and hud != self.last
        self.last = hud
        if base is not None and hud == base:
            self.buff = self.cand = self.spent = None
            return None
        if base is not None:
            b = self.buff
            if b and t > b["ends"] + SLACK_S:
                self.spent, self.buff, b = hud, None, None
            if b:
                if abs(hud - formulas.buffed_max(base, b["pct"])) <= TOL:
                    return None                 # still the same buff
                self.buff = None                # something else now
            if hud != self.spent:
                b = self._explain(t, hud, base, levels)
                if b:
                    self.buff, self.cand = b, None
                    return None
        # a lasting difference: N_SAME looks in a row, or right after a level-up
        if self.cand and self.cand[0] == hud:
            self.cand[1] += 1
        else:
            self.cand = [hud, 1]
        if (levelup and jump and base is not None) or self.cand[1] >= N_SAME:
            self.cand = self.spent = self.buff = None
            return hud
        return None
