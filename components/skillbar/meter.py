"""meter.py - the HP / MP regen clock (Meter) and its settings (regen_settings, REGEN_DEFAULTS).

Core side, no pixels: fed (t, cur, max) looks from the 'vitals' facts (engine/vitals.py reads the
numbers from the status bar). Moved here from engine/vitals.py in Phase 4b so the engine only reads.
mpuse.py explains every other change (casts, potions, hits, ...).
"""
import math
import statistics
from collections import deque

REGEN_DEFAULTS = {"min_ticks": 1, "max_ticks": 6, "step": 2, "burst_fps": 10, "pad_s": 0.6}
_regen_cache = {"key": None, "val": dict(REGEN_DEFAULTS)}


def regen_settings(path=None):
    """helper_settings.json "regen_check" (stickiness of the regen clock's checks), cached by
    file time like mapwatch.settings."""
    import json
    import os
    if path is None:
        from base.paths import DATA
        path = DATA / "helper_settings.json"
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        return dict(REGEN_DEFAULTS)
    if key != _regen_cache["key"]:
        try:
            m = json.loads(open(path, encoding="utf-8").read()).get("regen_check", {})
        except (OSError, ValueError, AttributeError):
            m = {}
        v = {}
        for k, d in REGEN_DEFAULTS.items():
            try:
                v[k] = float(m.get(k, d))
            except (TypeError, ValueError):
                v[k] = float(d)
        v["min_ticks"] = max(1.0, round(v["min_ticks"]))
        v["max_ticks"] = max(v["min_ticks"], round(v["max_ticks"]))
        v["step"] = max(1.0, v["step"])
        v["burst_fps"] = min(30.0, max(2.0, v["burst_fps"]))
        v["pad_s"] = min(3.0, max(0.2, v["pad_s"]))
        _regen_cache.update(key=key, val=v)
    return dict(_regen_cache["val"])


class Meter:
    """One meter's regen clock: tick size, rhythm and exact timing, from (t, cur, max) looks.

    Measured 2026-10-24 (NOTES 20:55-21:12, dev/vitals_log.py at 8-10 looks/s): MP ticks every
    10.0 s (+-0.15 s) like a clock while you stay on one map, whatever you do (casts, hits,
    potions, walking). Only a map change (portal) moves it. There is no server-wide beat: each
    character has its own. So: catch one tick precisely (a burst of looks), predict the rest
    (anchor + k x period), and check now and then whether the clock still holds. The checks
    spread out while they land on time ("stickiness", like mapwatch.py): every tick at first,
    +1 tick per `step` on-time checks, up to every `max_ticks` ticks. `reset_clock()` (the screen
    went dark: portal or channel change) starts over with a burst. HP is assumed to work the same
    (user, 2026-10-24: verify with a warrior's Improved HP Recovery).

    The rhythm: the recovery skill's text when it states one (`prior`, "every 10 seconds"; user
    rule 2026-10-23: the skill says the rhythm, the screen only the timing), else the measured
    gap, else 10 s (Classic's regen beat). The tick size follows the max and what you do (+12
    standing, +9/+10 moving), so the last few sizes are kept and a rise near them is a tick.

    feed(t, cur, max, tick=None): tick = the size of the regen tick in (previous look, t] as found
    by a caller that explains every change (mpuse.Usage; 0 = none, e.g. a cast hid it);
    None = decide here (a rise of a tick's size is a tick).
    """

    MAX_TICK_FRAC = 0.15    # without known sizes, a rise up to 15% of max (or 30) may be a tick
    TICK_SLACK = 3          # a rise within this of a recent tick size is a tick
    ON_TIME_S = 0.35        # a tick this close to the clock (+ half the look's width) is on time
    PRECISE_S = 0.3         # a look window this narrow places the clock well enough
    MISSES_LOST = 2         # this many missed checks in a row: the clock is lost, find it again
    DEFAULT_PERIOD = 10.0
    MIN_PERIOD, MAX_PERIOD = 2.0, 60.0
    CLUSTER_S = 1.5
    MIN_CLUSTER = 3

    def __init__(self, prior=None, settings_fn=None):
        self.prior = prior
        self.settings = settings_fn or regen_settings
        self.prev = None                    # (t, cur, max)
        self.rises = deque(maxlen=40)       # (lo, hi, amount): ticks seen
        self.sizes = deque(maxlen=6)        # recent tick sizes
        self.known = []                     # sizes the character's numbers predict (regen.py),
                                            # most likely first: known before any tick is seen
        self.amount = None                  # size of the last tick
        self.period = None                  # measured usual gap (information)
        self.last = None                    # (lo, hi) of the last tick seen
        self.width = None
        # the clock
        self.anchor = None                  # a tick time on the clock's lattice
        self.anchor_w = None                # how wide the look window that placed it was
        self.ok = 0                         # on-time checks since the clock (re)started
        self.misses = 0                     # missed checks in a row
        self.next_check = None              # predicted tick the next check looks at
        self.checking = None                # (start, end, tick time) of the check under way
        self.restarts = 0                   # times the clock was found off and restarted
        self.checks = {"on_time": 0, "missed": 0}
        self.finding_since = None           # when the find-burst started (None = not yet)

    # -- clock ---------------------------------------------------------------------
    @property
    def per(self):
        return self.prior or self.period or self.DEFAULT_PERIOD

    def _lattice(self, t, up=True):
        """The clock's tick at or after t (up) / at or before t."""
        k = (t - self.anchor) / self.per
        k = math.ceil(k - 1e-9) if up else math.floor(k + 1e-9)
        return self.anchor + k * self.per

    def expected(self, lo, hi, tol=None):
        """The clock's tick inside (lo - tol, hi + tol], or None."""
        if self.anchor is None:
            return None
        if tol is None:
            tol = self.ON_TIME_S + (self.anchor_w or 0) / 2
        tp = self._lattice(lo - tol)
        return tp if tp <= hi + tol else None

    def is_tick_size(self, d, mx):
        if d <= 0:
            return False
        pool = list(self.sizes) + list(self.known)
        if pool:
            return min(pool) - self.TICK_SLACK <= d <= max(pool) + self.TICK_SLACK
        return d <= max(30, self.MAX_TICK_FRAC * (mx or 0))

    def check_every(self):
        s = self.settings()
        return int(min(s["max_ticks"], s["min_ticks"] + self.ok // s["step"]))

    def _schedule(self, after):
        self.next_check = self._lattice(after + 0.5) + (self.check_every() - 1) * self.per

    def reset_clock(self, t=None):
        """Map or channel change (the screen went dark at `t`): the clock starts over. Entering
        a map restarts the game's own regen timer: the first tick comes 10 s after the screen is
        back (measured 2026-10-25: 9.78-10.09 s, 3 of 3 portals, also after two portals 2 s
        apart). So the first look with numbers again places the clock (rough: between the last
        dark look and that look), and a short burst at +10 s makes it exact."""
        self.anchor = self.anchor_w = self.next_check = self.checking = None
        self.ok = self.misses = 0
        self.finding_since = None
        self.dark_t = t                     # the last dark look (None: unknown)

    def _tick(self, lo, hi, d):
        mid, w = (lo + hi) / 2, hi - lo
        self.rises.append((lo, hi, d))
        self.sizes.append(d)
        self.last, self.amount, self.width = (lo, hi), d, w
        self._fit()
        tp = self.expected(lo, hi)
        checked = False
        if tp is None:                      # first tick, or off the clock: (re)start it here
            if self.anchor is not None:
                self.restarts += 1
            self.anchor, self.anchor_w, self.ok = mid, w, 0
            checked = True
        else:
            if self.checking and abs(self.checking[2] - tp) < 0.5:
                self.ok += 1
                self.checks["on_time"] += 1
                checked = True
            if w <= self.anchor_w:          # a narrower look places it better
                self.anchor, self.anchor_w = mid, w
        self.misses = 0
        # a tick a normal look happened to see only confirms the clock; the next check stays put
        if checked or self.next_check is None or self.next_check < hi:
            self.checking = None
            self._schedule(hi)

    def _no_tick(self, lo, hi, full):
        """A look window without a tick. Closes a check whose window has passed."""
        if self.checking and hi >= self.checking[1]:
            self.checking = None
            if full:                        # at max nothing shows: not a miss
                self._schedule(hi)
                return
            self.ok = 0
            self.misses += 1
            self.checks["missed"] += 1
            if self.misses >= self.MISSES_LOST:
                self.anchor = self.anchor_w = self.next_check = None    # find it again
                self.finding_since = None
            else:
                self._schedule(hi)

    def wants_fast(self, now):
        """True while looks should come at burst rate: finding the clock, placing a rough one,
        or a check around a predicted tick. At max (nothing to see) never."""
        p = self.prev
        if p is None or p[1] >= p[2]:
            return False
        if self.anchor is None:
            # one regen period of dense looks; no tick in it (e.g. HP while fighting: no regen)
            # = stop, a normal look will catch the next tick roughly and a short burst refines it
            if self.finding_since is None:
                self.finding_since = now
            return now - self.finding_since <= self.per + self.settings()["pad_s"]
        pad = self.settings()["pad_s"]
        if self.anchor_w > self.PRECISE_S:  # rough: look densely around the next tick
            half = pad + self.anchor_w / 2
            return self._lattice(now - half) - half <= now
        if self.next_check is None:
            self._schedule(now)
        if self.checking is None and self.next_check - pad <= now:
            if now > self.next_check + pad:             # overslept it: take the next tick
                self.next_check = self._lattice(now + pad)
                return False
            self.checking = (self.next_check - pad, self.next_check + pad, self.next_check)
        return self.checking is not None and now <= self.checking[1] + 0.2

    def burst_in(self, now):
        """Seconds until looks should come at burst rate (0 = now, None = nothing planned)."""
        if self.wants_fast(now):
            return 0.0
        p = self.prev
        if self.anchor is None or p is None or p[1] >= p[2]:
            return None
        pad = self.settings()["pad_s"]
        if self.anchor_w > self.PRECISE_S:
            return max(0.0, self._lattice(now) - pad - self.anchor_w / 2 - now)
        return max(0.0, (self.next_check or now) - pad - now)

    # -- looks ---------------------------------------------------------------------
    def feed(self, t, cur, mx, tick=None):
        p, self.prev = self.prev, (t, cur, mx)
        dark, self.dark_t = getattr(self, "dark_t", None), None
        if dark is not None and self.anchor is None and 0 < t - dark <= 3.0:
            # the first numbers after a map change: the screen came back in (dark, t], the
            # game's timer started there, the first tick is 10 s later
            self.anchor = (dark + t) / 2      # a lattice point: ticks at +10, +20 ...
            self.anchor_w = max(0.1, t - dark)
            self.entered = round(t, 2)
        if p is None:
            return
        if mx != p[2]:                      # max changed (level-up, gear): sizes change, the
            self.amount = None              # rhythm doesn't; the refill isn't a tick
            self.sizes.clear()
            return
        lo, hi = p[0], t
        if hi - lo > 3.0:                   # a gap: nothing can be placed
            return
        d = cur - p[1]
        if tick is None:
            tick = d if self.is_tick_size(d, mx) else 0
            # a tick-sized rise right after the last tick (well inside the gap) isn't the next
            if tick and self.last and lo - self.last[1] < 0.6 * self.per:
                tick = 0
        if tick:
            self._tick(lo, hi, tick)
        else:
            self._no_tick(lo, hi, cur >= mx)

    def _fit(self):
        mids = [(a + b) / 2 for a, b, _ in self.rises]
        gaps = sorted(g for g in (y - x for x, y in zip(mids, mids[1:]))
                      if self.MIN_PERIOD <= g <= self.MAX_PERIOD)
        best = None
        for g in gaps:
            near = [h for h in gaps if abs(h - g) <= self.CLUSTER_S]
            if len(near) >= self.MIN_CLUSTER and (best is None or len(near) > len(best)):
                best = near
        if best:
            self.period = round(statistics.median(best) * 2) / 2

    def next_tick(self, now):
        if self.anchor is None:
            return None
        return self._lattice(now)

    def state(self, now):
        p = self.prev
        nxt = self.next_tick(now)
        return {
            "cur": p[1] if p else None, "max": p[2] if p else None,
            # the size to show: the last tick when it's one the numbers predict (moving +10 vs
            # sitting +13), else the prediction (a tick cut off at max shows +28 of +50)
            "tick": (self.amount if (self.amount and (not self.known or self.amount in self.known))
                     else (self.known[0] if self.known else self.amount)),
            "tick_expected": list(self.known),
            "period": self.per if self.anchor is not None else (self.prior or self.period),
            "from_skill": self.prior is not None,
            "measured_gap": self.period,           # for information; never replaces the skill
            "next": round(nxt, 2) if nxt else None,
            "late": bool(self.misses),             # the last check found no tick
            "sure_s": round(self.anchor_w, 2) if self.anchor_w is not None else None,
            "ticks_seen": len(self.rises),
            "clock": ("finding" if self.anchor is None else
                      "rough" if self.anchor_w > self.PRECISE_S else "locked"),
            "check_every": self.check_every() if self.anchor is not None else None,
            "checks": dict(self.checks), "restarts": self.restarts,
        }
