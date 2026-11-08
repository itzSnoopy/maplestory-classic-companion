"""translate.py - what the engine's raw readings mean in Classic World.

The engine (engine/) reads pixels and text and knows no game names. It is handed this module
(launcher/compose.py injects it) and asks it:

    map_name(text)         minimap title text -> (name, map_id): snapped to the 453 real Classic maps
                           (maplist.py), else the cleaned raw read; map_id is None when not snapped
    job_name(text)         OCR'd job text -> a pre-Big Bang job name, or None
    next_job_level(job)    level from which the next job advancement is possible (None: no more)
    snap_skill(text)       OCR'd skill name -> the candidate skills (skills_classic.json), cached
    skill_kind(id)         skill id -> {kind, sub, target, duration_s, until, attacks, name} (skill_kinds_classic.json), or None
    exp_needed(level)      EXP needed at that level (exp_table_classic.json), or None
    game_canvas / game_children / game_stamp / game_install   the player's own game files (gamefiles.py), None on any problem
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
KINDS_FILE = asset("gamedata/skill_kinds_classic.json")


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
# other names the game shows for the same job: Classic's status bar says "Swordsman" for 1st-job
# warriors, while equipment and the job lists say "Warrior" (player, 2026-11-07); keep one name each
JOB_ALIASES = {"Swordsman": "Warrior", "Swordman": "Warrior", "Archer": "Bowman", "Rogue": "Thief"}
_NORM.update({_norm(a): n for a, n in JOB_ALIASES.items()})


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


# game job ids: a skill id's first digits are its job's id (2301001 = Heal = 230 Cleric, 0001000 = 0 Beginner)
JOB_IDS = {
    "Beginner": 0,
    "Warrior": 100, "Fighter": 110, "Crusader": 111, "Hero": 112, "Page": 120, "White Knight": 121,
    "Paladin": 122, "Spearman": 130, "Dragon Knight": 131, "Dark Knight": 132,
    "Magician": 200, "Wizard (F/P)": 210, "Mage (F/P)": 211, "Arch Mage (F/P)": 212,
    "Wizard (I/L)": 220, "Mage (I/L)": 221, "Arch Mage (I/L)": 222, "Cleric": 230, "Priest": 231,
    "Bishop": 232,
    "Bowman": 300, "Hunter": 310, "Ranger": 311, "Bowmaster": 312, "Crossbowman": 320, "Sniper": 321,
    "Marksman": 322,
    "Thief": 400, "Assassin": 410, "Hermit": 411, "Night Lord": 412, "Bandit": 420, "Chief Bandit": 421,
    "Shadower": 422,
    "Pirate": 500, "Brawler": 510, "Marauder": 511, "Buccaneer": 512, "Gunslinger": 520, "Outlaw": 521,
    "Corsair": 522,
}


def _on_path(older, newer):
    """True when job id `older` is `newer` or one of the jobs before it (0 -> 200 -> 230 -> 231)."""
    if older == 0 or older == newer:
        return True
    if older // 100 != newer // 100:
        return False
    b_old, b_new = older % 100 // 10, newer % 100 // 10
    return (b_old == 0 or b_old == b_new) and older % 10 <= newer % 10 and older <= newer


def skill_fits_job(sid, job):
    """Can a character of `job` have skill `sid`? The skill's job and `job` must be on one
    advancement path, either way round: a stored job can lag behind an advancement (the Stat window
    not opened since), so a "Magician" may already have Cleric skills, but a Warrior never has
    them. True when the job or the skill id is unknown: only a sure mismatch says no."""
    j = JOB_IDS.get(job)
    try:
        s = int(str(sid)[:-4] or 0)
    except ValueError:
        return True
    return True if j is None else (_on_path(s, j) or _on_path(j, s))


def same_job_line(a, b):
    """Could jobs `a` and `b` be one character at two times (one advanced from the other)?
    True when either is unknown."""
    ja, jb = JOB_IDS.get(a), JOB_IDS.get(b)
    if ja is None or jb is None:
        return True
    return _on_path(ja, jb) or _on_path(jb, ja)


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


_kinds = {"d": None}


def skill_kind(sid):
    """{kind: attack|buff|passive|other, sub, target, duration_s[], until, name} for a skill id, or None.
    Made by dev/make_skill_kinds.py (skill_kinds_classic.json); never classify from the text at run time."""
    if _kinds["d"] is None:
        try:
            _kinds["d"] = json.loads(KINDS_FILE.read_text(encoding="utf-8"))["kinds"]
        except (OSError, ValueError, KeyError):
            _kinds["d"] = {}
    return _kinds["d"].get(str(sid).zfill(7))


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


# ------------------------------------------------------------------ the player's own game files (gamefiles.py)
# The engine may import only base, so it reaches these through the injected translator. Never raise.
def game_canvas(pack_rel, img, path):
    """Rows of (r, g, b, a) of one canvas in the player's install (e.g. 'UI/UI_000.wz', 'StatusBar.img',
    'gauge/number/5'), or None."""
    try:
        from gamedata import gamefiles
        return gamefiles.canvas(pack_rel, img, path)
    except Exception:
        return None


def game_children(pack_rel, img, path=""):
    """Child names of a node in the player's install, or None."""
    try:
        from gamedata import gamefiles
        return gamefiles.children(pack_rel, img, path)
    except Exception:
        return None


def game_stamp():
    """Size + mtime of the game files read (changes with a game update), or None."""
    try:
        from gamedata import gamefiles
        return gamefiles.stamp()
    except Exception:
        return None


def game_install():
    """The player's install ({data, exe, exe_version, how}) or None."""
    try:
        from gamedata import gamefiles
        return gamefiles.find_install()
    except Exception:
        return None
