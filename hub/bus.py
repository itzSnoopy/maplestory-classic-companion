"""bus.py - the in-process hub the tracker talks to and components plug into (tracker process only).

The tracker emits events (names and payloads: base/contract.py EVENTS); components subscribe in
Component.attach(). The helper process never sees this: it still reads status/live/digest.json.

  hub.on(event, fn) / hub.emit(event, **data)   events; each subscriber is timed as perf "bus.<event>",
                                                an exception in one is logged and the rest still run
  hub.publish(section, value) / hub.published() a section of live.json: a value, or fn(now, prof) called at
                                                write time (hub/live.py composes the file)
  hub.feed_facts(facts, prof)                   the tracker's facts: facts_begin, one "fact" event each,
                                                the live.json write, then "facts" (base/contract.py EVENTS)
  hub.settings(name)                            a component's slice of helper_settings.json (read-only)
  hub.provide(name, fn) / hub.ask(name)         pull-style data one component offers another (no imports)
  hub.state                                     read-only view of the tracker (level, map, job, ...)
  hub.wake()                                    "read the game right away" (the wait loop checks it)
  hub.snapshot(...) / hub.album_shot(...)       pictures from the current source, paths relative to root
  hub.tell_source(**attrs)                      set an optional flag on the source (e.g. album_watch)
  hub.journal(text) / hub.log(text)             journal.md line / tracker_live.log line
  hub.on_idle(fn) / hub.idle(until)             work to do between quick looks (the regen clock's bursts)

The tracker fills hub.source, hub.take_facts, hub.journal_write and hub.state while the service runs.
"""
import time
import traceback
from pathlib import Path

from base import perf
from hub import live


class State:
    """What a component may read about the tracker, live (bound to the current session's Tracker)."""

    def __init__(self):
        self._tr = None
        self.playing = False        # the game is on screen (set at the top of every loop pass)

    def bind(self, tracker):
        self._tr = tracker          # None when no session is open

    @property
    def in_session(self):
        return self._tr is not None

    level = property(lambda s: s._tr.level if s._tr else None)
    cur = property(lambda s: s._tr.cur if s._tr else None)
    need = property(lambda s: s._tr.need if s._tr else None)
    map = property(lambda s: s._tr.map if s._tr else None)
    job = property(lambda s: s._tr.job if s._tr else None)
    active_total = property(lambda s: s._tr.active_total if s._tr else 0.0)
    exp_total = property(lambda s: s._tr.exp_total if s._tr else 0)      # EXP gained this session, across level-ups


class Hub:
    def __init__(self, root, log=print):
        self.root = Path(root)
        self.log = log
        self.state = State()
        self.components = []
        self.source = None                  # the Source the service reads (set by tracker.run)
        self.take_facts = lambda: None      # tracker: collect the source's character facts now
        self.journal_write = lambda text: None
        self._subs = {}
        self._names = {}
        self._live = {}
        self._services = {}
        self._idle = []
        self._wake = False
        self.live = live.LiveFile(self)

    # -- events
    def on(self, event, fn):
        self._subs.setdefault(event, []).append(fn)

    def emit(self, event, **data):
        subs = self._subs.get(event)
        if not subs:
            return
        name = self._names.get(event)
        if name is None:
            name = self._names[event] = f"bus.{event}"
        for fn in subs:
            try:
                with perf.timed(name):
                    fn(**data)
            except Exception:
                self.log(f"bus: {event} -> {getattr(fn, '__qualname__', fn)} failed:\n" + traceback.format_exc())

    def feed_facts(self, facts, prof, now=None):
        """Deal out a batch of the source's facts (prof already holds them). The order is what the
        components rely on: per-batch work, every fact in the order the source saw them (so the
        training visit's start sees the HP/MP looks that came before its map, not after), then
        live.json (written once a batch has a vitals look), then the whole batch."""
        now = now or time.time()
        self.emit("facts_begin", facts=facts, prof=prof, now=now)
        for t, kind, value in facts:
            self.emit("fact", t=t, kind=kind, value=value, prof=prof)
        self.live.after_facts(facts, prof, now)
        self.emit("facts", facts=facts, prof=prof)

    # -- live feed sections
    def publish(self, section, value):
        """A live.json section: a value, or fn(now, prof) evaluated at every write (hub/live.py)."""
        self._live[section] = value

    def published(self):
        return self._live

    # -- settings
    def settings(self, name):
        """This component's slice of helper_settings.json merged over the defaults (a copy)."""
        from hub import helper_settings
        return helper_settings.load().get(name, {})

    # -- services one component offers another
    def provide(self, name, fn):
        self._services[name] = fn

    def ask(self, name, default=None):
        fn = self._services.get(name)
        return fn() if fn is not None else default

    # -- wake the wait loop
    def wake(self):
        self._wake = True

    def take_wake(self):
        w, self._wake = self._wake, False
        return w

    # -- journal
    def journal(self, text):
        self.journal_write(text)

    # -- pictures
    @property
    def shots_dir(self):
        return self.root / "screenshots" / "diary"

    def _rel(self, paths):
        return [Path(p).relative_to(self.root).as_posix() for p in paths]

    def snapshot(self, out_dir, stem, delays=(0,)):
        """Source.snapshot(); returns the paths relative to root ([] when it failed)."""
        try:
            paths = self.source.snapshot(out_dir, stem, delays)
        except Exception as ex:
            self.log(f"diary snapshot failed: {ex}")
            paths = []
        return self._rel(paths)

    def album_shot(self, out_dir, stem, t=None):
        """The first-kill photo: Source.album_shot (screen: the middle square from just before t)."""
        try:
            paths = self.source.album_shot(out_dir, stem, t)
        except Exception as ex:
            self.log(f"album photo failed: {ex}")
            paths = []
        return self._rel(paths)

    def tell_source(self, **attrs):
        for k, v in attrs.items():
            setattr(self.source, k, v)

    # -- between quick looks
    def on_idle(self, fn):
        self._idle.append(fn)

    def idle(self, until):
        for fn in self._idle:
            fn(until)
