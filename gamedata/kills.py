"""kills.py - which whole-mob kills an EXP rise on a map can be (shared by training, album, challenge).

A rise is "whole kills" when it equals the sum of 1..n of the map's mob EXP values (exact, no party
share in it). Pure functions over the spawn list of `map_mobs.json`; no state besides caches.

- `map_mobs(maps)`        {map name: [(mob name, exp, count)]} from the map_mobs.json "maps" dict
- `best_split(mobs, n)`   {total: [mob names]} for 1..n kills, fewest kills first (training)
- `multi_sums(name, n)`   (single EXPs, totals of 2..n kills, each mob at most as often as it spawns) (album)
- `whole_kills(name, d)`  how many kills a rise d is on this map, or 0 (challenge)
"""
import json
from collections import Counter
from itertools import combinations_with_replacement

from base.paths import asset

_maps = {"v": None}
_sums = {}
_splits = {}


def _load_maps():
    try:
        return json.loads(asset("gamedata/map_mobs.json").read_text(encoding="utf-8")).get("maps", {})
    except (OSError, ValueError):
        return {}


def map_mobs(maps=None):
    """{map name: [(mob name, exp, count)]} for maps with EXP-giving mobs. Default: the shipped data (cached)."""
    if maps is None:
        if _maps["v"] is None:
            _maps["v"] = map_mobs(_load_maps())
        return _maps["v"]
    out = {}
    for m in maps.values():
        ms = [(x["name"], x["exp"], x.get("count") or 1) for x in m.get("mobs") or [] if x.get("exp")]
        if ms:
            out[m["name"]] = ms
    return out


def best_split(mobs, max_kills):
    """{EXP total: [mob names]} for 1..max_kills kills of `mobs` [(name, exp, count)]: fewer kills first,
    then the mobs there are more of."""
    best = {}
    total_count = sum(c for _, _, c in mobs)
    for n in range(1, max_kills + 1):
        for combo in combinations_with_replacement(mobs, n):
            s = sum(e for _, e, _ in combo)
            score = (n, -sum(c for _, _, c in combo) / total_count)
            if s not in best or score < best[s][0]:
                best[s] = (score, [m for m, _, _ in combo])
    return {s: v[1] for s, v in best.items()}


def multi_sums(map_name, max_kills):
    """(set of single-mob EXPs, set of totals 2..max_kills kills can add up to) on this map, using each mob
    at most as often as it spawns there (Stumpy's 276 EXP is 4 Jr. Boogies on paper, but only 2 Boogies spawn)."""
    key = (map_name, max_kills)
    if key not in _sums:
        cap = Counter()
        for name, exp, count in map_mobs().get(map_name, []):
            cap[exp] += count
        s = set()
        for n in range(2, max_kills + 1):
            for combo in combinations_with_replacement(sorted(cap), n):
                if all(combo.count(e) <= cap[e] for e in set(combo)):
                    s.add(sum(combo))
        _sums[key] = (set(cap), s)
    return _sums[key]


def whole_kills(map_name, d, max_kills=2):
    """Number of kills (1..max_kills) an EXP rise d is on this map, else 0 (a share, a quest, no spawn list).
    Ambiguous totals take the fewest kills."""
    if not map_name or not d or d <= 0:
        return 0
    key = (map_name, max_kills)
    if key not in _splits:
        mobs = map_mobs().get(map_name)
        _splits[key] = best_split(mobs, max_kills) if mobs else {}
    return len(_splits[key].get(d) or ())
