"""skillicons.py - skill pictures for the skill bar, fetched once from NiaMeowDB.

Used when a skill has no picture cut from the player's own Skill window (e.g. a party member's Heal).
Nexon game art: never shipped, committed or sent through the relay. Each app downloads only the ones
it shows, once, into <data>/assets/skill_icons/<skill id>.png (assets/ is git-ignored and never
packaged; the player's own cuts in templates/skill_icons/ are a different thing and never touched
here). Only while Settings > Internet > "Skill pictures" is on (internet.allowed("skillicons")).
index.json in the same folder remembers what was fetched and what failed (not retried for a day).
Skill ids are 7-digit strings with leading zeros.
"""
import json
import threading
import time
import urllib.request

from base import internet
from base.paths import DATA

URL = "https://meowdb.com/msclassic/classes/icons/{key}.png"
UA = {"User-Agent": "MapleClassicCompanion skill icons (personal use)"}
MAX_BYTES = 200_000
RETRY_S = 24 * 3600
_busy = set()
_index = None


def folder():
    return DATA / "assets" / "skill_icons"


def _idx():
    """{"source", "fetched": {id: iso}, "failed": {id: epoch}} (assets/skill_icons/index.json)."""
    global _index
    if _index is None:
        try:
            _index = json.loads((folder() / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _index = {}
        _index.setdefault("source", "meowdb.com/msclassic/classes/icons (Nexon game art: "
                                    "local only, never share)")
        _index.setdefault("fetched", {})
        _index.setdefault("failed", {})
    return _index


def _save_idx():
    try:
        folder().mkdir(parents=True, exist_ok=True)
        tmp = folder() / "index.json.tmp"
        tmp.write_text(json.dumps(_idx(), indent=1), encoding="utf-8")
        tmp.replace(folder() / "index.json")
    except OSError:
        pass


def path(sid):
    """The local picture for skill `sid`, or None. Starts a download when it's missing and
    allowed; call again later."""
    if not sid or not str(sid).isdigit():
        return None
    key = str(sid)
    p = folder() / f"{key}.png"
    if p.exists():
        return p
    if key in _busy or time.time() - _idx()["failed"].get(key, 0) < RETRY_S:
        return None
    if not internet.allowed("skillicons"):
        return None
    _busy.add(key)
    threading.Thread(target=_fetch, args=(key, p), daemon=True).start()
    return None


def _fetch(key, p):
    try:
        req = urllib.request.Request(URL.format(key=key), headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("too big")
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data))
        img.load()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        img.convert("RGBA").save(tmp, "PNG")
        tmp.replace(p)
        _idx()["fetched"][key] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _idx()["failed"].pop(key, None)
    except Exception:
        _idx()["failed"][key] = round(time.time())
    finally:
        _save_idx()
        _busy.discard(key)
