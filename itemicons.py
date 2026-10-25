"""itemicons.py - item pictures (potions) for the training panel, fetched once from NiaMeowDB.

They're Nexon game art: never shipped or committed. Each player's app downloads only the ones it
shows, once, into its own persistent folder: <data>/assets/item_icons/<item id>.png (data =
%LOCALAPPDATA%/MapleClassicCompanion in the packaged app, shared by every installed version, so
a new version never downloads them again; assets/ is git-ignored and never packaged). Only while
Settings > Internet > "Item pictures" is on (internet.allowed("icons")). One small GET per item,
in the background. index.json in the same folder remembers when each was fetched and which ones
failed (not tried again for a day, across restarts and versions).
"""
import json
import threading
import time
import urllib.request

import internet
from paths import DATA

URL = "https://meowdb.com/msclassic/api/assets/icons/{key}"
UA = {"User-Agent": "MapleClassicCompanion item icons (personal use)"}
MAX_BYTES = 200_000
RETRY_S = 24 * 3600
_busy = set()
_index = None


def folder():
    return DATA / "assets" / "item_icons"


def _idx():
    """{"source", "fetched": {key: iso}, "failed": {key: epoch}} (assets/item_icons/index.json)."""
    global _index
    if _index is None:
        try:
            _index = json.loads((folder() / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _index = {}
        _index.setdefault("source", "meowdb.com/msclassic/api/assets/icons (Nexon game art: "
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


def _migrate():
    """0.6.97 kept them in templates/item_icons: move them over once."""
    old = DATA / "templates" / "item_icons"
    if old.is_dir():
        folder().mkdir(parents=True, exist_ok=True)
        for f in old.glob("*.png"):
            if not (folder() / f.name).exists():
                f.replace(folder() / f.name)
        try:
            old.rmdir()
        except OSError:
            pass


def path(key):
    """The local picture for item `key` (MeowDB iconKey), or None. Starts a download when it's
    missing and allowed; call again later."""
    if not key or not str(key).isdigit():
        return None
    key = str(key)
    p = folder() / f"{key}.png"
    if p.exists():
        return p
    _migrate()
    if p.exists():
        return p
    if key in _busy or time.time() - _idx()["failed"].get(key, 0) < RETRY_S:
        return None
    if not internet.allowed("icons"):
        return None
    _busy.add(key)
    threading.Thread(target=_fetch, args=(str(key), p), daemon=True).start()
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
