"""dev/burst.py - save game-window frames fast, for studying short events (deaths, boss bars).

  python dev/burst.py [seconds=240] [fps=3]

Frames go to dev/fixtures/burst/<timestamp>/ (gitignored: Nexon imagery, local only), named by
the elapsed time. Runs next to the live tracker (its own capture session). Ctrl+C or a file
named `stop` in the output folder ends it early.
"""
import sys
import time
from datetime import datetime
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from engine.sources.wgc import WgcFrames  # noqa: E402


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 240
    fps = float(sys.argv[2]) if len(sys.argv) > 2 else 3
    out = CODE / "dev" / "fixtures" / "burst" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(f"saving to {out}", flush=True)
    src = WgcFrames({"wgc": {"min_update_ms": 100}})    # bursts need several frames a second
    t0, n = time.time(), 0
    try:
        while time.time() - t0 < secs and not (out / "stop").exists():
            tick = time.time()
            img = src.grab()
            el = tick - t0
            img.save(out / f"{el:07.2f}_{datetime.now():%H%M%S}.jpg", quality=92)
            n += 1
            if n % 30 == 0:
                print(f"{n} frames, {el:.0f}s", flush=True)
            time.sleep(max(0.0, 1 / fps - (time.time() - tick)))
    finally:
        src.close()
        print(f"done: {n} frames in {out}", flush=True)


if __name__ == "__main__":
    main()
