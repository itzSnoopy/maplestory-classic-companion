"""Engine dev tools that need the game-name translator (gamedata/translate.py), handed in here.

  python dev/engine_tools.py record [FRAME.png]       measure this window size into layouts.json
                                                      (no frame = grab the game window)
  python dev/engine_tools.py panels FRAME [FRAME...]  what game windows are open and what they say
  python dev/engine_tools.py panels learn FRAME FIELD=TEXT..   teach window digit glyphs
"""
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from base import lean  # noqa: F401,E402
from gamedata import translate  # noqa: E402


def main(argv):
    if len(argv) in (1, 2) and argv[0] == "record":
        from PIL import Image
        from base.paths import load_config
        from engine import layout
        cfg = load_config()
        if len(argv) == 2:
            frame = Image.open(argv[1]).convert("RGB")
        else:
            from engine.sources.wgc import WgcFrames
            f = WgcFrames(cfg)
            frame = f.grab()
            f.close()
        layout.record(frame, cfg, translate)
    elif argv and argv[0] == "panels":
        from engine import panels
        panels._main(argv[1:], translate)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
