"""dev/regen_live.py - try the regen clock + HP/MP usage live, next to the running tracker.

  python dev/regen_live.py OUT_DIR [seconds=120] [fps=2]

Looks at the game like the tracker would (HP/MP strip at `fps`, plus the clock's bursts with the
frame-rate boost) through its own capture session, and writes everything under OUT_DIR (live.json,
history/usage/*.csv, looks.csv) - never the real history. Prints the clock, the checks, the look
count and the usage summary at the end. Run dev/vitals_log.py at 10/s alongside for the truth.
"""
import csv
import json
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
import lean  # noqa: F401,E402
import capture  # noqa: E402
import history as H  # noqa: E402
import live  # noqa: E402
import vitals  # noqa: E402
from sources.wgc import WgcFrames  # noqa: E402


def main():
    out = Path(sys.argv[1])
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 2
    out.mkdir(parents=True, exist_ok=True)
    H.ROOT = out                                # live.py writes history/usage under H.ROOT
    feed = live.Live(path=out / "live.json")
    for m in feed.meters.values():
        m.prior = 10.0
    levels = {"2000000": 5, "2000001": 1, "2001002": 1, "2001003": 9, "0001000": 3,
              "0001001": 3, "0001002": 3}
    feed.usage.set_context(levels=levels)
    src = WgcFrames({})
    full = src.grab()
    box = vitals.strip_box(full.size)
    rows, slow = [], []
    t0 = time.time()
    next_poll = t0
    rs = vitals.regen_settings()

    def look(kind):
        a = time.time()
        img = src.grab_parts([{"box": box}], timeout=1.0)[0]
        b = time.time()
        r = vitals.read(img, origin=True, learn=False)
        if r["hp"] or r["mp"]:
            feed.usage.set_context(levels=levels)
            ev = feed.usage.look(b, r["hp"], r["mp"])
            feed._log_events(ev)
        rows.append([round(b, 3), kind, round(b - a, 3), *(r["hp"] or ["", ""]),
                     *(r["mp"] or ["", ""])])
        if b - a > 0.3:
            slow.append(round(b - a, 2))

    while time.time() - t0 < secs:
        now = time.time()
        if now >= next_poll:
            look("poll")
            next_poll = now + 1 / fps
            continue
        if feed.wants_fast(now):
            capture.boost(rs["burst_fps"], 1.5)
            look("burst")
            time.sleep(max(0.0, 1 / rs["burst_fps"] - (time.time() - now)))
            continue
        d = feed.burst_in(now)
        if d is not None and d <= 0.8:
            capture.boost(rs["burst_fps"], 1.5)
        time.sleep(max(0.01, min(next_poll - now, d if d is not None else 9)))
    src.close()
    with open(out / "looks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "kind", "grab_s", "hp", "hp_max", "mp", "mp_max"])
        w.writerows(rows)
    mins = secs / 60
    n_b = sum(1 for r in rows if r[1] == "burst")
    print(f"{len(rows)} looks in {mins:.1f} min ({len(rows) / mins:.0f}/min, {n_b} burst); "
          f"plain {fps:g}/s = {secs * fps:.0f}; slow grabs (>0.3 s): {slow}")
    st = feed.meters["mp"].state(time.time())
    print("MP clock:", {k: st[k] for k in ("clock", "tick", "check_every", "checks", "restarts",
                                           "ticks_seen", "sure_s")})
    nxt = st["next"]
    print("MP next tick at", time.strftime("%H:%M:%S", time.localtime(nxt)) +
          f".{int(nxt * 100) % 100:02d}" if nxt else None)
    sm = feed.usage.summary()
    print("MP:", json.dumps({k: v for k, v in sm["mp"].items()}))
    print("HP:", json.dumps({k: v for k, v in sm["hp"].items()}))
    print("levels seen:", sm["levels_seen"])


if __name__ == "__main__":
    main()
