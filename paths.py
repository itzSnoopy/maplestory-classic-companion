"""Where things live.

APP  = files that ship with the program (read-only): maps_classic.json, exp_table_classic.json,
       layouts.json, config.example.json, art/, bundled tesseract/.
DATA = the player's files (written): config.json, helper_settings.json, history/, sessions/,
       screenshots/, templates/ and glyphs (cut from the player's own screen), logs, digest.

Run from source, both are this folder, so a dev checkout works as it always did.
In the packaged .exe, APP is the unpacked bundle and DATA is %LOCALAPPDATA%\\MapleClassicCompanion.
MAPLE_COMPANION_DATA overrides DATA (tests, portable installs).
"""
import json
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
APP = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _data_dir():
    env = os.environ.get("MAPLE_COMPANION_DATA")
    if env:
        return Path(env)
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MapleClassicCompanion"
    return Path(__file__).resolve().parent


DATA = _data_dir()
DATA.mkdir(parents=True, exist_ok=True)

CONFIG = DATA / "config.json"
BUNDLED_TESSERACT = APP / "tesseract" / "tesseract.exe"


def default_config():
    cfg = json.loads((APP / "config.example.json").read_text(encoding="utf-8"))
    if BUNDLED_TESSERACT.exists():
        cfg["tesseract"] = str(BUNDLED_TESSERACT)
    return cfg


def load_config(path=None):
    """config.json from DATA; created from config.example.json on first run."""
    p = Path(path) if path else CONFIG
    if not p.exists() and p == CONFIG:
        p.write_text(json.dumps(default_config(), indent=2), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8"))


def self_command(*args):
    """Command line that starts this program again with `args` (e.g. "--service")."""
    if FROZEN:
        return [sys.executable, *args]
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return [str(w if w.exists() else exe), str(APP / "companion.py"), *args]
