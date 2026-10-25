"""dev/vitals_log.py - log the status bar's HP/MP numbers several times a second (regen studies).

  python dev/vitals_log.py [seconds=120] [fps=4] [out.csv]

Runs next to the live tracker (its own capture session, only the HP/MP strip and the EXP text are
copied). Writes t, hp, hp_max, mp, mp_max, exp, exp_pct (blank when unreadable) to the CSV
(default: print only). EXP: known glyphs only (kill jumps for training.py).
"""
import csv
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
import lean  # noqa: F401,E402
import glyphs  # noqa: E402
import parse  # noqa: E402
import vitals  # noqa: E402
from layout import anchor_for, place  # noqa: E402
from paths import load_config  # noqa: E402
from sources.wgc import WgcFrames  # noqa: E402


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 120
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 4
    out = sys.argv[3] if len(sys.argv) > 3 else None
    src = WgcFrames({"wgc": {"min_update_ms": int(1000 / fps) - 20}})
    rows, t0 = [], time.time()
    full = src.grab()
    box = vitals.strip_box(full.size)
    rc = load_config()["regions"]["exp"]
    ebox = place(full, list(rc["box"]), (1920, 1080), anchor_for("exp", rc), "exp")
    while time.time() - t0 < secs:
        tick = time.time()
        img, eimg = src.grab_parts([{"box": box}, {"box": ebox}])
        r = vitals.read(img, origin=True, learn=False)
        hp, mp = r["hp"] or [None, None], r["mp"] or [None, None]
        g = glyphs.read(eimg, rc.get("white_min", 230))
        cur, _, pct = parse.parse_exp(g) if g and "?" not in g else (None, None, None)
        rows.append([round(tick, 3), hp[0], hp[1], mp[0], mp[1], cur, pct])
        time.sleep(max(0.0, 1 / fps - (time.time() - tick)))
    src.close()
    if out:
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "hp", "hp_max", "mp", "mp_max", "exp", "exp_pct"])
            w.writerows(rows)
    print(f"{len(rows)} rows")


if __name__ == "__main__":
    main()
