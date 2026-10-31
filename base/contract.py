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
    album_watch = False   # set by the album (hub.tell_source): a first kill is possible on this map,
                          # so keep what album_shot needs. Sources without pictures ignore it

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

    def album_shot(self, out_dir, stem, t=None):
        """The monster album's first-kill photo, for an EXP rise at time t. Returns the file paths
        that are or will be written. Default: a snapshot just after now."""
        return self.snapshot(out_dir, stem, (0.05,))

    def poll(self, t):
        """Quick look between readings (about once a second while playing). Returns True while
        the "You have died." box is on screen, False when it isn't, None if this source can't
        tell. Must be cheap; sources without live frames return None."""
        return None

    def quick_vitals(self, t):
        """A burst look at just the HP/MP numbers (the skill bar's regen clock); adds a 'vitals'
        fact. False when this source can't (default)."""
        return False

    def watch_buffs(self, ids):
        """Skill ids (strings) whose buff icon at the top right of the game window should be watched. The source
        then adds a 'buffs' fact {"on": [ids showing]} once at start and whenever the set changes (ids it has no
        icon for are never reported; calling again with the same ids is cheap). Default: no-op."""

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

    def party_name(self, i, mask, known, t):
        """Name of party box row i from its text mask (OCR), snapped to `known` names; None when it
        can't tell. The party component calls it for every 'party' fact. Default: None."""
        return None

    def party_reset(self):
        """The party box changed its row count: forget the row names read so far."""

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
# exp_total, in_session, playing).
EVENTS = (
    # -- service
    ("service_start", "maps: [names seen before]. The service loop is about to run (lock held, log set up)"),
    ("service_stop", "-. The loop ended; stop threads"),
    ("loop", "-. Top of every service-loop pass, before the source is read (state.playing is current)"),
    ("poll", "now. Each quick look between readings while playing (about once a second)"),
    ("facts_begin", "facts [(t, kind, value)], prof (character.Profile), now. A batch of facts is about to be dealt out "
                    "(prof already holds it): per-batch work (skillbar: another character starts over)"),
    ("fact", "t, kind, value, prof. Each fact of the batch in order: 'vitals' {hp, mp: [cur, max]}, 'away', 'map' "
             "(name), 'exp' {cur, pct}, 'party' (box rows), 'buffs' {on: [skill ids whose buff icon shows; only ids asked for with Source.watch_buffs]}, plus the Profile's who / stat / skills / channel. "
             "Components that share state through the facts see them interleaved exactly as the source made them"),
    ("facts", "facts [(t, kind, value)], prof (character.Profile). The whole batch, after every fact event and the "
              "live.json write; prof is updated"),
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
                 "(s since the last look). Every EXP rise on a quick look (the training component)"),
    ("first_kill", "mob (album record), map, level, job, shots [relative paths], caught (album count), text "
                   "(the diary line). The album caught a mob for the first time and already saved it"),
)
EVENT_NAMES = tuple(n for n, _ in EVENTS)

# Services: hub.provide(name, fn) / hub.ask(name, default): data one component offers another, no imports.
# fn takes no arguments (a service that needs one returns a function). Provided in service_start, in
# registry order, so a provider comes before the components that ask in the same event.
# training provides nothing (and emits no exp_rise) when skillbar's "usage" is missing: it has no HP/MP
# accounting to work from. Askers then get their default (no visit EXP/hr, no visit map, no prices).
SERVICES = (
    ("exp_rate", "training. EXP/hr summary of this map visit, or None outside a visit (the tracker's ETA)"),
    ("visit_map", "training. Name of the map visit under way, else the tracker's map (album)"),
    ("seen_places", "diary. Places already visited, to seed the album"),
    ("usage", "skillbar. The mpuse.Usage of the current character: every HP/MP change explained (training "
              "diffs its totals; replaced when another character plays, training starts over then)"),
    ("mp_price", "training. (potion name, mesos per MP point, MP per potion): what one MP costs you (skills list)"),
    ("price_of", "training. Returns fn(item name) -> mesos or None: the player's price, else the shop's"),
    ("party_on_map", "party. True while another party member is on your map (so EXP rises are shares)"),
)

# live.json (hub/live.py writes it about once a second while playing; the helper's overlays read it): t and
# character come from the hub, the rest are sections the components publish with hub.publish(name, fn(now,
# prof)): hp, mp, regen, skills, usage (skillbar), training (training), party (party).


class Component:
    """One feature. Subclass it in components/<name>/component.py and list it in components/__init__.py.

    attach(hub)  tracker process, once, before the service runs: subscribe with hub.on(), offer
                 services with hub.provide(); don't start threads or touch files yet (do that on
                 service_start). Never import another component: talk through events and services.
    ui(shell)    helper process: register menu items, settings pages, panels and pages (hub/ui/shell.py).
    """
    name = ""
    settings_defaults = {}          # this component's slice of helper_settings.json (same keys as today)

    def attach(self, hub):
        pass

    def ui(self, shell):
        pass
