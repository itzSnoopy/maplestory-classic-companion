"""Source-agnostic data model.

Everything downstream (Tracker, journal, history, digest) consumes `Observation`s and never
knows where they came from. A source only has to turn "whatever it reads" into Observations.

Today: `wgc` (game window frames) and `screen` (OBS frames), both read with glyphs/Tesseract.
Tomorrow: an official API, a game
export, a replay of saved frames, etc. Register new sources in sources/__init__.py.

Caveat for future sources on the OFFICIAL Classic World server: anything that reads game
memory or intercepts game traffic violates Nexon's terms and risks an anti-cheat ban.
That's the player's explicit call; never add one by default.
"""
from dataclasses import dataclass, field
from typing import Any, Optional

OK = "ok"                    # a reading of the game
ABSENT = "absent"            # source works, but the game isn't there (closed, loading, cash shop)
UNAVAILABLE = "unavailable"  # the source itself isn't reachable (OBS closed, API down)


@dataclass
class Observation:
    t: float                                 # epoch seconds when the reading was taken
    status: str = OK
    exp_cur: Optional[int] = None            # EXP into the current level
    exp_pct: Optional[float] = None          # % of the current level, 2 decimals
    level: Optional[int] = None
    map: Optional[str] = None
    exp_needed: Optional[int] = None         # set only by sources that know it exactly
    job: Optional[str] = None
    gains: list = field(default_factory=list)   # per-kill EXP breakdown: [(base, bonus), ...]
    suspect: bool = False                    # the source isn't confident in this reading
    raw: dict = field(default_factory=dict)  # source-specific raw values (logged as JSON)
    source: str = ""
    note: str = ""                           # why absent/unavailable, etc.


class Source:
    """Interface every source implements. Only `read` is required."""
    name = "base"
    retry_s = 20          # how long to wait after UNAVAILABLE

    def read(self, hints: dict) -> Observation:
        """Take one reading. `hints` carries tracker context a source may use to read better
        (e.g. 'need' = EXP needed for this level, 'plausible' = callable(cur, pct))."""
        raise NotImplementedError

    # optional hooks ---------------------------------------------------------------
    def begin_session(self, session_dir, gallery_dir):
        """A play session started; artifacts (images, debug) may go under session_dir."""

    def after_step(self, obs: Observation, event: Optional[str], ctx: dict):
        """Called after the tracker processed `obs`. event: None|levelup|death|rejected.
        ctx: level, last_levelup_t, exp_total, forced_level. Lets a source save evidence
        (e.g. level-up screenshots) or self-calibrate (e.g. learn glyphs)."""

    def snapshot(self, out_dir, stem, delays=(0,)):
        """Save pictures of this moment for the diary (full frame + character close-up).
        delays: seconds after now (0 = the current reading; later ones are taken on the first
        reading after that time). Returns the file paths that are or will be written.
        Sources without pictures return []."""
        return []

    def end_session(self):
        pass

    def close(self):
        pass


def describe(obs: Observation) -> Any:
    """Compact text for logs."""
    return f"{obs.exp_cur}[{obs.exp_pct}%] Lv {obs.level} @ {obs.map}"
