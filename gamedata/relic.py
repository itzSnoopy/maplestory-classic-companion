"""relic.py - what an EXP rise is worth to the relic hunt (the app side of the fairness rules).

eligible kills = mobs' worth of the rise (rise / the map's spawn-weighted average mob EXP, training's
formula) x the map's in-band share (the spawn-weighted share of the map's mobs whose level is within
+-BAND of yours). Works the same solo or partied: mobs' worth, so party shares count proportionally.
An unknown map (not in map_mobs.json, or no mob with EXP and a level) is worth 0.

  mobs_worth(map, d)            rise / average mob EXP, or 0
  in_band_share(map, level)     0..1, 0 for an unknown map or level
  eligible_kills(map, d, level) the product

Pure functions over gamedata/map_mobs.json (cached); no state besides the cache.
"""
import json

from base.paths import asset

BAND = 10
_cache = {"v": None}


def _maps():
    if _cache["v"] is None:
        out = {}
        try:
            raw = json.loads(asset("gamedata/map_mobs.json").read_text(encoding="utf-8")).get("maps", {})
        except (OSError, ValueError):
            raw = {}
        for m in raw.values():
            mobs = [(x.get("level"), x["exp"], x.get("count") or 1) for x in m.get("mobs") or []
                    if x.get("exp") and isinstance(x.get("level"), int)]
            if mobs:
                out[m["name"]] = mobs
        _cache["v"] = out
    return _cache["v"]


def mobs_worth(map_name, d, maps=None):
    """A rise d on this map as a (fractional) number of average mobs; 0 for an unknown map."""
    mobs = (maps if maps is not None else _maps()).get(map_name)
    if not mobs or not d or d <= 0:
        return 0.0
    total = sum(c for _, _, c in mobs)
    avg = sum(e * c for _, e, c in mobs) / total
    return d / avg if avg else 0.0


def in_band_share(map_name, level, band=BAND, maps=None):
    """Spawn-weighted share of the map's mobs within +-band levels of `level`."""
    mobs = (maps if maps is not None else _maps()).get(map_name)
    if not mobs or not level:
        return 0.0
    total = sum(c for _, _, c in mobs)
    return sum(c for lv, _, c in mobs if abs(lv - level) <= band) / total


def eligible_kills(map_name, d, level, maps=None):
    """Kills the relic hunt may count for a rise d on `map_name` at your `level`."""
    share = in_band_share(map_name, level, maps=maps)
    return mobs_worth(map_name, d, maps) * share if share else 0.0
