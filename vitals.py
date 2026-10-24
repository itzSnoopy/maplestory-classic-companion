"""vitals.py - exact HP / MP from the status bar, and how they regenerate.

Reading (screen side). The status bar shows `HP[408/408]` and `MP[606/606]` in a crisp white
pixel font with green brackets, like the EXP bar but narrower digits (own templates:
glyphs_hud.json, learned locally). Only the glyphs between the brackets are read. Unknown digits
are taught from the Character Stat window, which shows the same current/max values in the same
frame (screen.py calls try_learn). Measured 2026-10-23 at 1920x1080: HP text x 790..880, MP text
x 882..998, y 1046..1060; the status bar moves with the window size ("bottom-center").

Regen model (core side, no pixels). Classic regenerates in ticks (Improved MP Recovery: "1% of
Max MP every 10 seconds", skills_classic.json). From the numbers seen once a second, `Meter`
learns each meter's tick: its size (the most common small rise), its period (gaps between rises)
and its phase. A rise seen at look t means the tick happened in (previous look, t], so the
windows from many ticks, folded by the period, narrow the phase to well under a second.
Drops (skills, hits) and big rises (potions, level-up refill) are not ticks.

  python vitals.py FRAME [FRAME...]      read HP/MP of saved frames (dev check)
"""
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


def read_text(img, name, ref=REF, origin=None):
    """'408/408', with '?' for unknown glyphs; None if the bracketed text isn't there."""
    keys = _keys(_crop(img, name, ref, origin))
    if keys is None:
        return None
    t = glyphs.load(TEMPLATES)
    return "".join(t.get(k, "?") for k in keys)


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
class Meter:
    """Learns one meter's regen tick from (t, cur, max) looks: its usual gap and its size, and
    restarts the countdown at every tick it sees.

    Measured 2026-10-23 (dev/vitals_log.py): standing still, MP ticks every 10.0 s like a clock;
    while playing the same ticks come 9.8, 10.3, 9.8, 14.8, 12.3 s apart (what you do pushes the
    next one back). So the next tick is "last tick + usual gap", re-anchored at each tick, and the
    usual gap is the most common short gap between ticks (delayed and masked ticks only make gaps
    longer). When the skill states its rhythm (`prior`, "every 10 seconds") that rhythm is always
    used and the screen only sets the timing; the measured gap is kept for information (an earlier
    model measured "5 s", which a 10 s beat also fits). The tick size follows the max (1% of max MP + base) and what you do (+12 standing,
    +9 moving), so `amount` is the last tick's size; a max change (level-up) keeps the gap.
    """

    MAX_TICK_FRAC = 0.25    # a rise above 25% of max (or 30) is a potion or a refill
    MIN_PERIOD, MAX_PERIOD = 2.0, 60.0
    CLUSTER_S = 1.5         # gaps within this of each other are "the same gap" (looks are
                            # 0.5-1 s apart, so a 10 s gap is measured as 9-11 s)
    MIN_CLUSTER = 3         # this many similar gaps before the usual gap is trusted

    def __init__(self, prior=None):
        self.prior = prior                  # the rhythm the skill says ("every 10 seconds"):
                                            # always used when known (user, 2026-10-23); the
                                            # screen only places the ticks in time
        self.prev = None                    # (t, cur, max)
        self.rises = deque(maxlen=40)       # (t_prev, t, amount): small rises (tick candidates)
        self.amount = None                  # size of the last tick
        self.period = None                  # the usual gap between ticks (s)
        self.last = None                    # (lo, hi): the last tick happened in this window
        self.width = None

    def feed(self, t, cur, mx):
        p, self.prev = self.prev, (t, cur, mx)
        if p is None:
            return
        if mx != p[2]:                      # max changed (level-up, gear): sizes change, the
            self.amount = None              # rhythm doesn't; the refill isn't a tick
            return
        d = cur - p[1]
        if 0 < d <= max(30, self.MAX_TICK_FRAC * mx) and t - p[0] <= 3.0:
            # a rise right after the last tick (well inside the usual gap) isn't the next tick
            per = self.prior or self.period
            if per and self.last and p[0] - self.last[1] < 0.6 * per:
                return
            self.rises.append((p[0], t, d))
            self.last, self.amount = (p[0], t), d
            self.width = t - p[0]
            self._fit()

    def _fit(self):
        mids = [(a + b) / 2 for a, b, _ in self.rises]
        gaps = sorted(g for g in (y - x for x, y in zip(mids, mids[1:]))
                      if self.MIN_PERIOD <= g <= self.MAX_PERIOD)
        best = None                         # the gap most others agree with (ties: the shorter)
        for g in gaps:
            near = [h for h in gaps if abs(h - g) <= self.CLUSTER_S]
            if len(near) >= self.MIN_CLUSTER and (best is None or len(near) > len(best)):
                best = near
        if best:
            self.period = round(statistics.median(best) * 2) / 2

    def next_tick(self, now):
        per = self.prior or self.period
        if not per or not self.last:
            return None
        return (self.last[0] + self.last[1]) / 2 + per

    def state(self, now):
        p = self.prev
        nxt = self.next_tick(now)
        return {
            "cur": p[1] if p else None, "max": p[2] if p else None,
            "tick": self.amount, "period": self.prior or self.period,
            "from_skill": self.prior is not None,
            "measured_gap": self.period,           # for information; never replaces the skill
            "next": round(nxt, 2) if nxt else None,
            "late": bool(nxt and now > nxt + 1.0),     # a tick is overdue (pushed back by play)
            "sure_s": round(self.width, 2) if self.width is not None else None,
            "ticks_seen": len(self.rises),
        }


if __name__ == "__main__":
    from PIL import Image
    for p in sys.argv[1:]:
        r = read(Image.open(p).convert("RGB"))
        print(p, r["raw"], r["hp"], r["mp"])
