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
import sys
from collections import Counter, deque

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
def _common_phase(windows, P):
    """Fold each (a, b] window to offsets in (-P, 0] before the last sighting and find the spot
    covered by the most of them. Returns (count, lo, hi, ref) of that best overlap, or None."""
    ref = windows[-1][1]
    ivs = []
    for a, b in windows:
        k = round((ref - b) / P)
        lo, hi = a + k * P - ref, b + k * P - ref
        if hi - lo >= P:
            continue                        # a window as long as the period says nothing
        for s in (-P, 0.0, P):              # the fold can wrap around: try the neighbours too
            ivs.append((lo + s, hi + s))
    best = None
    for lo0, _ in ivs:                      # the best overlap starts at some window's start
        x = lo0 + 1e-6
        inside = [(lo, hi) for lo, hi in ivs if lo < x <= hi]
        if not inside:
            continue
        lo, hi = max(i[0] for i in inside), min(i[1] for i in inside)
        if -P < (lo + hi) / 2 <= 0 and (best is None or len(inside) > best[0]):
            best = (len(inside), lo, hi, ref)
    return best


class Meter:
    """Learns one meter's regen tick (size, period, phase) from (t, cur, max) looks."""

    MAX_TICK_FRAC = 0.25    # a rise above 25% of max (or 30) is a potion or a refill; smaller
                            # ones count as ticks only on a regular beat (HP +50 of 408 is one)
    MIN_PERIOD, MAX_PERIOD = 2.0, 60.0

    def __init__(self):
        self.prev = None                    # (t, cur, max)
        self.rises = deque(maxlen=40)       # (t_prev, t, amount) for small rises
        self.amount = None
        self.period = None
        self.phase = None                   # a tick time (epoch s); ticks at phase + k*period
        self.width = None                   # how sure the phase is (s)
        self.full = deque(maxlen=200)       # look times at full (ticks can't show then)

    def feed(self, t, cur, mx):
        p, self.prev = self.prev, (t, cur, mx)
        if p is None or mx != p[2]:
            if p is not None:               # max changed (level-up, gear): start over
                self.rises.clear()
                self.amount = self.period = self.phase = None
            return
        if cur >= mx:
            self.full.append(t)
        d = cur - p[1]
        if 0 < d <= max(30, self.MAX_TICK_FRAC * mx) and t - p[0] <= 3.0:
            self.rises.append((p[0], t, d))
            self._fit()

    def _fit(self):
        amounts = Counter(d for _, _, d in self.rises)
        amt, n = amounts.most_common(1)[0]
        if n < 2:
            return
        ticks = [(a, b) for a, b, d in self.rises if d == amt][-15:]
        if len(ticks) < 4:              # 3 sightings can't tell 10.0 s from 10.5 s
            return
        # period: the longest one (half-second steps) on which the tick windows (previous look,
        # sighting] line up on one phase, ~1 in 10 strays allowed. A true period P also fits P/2,
        # P/3.. (shorter ones line up with almost anything), so the longest fit wins. Strays only
        # shorten gaps and masked ticks only lengthen them, so P is at most the longest gap.
        # (2026-10-23: capping by the *shortest* gap let one stray collapse 10 s to 2 s.)
        gaps = [b2 - b1 for (_, b1), (_, b2) in zip(ticks, ticks[1:])]
        n = len(ticks)
        need = n - (max(1, n // 4) if n >= 5 else 0)     # up to 1 in 4 strays (2x P fails half)
        best = None
        P = round(min(max(gaps) + 1.5, self.MAX_PERIOD) * 2) / 2
        while P >= self.MIN_PERIOD - 1e-9:
            fit = _common_phase(ticks, P)
            if fit and fit[0] >= need and self._coverage(ticks, P) >= 0.45:
                best = (P, fit)
                break
            P -= 0.5
        if best is None:
            return
        # refine: with few ticks a range of periods fits (10.0 and 10.5 both fit 3 sightings);
        # the sightings' own spacing, divided by the ticks in between, is closer
        P0 = best[0]
        ks = [round(g / P0) for g in gaps]
        span = sum(g for g, k in zip(gaps, ks) if k >= 1 and abs(g - k * P0) <= 1.5)
        cnt = sum(k for g, k in zip(gaps, ks) if k >= 1 and abs(g - k * P0) <= 1.5)
        if cnt:
            P1 = round(span / cnt * 2) / 2
            if P1 != P0 and self.MIN_PERIOD <= P1 <= self.MAX_PERIOD:
                fit = _common_phase(ticks, P1)
                if fit and fit[0] >= need:
                    best = (P1, fit)
        P, (_, lo, hi, ref) = best
        self.amount, self.period = amt, P
        self.phase, self.width = ref + (lo + hi) / 2, hi - lo

    def _coverage(self, ticks, P):
        """Seen ticks / ticks the period predicts, over the gaps between sightings (gaps that
        include time at full are skipped). A too-short period predicts ticks that never came."""
        seen = pred = 0
        for (_, b1), (_, b2) in zip(ticks, ticks[1:]):
            if any(b1 < f < b2 for f in self.full):
                continue
            seen += 1
            pred += max(1, round((b2 - b1) / P))
        return seen / pred if pred else 1.0

    def next_tick(self, now):
        if self.phase is None or not self.period:
            return None
        k = max(0, int((now - self.phase) // self.period) + 1)
        return self.phase + k * self.period

    def state(self, now):
        p = self.prev
        return {
            "cur": p[1] if p else None, "max": p[2] if p else None,
            "tick": self.amount, "period": self.period,
            "next": round(self.next_tick(now), 2) if self.phase is not None else None,
            "sure_s": round(self.width, 2) if self.width is not None else None,
            "ticks_seen": len(self.rises),
        }


if __name__ == "__main__":
    from PIL import Image
    for p in sys.argv[1:]:
        r = read(Image.open(p).convert("RGB"))
        print(p, r["raw"], r["hp"], r["mp"])
