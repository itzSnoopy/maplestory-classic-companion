"""Job name from the status bar (the line above the character name, left of HP).

Read rarely, because the job only changes at a job advancement:
  - once when the tracker starts (retried every minute until a read succeeds)
  - while an advancement is *possible* (level reached the next advancement level but the job
    is still the old tier): every few minutes, until the new job shows up
  - never otherwise.
Advancement happens when the player visits the job NPC, not at the level-up itself, which is
why it keeps checking while one is possible instead of reading once at the level-up.

The text is the same crisp white pixel font as the HUD; Tesseract reads it well with a
near-white mask (verified on Lv 5–8 frames: "Beginner"). Reads are snapped to a list of
pre-Big Bang job names, so OCR noise can't invent a job.
"""
import difflib
import re
import time

import ocr
from capture_obs import crop
from layout import anchor_for

# 1920x1080 status bar; same anchor as the level badge (bottom-center UI)
DEFAULT_RC = {"box": [643, 1049, 775, 1062], "white_min": 220, "scale": 5, "psm": 7}

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

RETRY_UNKNOWN_S = 60      # first read failed: try again after this
PENDING_EVERY_S = 300     # advancement possible: check this often


def _norm(s):
    return re.sub(r"[^a-z]", "", s.lower())


_NORM = {_norm(n): n for n in TIER}


def snap(text):
    """OCR text -> a known job name, or None."""
    k = _norm(text or "")
    if len(k) < 3:
        return None
    if k in _NORM:
        return _NORM[k]
    m = difflib.get_close_matches(k, list(_NORM), n=1, cutoff=0.8)
    return _NORM[m[0]] if m else None


def read_job(frame, rc=None, res=(1920, 1080)):
    """Returns (job or None, raw OCR text)."""
    rc = {**DEFAULT_RC, **(rc or {})}
    if not ocr.READY:
        return None, ""
    try:
        raw = ocr._tess(crop(frame, rc["box"], res, anchor_for("job", rc), "job"), rc)
    except Exception as e:
        return None, f"<ocr error: {e}>"
    return snap(raw), raw


class JobWatcher:
    """Decides when to read and remembers the last job read."""

    def __init__(self, rc=None, res=(1920, 1080), log=print):
        self.rc, self.res, self.log = rc, res, log
        self.job = None
        self.last_try = 0.0
        self.last_raw = ""

    def due(self, level, now):
        if self.job is None:
            return now - self.last_try >= RETRY_UNKNOWN_S if self.last_try else True
        nxt = NEXT_ADVANCE.get(TIER.get(self.job, -1))
        return bool(level and nxt and level >= nxt and now - self.last_try >= PENDING_EVERY_S)

    def update(self, frame, level, now=None):
        """Call on every good frame; reads only when due. Returns the current job (or None)."""
        now = now or time.time()
        if not self.due(level, now):
            return self.job
        self.last_try = now
        job, raw = read_job(frame, self.rc, self.res)
        if job and job != self.job:
            self.log(f"job read: {job!r}" + (f" (was {self.job})" if self.job else ""))
            self.job = job
        elif not job and raw != self.last_raw:
            self.log(f"job read failed: {raw!r}")
        self.last_raw = raw
        return self.job
