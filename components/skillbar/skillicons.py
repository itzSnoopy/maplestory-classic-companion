"""skillicons.py - skill pictures for the skill bar, fetched once from NiaMeowDB.

Used when a skill has no picture cut from the player's own Skill window (e.g. a party member's Heal).
Nexon game art: never shipped, committed or sent through the relay. Each app downloads only the ones
it shows, once, into <data>/assets/skill_icons/<skill id>.png (assets/ is git-ignored and never
packaged; the player's own cuts in templates/skill_icons/ are a different thing and never touched
here). Only while Settings > Internet > "Skill pictures" is on (internet.allowed("skillicons")).
index.json in the same folder remembers what was fetched and what failed (not retried for a day).
Skill ids are 7-digit strings with leading zeros.

Game first (gamedata/gamefiles.py): when the player's own install has the skill, `game_path(sid)` decodes
its `icon` (== the MeowDB art, used for buff matching) into <id>.png and its `iconMouseOver` (what the bar
shows) into hover/<id>.png, and nothing is downloaded. `path(sid)` order: game -> an existing MeowDB file ->
download. index.json "from" {id: "game"|"meowdb"} and "game_stamp" (gamefiles.stamp()); when the stamp
changes (a game update) `refresh_from_game()` re-decodes the "game" ids once per process and rewrites the
changed ones (logged in history/icon_sources.jsonl). Decoded art stays in the data folder only.
"""
import json
import threading
import time
import urllib.request

from base import internet
from base.paths import DATA
from gamedata import gamefiles

URL = "https://meowdb.com/msclassic/classes/icons/{key}.png"
UA = {"User-Agent": "MapleClassicCompanion skill icons (personal use)"}
MAX_BYTES = 200_000
RETRY_S = 24 * 3600
NEG_S = 60                      # a skill the game could not give is not asked again for this long
# ids the game lacks whose art is shared with another: id -> (game id, variant of `icon`)
ALIASES = {2200000: (2100000, "icon"), 2300000: (2100000, "icon"),
           1110000: (2000000, "iconMouseOver")}      # 1210000 left out: not verified against MeowDB
_busy = set()
_index = None
_lock = threading.RLock()
_neg = {}                       # id -> time of the last failed game decode
_refreshed = False


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
        _index.setdefault("from", {})
    return _index


def _save_idx():
    try:
        folder().mkdir(parents=True, exist_ok=True)
        tmp = folder() / "index.json.tmp"
        tmp.write_text(json.dumps(_idx(), indent=1), encoding="utf-8")
        tmp.replace(folder() / "index.json")
    except OSError:
        pass


def _sources(sid):
    """(game id, icon variant, hover variant) for a skill id."""
    g, v = ALIASES.get(int(sid), (int(sid), "icon"))
    return "%07d" % g, v, ("iconMouseOver" if v == "icon" else v)


def _decode(key):
    """(icon PIL, hover PIL) from the game, or None. hover falls back to the icon."""
    gid, v_icon, v_hover = _sources(key)
    ic = gamefiles.skill_icon(gid, v_icon)
    if ic is None:
        return None
    return ic, (gamefiles.skill_icon(gid, v_hover) or ic)


def _save_png(img, p):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    img.convert("RGBA").save(tmp, "PNG")
    tmp.replace(p)


def game_path(sid):
    """Decode skill `sid` from the player's install into <id>.png (icon) and hover/<id>.png (iconMouseOver, or the
    icon when the game has none). Returns the icon path, or None (no install / not in the game data: a 60 s
    negative cache, gamefiles.why_not says why)."""
    if not sid or not str(sid).isdigit():
        return None
    key = str(sid)
    p, h = folder() / f"{key}.png", folder() / "hover" / f"{key}.png"
    with _lock:
        if _idx()["from"].get(key) == "game" and p.exists() and h.exists():
            return p
        if time.time() - _neg.get(key, 0) < NEG_S:
            return None
        try:
            both = _decode(key)
        except Exception:
            both = None
        if both is None:
            _neg[key] = time.time()
            return None
        try:
            _save_png(both[0], p)
            _save_png(both[1], h)
        except OSError:
            return None
        _idx()["from"][key] = "game"
        _idx()["game_stamp"] = gamefiles.stamp()
        _save_idx()
        return p


def hover_path(sid):
    """The picture the bar shows (iconMouseOver from the game), or None."""
    if not sid or not str(sid).isdigit():
        return None
    h = folder() / "hover" / f"{sid}.png"
    if h.exists() and _idx()["from"].get(str(sid)) == "game":
        return h
    return h if game_path(sid) and h.exists() else None


def refresh_from_game():
    """When the game files changed (gamefiles.stamp() differs from index.json's), re-decode every id marked "game"
    and rewrite the pictures whose pixels changed. Returns the changed ids (also logged as event=updated)."""
    from PIL import Image
    changed = []
    with _lock:
        idx = _idx()
        st = gamefiles.stamp()
        if st is None or json.dumps(st, sort_keys=True) == json.dumps(idx.get("game_stamp"), sort_keys=True):
            return changed
        for key, src in list(idx["from"].items()):
            if src != "game":
                continue
            try:
                both = _decode(key)
            except Exception:
                both = None
            if both is None:
                continue
            diff = False
            for img, f in ((both[0], folder() / f"{key}.png"), (both[1], folder() / "hover" / f"{key}.png")):
                try:
                    old = Image.open(f).convert("RGBA")
                    same = old.size == img.size and old.tobytes() == img.convert("RGBA").tobytes()
                except OSError:
                    same = False
                if not same:
                    try:
                        _save_png(img, f)
                        diff = True
                    except OSError:
                        pass
            if diff:
                changed.append(key)
                gamefiles.note_fallback("game", key, why="pixels changed after a game update", event="updated")
        idx["game_stamp"] = st
        _save_idx()
    return changed


def path(sid):
    """The local picture for skill `sid`, or None. Order: decoded from the game install, an existing MeowDB file,
    else a background download when allowed (call again later)."""
    global _refreshed
    if not sid or not str(sid).isdigit():
        return None
    key = str(sid)
    if not _refreshed:
        _refreshed = True
        try:
            refresh_from_game()
        except Exception:
            pass
    g = game_path(key)
    if g:
        return g
    p = folder() / f"{key}.png"
    if p.exists():
        return p
    gamefiles.note_fallback("meowdb", key, why=gamefiles.why_not(key))
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
        _idx()["from"][key] = "meowdb"
        _idx()["failed"].pop(key, None)
    except Exception:
        _idx()["failed"][key] = round(time.time())
    finally:
        _save_idx()
        _busy.discard(key)
