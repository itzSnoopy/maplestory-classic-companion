"""sprites.py - the opponent's character picture for the versus board, from ClassicRank, fetched by this PC.

ClassicRank (classicrank.com/user/<Name>) shows every scanned character as a 96x96 transparent PNG
(classicrank.com/photos/<id>.png; the id is in the profile page). Only the NAME travels through the party relay;
each client fetches the picture itself, behind internet.allowed("avatars"), keeps it in <data>/assets/avatars/ and
asks again at most once a day. Never blocks the UI: `get()` returns what is cached (or None) and starts a
background fetch when one is due. No picture (unknown name, offline, not allowed): `silhouette()` is drawn.

Pictures are game art and third-party data: local only, never committed, never sent anywhere.
"""
import json
import re
import threading
import time
import urllib.request

from base import internet
from base.paths import DATA

HOST = "https://classicrank.com"
REFRESH_S = 86400           # at most one request pair per name per day
FAIL_RETRY_S = 3600         # a name ClassicRank doesn't know is asked again after an hour
TIMEOUT_S = 8
NAME_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
PHOTO_RE = re.compile(r"photos/(\d+)\.png")
UA = "MapleClassicCompanion"

_lock = threading.Lock()
_busy = set()
_mem = {}                   # name -> (file mtime, PIL image)


def _dir():
    d = DATA / "assets" / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index():
    try:
        return json.loads((_dir() / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_index(idx):
    (_dir() / "index.json").write_text(json.dumps(idx), encoding="utf-8")


IMG_RE = re.compile(r"<img[^>]*?photos/(\d+)\.png[^>]*>")
WIDTH_RE = re.compile(r'width="(\d+)"')
SHARE_RE = re.compile(r"og:image\"[^>]*?/share/(\d+)-")


def _photo_id(html):
    """The profile's own picture id. The page also shows a decorative avatar (another character, 96 px) before it,
    so take the og:image share id (the profile's character id), else the biggest photo on the page."""
    m = SHARE_RE.search(html)
    if m:
        return m.group(1)
    best, bw = None, -1
    for im in IMG_RE.finditer(html):
        w = WIDTH_RE.search(im.group(0))
        w = int(w.group(1)) if w else 0
        if w > bw:
            best, bw = im.group(1), w
    return best


def _fetch(name):
    """One profile page + one picture. Sets the index entry whatever happens (so we don't hammer)."""
    now = time.time()
    entry = {"t": now, "ok": False, "v": 2}
    try:
        req = urllib.request.Request(f"{HOST}/user/{name}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            pid = _photo_id(r.read(512 * 1024).decode("utf-8", "replace"))
        if pid:
            req = urllib.request.Request(f"{HOST}/photos/{pid}.png", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                data = r.read(512 * 1024)
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                (_dir() / f"{name}.png").write_bytes(data)
                entry["ok"] = True
    except Exception:
        pass
    with _lock:
        idx = _index()
        idx[name] = entry
        _save_index(idx)
        _busy.discard(name)


def get(name):
    """The cached picture of `name` (PIL RGBA) or None; starts a fetch in the background when it is due."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    with _lock:
        e = _index().get(name) or {}
        age = time.time() - (e.get("t") or 0)
        due = age > (REFRESH_S if e.get("ok") else FAIL_RETRY_S) or e.get("v") != 2   # v1 entries cached the wrong picture
        if due and name not in _busy and internet.allowed("avatars"):
            _busy.add(name)
            threading.Thread(target=_fetch, args=(name,), daemon=True, name="avatar").start()
        path = _dir() / f"{name}.png"
    try:
        m = path.stat().st_mtime
    except OSError:
        return None
    if _mem.get(name, (None,))[0] != m:
        try:
            from PIL import Image
            img = Image.open(path).convert("RGBA")
        except Exception:
            return None
        _mem[name] = (m, img)
    return _mem[name][1]


def silhouette(size=96, color=(70, 72, 82, 255)):
    """A code-drawn stand-in: head and shoulders."""
    from PIL import Image, ImageDraw
    k = 4
    im = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size * k
    d.ellipse((s * 0.32, s * 0.12, s * 0.68, s * 0.48), fill=color)
    d.rounded_rectangle((s * 0.2, s * 0.52, s * 0.8, s * 1.1), radius=int(s * 0.18), fill=color)
    return im.resize((size, size), Image.LANCZOS)
