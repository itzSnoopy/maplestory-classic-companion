"""history.py - long-term records written by the tracker, readable without any AI.

  journal.md              human-readable log: sessions, level-ups, map changes, deaths
  history/sessions.csv    one row per play session
  history/levels.csv      one row per level-up (written by tracker.py)
  history/map_stats.json  per-map totals across all sessions (active time, EXP, EXP/hr)
  digest.json             compact summary (~1-2 KB) for a quick check-in by Claude or a script
"""
import csv
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from paths import APP, DATA
REF_TABLE = APP / "exp_table_classic.json"


def set_root(root):
    """Where history lives (default: this folder). tracker.set_root() calls this."""
    global ROOT, HISTORY, JOURNAL, SESSIONS_CSV, LEVELS_CSV, MAP_STATS, DIGEST, STATUS
    ROOT = Path(root)
    HISTORY = ROOT / "history"
    JOURNAL = ROOT / "journal.md"
    SESSIONS_CSV = HISTORY / "sessions.csv"
    LEVELS_CSV = HISTORY / "levels.csv"
    MAP_STATS = HISTORY / "map_stats.json"
    DIGEST = ROOT / "digest.json"
    STATUS = ROOT / "status.json"


set_root(DATA)

SESSION_FIELDS = ["start", "end", "end_reason", "active_s", "level_start", "pct_start",
                  "level_end", "pct_end", "exp_gained", "exp_per_active_hour", "level_ups",
                  "deaths", "exp_lost", "main_map", "folder"]


# ------------------------------------------------------------------ small helpers
def fmt_secs(s):
    if s is None or s < 0 or s > 999 * 3600:
        return "?"
    s = int(s)
    if s < 60:
        return f"{s}s"
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def write_json(path, data, indent=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_csv(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def append_csv(path, row, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def _num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def lvl_pct(level, pct):
    if level is None:
        return f"{pct:.2f}%" if pct is not None else "?"
    return f"Lv {level} {pct:.2f}%" if pct is not None else f"Lv {level}"


# ------------------------------------------------------------------ journal
class Journal:
    """Appends one line per event to journal.md, with a header per day."""

    def __init__(self, path=None):
        self.path = path = path or JOURNAL
        self.last_day = None
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    self.last_day = line[3:].strip()
        else:
            path.write_text("# MapleStory Classic journal\n\nWritten automatically by tracker.py. "
                            "Newest entries at the bottom.\n", encoding="utf-8")

    def write(self, text, when=None):
        when = when or datetime.now()
        day = when.strftime("%Y-%m-%d (%a)")
        out = []
        if day != self.last_day:
            out.append(f"\n## {day}\n\n")
            self.last_day = day
        out.append(f"- {when:%H:%M}  {text}\n")
        with open(self.path, "a", encoding="utf-8") as f:
            f.writelines(out)
        print(f"  [journal] {text}")


# ------------------------------------------------------------------ aggregates
def add_map_stint(name, active_s, exp, level):
    """Fold one stay on a map into the all-time per-map totals."""
    if not name or active_s < 30:
        return
    d = read_json(MAP_STATS, {})
    m = d.setdefault(name, {"active_s": 0, "exp": 0, "visits": 0,
                            "level_min": level, "level_max": level, "last_seen": None})
    m["active_s"] += round(active_s)
    m["exp"] += int(exp)
    m["visits"] += 1
    if level:
        m["level_min"] = min(m["level_min"] or level, level)
        m["level_max"] = max(m["level_max"] or level, level)
    m["last_seen"] = datetime.now().isoformat(timespec="minutes")
    write_json(MAP_STATS, d)


def add_session(row):
    append_csv(SESSIONS_CSV, row, SESSION_FIELDS)


# ------------------------------------------------------------------ digest
def build_digest():
    """Compact snapshot of everything worth knowing; rebuilt by the tracker regularly."""
    status = read_json(STATUS, {})
    sessions = read_csv(SESSIONS_CSV)
    levels = read_csv(LEVELS_CSV)
    maps = read_json(MAP_STATS, {})
    ref = {int(k): v for k, v in read_json(REF_TABLE, {}).items() if not k.startswith("_")}
    now = datetime.now()

    # per-day totals, last 7 days
    days = {}
    for s in sessions:
        d = (s.get("start") or "")[:10]
        if not d:
            continue
        x = days.setdefault(d, {"active": 0, "exp": 0, "level_ups": 0, "sessions": 0})
        x["active"] += _num(s.get("active_s"), int) or 0
        x["exp"] += _num(s.get("exp_gained"), int) or 0
        x["level_ups"] += _num(s.get("level_ups"), int) or 0
        x["sessions"] += 1
    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    last7 = [{"date": d, "played": fmt_secs(x["active"]), "exp": x["exp"],
              "level_ups": x["level_ups"], "sessions": x["sessions"]}
             for d, x in sorted(days.items()) if d >= cutoff]
    act7 = sum(x["active"] for d, x in days.items() if d >= cutoff)
    exp7 = sum(x["exp"] for d, x in days.items() if d >= cutoff)
    rate7 = round(exp7 / (act7 / 3600)) if act7 >= 600 else None

    # time per level (active play), most recent 15
    lv = [{"lv": _num(r.get("level_reached"), int), "at": (r.get("time") or "")[:16],
           "took": fmt_secs(_num(r.get("active_seconds_in_prev_level"))),
           "map": r.get("map")} for r in levels[-15:]]

    # map ranking by EXP per active hour (only maps with >= 5 min of data)
    ranking = sorted(
        ({"map": k, "exp_per_hour": round(v["exp"] / (v["active_s"] / 3600)),
          "played": fmt_secs(v["active_s"]), "levels": f"{v['level_min']}-{v['level_max']}",
          "last_seen": v.get("last_seen")}
         for k, v in maps.items() if v.get("active_s", 0) >= 300),
        key=lambda m: -m["exp_per_hour"])[:10]

    # projection for the next levels
    level, cur = status.get("level"), status.get("exp_current")
    basis = status.get("exp_per_hour_recent") or rate7
    proj = []
    if level and basis:
        left = (status.get("exp_needed") or ref.get(level, 0)) - (cur or 0)
        for L in range(level, min(level + 5, 100)):
            need = left if L == level else ref.get(L)
            if not need:
                break
            proj.append({"to_lv": L + 1, "exp": need, "hours": round(need / basis, 2)})

    recent = [{"start": s.get("start", "")[:16], "played": fmt_secs(_num(s.get("active_s"))),
               "from": lvl_pct(_num(s.get("level_start"), int), _num(s.get("pct_start"))),
               "to": lvl_pct(_num(s.get("level_end"), int), _num(s.get("pct_end"))),
               "exp_per_hour": _num(s.get("exp_per_active_hour"), int),
               "map": s.get("main_map"), "end": s.get("end_reason")} for s in sessions[-5:]]

    digest = {
        "generated": now.isoformat(timespec="seconds"),
        "now": {k: status.get(k) for k in ("state", "level", "pct", "exp_current", "exp_needed",
                                           "eta_to_level", "exp_per_hour_recent", "map",
                                           "time_in_level_active", "updated")},
        "last_7_days": last7,
        "exp_per_active_hour_7d": rate7,
        "level_times": lv,
        "best_maps": ranking,
        "projection": {"basis_exp_per_hour": basis, "levels": proj},
        "recent_sessions": recent,
        "totals": {"sessions": len(sessions), "level_ups_logged": len(levels),
                   "played": fmt_secs(sum(_num(s.get("active_s"), int) or 0 for s in sessions))},
    }
    write_json(DIGEST, digest, indent=1)
    return digest


# ------------------------------------------------------------------ cleanup
def cleanup(retention_days=14):
    """Delete bulky per-session images older than retention_days; keep CSV/summary files
    and the screenshots/levelups gallery forever."""
    cutoff = datetime.now() - timedelta(days=retention_days)
    freed = 0
    for sd in (ROOT / "sessions").glob("*"):
        try:
            when = datetime.strptime(sd.name, "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        if when >= cutoff:
            continue
        for sub in ("frames", "levelups"):
            p = sd / sub
            if p.exists():
                freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p, ignore_errors=True)
    if freed:
        print(f"  cleanup: removed {freed / 1e6:.0f} MB of old session images")
    return freed
