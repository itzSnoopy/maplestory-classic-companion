"""translate.py - what the engine's raw readings mean in Classic World.

The engine (engine/) reads pixels and text and knows no game names. It is handed this module
(launcher/compose.py injects it) and asks it:

    map_name(text)         minimap title text -> (name, map_id): snapped to the 453 real Classic maps
                           (maplist.py), else the cleaned raw read; map_id is None when not snapped
    job_name(text)         OCR'd job text -> a pre-Big Bang job name, or None
    next_job_level(job)    level from which the next job advancement is possible (None: no more)
    snap_skill(text)       OCR'd skill name -> the candidate skills (skills_classic.json), cached
    exp_needed(level)      EXP needed at that level (exp_table_classic.json), or None
"""
import difflib
import functools
import json
import re

from base.paths import asset
from gamedata import maplist
from gamedata import parse

SKILLS_FILE = asset("gamedata/skills_classic.json")
EXP_TABLE_FILE = asset("gamedata/exp_table_classic.json")


# ------------------------------------------------------------------ maps
def map_name(text):
    """(name, map_id) for the minimap title text: the snapped Classic name and its id, or the
    cleaned raw read (parse_map) with None."""
    known = maplist.snap(text)
    name = known["name"] if known else parse.parse_map(text)
    return name, (known["id"] if known else None)


# ------------------------------------------------------------------ jobs
# pre-Big Bang jobs by tier (0 = Beginner, 1 = 1st job, ... 4 = 4th job)
JOBS = {
    0: ["Beginner"],
    1: ["Warrior", "Magician", "Bowman", "Thief", "Pirate"],
    2: ["Fighter", "Page", "Spearman", "Wizard (F/P)", "Wizard (I/L)", "Cleric", "Hunter",
        "Crossbowman", "Assassin", "Bandit", "Brawler", "Gunslinger"],
    3: ["Crusader", "White Knight", "Dragon Knight", "Mage (F/P)", "Mage (I/L)", "Priest",
        "Ranger", "Sniper", "Hermit", "Chief Bandit", "Marauder", "Outlaw"],
    4: ["Hero", "Paladin", "Dark Knight", "Arch Mage (F/P)", "Arch Mage (I/L)", "Bishop",
        "Bowmaster", "Marksman", "Night Lord", "Shadower", "Buccaneer", "Corsair"],
}
TIER = {name: t for t, names in JOBS.items() for name in names}
# level from which the next advancement is possible, by current tier.
# Classic World moved Magician's 1st job to Lv 10 like the others (user, 2026-10-23).
# A late advancement is still caught: checks continue every few minutes until the job changes.
NEXT_ADVANCE = {0: 10, 1: 30, 2: 70, 3: 120}


def _norm(s):
    return re.sub(r"[^a-z]", "", s.lower())


_NORM = {_norm(n): n for n in TIER}


def job_name(text):
    """OCR text -> a known job name, or None."""
    k = _norm(text or "")
    if len(k) < 3:
        return None
    if k in _NORM:
        return _NORM[k]
    m = difflib.get_close_matches(k, list(_NORM), n=1, cutoff=0.8)
    return _NORM[m[0]] if m else None


def next_job_level(job):
    """Level from which `job` can advance (None: unknown job or the last tier)."""
    return NEXT_ADVANCE.get(TIER.get(job, -1))


# ------------------------------------------------------------------ skill names
_skills = {"list": None}


def skills():
    if _skills["list"] is None:
        try:
            d = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))["skills"]
        except (OSError, ValueError, KeyError):
            d = {}
        _skills["list"] = [{"id": k, **v} for k, v in d.items()]
    return _skills["list"]


def _sq(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


@functools.lru_cache(maxsize=512)
def _snap_cached(text):
    return tuple(_snap(text))


def snap_skill(text):
    """See _snap; cached (the same names are read every look while the window is open)."""
    return list(_snap_cached(text or ""))


def _snap(text):
    """OCR'd skill name ('Improved WP Reco...', 'hax MP Increase') -> the candidate skills: every
    skill scoring within 0.05 of the best (>= 0.8). Truncated names ('...') match by prefix.
    Several candidates = a tie (same name in several jobs, or 'WP' fitting HP and MP); the
    caller breaks it with the other skills on the same tab."""
    if not text:
        return []
    trunc = "..." in text or "…" in text
    q = _sq(text.split("...")[0].split("…")[0])
    if len(q) < 3:
        return []
    scored = []
    for s in skills():
        n = _sq(s["name"])
        scored.append((difflib.SequenceMatcher(None, q, n[:len(q)] if trunc else n).ratio(), s))
    best = max((r for r, _ in scored), default=0)
    if best < 0.8:
        return []
    return [s for r, s in scored if r >= best - 0.05 and (r == best or best < 0.999)]


# ------------------------------------------------------------------ EXP table
def exp_table():
    t = json.loads(EXP_TABLE_FILE.read_text(encoding="utf-8"))
    return {int(k): v for k, v in t.items() if not k.startswith("_")}


def exp_needed(level):
    """EXP needed to finish `level` (Classic World), or None if it isn't in the table."""
    return exp_table().get(level)
