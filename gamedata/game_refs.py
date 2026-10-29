"""game_refs.py - small game icons for caption sources (the "— Dark Souls" tag in diary.html).

game_refs.json maps a caption `source` to its Steam app id. `fetch_icons()` asks Steam's store API
for each game's small capsule image (184x69) and saves it as game_icons/<appid>.jpg in the player's
data folder. Only Steam's own hosts are downloaded from.

  python -m components.diary.diary icons   fetch missing icons (then rebuild diary.html)

The icons are the publishers' art: local only, never commit, publish or ship them.
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

from base.paths import DATA, asset

ICONS = DATA / "game_icons"
STEAM_HOSTS = ("steamstatic.com", "steampowered.com", "akamaihd.net")
UA = {"User-Agent": "MapleClassicCompanion diary (personal use)"}


def refs():
    """source -> Steam app id (None = not on Steam). The player's copy in DATA wins."""
    for path in (DATA / "game_refs.json", asset("gamedata/game_refs.json")):
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        return {k: v for k, v in d.items() if not k.startswith("_")}
    return {}


def icon_rel(source):
    """Path of the source's icon relative to the data folder, or None if it isn't downloaded."""
    aid = refs().get(source)
    if aid and (ICONS / f"{aid}.jpg").exists():
        return f"game_icons/{aid}.jpg"
    return None


def icons():
    """{source: relative icon path} for every source that has one."""
    out = {}
    for src, aid in refs().items():
        if aid and (ICONS / f"{aid}.jpg").exists():
            out[src] = f"game_icons/{aid}.jpg"
    return out


def _steam_url(url):
    host = urllib.parse.urlparse(url).hostname or ""
    return url.startswith("https://") and any(host == h or host.endswith("." + h) for h in STEAM_HOSTS)


def fetch_icons(force=False, log=print):
    ICONS.mkdir(parents=True, exist_ok=True)
    got = 0
    for src, aid in refs().items():
        if not aid:
            continue
        out = ICONS / f"{aid}.jpg"
        if out.exists() and not force:
            continue
        try:
            api = f"https://store.steampowered.com/api/appdetails?appids={int(aid)}&filters=basic"
            with urllib.request.urlopen(urllib.request.Request(api, headers=UA), timeout=15) as r:
                d = json.load(r).get(str(aid), {})
            url = (d.get("data") or {}).get("capsule_imagev5") or (d.get("data") or {}).get("capsule_image")
            if not d.get("success") or not url or not _steam_url(url):
                log(f"  {src}: no icon on Steam")
                continue
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
                data = r.read(2_000_000)
            if not data.startswith(b"\xff\xd8"):          # JPEG only
                log(f"  {src}: not a JPEG, skipped")
                continue
            out.write_bytes(data)
            got += 1
            log(f"  {src}: {out.name} ({len(data) // 1024} KB)")
        except Exception as ex:
            log(f"  {src}: failed ({ex})")
    return got
