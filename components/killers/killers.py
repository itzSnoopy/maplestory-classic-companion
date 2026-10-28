"""killers.py - guess which mob killed the player, from the map they died on.

The game never names the killer on screen, but many maps make it obvious:
  1. curated     killers.json says so for this map (boss maps, case by case: the Zakum altar is
                 Zakum, but a field where Mushmom spawns among 30 mushrooms is not blamed on her)
  2. only_mob    the map has a single mob species
  3. strongest   "probably" the top regular mob, when it's the only regular species, the only
                 one within WEAK_BELOW levels of the player, or STRONGER_BY levels above the rest.
                 Bosses and rare wanderers (_rare) are never guessed: curate in killers.json
Otherwise no killer, only `suspects` (strongest first), so the diary never blames the wrong mob.

Mob lists come from map_mobs.json (dev/fetch_map_mobs.py, NiaMeowDB spawn lists). Several maps
share a name; their mob lists are merged, since the tracker only reads the name.

  python killers.py "<map name>" [level]      show what a death there would say
  python killers.py audit                      maps where a death would get no killer
"""
import json
import sys
from functools import lru_cache

from base.paths import DATA, asset

STRONGER_BY = 10        # levels above every other species on the map to count as "probably"
WEAK_BELOW = 10         # a mob this many levels under the player isn't a plausible killer
BOSS_RESPAWN_S = 1800   # respawn timer this long = boss / rare spawn (Mushmom 3600 s); shorter
                        # timers (1-600 s) are ordinary mobs on a fast or slow respawn
RARE_COUNT, RARE_RESPAWN_S = 3, 180   # <= 3 spawns on a >= 3 min timer: a rare wanderer
                        # (Jr. Boogie, fairies): listed as a suspect, never the guess


def _rare(x):
    return x["respawn"] >= BOSS_RESPAWN_S or (x["count"] <= RARE_COUNT and x["respawn"] >= RARE_RESPAWN_S)


def _load(name, rel):
    for path in (DATA / name, asset(rel)):      # the player's copy in DATA wins
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


@lru_cache(maxsize=1)
def _mobs_by_map():
    """map name -> {mob name: {"level", "count", "respawn"}} (same-name maps merged)."""
    out = {}
    for m in _load("map_mobs.json", "gamedata/map_mobs.json").get("maps", {}).values():
        mobs = out.setdefault(m["name"], {})
        for x in m["mobs"]:
            have = mobs.get(x["name"])
            if have:
                have["count"] += x["count"]
            else:
                mobs[x["name"]] = {"level": x.get("level") or 0, "count": x["count"],
                                   "respawn": x.get("respawn") or 0}
    return out


@lru_cache(maxsize=1)
def _curated():
    return {e["map"]: e for e in _load("killers.json", "components/killers/killers.json").get("maps", [])}


def mobs_on(map_name):
    return _mobs_by_map().get(map_name, {})


def attribute(map_name, level=None):
    """{"killer": name or None, "how": curated/only_mob/strongest/None, "sure": bool,
        "suspects": [names, strongest first], "why": text or None}"""
    mobs = mobs_on(map_name)
    cur = _curated().get(map_name, {})
    ignore = set(cur.get("ignore", []))
    ranked = sorted((n for n in mobs if n not in ignore), key=lambda n: -mobs[n]["level"])
    res = {"killer": None, "how": None, "sure": False, "suspects": ranked[:5], "why": None}
    if cur.get("killer"):
        return {**res, "killer": cur["killer"], "how": "curated", "sure": not cur.get("probably"),
                "why": cur.get("why")}
    if len(ranked) == 1:
        return {**res, "killer": ranked[0], "how": "only_mob", "sure": True}
    common = [n for n in ranked if not _rare(mobs[n])]
    if len(common) == 1:                    # one regular species plus rare wanderers
        return {**res, "killer": common[0], "how": "strongest", "sure": False}
    if len(common) >= 2:
        top, second = mobs[common[0]], mobs[common[1]]
        # mobs far below the player barely scratch them; drop those from the line-up
        plausible = [n for n in common if level is None or mobs[n]["level"] >= level - WEAK_BELOW]
        alone = len(plausible) == 1 and plausible[0] == common[0]
        if alone or (top["level"] - second["level"] >= STRONGER_BY and common[0] in plausible):
            return {**res, "killer": common[0], "how": "strongest", "sure": False}
    return res


def label(a):
    """The killer's name when sure, else None. Guesses ("probably") and suspects are kept in the
    timeline entry for later review but never shown by name (user, 2026-10-23)."""
    if not a or not a.get("killer") or not a.get("sure"):
        return None
    return a["killer"]


def _audit():
    by_map = _mobs_by_map()
    counts = {}
    for name, mobs in sorted(by_map.items()):
        a = attribute(name)
        counts[a["how"]] = counts.get(a["how"], 0) + 1
        bosses = [n for n, x in mobs.items() if x["respawn"] >= BOSS_RESPAWN_S]
        if a["how"] is None and mobs:
            print(f"- {name}: {', '.join(f'{n} {mobs[n]['level']}' for n in a['suspects'])}"
                  + (f"  [timed: {', '.join(bosses)}]" if bosses else ""))
        elif bosses and a["how"] != "curated":
            print(f"* {name}: {a['how']} -> {a['killer']}  [timed: {', '.join(bosses)}]")
    print(counts, f"(+ {sum(1 for m in by_map.values() if not m)} maps without mobs)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "audit":
        _audit()
    elif len(sys.argv) > 1:
        lv = int(sys.argv[2]) if len(sys.argv) > 2 else None
        a = attribute(sys.argv[1], lv)
        print(json.dumps(a, indent=1), "\n->", label(a) or "no killer named")
    else:
        print(__doc__)
