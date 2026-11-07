"""relic.py - what an EXP rise is worth to the relic hunt (the app side of the fairness rules).

Since 2026-11-02 a rise counts the duel's way: the number of whole kills of MINE in it (white EXP only: the
rise_kills service of the challenge component; solo = the map's mob EXP, partied = kills.party_kills, the
party bonus, teammates' shares, quest EXP and PQ stage clears count nothing), then
    eligible = kills x weight(map, level)
weight = 1 on a party quest map (pq_classic.json: PQ mobs are often far below you, the PQ is still the right
place to be), else the map's in-band share (spawn-weighted share of its mobs within +-BAND of your level).

  in_band_share(map, level)          0..1, 0 for an unknown map or level
  pq_maps()                          set of party quest map names
  weight(map, level)                 1 on a PQ map, else in_band_share
  eligible(map, kills, level)        kills x weight
  mobs_worth / eligible_kills        the old fractional reading (rise / average mob EXP), kept for tools

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


def pq_maps():
    """Every map of every party quest in pq_classic.json (stages, bonus, other)."""
    if _cache.get("pq") is None:
        out = set()
        try:
            for pq in json.loads(asset("gamedata/pq_classic.json").read_text(encoding="utf-8")).get("pqs", []):
                out.update(s.get("map") for s in pq.get("stages") or [])
                out.update([pq.get("first"), pq.get("bonus")] + list(pq.get("other") or []))
        except (OSError, ValueError):
            pass
        out.discard(None)
        _cache["pq"] = out
    return _cache["pq"]


def weight(map_name, level, maps=None):
    """How much one of my kills on this map counts: 1 on a PQ map, else the map's in-band share."""
    if map_name in pq_maps():
        return 1.0
    return in_band_share(map_name, level, maps=maps)


def eligible(map_name, kills, level, maps=None):
    """Eligible kills for `kills` whole kills of mine on `map_name` at your `level`."""
    if not kills or kills <= 0 or not level:
        return 0.0
    return kills * weight(map_name, level, maps)


def eligible_kills(map_name, d, level, maps=None):
    """Kills the relic hunt may count for a rise d on `map_name` at your `level`."""
    share = in_band_share(map_name, level, maps=maps)
    return mobs_worth(map_name, d, maps) * share if share else 0.0
