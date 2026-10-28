"""mobicons.py - monster pictures for the monster album, fetched once from NiaMeowDB.

Nexon game art: never shipped or committed. Each player's app downloads them into its own
persistent folder, <data>/assets/mob_icons/<mob id>.png (assets/ is git-ignored and never
packaged), only while Settings > Internet > "Monster pictures" is on (internet.allowed("mobs")).
One small GET per monster, one at a time in a background thread. index.json in the same folder
remembers what was fetched and what failed (not tried again for a day).
"""
import json
import threading
import time
import urllib.request

from base import internet
from base.paths import DATA

URL = "https://meowdb.com/msclassic/monsters/sprites/mob_{id}.png"
UA = {"User-Agent": "MapleClassicCompanion monster pictures (personal use)"}
MAX_BYTES = 400_000
RETRY_S = 24 * 3600
GAP_S = 0.5                 # between downloads: be gentle with the site
_queue, _lock = [], threading.Lock()
_worker = None
_index = None


def folder():
    return DATA / "assets" / "mob_icons"


def _idx():
    global _index
    if _index is None:
        try:
            _index = json.loads((folder() / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _index = {}
        _index.setdefault("source", "meowdb.com/msclassic/monsters/sprites (Nexon game art: local only, "
                                    "never share)")
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


def path(mob_id):
    """The local picture for a mob, or None. Queues a download when it's missing and allowed."""
    mob_id = str(mob_id or "")
    if not mob_id.isdigit():
        return None
    p = folder() / f"{mob_id}.png"
    if p.exists():
        return p
    want([mob_id])
    return None


def want(ids):
    """Queue downloads for these mob ids (missing ones only)."""
    global _worker
    if not internet.allowed("mobs"):
        return
    now = time.time()
    with _lock:
        for i in ids:
            i = str(i)
            if (i.isdigit() and i not in _queue and not (folder() / f"{i}.png").exists()
                    and now - _idx()["failed"].get(i, 0) >= RETRY_S):
                _queue.append(i)
        if _queue and (_worker is None or not _worker.is_alive()):
            _worker = threading.Thread(target=_run, daemon=True)
            _worker.start()


def pending():
    with _lock:
        return len(_queue)


def _run():
    while True:
        with _lock:
            if not _queue:
                return
            mob_id = _queue[0]
        if internet.allowed("mobs"):
            _fetch(mob_id)
        with _lock:
            if _queue and _queue[0] == mob_id:
                _queue.pop(0)
        time.sleep(GAP_S)


def _fetch(mob_id):
    p = folder() / f"{mob_id}.png"
    try:
        req = urllib.request.Request(URL.format(id=mob_id), headers=UA)
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
        _idx()["fetched"][mob_id] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _idx()["failed"].pop(mob_id, None)
    except Exception:
        _idx()["failed"][mob_id] = round(time.time())
    finally:
        _save_idx()
