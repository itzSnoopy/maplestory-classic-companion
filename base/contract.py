"""contract.py - what every layer shares: Observation, Source, the event names and Component.

Imports nothing internal. (Observation and Source were engine/sources/base.py, which now re-exports them.)

Source-agnostic data model.

Everything downstream (Tracker, journal, history, digest) consumes `Observation`s and never
knows where they came from. A source only has to turn "whatever it reads" into Observations.

Today: `wgc` (game window frames, read with glyphs/Tesseract) and `csv_replay` (tests).
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
UNAVAILABLE = "unavailable"  # the source itself isn't reachable (capture failed, API down)


@dataclass
class Observation:
    t: float                                 # epoch seconds when the reading was taken
    status: str = OK
    exp_cur: Optional[int] = None            # EXP into the current level
    exp_pct: Optional[float] = None          # % of the current level, 2 decimals
    level: Optional[int] = None
    map: Optional[str] = None
    map_confirmed: bool = False              # the source already checked this name twice: the
                                             # tracker takes a new map at once (else 2 readings)
    exp_needed: Optional[int] = None         # set only by sources that know it exactly
    job: Optional[str] = None
    hp_pct: Optional[float] = None           # HP bar fill 0-100 (0 = empty: dead, or under ~1%)
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

    def poll(self, t):
        """Quick look between readings (about once a second while playing). Returns True while
        the "You have died." box is on screen, False when it isn't, None if this source can't
        tell. Must be cheap; sources without live frames return None."""
        return None

    def quick_vitals(self, t):
        """A burst look at just the HP/MP numbers (regen clock, vitals.Meter); adds a 'vitals'
        fact. False when this source can't (default)."""
        return False

    def take_facts(self):
        """Long-lived character facts seen since the last call: [(t, kind, value)], kind 'stat' |
        'skills' | 'channel' (dicts as panels.py returns them) or 'away' (the game left the
        screen: confirms a channel change). Consumed by character.Profile. Default: none."""
        return []

    def checks_per_s(self):
        """The quick-look rate the player chose (Settings "Screen checks per second"); the
        tracker's poll loop and perf.csv follow it. Sources without live frames: 1."""
        return 1.0

    def boost(self, fps, seconds):
        """Ask for a higher frame rate for the next `seconds` (the regen clock's bursts). Default: none."""

    def end_session(self):
        pass

    def close(self):
        pass


def describe(obs: Observation) -> Any:
    """Compact text for logs."""
    return f"{obs.exp_cur}[{obs.exp_pct}%] Lv {obs.level} @ {obs.map}"


# ---------------------------------------------------------------------------------------- events
# The tracker process emits these on hub.emit(name, **payload) (hub/bus.py); components subscribe with
# hub.on(name, fn). A handler is called fn(**payload): name the keys you use and end with **_ so a key
# added later doesn't break it. Handlers run in registration order (components/__init__.py) on the
# tracker's thread, so keep them short; an exception is logged and never stops the tracker.
# "state" = hub.state (read-only view of the tracker: level, cur, need, map, job, active_total,
# in_session, playing).
EVENTS = (
    # -- service
    ("service_start", "maps: [names seen before]. The service loop is about to run (lock held, log set up)"),
    ("service_stop", "-. The loop ended; stop threads"),
    ("loop", "-. Top of every service-loop pass, before the source is read (state.playing is current)"),
    ("poll", "now. Each quick look between readings while playing (about once a second)"),
    ("facts", "facts [(t, kind, value)], prof (character.Profile). Character facts the source saw; prof is updated"),
    # -- session
    ("session_start", "dir (Path), source (name). A play session's files exist"),
    ("observation", "obs. A reading is about to be processed (start of Session.process)"),
    ("progress", "level, cur, need. After a reading was processed"),
    ("moment", "level, map, job, active_total. After the events of a reading: a chance for a random-moment picture"),
    ("session_end", "reason. The session closed (source.end_session() done, before the summary is written)"),
    # -- game events, one per entry of Tracker.events (payload also carries job = the job now)
    ("levelup", "time, level_reached, active_seconds_in_prev_level, wall_seconds_in_prev_level, "
                "exp_needed_prev_level, map, job"),
    ("death_guess", "map, level, killer (a dict: subscribers fill it; killers sets killer/how/sure/suspects/why/label)."
                    " Asked once when a death is detected; the result is the death event's killer"),
    ("death", "level, map, lost, pct_lost, box, hp, killer (dict or None; killer['label'] = the name when sure), job"),
    ("job_change", "old, new, level, map, job"),
    ("map_enter", "map, level, job. A new map was entered (the first reading of the session counts)"),
    ("map_change", "to, from_stint ({map, start, active, exp}). Left a map after >= 30 s of play"),
    # -- from components, for other components
    ("exp_rise", "t, map, d (EXP gained since the last look), party (another member on your map), gap "
                 "(s since the last look). Every EXP rise on a quick look (skillbar's training feed)"),
    ("first_kill", "mob (album record), map, level, job, shots [relative paths], caught (album count), text "
                   "(the diary line). The album caught a mob for the first time and already saved it"),
)
EVENT_NAMES = tuple(n for n, _ in EVENTS)


class Component:
    """One feature. Subclass it in components/<name>/component.py and list it in components/__init__.py.

    attach(hub)  tracker process, once, before the service runs: subscribe with hub.on(), offer
                 services with hub.provide(); don't start threads or touch files yet (do that on
                 service_start). Never import another component: talk through events and services.
    ui(shell)    helper process (Phase 4; nothing calls it yet): register settings pages and windows.
    """
    name = ""
    settings_defaults = {}          # this component's slice of helper_settings.json (same keys as today)

    def attach(self, hub):
        pass

    def ui(self, shell):
        pass
