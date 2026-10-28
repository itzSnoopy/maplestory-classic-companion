"""Where things live.

APP  = files that ship with the program (read-only): gamedata/*.json, engine/layouts.json,
       config.example.json, art/, bundled tesseract/. Reach them with asset("gamedata/maps_classic.json").
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
APP = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))   # repo root from source


def _data_dir():
    env = os.environ.get("MAPLE_COMPANION_DATA")
    if env:
        return Path(env)
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MapleClassicCompanion"
    return Path(__file__).resolve().parent.parent      # source run: the repo root, as it always was


DATA = _data_dir()
DATA.mkdir(parents=True, exist_ok=True)

CONFIG = DATA / "config.json"
BUNDLED_TESSERACT = APP / "tesseract" / "tesseract.exe"


def asset(rel):
    """A shipped read-only file, e.g. asset("gamedata/maps_classic.json") (= APP / rel). The
    player's own files go through DATA, never through this."""
    return APP / rel


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
    cfg = json.loads(p.read_text(encoding="utf-8"))
    # packaged app: always the Tesseract of the version that's running. A saved path points into
    # one version's folder, which a later update deletes (2026-10-24: map reads failed mid-play)
    if FROZEN and BUNDLED_TESSERACT.exists():
        cfg["tesseract"] = str(BUNDLED_TESSERACT)
    return cfg


def self_command(*args):
    """Command line that starts this program again with `args` (e.g. "--service")."""
    if FROZEN:
        return [sys.executable, *args]
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return [str(w if w.exists() else exe), str(APP / "companion.py"), *args]
