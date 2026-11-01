"""kills.py - which whole-mob kills an EXP rise on a map can be (shared by training, album, challenge).

A rise is "whole kills" when it equals the sum of 1..n of the map's mob EXP values (exact, no party
share in it). Pure functions over the spawn list of `map_mobs.json`; no state besides caches.

- `map_mobs(maps)`        {map name: [(mob name, exp, count)]} from the map_mobs.json "maps" dict
- `best_split(mobs, n)`   {total: [mob names]} for 1..n kills, fewest kills first (training)
- `multi_sums(name, n)`   (single EXPs, totals of 2..n kills, each mob at most as often as it spawns) (album)
- `whole_kills(name, d)`  how many kills a rise d is on this map, or 0 (challenge)
"""
import json
import math
from collections import Counter, namedtuple
from fractions import Fraction
from functools import lru_cache
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


# ---------------------------------------------------------------------------------------------------------
# Party shares (player's observation, Classic World, 2026-11-01). In a party with eligible members of levels
# L_i (sumL = their sum, me included), a mob with EXP E that dies gives
#     my own kill        base_own(E)   = ceil(E * (0.2 + 0.8 * L_me / sumL))
#     a teammate's kill  base_other(E) = ceil(E * 0.8 * L_me / sumL)
# (4 x Lv 28: Horny Mushroom 41 -> 17 / 9, Green Mushroom 26 -> 11 / 6). The PARTY BONUS is a separate
# amount on top, shown by the game as its own number: bonus = round_mode(base * b), b unknown for Classic,
# learned from the player's rises (`fit_bonus`, per eligible party size). The EXP bar can show base+bonus in
# one rise (both landed between two looks) or base and bonus as two rises.
# `party_kills` reads one rise: (mine, others) kills, (0, 0) = unknown / a lone bonus / ambiguous.
# ---------------------------------------------------------------------------------------------------------
MODES = ("ceil", "floor", "round")                       # how the bonus is rounded (learned too)
BONUS_GRID = [Fraction(i, 100) for i in range(10, 61)]   # b = 0.10 .. 0.60 in 0.01 steps (party of 4: ~0.1-0.6)
_TIER_ORDER = ("base", "all")


class BonusFit(namedtuple("BonusFit", "b mode")):
    """A learned bonus rule: bonus = mode(base * b)."""
    def __str__(self):
        return "%.2f %s" % (float(self.b), self.mode)


def base_own(exp, me_level, party_levels):
    """EXP my own kill of a mob gives me: ceil(E * (0.2 + 0.8 * L_me / sumL)), exact integer maths."""
    tot = me_level + sum(party_levels)
    return -(-(exp * (tot + 4 * me_level)) // (5 * tot))


def base_other(exp, me_level, party_levels):
    """EXP a teammate's kill gives me: ceil(E * 0.8 * L_me / sumL)."""
    tot = me_level + sum(party_levels)
    return -(-(4 * exp * me_level) // (5 * tot))


def bonus_amount(base, b, mode="ceil"):
    """The party bonus on a base amount: mode(base * b)."""
    x = base * Fraction(b).limit_denominator(1000)
    if mode == "floor":
        return math.floor(x)
    if mode == "round":
        return math.floor(x + Fraction(1, 2))
    return math.ceil(x)


def _amounts(base, fit):
    """Possible bonus amounts (> 0) on `base`: the learned rule's one, else every grid b and mode."""
    if fit is not None:
        out = {bonus_amount(base, fit.b, fit.mode)}
    else:
        out = {bonus_amount(base, b, m) for b in BONUS_GRID for m in MODES}
    return sorted(a for a in out if a > 0)


def _add(idx, val, outcome):
    idx.setdefault(val, set()).add(outcome)


@lru_cache(maxsize=4096)
def _party_index(map_name, me_level, levels, fit, tol):
    """{tier: {EXP value: {(mine, others, kind)}}} of what ONE look can show on this map: "base" = a bare base
    amount (kind "kill"); "all" = also base+bonus (kill) and a lone bonus (kind "bonus", counts 0). `tol` widens
    every base by +-tol (levels that are stand-ins)."""
    base_ix, all_ix = {}, {}
    for exp in sorted({e for _, e, _ in map_mobs().get(map_name, [])}):
        for b0, kill in ((base_own(exp, me_level, levels), (1, 0)), (base_other(exp, me_level, levels), (0, 1))):
            for e in range(-tol, tol + 1):
                base = b0 + e
                if base < 1:
                    continue
                _add(base_ix, base, kill + ("kill",))
                _add(all_ix, base, kill + ("kill",))
                for amt in _amounts(base, fit):
                    _add(all_ix, base + amt, kill + ("kill",))
                    _add(all_ix, amt, (0, 0, "bonus"))
    return {"base": base_ix, "all": all_ix}


def _decide(found):
    pairs = {(m, o) for m, o, _ in found}
    if len(pairs) != 1:
        return (0, 0, "ambiguous")
    m, o = next(iter(pairs))
    return (m, o, "kill" if (m or o) else "bonus")


def _pairs(idx, d):
    found = set()
    for v1, o1 in idx.items():
        r = d - v1
        if r < v1:
            continue
        o2 = idx.get(r)
        if o2:
            for a in o1:
                for b in o2:
                    found.add((a[0] + b[0], a[1] + b[1], "kill" if "kill" in (a[2], b[2]) else "bonus"))
    return found


def read_rise(map_name, d, me_level, party_levels, fit=None, tol=0, max_kills=2):
    """(mine, others, kind) for a rise: kind "kill" (counts), "bonus" (a lone party bonus: explained, 0 kills),
    "ambiguous" (explainable as different owners, or a kill and a bonus: refused) or "none" (unexplained).

    Fewest kills first (1 then 2 in the rise). With a learned `fit` every candidate is an exact value and any
    disagreement refuses. Without one (estimating) the bare base amounts (1 or 2 kills) are tried first and win
    if they match; only if none does, base + any grid bonus / a lone grid bonus are tried (strict again).
    "ambiguous" = the rise is explained, but not to one owner."""
    if not map_name or not d or d <= 0 or not me_level or not party_levels:
        return (0, 0, "none")
    ix = _party_index(map_name, int(me_level), tuple(int(x) for x in party_levels), fit, tol)
    tiers = [ix["all"]] if fit is not None else [ix[t] for t in _TIER_ORDER]
    for idx in tiers:
        for n in range(1, min(max_kills, 2) + 1):
            found = idx.get(d) if n == 1 else _pairs(idx, d)
            if found:
                return _decide(found)
    return (0, 0, "none")


def _tol(party_levels, uncertain):
    return 1 if (uncertain or getattr(party_levels, "guessed", 0)) else 0


def party_kills(map_name, d, me_level, party_levels, bonus=None, max_kills=2, uncertain=False):
    """Read an EXP rise d on a map while partied with `party_levels` (levels of the OTHER eligible members on
    your map; empty = solo, use whole_kills instead). `bonus` = a BonusFit or None (not learned yet: estimating).
    Returns (mine, others): how many of the rise's kills were yours / a teammate's, or (0, 0) = unknown (no
    spawn list, no match, a lone bonus, or ambiguous).

    `uncertain` (or a `party_levels` list with a truthy `.guessed`, see partyexp.Levels): some level is a
    stand-in for an unknown one, so every base amount may be off by 1."""
    m, o, _ = read_rise(map_name, d, me_level, party_levels, bonus, _tol(party_levels, uncertain), max_kills)
    return (m, o)


def score_bonus(rises, bonus=None, max_kills=2):
    """How well a rule explains rises [(map, d, me_level, party_levels[, uncertain])]:
    (explained, mine, others). A lone bonus counts as explained (0 kills); an ambiguous rise does NOT (crowded
    models would otherwise "explain" everything); None = estimating reading."""
    ex = mi = ot = 0
    for r in rises:
        m, d, lme, lv = r[:4]
        a, b, kind = read_rise(m, d, lme, lv, bonus, _tol(lv, len(r) > 4 and r[4]), max_kills)
        if kind in ("kill", "bonus"):
            ex += 1
            mi += a
            ot += b
    return ex, mi, ot


def score_bonus_base(rises, max_kills=2):
    """Rises explained by the bare base amounts alone (no bonus at all)."""
    n = 0
    for r in rises:
        m, d, lme, lv = r[:4]
        if not (m and lme and lv and d and d > 0):
            continue
        ix = _party_index(m, int(lme), tuple(int(x) for x in lv), None, _tol(lv, len(r) > 4 and r[4]))["base"]
        if ix.get(d) or (max_kills >= 2 and _pairs(ix, d)):
            n += 1
    return n


def fit_bonus(rises, grid=BONUS_GRID, modes=MODES, min_explained=15, min_fraction=0.5, min_gain=5, max_kills=2):
    """Learn the bonus rule (b and rounding mode) from the rises of one party size. Returns
    {b, mode, explained, n, base, mean} or None when none explains them well: the best (b, mode) must explain
    at least `min_explained` rises and `min_fraction` of all, at least `min_gain` more than the bare base
    amounts do (the bonus has to be SEEN), and clearly more (1.3x) than the average rule does (otherwise any
    rule "explains" a map with many small mobs: coincidences). Within the best mode, b = centre of the longest
    run of equally good grid values."""
    if len(rises) < min_explained:
        return None
    base_only = score_bonus_base(rises, max_kills)
    res = {mode: [score_bonus(rises, BonusFit(b, mode), max_kills)[0] for b in grid] for mode in modes}
    best = max(max(v) for v in res.values())
    allv = [x for v in res.values() for x in v]
    mean = sum(allv) / len(allv)
    if (best < min_explained or best < min_fraction * len(rises) or best - base_only < min_gain
            or best < 1.3 * mean):
        return None
    mode = next(m for m in modes if max(res[m]) == best)
    scores = res[mode]
    runs, cur = [], []
    for i, s in enumerate(scores):
        if s == best:
            cur.append(i)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    run = max(runs, key=len)
    return {"b": float(grid[run[len(run) // 2]]), "mode": mode, "explained": best, "n": len(rises),
            "base": base_only, "mean": round(mean, 1)}
