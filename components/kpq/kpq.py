"""kpq.py - the party quest run tracker (plain logic, no hub: Runs is unit-testable).

PQs are data (gamedata/pq_classic.json: first map, stages in order, bonus map, other maps). A run opens at
the first reading on a PQ's first map, is in phase "stages" until the first reading on its bonus map, then
phase "bonus". It ends when the tracker confirms a map that is not a PQ map of the run (on_left), or when
the session ends (close("session")). EXP is exp_total of the tracker (EXP gained this session, across
level-ups) at the first reading of each map. The end TIME is the last PQ reading; the end EXP is the first
non-PQ reading after it (the "tail": the reading that shows the map change often carries the last kills'
EXP). Only exact PQ names move phases (an OCR garble never does).

Bonus "kills" = mobs' worth of EXP, like the training component's: bonus EXP / the bonus map's average mob
EXP weighted by spawn count (gamedata/map_mobs.json), floor. The bonus EXP/hr (bonus EXP / time in the bonus)
is only shown from 10 kills (some parties leave at once). The completion reward (the first EXP rise after
entering the bonus, within 20 s of that reading, >= 500) belongs to the Last stage, not to the bonus.

view: what the helper shows instead of the EXP tracker: the open run, then (done) the finished run until a
non-PQ reading gains EXP (not the tail reading), or a new run replaces it.
"""
KEEP = 3                    # finished runs kept (the helper steps through them); "best" lives apart
SAVE_EVERY_S = 60
MIN_KILLS_FOR_RATE = 10
REWARD_WITHIN_S = 20
REWARD_MIN = 500
_P = "1st Accompaniment <"
FALLBACK = [{
    "key": "kpq", "name": "Kerning Party Quest", "first": _P + "1st Stage>",
    "stages": [{"map": _P + "1st Stage>", "label": "Stage 1"}, {"map": _P + "2nd Stage>", "label": "Stage 2"},
               {"map": _P + "3rd Stage>", "label": "Stage 3"}, {"map": _P + "4th stage>", "label": "Stage 4"},
               {"map": _P + "Last Stage>", "label": "King Slime"}],
    "bonus": _P + "Bonus>", "other": [_P + "Exit>"]}]
_cache = {}


def load_pqs():
    """The PQ definitions from gamedata/pq_classic.json (the hardcoded KPQ when it can't be read)."""
    if "pqs" not in _cache:
        try:
            import json
            from base import paths
            pqs = json.loads(paths.asset("gamedata/pq_classic.json").read_text(encoding="utf-8"))["pqs"]
            if not pqs or any(not p.get("first") or not p.get("stages") or not p.get("bonus") for p in pqs):
                raise ValueError("incomplete")
            _cache["pqs"] = pqs
        except Exception:
            _cache["pqs"] = FALLBACK
    return _cache["pqs"]


def bonus_averages(pqs):
    """{bonus map: average mob EXP weighted by spawn count} from gamedata/map_mobs.json (missing: no entry)."""
    out = {}
    try:
        import json
        from base import paths
        maps = json.loads(paths.asset("gamedata/map_mobs.json").read_text(encoding="utf-8"))["maps"]
        want = {p["bonus"] for p in pqs}
        for m in maps.values():
            if m.get("name") in want:
                mobs = [(x["exp"], x.get("count") or 1) for x in m.get("mobs") or [] if x.get("exp")]
                if mobs:
                    out[m["name"]] = sum(e * c for e, c in mobs) / sum(c for _, c in mobs)
    except Exception:
        pass
    return out


def stages_from(pq, maps, completed, is_open, reward=0):
    """Per stage of the definition, in order: {label, exp, minutes, state}. maps = the run's visits in order
    [{map, exp, secs}]; every visit to a stage's map counts. state: "done" (a later PQ map was read),
    "now" (the open run's current map), "left" (a closed run ended there), "todo" (never reached)."""
    out = []
    for s in pq["stages"]:
        idx = [i for i, m in enumerate(maps) if m["map"] == s["map"]]
        if not idx:
            out.append({"label": s["label"], "exp": 0, "minutes": 0.0, "state": "todo"})
            continue
        state = "done" if idx[-1] < len(maps) - 1 else ("now" if is_open else "left")
        out.append({"label": s["label"], "exp": sum(maps[i]["exp"] for i in idx),
                    "minutes": round(sum(maps[i]["secs"] for i in idx) / 60, 2), "state": state})
    if reward and out:
        out[-1]["exp"] += reward
    return out


def normalize(rec, pqs):
    """A stored run from an older version (stages without state/minutes): rebuilt from its maps."""
    pq = next((p for p in pqs if p["key"] == rec.get("pq", "kpq")), None)
    st = rec.get("stages")
    if pq is None or (st and all("state" in s for s in st)):
        return rec
    maps = rec.get("maps") or []
    mm = [{"map": m["map"], "exp": m["exp"],
           "secs": max(0, (maps[i + 1]["start"] if i + 1 < len(maps) else rec.get("end", m["start"])) - m["start"])}
          for i, m in enumerate(maps)]
    stages = stages_from(pq, mm, bool(rec.get("completed")), False)
    for i, s in enumerate(stages):          # phase-2 records had the reward in the Last row: keep their EXP
        if st and i < len(st) and s["state"] != "todo":
            s["exp"] = st[i]["exp"]
    out = dict(rec, pq=pq["key"], stages=stages)
    out.setdefault("bonus_kills", 0)
    out.setdefault("bonus_rate", None)
    return out


def update_best(best, rec):
    """The FASTEST per stage (cleared stages only) and per completed run (stage time, bonus not counted)."""
    b = best.setdefault(rec["pq"], {"stages": {}})
    for s in rec["stages"]:
        old = b["stages"].get(s["label"])
        # a cleared stage that gave nothing (0 EXP) is a misread map, not a record (30 s replay rows)
        if s["state"] == "done" and s["minutes"] > 0 and s["exp"] > 0 and (old is None or s["minutes"] < old["minutes"]):
            b["stages"][s["label"]] = {"minutes": s["minutes"], "exp": s["exp"], "start": rec["start"]}
    old = b.get("run")
    if rec["completed"] and rec["stage_minutes"] > 0 and (old is None or rec["stage_minutes"] < old["stage_minutes"]):
        b["run"] = {"stage_minutes": rec["stage_minutes"], "stage_exp": rec["stage_exp"], "start": rec["start"]}
    return best


def compute_best(runs, pqs):
    best = {}
    for r in runs:
        update_best(best, normalize(r, pqs))
    return best


def pq_maps(pq):
    return {s["map"] for s in pq["stages"]} | {pq["first"], pq["bonus"]} | set(pq.get("other") or [])


class Runs:
    def __init__(self, runs=None, on_save=None, pqs=None, avg=None, best=None):
        self.pqs = pqs if pqs is not None else load_pqs()
        runs = [normalize(r, self.pqs) for r in runs or []]
        self.best = best if isinstance(best, dict) else compute_best(runs, self.pqs)   # (none yet: from the runs)
        self.runs = runs[-KEEP:]
        self.open = None            # the run in progress (internal dict)
        self.on_save = on_save      # fn(self): on phase changes, run ends, view changes, every 60 s while open
        self.avg = avg if avg is not None else bonus_averages(self.pqs)
        self.names = set().union(*(pq_maps(p) for p in self.pqs))
        self.done = None            # the finished run's record while it is still the view
        self.prev_exp = None        # exp_total at the previous reading
        self._saved_at = 0.0

    def new_session(self):
        self.prev_exp = None        # exp_total starts over

    # -- readings
    def feed(self, t, map_name, exp_total, level=None):
        """One reading is done: map_name as read (exact match only), exp_total = EXP gained this session.
        Returns the run record this reading closed, else None."""
        prev, self.prev_exp = self.prev_exp, exp_total
        o = self.open
        pq = o["pq"] if o is not None else None
        if map_name not in (pq_maps(pq) if pq else self.names):
            return self._outside(o, exp_total, level, prev)
        closed = None
        if o is not None:
            self._reward(o, t, exp_total)
        first = next((p for p in self.pqs if p["first"] == map_name), None)
        if o is not None and first is not None and len(o["_seen"]) > 1:
            closed = self.close("left")         # 1st map again after later stages: a new run
            o = None
        if o is None:
            if first is None:
                return closed                   # joined mid-run: no honest stage EXP, don't track it
            o = self.open = {"pq": first, "start": t, "level_start": level, "bonus_t": None, "bonus_exp": None,
                             "_seen": [], "maps": [], "_exp0": exp_total, "leaving": False,
                             "reward": 0, "reward_done": False}
            self.done = None
        o["last_t"], o["last_exp"], o["level_end"], o["tail"] = t, exp_total, level, None
        if not o["maps"] or o["maps"][-1]["map"] != map_name:
            o["maps"].append({"map": map_name, "start": t, "exp": 0, "_e0": exp_total})
            if map_name not in o["_seen"]:
                o["_seen"].append(map_name)
        phase_change = len(o["maps"]) == 1
        if map_name == o["pq"]["bonus"] and o["bonus_t"] is None:
            o["bonus_t"], o["bonus_exp"] = t, exp_total
            phase_change = True
        self._save(t, force=phase_change)
        return closed

    def _reward(self, o, t, exp_total):
        """The first EXP rise after the bonus was entered: the completion reward when quick and big."""
        if o["bonus_t"] is None or o["reward_done"] or t <= o["bonus_t"]:
            return
        d = exp_total - o["last_exp"]
        if d > 0:
            o["reward_done"] = True
            if t - o["bonus_t"] <= REWARD_WITHIN_S and d >= REWARD_MIN:
                o["reward"] = d

    def _outside(self, o, exp_total, level, prev):
        """A reading that isn't on a PQ map (of the open run)."""
        gained = prev is not None and exp_total > prev
        if o is None:
            if self.done is not None and gained:
                self.done = None                # EXP outside the PQ: back to the normal view
                self._save(0, force=True)
            return None
        is_tail = o.get("tail") is None
        if is_tail:
            o["tail"] = (exp_total, level)      # first non-PQ reading after the last PQ one (see _record)
        if not o["leaving"]:
            return None
        rec = self.close("left")
        if not is_tail and gained:              # a later reading than the tail gained EXP: clear at once
            self.done = None
            self._save(0, force=True)
        return rec

    def on_left(self, map_name):
        """The tracker confirmed a map change to map_name: a non-PQ map ends the run (at the next
        reading, which is the one that is processed right after this event)."""
        o = self.open
        if o is not None and map_name not in pq_maps(o["pq"]):
            o["leaving"] = True

    def close(self, reason):
        o, self.open = self.open, None
        if o is None:
            return None
        rec = self._record(o, reason)
        update_best(self.best, rec)             # before the list is trimmed
        self.runs.append(rec)
        del self.runs[:-KEEP]
        self.done = rec
        self._save(o["last_t"], force=True)
        return rec

    def _record(self, o, reason):
        t0, t1 = o["start"], o["last_t"]
        e0, e1 = o["_exp0"], o["last_exp"]
        level_end = o["level_end"]
        if o.get("tail") is not None:       # the reading that shows the map change often carries the last kills' EXP
            e1, level_end = o["tail"][0], o["tail"][1] if o["tail"][1] is not None else level_end
        pq = o["pq"]
        done = o["bonus_t"] is not None
        reward = o["reward"] if done else 0
        stage = (o["bonus_exp"] if done else e1) - e0 + reward
        bonus = e1 - o["bonus_exp"] - reward if done else 0
        total = stage + bonus
        wall = max(0.0, t1 - t0)
        maps = []
        for i, m in enumerate(o["maps"]):       # EXP between first readings of consecutive maps
            last = i + 1 == len(o["maps"])
            nxt = e1 if last else o["maps"][i + 1]["_e0"]
            nt = t1 if last else o["maps"][i + 1]["start"]
            maps.append({"map": m["map"], "start": round(m["start"]), "exp": nxt - m["_e0"],
                         "_s": nt - m["start"]})
        stages = stages_from(pq, [{"map": m["map"], "exp": m["exp"], "secs": m["_s"]} for m in maps],
                             done, reason == "open", reward)
        for m in maps:
            del m["_s"]
        avg = self.avg.get(pq["bonus"])
        bmin = (t1 - o["bonus_t"]) / 60 if done else 0.0
        kills = int(bonus / avg) if done and avg else 0
        rate = int(bonus / (bmin / 60)) if done and kills >= MIN_KILLS_FOR_RATE and bmin > 0 else None
        return {
            "pq": pq["key"], "start": round(t0), "end": round(t1), "minutes": round(wall / 60, 1),
            "stage_exp": stage, "bonus_exp": bonus, "total_exp": total,
            "exp_per_hour": int(total / (wall / 3600)) if wall > 0 else 0,
            "stage_minutes": round(((o["bonus_t"] if done else t1) - t0) / 60, 2),
            "bonus_minutes": round(bmin, 1), "bonus_kills": kills, "bonus_rate": rate,
            "level_start": o["level_start"], "level_end": level_end,
            "completed": done, "reason": reason, "stages": stages, "maps": maps,
        }

    # -- the live view
    def view(self):
        """What the helper shows instead of the EXP tracker, or None."""
        o = self.open
        if o is not None and "last_t" in o:
            return self._view(self._record(o, "open"), False)
        return self._view(self.done, True) if self.done is not None else None

    def _view(self, r, done):
        pq = next((p for p in self.pqs if p["key"] == r["pq"]), None)
        return {
            "pq": pq["name"] if pq else r["pq"], "done": done,
            "key": r["pq"], "stages": r["stages"], "stage_exp": r["stage_exp"], "stage_minutes": r["stage_minutes"],
            "bonus": {"exp": r["bonus_exp"], "minutes": r["bonus_minutes"], "kills": r["bonus_kills"],
                      "rate": r["bonus_rate"]} if r["completed"] else None,
            "total": r["total_exp"], "minutes": r["minutes"], "since": r["start"],
            "completed": r["completed"],
        }

    def _save(self, t, force=False):
        if self.on_save is not None and (force or t - self._saved_at >= SAVE_EVERY_S):
            self._saved_at = t
            self.on_save(self)

    def snapshot(self):
        o = self.open
        return {"runs": self.runs, "best": self.best, "open": self._record(o, "open") if o is not None else None,
                "view": self.view()}
