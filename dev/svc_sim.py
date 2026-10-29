"""Offline end-to-end tests of the tracker service. All outputs go to OUT_DIR, never the real
history/, journal.md or sessions/.

  python dev/svc_sim.py OUT_DIR frames   screen source on saved frames: play, level-up,
                                         source unreachable, game gone (session closes), game back
  python dev/svc_sim.py OUT_DIR csv PATH csv_replay source on a recorded exp_log.csv

Fixture frames: a sessions/*/levelups/Lv*/ folder (before_*, at_*, after_*). Edit FIXTURE if
the original (pruned after 14 days) is gone. Local only: Nexon imagery.
"""
import json
import os
import sys
import types
from pathlib import Path
os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")   # simulations never go online (party relay, updates)

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from base import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
from hub import tracker as T                                  # noqa: E402
from launcher import compose                                  # noqa: E402
from engine.sources import make_source                      # noqa: E402
from engine.sources.screen import ImageFrames  # noqa: E402

# the original is pruned after 14 days; dev/fixtures/ keeps a local copy (git-ignored, Nexon imagery)
FIXTURE = next((p for p in (Path(os.environ["SVC_SIM_FIXTURE"]) if os.environ.get("SVC_SIM_FIXTURE") else None,
                            CODE / "sessions" / "20261023_021038" / "levelups" / "Lv5_021225",
                            CODE / "dev" / "fixtures" / "levelup_lv5") if p and p.exists()),
               CODE / "dev" / "fixtures" / "levelup_lv5")


def main():
    out, mode = Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "frames"
    out.mkdir(parents=True, exist_ok=True)
    hub = compose.make_hub(out)          # the same composition path as the launcher's
    cfg = json.loads((CODE / "config.json").read_text(encoding="utf-8"))
    cfg.update(interval=0.3, fast_interval=0.05)
    cfg["service"].update(close_after_absent_s=1, idle_poll_s=0.15, close_after_afk_s=999,
                          source_retry_s=0.1)
    args = types.SimpleNamespace(level=None, interval=None, service=False)

    if mode == "frames":
        from engine import ocr
        ocr.setup(cfg.get("tesseract"))
        play = sorted(FIXTURE.glob("before_*.png")) + sorted(FIXTURE.glob("at_*.png")) \
            + sorted(FIXTURE.glob("after_*.png"))
        seq = [play[0]] * 2 + play[1:] + ["unavailable"] * 2 + [None] * 12 + [play[-1]] * 3
        source = compose.screen_source(cfg, ImageFrames(seq))
    else:
        cfg["source"] = {"type": "csv_replay", "path": sys.argv[3]}
        cfg.update(interval=0, fast_interval=0)
        source = make_source(cfg, None)         # csv replay: nothing to translate

    T.run(cfg, args, source=source, hub=hub)
    for name in ("journal.md", "history/sessions.csv", "history/levels.csv", "status.json"):
        p = out / name
        print(f"===== {name}\n" + (p.read_text(encoding="utf-8") if p.exists() else "(none)"))


if __name__ == "__main__":
    main()
