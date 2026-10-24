"""MapleStory Classic Companion: one program for both parts.

    companion.exe              helper window + tray (starts the tracker; first run opens setup)
    companion.exe --service    the background tracker (the helper starts it this way)

Run from source the same way: `pythonw companion.py`. `python tracker.py` and
`python helper_ui.py` still work on their own for development.

A crash is written to crash.log in the data folder (the packaged app has no console).
"""
import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
import sys
import traceback
from datetime import datetime


def main():
    args = sys.argv[1:]
    try:
        import updater                     # packaged: run the newest installed version
        if updater.adopt(args):
            return
        if "--service" in args:
            import tracker
            tracker.main([a for a in args if a not in ("--no-handoff", "--after-update")])
        else:
            import helper_ui
            helper_ui.main()
    except SystemExit:
        raise
    except BaseException:
        try:
            from paths import DATA
            with open(DATA / "crash.log", "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S}  {' '.join(args) or 'helper'}\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
