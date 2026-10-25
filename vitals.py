"""vitals.py - exact HP / MP from the status bar, and how they regenerate.

Reading (screen side). The status bar shows `HP[408/408]` and `MP[606/606]` in a crisp white
pixel font with green brackets, like the EXP bar but narrower digits (own templates:
glyphs_hud.json, learned locally). Only the glyphs between the brackets are read. Unknown digits
are taught from the Character Stat window, which shows the same current/max values in the same
frame (screen.py calls try_learn). Measured 2026-10-23 at 1920x1080: HP text x 790..880, MP text
x 882..998, y 1046..1060; the status bar moves with the window size ("bottom-center").

Regen model (core side, no pixels). Classic regenerates in ticks (Improved MP Recovery: "1% of
Max MP every 10 seconds", skills_classic.json), on a per-character 10 s clock that only a map
change moves. `Meter` catches one tick with a burst of looks, predicts the rest and checks now and
then, less often while the checks land on time (see Meter). mpuse.py explains every other change
(casts, potions, hits, ...).

  python vitals.py FRAME [FRAME...]      read HP/MP of saved frames (dev check)
"""
import math
import statistics
import sys
from collections import deque

import glyphs
from layout import place

TEMPLATES = "glyphs_hud.json"
BOXES = {"hp": (790, 1046, 880, 1060), "mp": (882, 1046, 998, 1060)}
REF = (1920, 1080)
# the bars' fill (interior rows with the strongest colour; hpbar.py measured the red one):
# a rough value that settles unknown digits when only one digit fits ("3?6/606" at 52% -> 316)
BARS = {"hp": (780, 1066, 884, 1070), "mp": (888, 1066, 992, 1070)}
STRIP = (780, 1046, 998, 1071)          # text + bars, for a frame provider that grabs just this part
FILL_TOL = 0.025                        # bar fill vs value: about 2.5 px of the 105 px bar


# ------------------------------------------------------------------ reading
def strip_box(img_size, ref=REF):
    """Where the HP+MP text strip is in a frame of this size (for WgcFrames grab_parts)."""
    class _S:
        size = img_size
    return place(_S(), list(STRIP), ref, "bottom-center", "vitals_strip")


def _keys(crop):
    """Glyph keys between the first two green brackets ('[' ... ']')."""
    seg = glyphs.segment(crop)
    greens = [i for i, (k, _) in enumerate(seg) if k == "green"]
    if len(greens) < 2:
        return None
    return [v for k, v in seg[greens[0] + 1: greens[1]] if k == "glyph"]


def _crop(img, name, ref=REF, origin=None):
    """Crop `name`'s text from a full frame, or from a strip grabbed at `origin` (x, y)."""
    return _crop_box(img, BOXES[name], name + "_text", ref, origin)


def _crop_box(img, box, name, ref=REF, origin=None):
    if origin is None:
        return img.crop(tuple(place(img, list(box), ref, "bottom-center", name)))
    sx, sy = STRIP[0], STRIP[1]
    x0, y0, x1, y1 = box
    return img.crop((x0 - sx, y0 - sy, x1 - sx, y1 - sy))


def fill(img, name, ref=REF, origin=None):
    """How full the HP (red) / MP (blue) bar is, 0-1; None if the bar isn't there."""
    import numpy as np
    a = np.asarray(_crop_box(img, BARS[name], name + "_bar", ref, origin).convert("RGB"),
                   dtype=np.int16)
    if not a.size:
        return None
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    if name == "hp":
        on = (r > 150) & (g < 70) & (b < 50)
    else:
        on = (b > 180) & (r < 80) & (g > 80) & (g < 190)
    grey = (abs(r - g) < 20) & (abs(g - b) < 20) & (r > 120)
    col_on = on.mean(0) > 0.5
    if (col_on | (grey.mean(0) > 0.5)).mean() < 0.9:
        return None                         # something over the bar
    cols = np.nonzero(col_on)[0]
    return (int(cols[-1]) + 1 if cols.size else 0) / len(col_on)


def _solve(text, frac):
    """Fill the '?' in 'cur/max' so cur/max matches the bar. -> all candidate texts."""
    import itertools
    q = [i for i, ch in enumerate(text) if ch == "?"]
    if not q or len(q) > 3 or frac is None:
        return []
    out = []
    for combo in itertools.product("0123456789", repeat=len(q)):
        t = list(text)
        for i, d in zip(q, combo):
            t[i] = d
        t = "".join(t)
        v = parse(t)
        if v and not any(part.startswith("0") and len(part) > 1 for part in t.split("/")) \
                and abs(v[0] / v[1] - frac) <= FILL_TOL:
            out.append(t)
    return out


def _trim(key):
    """A glyph key without its empty top/bottom rows (the HUD crop sits 2 rows lower than EXP's)."""
    rows = key.split("/")
    while rows and "#" not in rows[0]:
        rows.pop(0)
    while rows and "#" not in rows[-1]:
        rows.pop()
    return "/".join(rows)


def _is_slash(key):
    """'/' by shape: one thin diagonal from top-right to bottom-left (2 px per column step)."""
    rows = _trim(key).split("/")
    if len(rows) < 5 or len(rows[0]) > 4:
        return False
    cols = []
    for r in rows:
        on = [i for i, c in enumerate(r) if c == "#"]
        if not on or on[-1] - on[0] > 1:
            return False
        cols.append(on[0])
    return (cols[0] == len(rows[0]) - 1 and cols[-1] == 0
            and all(b <= a for a, b in zip(cols, cols[1:])))


def _guess(keys):
    """Unknown HUD glyphs from what a fresh install already knows (2026-10-24): the HP/MP digits
    are the same shapes as the EXP bar's (glyphs.json, learned within minutes of play), just
    cropped 2 rows lower; '/' is recognised by its shape. -> {key: char}"""
    exp = {_trim(k): c for k, c in glyphs.load().items() if c.isdigit()}
    out = {}
    for k in keys:
        c = exp.get(_trim(k))
        if c is None and _is_slash(k):
            c = "/"
        if c is not None:
            out[k] = c
    return out


def read_text(img, name, ref=REF, origin=None):
    """'408/408', with '?' for unknown glyphs; None if the bracketed text isn't there.
    Unknown glyphs that match an EXP digit (or the '/' shape) are learned when the whole
    value then reads as a valid cur/max."""
    keys = _keys(_crop(img, name, ref, origin))
    if keys is None:
        return None
    t = glyphs.load(TEMPLATES)
    text = "".join(t.get(k, "?") for k in keys)
    if "?" in text:
        g = _guess([k for k in keys if k not in t])
        filled = "".join(t.get(k) or g.get(k, "?") for k in keys)
        if parse(filled):
            t.update(g)
            glyphs.save(t, TEMPLATES)
            text = filled
    return text


def parse(text):
    """'408/408' -> [408, 408] (current <= max), else None."""
    if not text or "?" in text or text.count("/") != 1:
        return None
    a, b = text.split("/")
    if not (a.isdigit() and b.isdigit()) or int(a) > int(b) or int(b) == 0:
        return None
    return [int(a), int(b)]


def read(img, ref=REF, origin=None, learn=True):
    """{'hp': [cur, max] | None, 'mp': ..., 'raw': {'hp': text, 'mp': text}, 'learned': {...}}.
    Unknown digits: the bar's fill picks the value; a glyph is learned only when every value
    that fits the bar gives it the same digit, and the reading is used only when one value fits."""
    raw = {n: read_text(img, n, ref, origin) for n in BOXES}
    out = {"raw": raw, "learned": {}}
    for n in BOXES:
        text = raw[n]
        if text and "?" in text:
            frac = fill(img, n, ref, origin)
            cands = _solve(text, frac)
            # learn only from a clear case: one value fits, and the bar isn't at an end (it lags
            # behind the text when it refills at a level-up, and a sliver can measure as empty)
            if len(cands) == 1 and learn and 0.03 < frac < 0.97:
                keys = _keys(_crop(img, n, ref, origin))
                agreed = {keys[i]: cands[0][i] for i, ch in enumerate(text) if ch == "?"}
                if agreed:
                    t = glyphs.load(TEMPLATES)
                    new = {k: c for k, c in agreed.items() if k not in t}
                    if new:
                        t.update(new)
                        glyphs.save(t, TEMPLATES)
                        out["learned"].update({c: k for k, c in new.items()})
            if len(cands) == 1:
                text = cands[0]
        out[n] = parse(text)
    return out


def try_learn(img, name, value, ref=REF, origin=None):
    """Teach the digits of `name` from a known [cur, max] (the Stat window, same frame).
    Only when every already-known glyph agrees. Returns the newly learned characters."""
    keys = _keys(_crop(img, name, ref, origin))
    text = f"{value[0]}/{value[1]}"
    if keys is None or len(keys) != len(text):
        return {}
    t = glyphs.load(TEMPLATES)
    if any(k in t and t[k] != c for k, c in zip(keys, text)):
        return {}
    new = {k: c for k, c in zip(keys, text) if k not in t}
    if new:
        t.update(new)
        glyphs.save(t, TEMPLATES)
    return {c: k for k, c in new.items()}


# ------------------------------------------------------------------ regen model
REGEN_DEFAULTS = {"min_ticks": 1, "max_ticks": 6, "step": 2, "burst_fps": 10, "pad_s": 0.6}
_regen_cache = {"key": None, "val": dict(REGEN_DEFAULTS)}


def regen_settings(path=None):
    """helper_settings.json "regen_check" (stickiness of the regen clock's checks), cached by
    file time like mapwatch.settings."""
    import json
    import os
    if path is None:
        from paths import DATA
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


if __name__ == "__main__":
    from PIL import Image
    for p in sys.argv[1:]:
        r = read(Image.open(p).convert("RGB"))
        print(p, r["raw"], r["hp"], r["mp"])
