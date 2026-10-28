"""MapleStory Classic Companion: entry point (PyInstaller's script, start_*.cmd, paths.self_command).

The program itself lives in launcher/companion.py; this shim only keeps the old entry name.
    companion.py              helper window + tray (starts the tracker; first run opens setup)
    companion.py --service    the background tracker
"""
import base.lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
from launcher.companion import main

if __name__ == "__main__":
    main()
