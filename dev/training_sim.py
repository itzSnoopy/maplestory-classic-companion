"""dev/training_sim.py - replay a dev/vitals_log.py recording (with EXP columns) through
mpuse.Usage + training.Training, as one visit to MAP, and print the training summary.

  python dev/training_sim.py LOG.csv "Map Name" [fps=2] [need=EXP_NEEDED] [skill_id=level ...]

fps: looks per second to keep from the recording (the live tracker looks 1-2/s; bursts aside).
need: EXP needed for the level (for the to-level numbers). Writes nothing.
"""
import os
import bisect
import csv
import json
import sys
from pathlib import Path
os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")   # simulations never go online (party relay, updates)

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from components.skillbar import mpuse  # noqa: E402
from components.training import training  # noqa: E402
from engine import vitals  # noqa: E402


def main():
    path, mapname = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]
    fps = float(next((a for a in rest if "=" not in a), 2))
    kv = dict(a.split("=", 1) for a in rest if "=" in a)
    need = int(kv.pop("need")) if "need" in kv else None
    levels = {k: int(v) for k, v in kv.items()}
    rows = list(csv.DictReader(open(path)))
    T = [float(r["t"]) for r in rows]
    fixed = lambda: dict(vitals.REGEN_DEFAULTS)     # noqa: E731
    meters = {k: vitals.Meter(prior=10.0, settings_fn=fixed) for k in ("hp", "mp")}
    u = mpuse.Usage(meters)
    u.set_context(levels=levels)
    tr = training.Training(u, path=Path("NUL"))
    tr._save = lambda *a, **k: None                  # replay: never write
    tr.on_map(T[0], mapname)
    tr.set_progress(exp_needed=need)
    t, rises = T[0], []
    prev_exp = None
    while t <= T[-1]:
        r = rows[bisect.bisect_right(T, t) - 1]
        hp = [int(r["hp"]), int(r["hp_max"])] if r["hp"] else None
        mp = [int(r["mp"]), int(r["mp_max"])] if r["mp"] else None
        if hp or mp:
            u.look(t, hp, mp)
        if r.get("exp"):
            cur, pct = int(r["exp"]), float(r["exp_pct"])
            if prev_exp is not None and cur != prev_exp:
                rises.append(cur - prev_exp)
            prev_exp = cur
            tr.on_exp(t, cur, pct)
        t += 1 / fps
    s = tr.summary(T[-1])
    print("EXP rises seen:", rises)
    print("mobs on map:", tr.mobs_by_map.get(mapname))
    print(json.dumps({k: v for k, v in s.items() if k not in ("casts", "potions")}, indent=1))
    print("casts:", s["casts"], "\npotions:", s["potions"])


if __name__ == "__main__":
    main()
