"""gamefiles.py - read facts from the player's own MapleStory Classic install (read-only, at run time).

Nexon game art: whatever this module decodes is cached only in the player's data folder (assets/, git-ignored,
never packaged, never uploaded). Nothing from the install is copied into the app.

  find_install()    -> {"data": Path to <game>/Data, "exe", "exe_version", "how"} or None.
                       Order: MAPLE_GAME_DIR env (the value "none" = no game at all, for tests), the saved answer (game_install.json in the data folder: a
                       Settings choice or an earlier find), the Steam libraries (LAYOUTS), the running game (window
                       class -> process id -> image path of MapleStory.exe: query-only, no game memory).
  set_install(path) -> (ok, message): the player's own choice, saved with how="settings".
  stamp()           -> the source files' size + mtime_ns and the exe version (stat only, no hashing). Icons cached
                       with a stamp are re-checked when it changes (a game update).
  canvas(pack, img, path) -> rows of (r, g, b, a) of any canvas (follows _outlink); children(...) -> child names.
  skill_icon(id)    -> PIL RGBA 32x32 of Skill/_Canvas/_Canvas_000.wz  <job>.img/skill/<id>/icon, or None.
  note_fallback()   -> one line per (kind, skill) and process into history/icon_sources.jsonl, so we can see how
                       often the older ways (the cut from the Skill window, MeowDB) were still needed.
  summary()         -> counts from that log (python dev/icon_sources_report.py).
  close()           -> release every mapped package; also runs by itself IDLE_CLOSE_S (10 s) after the last read,
                       so a game patch can replace the files while the companion runs.

The skill images live in Skill/_Canvas/_Canvas_000.wz (flat PKG1, zero-key names, one .img per job); the stub
Skill.wz next to it is only a directory. Packs/Skill_00000.ms (newer, encrypted) is NOT read: if it overrides some
icons we would not see it (open question, see NOTES.md).
"""
import ctypes
import json
import mmap
import os
import re
import struct
import threading
import time
import zlib
from pathlib import Path

from base.paths import DATA

WINDOW_CLASS = "MapleStoryClass"
UI_PACK = "UI/UI_000.wz"                     # a Data folder is valid when this exists
SKILL_PACK = "Skill/_Canvas/_Canvas_000.wz"
# (how, tail below a library / install root that ends in the Data folder). The Nexon launcher's layout will be
# added as a row once known.
LAYOUTS = [("steam", Path("steamapps/common/MapleStory/MSCW/Data"))]
_cache = {"found": None, "t": 0.0}           # process-wide answer of find_install
RETRY_S = 60                                 # a failed find is tried again after this
_why = {}                                    # skill id / canvas key -> why it gave None (for the log)


# ------------------------------------------------------------------ the install
def _install_file():
    return DATA / "game_install.json"


def _valid_data_dir(p):
    try:
        return (Path(p) / UI_PACK).is_file()
    except (OSError, TypeError):
        return False


def _exe_version(exe):
    """'1.2.3.4' from the exe's version resource, or None (win32api missing / no resource)."""
    try:
        import win32api
        i = win32api.GetFileVersionInfo(str(exe), "\\")
        return "%d.%d.%d.%d" % (i["FileVersionMS"] >> 16, i["FileVersionMS"] & 0xFFFF,
                                i["FileVersionLS"] >> 16, i["FileVersionLS"] & 0xFFFF)
    except Exception:
        return None


def _running_exe():
    """Full path of the process that owns the game window, or None (game not running)."""
    try:
        import win32gui
        hwnd = win32gui.FindWindow(WINDOW_CLASS, None)
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.c_ulong(len(buf))
            ok = k32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(n))
            return buf.value if ok else None
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        return None


def _data_near(exe):
    """The Data folder that belongs to MapleStory.exe: next to it or in a parent (up to 4 levels)."""
    p = Path(exe).parent
    for _ in range(5):
        for cand in [p / "Data"] + [p / lay[1] for lay in LAYOUTS] + \
                    [p / Path(*lay[1].parts[-2:]) for lay in LAYOUTS]:
            if _valid_data_dir(cand):
                return cand
        if p.parent == p:
            break
        p = p.parent
    return None


def _steam_libraries():
    roots = []
    try:
        import winreg
        for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, val)[0]))
            except OSError:
                pass
    except ImportError:
        return []
    libs = list(roots)
    for r in roots:
        try:
            txt = (r / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
            libs += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', txt)]
        except OSError:
            pass
    seen, out = set(), []
    for lib in libs:
        if str(lib).lower() not in seen:
            seen.add(str(lib).lower())
            out.append(lib)
    return out


def _from_steam():
    for lib in _steam_libraries():
        for how, tail in LAYOUTS:
            if how == "steam" and _valid_data_dir(lib / tail):
                return lib / tail
    return None


def _result(data, exe, how):
    return {"data": Path(data), "exe": str(exe) if exe else None,
            "exe_version": _exe_version(exe) if exe else None, "how": how}


def _save_install(found):
    try:
        _install_file().parent.mkdir(parents=True, exist_ok=True)
        _install_file().write_text(json.dumps(
            {"data": str(found["data"]), "exe": found["exe"], "exe_version": found["exe_version"],
             "how": found["how"], "found": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1), encoding="utf-8")
    except OSError:
        pass


def find_install(refresh=False):
    """See the module doc. Memoized; a miss is retried after RETRY_S. Steam / running-game finds are saved in
    <data>/game_install.json so later runs work with the game closed."""
    if os.environ.get("MAPLE_GAME_DIR", "").strip().lower() == "none":
        return None                          # tests: pretend there is no game (no saved answer, no Steam)
    now = time.time()
    if not refresh and _cache["found"]:
        return _cache["found"]
    if not refresh and now - _cache["t"] < RETRY_S:
        return None
    _cache["t"] = now
    found = None
    env = os.environ.get("MAPLE_GAME_DIR")
    if env and _valid_data_dir(env):
        found = _result(env, None, "env")
    if not found:
        try:
            c = json.loads(_install_file().read_text(encoding="utf-8"))
            if _valid_data_dir(c["data"]):
                found = {"data": Path(c["data"]), "exe": c.get("exe"),
                         "exe_version": c.get("exe_version"), "how": c.get("how") or "saved"}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if not found:
        d = _from_steam()
        if d:
            found = _result(d, None, "steam")
            _save_install(found)
    if not found:
        exe = _running_exe()
        d = _data_near(exe) if exe else None
        if d:
            found = _result(d, exe, "running game")
            _save_install(found)
    if found:
        _cache["found"] = found
    return found


def set_install(path):
    """The player's own choice (a future Settings field): a Data folder, or a folder / MapleStory.exe above it.
    Returns (ok, message); on success saved with how="settings"."""
    try:
        p = Path(str(path).strip().strip('"'))
        exe = None
        if p.is_file():
            exe, p = p, p.parent
        cands = [_data_near(exe)] if exe else []
        cands += [p, p / "Data"] + [p / lay[1] for lay in LAYOUTS]
        for c in cands:
            if c and _valid_data_dir(c):
                found = {"data": Path(c), "exe": str(exe) if exe else None,
                         "exe_version": _exe_version(exe) if exe else None, "how": "settings"}
                _save_install(found)
                close()
                _cache["found"] = found
                return True, "Game data found in %s" % c
        return False, "No game data there (%s is missing)" % UI_PACK
    except Exception as e:
        return False, "Could not use that path (%s)" % type(e).__name__


def status_text(found):
    """(text, ok) for the Settings / setup status line, from a find_install() result."""
    if found:
        return "Found (%s): %s" % (found.get("how") or "saved", found["data"]), True
    return "Not found: shapes and icons are learned while you play", False


def choose(path=None, refresh=False):
    """Shared by Settings and setup. With a path: set_install(path); without: find_install(refresh).
    Returns (text, ok) for the status line (a failed pick says why and keeps the old answer shown)."""
    if path:
        ok, msg = set_install(path)
        if not ok:
            return msg, False
        return status_text(_cache["found"])[0], True
    return status_text(find_install(refresh))


def _stat(p):
    try:
        st = p.stat()
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def stamp():
    """{"files": {name: [size, mtime_ns]}, "exe_version"} of the files read, or None (no install). Stat only."""
    inst = find_install()
    if not inst:
        return None
    d = inst["data"]
    files = {}
    for rel in (UI_PACK, "UI/_Canvas/_Canvas_000.wz", SKILL_PACK, "Packs/digest.md5"):
        st = _stat(d / rel)
        if st:
            files[rel] = st
    ev = inst.get("exe_version")
    if not ev and inst.get("exe"):
        ev = _exe_version(inst["exe"])
    return {"files": files, "exe_version": ev}


# ------------------------------------------------------------------ WZ reader (PKG1, zero-key names)
class _R:
    def __init__(s, data, pos=0):
        s.d, s.p = data, pos

    def u8(s):
        v = s.d[s.p]
        s.p += 1
        return v

    def i8(s):
        v = struct.unpack_from("<b", s.d, s.p)[0]
        s.p += 1
        return v

    def u16(s):
        v = struct.unpack_from("<H", s.d, s.p)[0]
        s.p += 2
        return v

    def i32(s):
        v = struct.unpack_from("<i", s.d, s.p)[0]
        s.p += 4
        return v

    def u32(s):
        v = struct.unpack_from("<I", s.d, s.p)[0]
        s.p += 4
        return v

    def i64(s):
        v = struct.unpack_from("<q", s.d, s.p)[0]
        s.p += 8
        return v

    def f32(s):
        v = struct.unpack_from("<f", s.d, s.p)[0]
        s.p += 4
        return v

    def f64(s):
        v = struct.unpack_from("<d", s.d, s.p)[0]
        s.p += 8
        return v

    def cint(s):
        v = s.i8()
        return s.i32() if v == -128 else v

    def clong(s):
        v = s.i8()
        return s.i64() if v == -128 else v

    def cfloat(s):
        v = s.i8()
        return s.f32() if v == -128 else float(v)

    def wstr(s):
        n = s.i8()
        if n == 0:
            return ""
        if n > 0:           # unicode, key 0xAAAA counting up
            if n == 127:
                n = s.i32()
            out, m = [], 0xAAAA
            for _ in range(n):
                out.append(chr(s.u16() ^ m))
                m = (m + 1) & 0xFFFF
            return "".join(out)
        n = -n              # ascii
        if n == 128:
            n = s.i32()
        b = bytes(s.d[s.p + i] ^ ((0xAA + i) & 0xFF) for i in range(n))
        s.p += n
        return b.decode("latin-1")


class _Node:
    __slots__ = ("kids", "val", "canvas")

    def __init__(s):
        s.kids, s.val, s.canvas = {}, None, None

    def get(s, path):
        n = s
        for p in path.split("/"):
            if p:
                n = n.kids.get(p)
                if n is None:
                    return None
        return n


def _sblock(r, base):
    k = r.u8()
    if k in (0, 0x73):
        return r.wstr()
    if k in (1, 0x1B):
        off = r.i32()
        save = r.p
        r.p = base + off
        s = r.wstr()
        r.p = save
        return s
    raise ValueError("string block %x" % k)


def _props(r, base, node):
    for _ in range(r.cint()):
        name = _sblock(r, base)
        t = r.u8()
        k = _Node()
        node.kids[name] = k
        if t == 0:
            pass
        elif t in (2, 11):
            k.val = r.u16()
        elif t in (3, 19):
            k.val = r.cint()
        elif t == 20:
            k.val = r.clong()
        elif t == 4:
            k.val = r.cfloat()
        elif t == 5:
            k.val = r.f64()
        elif t == 8:
            k.val = _sblock(r, base)
        elif t == 9:
            ln = r.u32()
            end = r.p + ln
            _extended(r, base, k)
            r.p = end
        else:
            raise ValueError("property type %d" % t)


def _extended(r, base, k):
    kind = _sblock(r, base)
    if kind == "Property":
        r.u16()
        _props(r, base, k)
    elif kind == "Canvas":
        r.u8()
        if r.u8():
            r.u16()
            _props(r, base, k)
        w, h, f1 = r.cint(), r.cint(), r.cint()
        f2 = r.u8()
        r.i32()
        ln = r.i32()
        r.u8()
        k.canvas = (w, h, f1 + f2, r.p, ln - 1)
    elif kind == "Shape2D#Vector2D":
        k.val = (r.cint(), r.cint())


def _tree(data, off):
    r = _R(data, off)
    _sblock(r, off)
    r.u16()
    root = _Node()
    _props(r, off, root)
    return root


def _index(data):
    """{image name: offset} of a flat package (images laid out back to back after the directory)."""
    if data[:4] != b"PKG1":
        raise ValueError("not a PKG1 package")
    r = _R(data, 12)
    start = r.u32()
    r = _R(data, start)
    r.u16()
    ents = []
    for _ in range(r.cint()):
        r.u8()
        name = r.wstr()
        size = r.cint()
        r.cint()
        r.u32()
        ents.append((name, size))
    pos, out = r.p, {}
    for name, size in ents:
        out[name] = pos
        pos += size
    return out


# ------------------------------------------------------------------ packages (cached), canvases, skill icons
_packs = {}                     # absolute path -> {"key", "fh", "mm", "index", "trees"}
_lock = threading.RLock()
IDLE_CLOSE_S = 10.0         # release the mapped game files this long after the last read (a patch can't replace a mapped file)
_closer = None


def _touch():
    """Restart the idle timer that releases every mapped package (called under _lock on each open/use)."""
    global _closer
    if _closer is not None:
        _closer.cancel()
    _closer = threading.Timer(IDLE_CLOSE_S, close)
    _closer.daemon = True
    _closer.start()


def _open_pack(path):
    """The mapped package (re-opened when its size/date changed: a game update). Raises OSError / ValueError."""
    path = str(path)
    st = os.stat(path)
    key = (st.st_size, st.st_mtime_ns)
    pk = _packs.get(path)
    if pk is None or pk["key"] != key:
        _drop(path)
        fh = open(path, "rb")
        try:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            pk = {"key": key, "fh": fh, "mm": mm, "index": _index(mm), "trees": {}}
        except Exception:
            fh.close()
            raise
        _packs[path] = pk
    _touch()
    return pk


def _drop(path):
    pk = _packs.pop(path, None)
    if pk:
        for k in ("mm", "fh"):
            try:
                pk[k].close()
            except (OSError, ValueError, BufferError):
                pass


def close():
    """Release every mapped file (so the game's updater can replace them)."""
    with _lock:
        for path in list(_packs):
            _drop(path)


def _node(pack_rel, img, path):
    """(pack dict, tree node) for a path inside an image; raises KeyError with the reason."""
    inst = find_install()
    if not inst:
        raise KeyError("no game install found")
    f = inst["data"] / pack_rel
    if not f.is_file():
        raise KeyError("package missing: %s" % pack_rel)
    pk = _open_pack(f)
    if img not in pk["index"]:
        raise KeyError("image not in package: %s" % img)
    t = pk["trees"].get(img)
    if t is None:
        t = pk["trees"][img] = _tree(pk["mm"], pk["index"][img])
    n = t.get(path or "")
    if n is None:
        raise KeyError("no such path: %s/%s" % (img, path))
    return pk, n


def children(pack_rel, img, path=""):
    """Names of the children of a node (in file order), or None (see why_canvas)."""
    key = "%s|%s|%s" % (pack_rel, img, path)
    try:
        with _lock:
            return list(_node(pack_rel, img, path)[1].kids)
    except KeyError as e:
        _why[key] = e.args[0]
    except Exception as e:
        close()
        _why[key] = "read error: %s" % type(e).__name__
    return None


def _decode(mm, canvas):
    """(w, h, RGBA bytes) from a canvas tuple (w, h, format, offset, length); 1 = BGRA4444, 2 = BGRA8888."""
    w, h, fmt, p, ln = canvas
    buf = zlib.decompressobj().decompress(bytes(mm[p:p + ln]))        # zlib without the checksum trailer
    if fmt == 2:
        out = bytearray(buf[:w * h * 4])
        out[0::4], out[2::4] = out[2::4], out[0::4]                    # BGRA -> RGBA
        return w, h, bytes(out)
    if fmt == 1:
        out = bytearray(w * h * 4)
        for i in range(w * h):
            lo, hi = buf[i * 2], buf[i * 2 + 1]
            out[i * 4:i * 4 + 4] = bytes(((hi & 15) * 17, (lo >> 4) * 17, (lo & 15) * 17, (hi >> 4) * 17))
        return w, h, bytes(out)
    raise ValueError("canvas format %d" % fmt)


def _rgba(mm, canvas):
    from PIL import Image
    w, h, data = _decode(mm, canvas)
    return Image.frombytes("RGBA", (w, h), data)


def _resolve(pack_rel, img, path, hops=3):
    """(pack, node) of the real canvas: follows _outlink ('UI/_Canvas/<x>.img/<path>', a 1x1 placeholder) into
    the _Canvas/_Canvas_000.wz next to the package."""
    pk, n = _node(pack_rel, img, path)
    if n.canvas is None:
        raise KeyError("not a canvas: %s/%s" % (img, path))
    link = n.kids.get("_outlink")
    if hops and n.canvas[0] * n.canvas[1] <= 1 and link is not None and isinstance(link.val, str):
        head, sep, tail = link.val.partition(".img/")
        if sep:
            folder = Path(pack_rel).parent
            return _resolve((folder / "_Canvas" / "_Canvas_000.wz").as_posix(),
                            head.rsplit("/", 1)[-1] + ".img", tail, hops - 1)
    return pk, n


def canvas(pack_rel, img, path):
    """Rows (top to bottom) of (r, g, b, a) tuples of one canvas, e.g. canvas("UI/UI_000.wz", "StatusBar.img",
    "gauge/number/5"); a 1x1 placeholder with an _outlink is followed. None on any problem (why_canvas says why)."""
    key = "%s|%s|%s" % (pack_rel, img, path)
    try:
        with _lock:
            pk, n = _resolve(pack_rel, img, path)
            w, h, data = _decode(pk["mm"], n.canvas)
        px = [tuple(data[i:i + 4]) for i in range(0, w * h * 4, 4)]
        return [px[y * w:(y + 1) * w] for y in range(h)]
    except KeyError as e:
        _why[key] = e.args[0]
    except Exception as e:
        close()
        _why[key] = "read error: %s" % type(e).__name__
    return None


def why_canvas(pack_rel, img, path):
    return _why.get("%s|%s|%s" % (pack_rel, img, path), "unknown")


def skill_icon(sid, variant="icon"):
    """PIL RGBA 32x32 for skill id (7 digits, e.g. '2301002') from the player's install, or None.
    On None, why_not(sid) says why."""
    sid = str(sid)
    if not find_install():
        _why[sid] = "no game install found"
        return None
    try:
        with _lock:
            pk = _open_pack(find_install()["data"] / SKILL_PACK)
            first = sid[:3] + ".img"
            for name in [first] + [n for n in pk["index"] if n != first]:
                if name not in pk["index"]:
                    continue
                t = pk["trees"].get(name)
                if t is None:
                    t = pk["trees"][name] = _tree(pk["mm"], pk["index"][name])
                node = t.get("skill/%s/%s" % (sid, variant))
                if node is not None and node.canvas:
                    return _rgba(pk["mm"], node.canvas)
                if name == first and t.get("skill") is not None and sid[:3].isdigit() and len(sid) == 7:
                    break       # right job image, skill not in it: no need to scan the rest
        _why[sid] = "skill not in the game data"
    except Exception as e:      # unreadable / format changed: callers fall back
        close()
        _why[sid] = "read error: %s" % type(e).__name__
    return None


def why_not(sid):
    return _why.get(str(sid), "unknown")


# ------------------------------------------------------------------ fallback log
_logged = set()


def _log_path():
    return DATA / "history" / "icon_sources.jsonl"


def note_fallback(kind, sid, why=None, **extra):
    """Append one line {t, used, id, why} (once per kind+skill and process). kind: 'learned' (cut from the Skill
    window), 'meowdb' (download), 'game' with extra event='updated' for a re-read after a game update."""
    key = (kind, str(sid), extra.get("event"))
    if key in _logged:
        return
    _logged.add(key)
    row = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "used": kind, "id": str(sid), "why": why}
    row.update(extra)
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def summary():
    """{used: {"lines": n, "ids": n, "days": [..]}} from icon_sources.jsonl (empty when no log)."""
    out = {}
    try:
        for line in _log_path().read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            e = out.setdefault(r.get("used", "?"), {"lines": 0, "ids": set(), "days": set()})
            e["lines"] += 1
            e["ids"].add(r.get("id"))
            e["days"].add((r.get("t") or "")[:10])
    except OSError:
        pass
    return {k: {"lines": v["lines"], "ids": sorted(v["ids"]), "days": sorted(v["days"])} for k, v in out.items()}
