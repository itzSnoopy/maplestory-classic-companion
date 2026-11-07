"""Pictures for the relic hunt: the owner's gallery folder and per-room uploads. Stdlib only.

Gallery: image files the server owner drops into `<data_dir>/relic_gallery/` (never in git).
Uploads: `<data_dir>/relic_uploads/<room id>/<sha256 first 16 hex>.<ext>`, at most UPLOAD_KEEP per room.
Every file is checked by its magic bytes; sizes are read from the header with the standard library only.
"""
import hashlib
import os
import re
import shutil
import struct
from collections import namedtuple
from urllib.parse import quote

from .errors import ApiError

GALLERY_DIR = "relic_gallery"
UPLOAD_DIR = "relic_uploads"
GALLERY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}\.(png|jpg|jpeg|webp)$", re.I)
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
ROOM_ID_RE = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
MAX_UPLOAD = 4 * 1024 * 1024
MAX_GALLERY_FILE = 8 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 64, 4096
UPLOAD_KEEP = 5
UPLOAD_RATE = (6, 600.0)            # at most 6 uploads per 10 minutes per room
CTYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
Info = namedtuple("Info", "ext ctype w h")
_gallery_cache = {}                 # path -> (mtime_ns, size, Info or None)


def sniff(data):
    """Info(ext, ctype, w, h) for a PNG / JPEG / WebP, else None. ext is png, jpg or webp."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return Info("png", CTYPES["png"], w, h)
        if data[:3] == b"\xff\xd8\xff":
            return _jpeg(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp(data)
    except (struct.error, IndexError):
        return None
    return None


def _jpeg(data):
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m == 0xFF:                       # fill byte
            i += 1
            continue
        if m == 0x01 or 0xD0 <= m <= 0xD8:   # standalone markers
            i += 2
            continue
        if m in (0xD9, 0xDA):               # end / scan: no frame header came first
            return None
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return Info("jpg", CTYPES["jpg"], w, h)
        i += 2 + seglen
    return None


def _webp(data):
    kind = data[12:16]
    if kind == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
    elif kind == b"VP8L":
        if data[20] != 0x2F:
            return None
        bits = struct.unpack("<I", data[21:25])[0]
        w, h = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif kind == b"VP8X":
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
    else:
        return None
    return Info("webp", CTYPES["webp"], w, h)


def _inside(folder, path):
    root = os.path.realpath(folder)
    real = os.path.realpath(path)
    return os.path.commonpath([root, real]) == root and real != root


# ---- gallery -------------------------------------------------------------------------------------------
def gallery_dir(data_dir):
    d = os.path.join(data_dir, GALLERY_DIR)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _gallery_info(path):
    """Info of a gallery file (cached by mtime and size); None when it is not a real image."""
    try:
        st = os.stat(path)
        if not os.path.isfile(path) or st.st_size > MAX_GALLERY_FILE or st.st_size < 16:
            return None
        hit = _gallery_cache.get(path)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
        with open(path, "rb") as f:
            info = sniff(f.read())
        if info and not (info.w >= 1 and info.h >= 1):
            info = None
        if len(_gallery_cache) > 500:
            _gallery_cache.clear()
        _gallery_cache[path] = (st.st_mtime_ns, st.st_size, info)
        return info
    except OSError:
        return None


def gallery_file(data_dir, name):
    """(path, Info) of a gallery picture or None. The name must match the strict regex and the real path
    must lie inside the gallery folder."""
    if not isinstance(name, str) or not GALLERY_NAME_RE.match(name):
        return None
    folder = gallery_dir(data_dir)
    path = os.path.join(folder, name)
    if not _inside(folder, path):
        return None
    info = _gallery_info(path)
    return (path, info) if info else None


def gallery_url(name):
    return "/rooms/gallery/" + quote(name)


def gallery_list(data_dir):
    out = []
    folder = gallery_dir(data_dir)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    for name in names:
        hit = gallery_file(data_dir, name)
        if hit:
            out.append({"name": name, "url": gallery_url(name), "w": hit[1].w, "h": hit[1].h})
    return {"images": out}


# ---- uploads -------------------------------------------------------------------------------------------
def _room_dir(data_dir, room_id):
    if not ROOM_ID_RE.match(room_id):
        raise ApiError(404, "not found")
    return os.path.join(data_dir, UPLOAD_DIR, room_id)


def save_upload(data_dir, room_id, data, ctype, keep=None):
    """Validate and store a raw upload. Returns (id, Info). Raises ApiError(400) for anything wrong."""
    if not data:
        raise ApiError(400, "the image is empty")
    info = sniff(data)
    if info is None:
        raise ApiError(400, "that is not a PNG, JPEG or WebP image")
    if ctype != info.ctype:
        raise ApiError(400, "the Content-Type does not match the image")
    if not (MIN_SIDE <= info.w <= MAX_SIDE and MIN_SIDE <= info.h <= MAX_SIDE):
        raise ApiError(400, f"the picture must be {MIN_SIDE} to {MAX_SIDE} pixels on each side")
    uid = hashlib.sha256(data).hexdigest()[:16]
    folder = _room_dir(data_dir, room_id)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, uid + "." + info.ext)
    with open(path, "wb") as f:
        f.write(data)
    os.utime(path)
    files = []
    for fn in os.listdir(folder):
        p = os.path.join(folder, fn)
        if os.path.isfile(p):
            files.append((os.stat(p).st_mtime_ns, fn, p))
    files.sort()
    extra = len(files) - UPLOAD_KEEP
    for _, fn, p in files:
        if extra <= 0:
            break
        if fn.split(".")[0] in (uid, keep):
            continue
        try:
            os.remove(p)
        except OSError:
            pass
        extra -= 1
    return uid, info


def upload_file(data_dir, room_id, uid):
    """(path, Info) of one of the room's uploads, or None."""
    if not isinstance(uid, str) or not UPLOAD_ID_RE.match(uid) or not ROOM_ID_RE.match(room_id or ""):
        return None
    folder = _room_dir(data_dir, room_id)
    for ext in ("png", "jpg", "webp"):
        path = os.path.join(folder, uid + "." + ext)
        if os.path.isfile(path) and _inside(folder, path):
            try:
                with open(path, "rb") as f:
                    info = sniff(f.read())
            except OSError:
                return None
            return (path, info) if info else None
    return None


def delete_room(data_dir, room_id):
    if ROOM_ID_RE.match(room_id or ""):
        shutil.rmtree(os.path.join(data_dir, UPLOAD_DIR, room_id), ignore_errors=True)
