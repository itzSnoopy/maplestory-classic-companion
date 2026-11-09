"""msgcorner.py - the gain messages in the bottom-right corner of the game window.

`You received EXP (+755)` then (in a party) `Bonus EXP for PARTY (+227)`, `You have gained mesos (+3)`:
each line is read exactly once and emitted as a fact

    (t, "exp_gain",  {"base": 755, "party_bonus": 227, "overflow": False, "own": True})
        # party_bonus 0 without a bonus line; own = the EXP line's first look was WHITE (your own kill),
        # yellow = a teammate's share (bonus lines are always yellow, only the EXP line decides)
    (t, "meso_gain", {"mesos": 3})
    (t, "item_gain", {"name": ..., "count": ...})         # format NOT KNOWN yet: ITEM_PATTERNS is empty

A line that matches no known format is dropped (stats["unknown"]), never counted.

How (measured on 1920x1080, see NOTES.md 2026-10-31):
- Lines are right-aligned (the ")" ends at x=1912), 14 px apart, newest at the bottom; slot k from the bottom
  has its text rows at y=918-14k..928-14k. A new line pushes the older ones up 14 px per line at once.
  A line fades for ~3.5 s (mean G 250 -> ~110, white turns yellow) and is gone. Identical lines repeat,
  so text alone can't tell a repeat from a continuation: the stack is TRACKED, frame to frame
  (`_best_shift`): how many new lines n were added at the bottom, chosen by matching shapes, left edges
  and brightness (a line can only get dimmer; a bright line can't vanish; a new line is bright).
- Only NEW lines are read. The font is a crisp 1 px pixel font with tabular digits (5 px wide, pitch 7), so
  the number is read by cell from the right edge against digit templates that the module LEARNS AT RUN
  TIME (Tesseract, two agreeing reads, bootstraps them; Nexon glyphs are never shipped or stored).
  The label (`You received EXP` ...) is matched by pixel pattern against learned label templates the same way.
- Cheap: an empty corner costs one 8-column strip test; frames with lines ~1-2 ms; Tesseract only for
  patterns not learned yet.

Usage: MsgCorner().feed(t, image, cropped=False) -> [(t, kind, value)].
Check: python -m engine.msgcorner test DIR   (replays a burst-fixture folder against its vitals.csv).
"""
import copy
import re
import sys
from difflib import SequenceMatcher

import numpy as np

from base import perf

# ---- geometry (1920x1080 pixels; layout.place moves it for other window sizes) ----------------
REF = (1920, 1080)
BOX = (1600, 790, 1920, 932)         # corner box, game pixels; anchored to the bottom-right
ANCHOR = "bottom-right"
RIGHT = 312                           # crop column of the ")" (x=1912)
SLOT0 = 127                           # crop row where slot 0's 13 rows start (text y=918 is row 128)
PITCH = 14
SLOTS = 10                            # lines tracked (6 seen stacked)
H = 13
DIGIT_X0 = 304                        # crop column of the last digit cell (5 wide), pitch 7 to the left
CELL_W, CELL_PITCH, CELL_H = 5, 7, 13

# ---- tracking numbers ------------------------------------------------------------------------
MIN_PIXELS = 100                      # colour-mask pixels for a line to count as "present" (full line ~300)
MAX_PIXELS = 900
B_NEW = 165                           # a new line is at least this bright (measured 180-250 on its first look)
B_STRONG = 200                        # clearly fresh: a line this bright can't vanish, can't appear unexplained
NEW_COST = 0.7                        # cost of one more new line (an old, dimmed line also looks "bright enough")
PEAK_TOL_UP = 11                      # ... but lines above the bottom one: the picture behind them changes (clouds)
PEAK_TOL = 11                         # a continuing line may not get brighter (98th percentile of G) than this (JPEG noise ~3)
NOFADE_COST = 1.0                     # ... but a bright line that did not fade at all is a little more likely a NEW identical one
PUSH_SLACK = 6                        # new lines may push a bright line off the top only if they are about as fresh as it
VISIBLE = 6                           # slots that can hold a line (= MAX_LINES); slots 6-9 only exist as geometry
MAX_LINES = 6                         # the game shows at most 6 lines; older ones are pushed out at once (more
                                      # than 6 kills in one tick are partly never shown)
B_GONE = 160                          # below this a line may vanish
OWN_RATIO = 0.73                      # first-look blue/red of the brightest 30% of the line: white 0.87-0.96, yellow 0.54-0.59
B_READ = 150                          # still readable
GAP_S = 5.0                           # no look for this long: every old line is gone
ROPE_MAXW = 4                         # a rope is at most this many columns wide (as seen by the mask)
B_YOUNG = 120                         # ... if it was this bright (a first look can be dim)
YOUNG_S, YOUNG_GONE = 0.6, 6.0         # a line younger than this that was seen last look cannot vanish (cost)
HELD_GONE = 0.3                       # cost of a held line staying unseen
HOLD_LOOKS = 3                        # a bright line missing for up to this many looks in a row is held, not forgotten
KEEP_S = 6.0                          # a tracked line older than this can't still be on screen
LEFT_TOL = 3
DIM_RATE, DIM_SLACK = 80.0, 25.0        # fastest believable dimming of a line (peak G per second) + noise
FRESH_PEAK, FADE_TOL = 250, 3          # a line dimmer than this is fading; a fading line does not get brighter than this
LEFT_FAR = 70                          # left edges further apart than this are different lines
CONTAIN = 0.70
B_SHAPE = 140                         # both looks at least this bright: compare exact shapes
SIM_DARK, SIM_DARK_DIM, SIM_SAME, SIM_DOUBT = 0.45, 0.65, 0.85, 0.75   # outline-in-the-picture test of 'same line' (see _sim)
EXTRA_DARK = 0.6                      # a shadow pixel is darker than this part of the ink / outline split
READ_THR, READ_THR_LO = 0.75, 0.62                       # ink / outline split of a glyph against the picture, as a part of the line's brightness
CELL_FLICKER = 4                      # px of flicker allowed per digit cell (busy backgrounds); different digits differ >= 5
NUM_X, NUM_ABS = 290, 5               # the number part of a line (digit cells, "+", parentheses) must agree almost exactly
SHAPE_ABS, SHAPE_REL = 10, 0.15       # allowed differing pixels (JPEG noise) for the same line

GAP_COLS = 9                          # wider ink-free gap = not part of the line
OUT_DARK, OUT_DROP = 90, 80           # outline test (see _outlined)

LABELS = {"exp": "You received EXP", "bonus": "Bonus EXP for PARTY", "meso": "You have gained mesos"}
LABEL_FUZZ = 0.78
LABEL_W_TOL = 8                       # label width may differ this much from the learned one (cut-off letters)

# Item pickups: `Squishy Liquid x1 earned. (Etc)` (seen on burst/20261030_222236, frames 0063.1-0066.1): name, "x<count>",
# "earned.", "(<tab>)"; proportional text with no "(+N)" tail, so the digit-cell reader does not apply and the line is
# read by Tesseract (budget-limited, crop-hash cached, two agreeing reads). Patterns: (regex on the OCR text of the
# whole line, name_group, count_group_or_None, tab_group_or_None). Only this one format is confirmed; any other
# line that is not an EXP/meso line comes out as a `msg_other` fact with its OCR text, so new formats can be found.
# `Snail's Closeness has increased (+1)` is NOT an item (pet closeness; it has a "(+N)" tail and a label not learned).
ITEM_PATTERNS = [(r"(.+?)\s*[xX]\s*(\d+)\s+earned\.?\s*\(([A-Za-z]+)\)", 1, 2, 3)]


def box_for(size):
    """Corner box in this frame's pixels (layout.place; bottom-right anchor)."""
    from engine.layout import place

    class _S:
        pass
    _S.size = tuple(size)
    return place(_S(), list(BOX), REF, ANCHOR)


def _colour_mask(a):
    """Pale yellow / white text: r>=100, g close to r (brown/tan roots have g<.88r), not green, not blue-ish."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (r >= 100) & (g * 100 >= 88 * r) & (g * 100 <= 118 * r) & (b * 100 <= 115 * g)


def _outlined(a):
    """Text has a dark 1 px outline: keep only pixels whose 3x3 neighbourhood holds a dark pixel (G <= 90) at least 80
    below them. Pale rock / clouds match the colour mask in big areas but have no such edge (NOTES 2026-11-08)."""
    g = a[..., 1]
    p = np.pad(g, 1, mode="edge")
    lo = g.copy()
    for dy, dx in ((1, 2), (2, 1), (2, 2)):          # the outline is a drop shadow: below / right / diagonal of the ink
        lo = np.minimum(lo, p[dy:dy + g.shape[0], dx:dx + g.shape[1]])
    return (lo <= OUT_DARK) & (g - lo >= OUT_DROP)


def _thin(m):
    """Strokes are 1 px, so ink never holds a 2x2 block: drop every mask pixel that sits in one (bright rock
    ridges, cloud edges and other wide bright shapes)."""
    h, w = m.shape
    blk = m[:-1, :-1] & m[1:, :-1] & m[:-1, 1:] & m[1:, 1:]
    bad = np.zeros_like(m)
    bad[:-1, :-1] |= blk
    bad[1:, :-1] |= blk
    bad[:-1, 1:] |= blk
    bad[1:, 1:] |= blk
    return m & ~bad


def _unrope(m):
    """Climbing ropes (thin bright vertical strands) cross the text and pass the colour/outline tests. Text never has ink in the
    free row between two lines (the 14th row of the pitch), a rope runs through it: a column with ink in 3 or more of those rows
    (of the rows beside the 6 line slots) is a rope and is cleared (+-1) in every slot. Wide runs of such columns are stone
    texture or a bright wall, not a rope (and the ")" columns at the right edge are never taken for one): they stay."""
    rows = [SLOT0 + H - PITCH * j for j in range(VISIBLE) if 0 <= SLOT0 + H - PITCH * j < m.shape[0]]
    cnt = m[rows].sum(0)
    cols = cnt >= 3
    cols[RIGHT - 4:] = False
    if cols.any():
        d = np.diff(np.concatenate(([0], cols.astype(np.int8), [0])))
        for st, en in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            if en - st > ROPE_MAXW:
                cols[st:en] = False
        cols = cols | np.roll(cols, 1) | np.roll(cols, -1)
    if cols.any():
        m = m & ~cols[None, :]
    return m, cols


def _dilate(m):
    p = np.pad(m, 1)
    d = np.zeros_like(m)
    for dy in range(3):
        for dx in range(3):
            d |= p[dy:dy + m.shape[0], dx:dx + m.shape[1]]
    return d


class _Slot:
    __slots__ = ("present", "left", "bright", "mask", "dil", "rows", "bits", "peak", "num", "rope", "fr", "held", "numlo", "kind", "line")

    def __init__(self):
        self.present = False
        self.left = 0
        self.bright = 0.0
        self.mask = self.dil = self.bits = None
        self.peak = 0.0
        self.rows = 0
        self.rope = None
        self.held = 0               # looks in a row this slot was not seen (a bright line cannot vanish: it is kept in mind)
        self.num = False            # the (+N) number read by templates from the picture: False = not looked at yet, None = none
        self.numlo = False
        self.line = None            # the tracked _Line this slot was matched to (its read number is known even when this look's picture is not)
        self.kind = False           # label kind by templates against the picture (False = not looked at)
        self.fr = None              # the frame this slot came from (shared dict: g, padded g)


class _Line:
    """One message line being tracked (follows its text up the stack)."""
    __slots__ = ("t", "frames", "state", "kind", "num", "votes", "emitted", "slot", "last_bright", "t_last", "tries", "overflow", "own", "item", "tvotes", "unseen")

    def __init__(self, t, state):
        self.t, self.t_last, self.frames = t, t, 1
        self.state = state          # pending | read | unknown | ghost | failed
        self.kind = self.num = None
        self.votes = {}
        self.tvotes = {}            # whole-line text reads (item / other lines), apart from the (kind, number) votes
        self.emitted = False
        self.unseen = False         # held in mind this look (not visible): nothing to read
        self.slot = None
        self.last_bright = 0.0
        self.tries = 0
        self.overflow = False       # born in a frame whose whole stack was new: more lines may have been missed
        self.item = None            # (name, count, tab) of an item line, or the OCR text of an unrecognised one
        self.own = None             # colour of the first look: True = white (your own kill), False = yellow (a teammate's)


class MsgCorner:
    def __init__(self, store=None):
        """store: path of the JSON file the learned digit/label templates are kept in (the player's data
        folder, never shipped); None = in memory only (tests)."""
        self.store = store
        self._dirty = False
        self.prev = [_Slot() for _ in range(SLOTS)]       # last frame's slots (bottom first)
        self.lines = [None] * SLOTS                       # tracked _Line per slot
        self.t_prev = None
        self.digits = {}                                  # char -> [bool arrays 13x5] (learned)
        self.labels = []                                  # [(kind, bool array 13xw)] (learned)
        self.label_seen = {}                              # kind -> times matched
        self.stats = {"frames": 0, "idle": 0, "new_lines": 0, "tess_calls": 0, "unknown": 0, "unread": 0,
                      "ghost": 0, "resync": 0, "orphan_bonus": 0, "conflict": 0, "learned_digits": 0,
                      "template_reads": 0, "tess_reads": 0, "items": 0, "other_text": 0}
        self._rings = {}
        self._dt = 0.3
        self._stack_key, self._stack_data, self._stack_ink = None, None, []
        self._gfull, self._y1 = None, 0
        self.text_cache = {}                              # line pixels -> OCR text (item / other lines)
        self.unknown_lines = []                           # [(t, why)] for the debug report
        self.out = []
        self._load()

    # ================================================================ learned templates on disk
    def _load(self):
        if not self.store:
            return
        try:
            import json
            from pathlib import Path
            d = json.loads(Path(self.store).read_text(encoding="utf-8"))
            if d.get("v") != 1:
                return
            dec = lambda rows: np.array([[c == "1" for c in r] for r in rows], dtype=bool)
            self.digits = {ch: [dec(p) for p in pats] for ch, pats in d.get("digits", {}).items()}
            self.labels = [(k, dec(rows)) for k, rows in d.get("labels", [])]
        except (OSError, ValueError, TypeError, AttributeError):
            self.digits, self.labels = {}, []

    def _save(self):
        if not self.store:
            return
        try:
            import json
            import os
            from pathlib import Path
            enc = lambda a: ["".join("1" if c else "0" for c in row) for row in a]
            d = {"v": 1, "note": "learned at run time from the player's own screen; local only, never ship or commit",
                 "digits": {ch: [enc(p) for p in pats] for ch, pats in self.digits.items()},
                 "labels": [[k, enc(b)] for k, b in self.labels]}
            tmp = str(self.store) + ".tmp"
            Path(tmp).parent.mkdir(parents=True, exist_ok=True)
            Path(tmp).write_text(json.dumps(d), encoding="utf-8")
            os.replace(tmp, self.store)
        except OSError:
            pass

    # ================================================================ per frame
    def feed(self, t, img, cropped=False):
        """One look. img = the whole frame (cropped=False) or just the corner box (`box_for(size)`).
        Returns the facts [(t, kind, value)] that became certain now (each line exactly once)."""
        with perf.timed("msgcorner"):
            try:
                return self._feed(t, img, cropped)
            except Exception as e:                      # never break the tracker over a message line
                self.stats["error"] = f"{type(e).__name__}: {e}"
                return []

    def _feed(self, t, img, cropped):
        if not cropped:
            img = img.crop(tuple(box_for(img.size)))
        a = np.asarray(img.convert("RGB"))
        self.stats["frames"] += 1
        if a.shape[0] < SLOT0 + H or a.shape[1] <= RIGHT + 1:
            return []
        # quick test: an idle corner has no ink in the ")" strip and nothing is tracked
        tracked = any(l is not None for l in self.lines)
        strip = _colour_mask(a[:, RIGHT - 3:RIGHT + 1].astype(np.int16))
        if not tracked and strip.sum() < 4:
            self.stats["idle"] += 1
            self.t_prev = t
            return []
        a16 = a.astype(np.int16)
        m, self._rope = _unrope(_thin(_colour_mask(a16) & _outlined(a16)))
        g = a16[..., 1]
        self._fr = {"g": g, "gp": None}
        cur = [self._slot(m, g, k) for k in range(SLOTS)]
        mt = m
        self.out = []
        gap = None if self.t_prev is None else t - self.t_prev
        self._track(t, cur, gap)
        self._read_pending(t, a16, mt, g)
        self._schedule(t)
        if self._dirty:
            self._dirty = False
            self._save()
        self.prev, self.t_prev = cur, t
        return self.out

    def _slot(self, m, g, k):
        s = _Slot()
        y1 = SLOT0 - PITCH * k
        if y1 < 0 or k >= VISIBLE:                 # the game never shows a 7th line: rows above are scenery (rock edges, stones)
            return s
        sl = m[y1:y1 + H]
        n = int(sl.sum())
        if n < MIN_PIXELS or n > MAX_PIXELS or sl[:, RIGHT - 3:RIGHT + 1].sum() < 4:
            return s
        colsum = sl.sum(0)
        run = np.convolve(colsum, np.ones(8, dtype=int), "valid")
        left = int(np.argmax(run >= 8))
        cols = _text_cols(sl | self._rope[None, :], 20)      # ink counted from the right while gaps stay word-sized: a rope or speckle far
        if len(cols) and cols[0] > left:           # to the left is not the start of the line
            left = int(cols[0])
        s.present, s.left = True, left
        s.bright = float(g[y1:y1 + H][sl].mean())
        s.peak = float(np.percentile(g[y1:y1 + H][sl], 98))
        s.mask = sl.copy()
        s.dil = _dilate(sl)
        gs = g[y1:y1 + H]
        s.bits = sl & (gs >= 0.7 * float(np.percentile(gs[sl], 95)))      # brightness-free shape of the line
        s.rows = y1
        s.rope = self._rope
        s.fr = self._fr
        return s

    def _slot_num(self, sl, lo=False):
        """The "(+N)" number of a slot as a string, read by the templates from the picture (cached); None if the line has no
        such tail or it cannot be read (item lines, ropes, templates not learned yet). lo = a laxer ink / outline split for
        fading lines (only used to confirm a number the other look already read)."""
        if sl.num is False:
            sl.num = None
            ln = sl.line
            if ln is not None and ln.state == "read" and ln.num is not None:
                sl.num = sl.numlo = str(ln.num)         # already read from an earlier look of this very line
            elif self.digits and sl.fr is not None and sl.peak >= 120:
                fr = sl.fr
                if fr["gp"] is None:
                    fr["gp"] = np.pad(fr["g"], 1, mode="edge")
                r = self._read_digits(fr["gp"], sl.rows, READ_THR * sl.peak)
                if r is not None:
                    sl.num = "".join(reversed(r))
        if lo:
            if sl.num is None and sl.numlo is False:
                sl.numlo = None
                r = self._read_digits(sl.fr["gp"], sl.rows, READ_THR_LO * sl.peak) if sl.fr is not None and sl.peak >= 120 else None
                if r is not None:
                    sl.numlo = "".join(reversed(r))
            return sl.num if sl.num is not None else sl.numlo
        return sl.num

    # ================================================================ stack tracking
    @staticmethod
    def _sim(p, c, keep):
        """How much of the outline (drop shadow) of p's ink is dark in c's picture, 0..1. Looks at the picture, not at c's
        mask, so speckles / cloud edges that flip the mask around do not matter; a different text has its shadow elsewhere."""
        ink = p.bits & keep[None, :]
        sh = np.zeros_like(ink)
        sh[1:, 1:] = ink[:-1, :-1]
        sh &= ~ink
        n = int(sh.sum())
        if n == 0 or c.fr is None:
            return 0.0
        dark = c.fr["g"][c.rows:c.rows + H] <= (SIM_DARK if c.peak >= 225 else SIM_DARK_DIM) * c.peak     # a fading shadow is only half dark
        return int((dark & sh).sum()) / n

    def _match(self, p, c):
        r = self._match_shape(p, c)
        if r > 0 and self._fading_same(p, c):
            return 0
        return r

    def _fading_same(self, p, c):
        """A line that fades on a pale background loses its shadow (the picture test fails) while its number stays readable:
        same number read on both sides, left edges close, p already dimmed and c not brighter = the same line, older. A fresh
        identical amount is full brightness (peak ~255), so it is brighter than p and stays a new line."""
        if p.peak >= FRESH_PEAK or c.peak > p.peak + FADE_TOL:
            return False
        pn = self._slot_num(p, True)                 # the laxer split: a faded number is read at 0.62 of the brightness
        if pn is None or pn != self._slot_num(c, True):
            return False
        kp, kc = self._slot_kind(p, pn), self._slot_kind(c, pn)
        return kp is not None and kp == kc          # (the left edge of a faded line is no use: the mask eats its first letters)

    def _slot_kind(self, sl, num):
        """Which label (exp / bonus / meso) a slot carries, by the label templates against the picture with the laxer split
        (cached); None when unclear. Used only to tell two faded lines with the same number apart."""
        if sl.kind is not False:
            return sl.kind
        sl.kind = None
        ln = sl.line
        if ln is not None and ln.state == "read" and ln.kind in LABELS:
            sl.kind = ln.kind
            return sl.kind
        if self.labels and sl.fr is not None and sl.peak >= 100:
            fr = sl.fr
            if fr["gp"] is None:
                fr["gp"] = np.pad(fr["g"], 1, mode="edge")
            thr = READ_THR_LO * sl.peak
            x_end = DIGIT_X0 - CELL_PITCH * len(num) - 4 - 2
            errs = {}
            for kind, ref in self.labels:
                if kind not in LABELS:
                    continue
                w = ref.shape[1]
                e = min(self._tscore(fr["gp"], sl.rows, x_end - w + dx, ref, thr) / max(1, int(ref.sum())) for dx in (-1, 0, 1))
                errs[kind] = min(e, errs.get(kind, 9))
            if len(errs) >= 2:
                order = sorted(errs.items(), key=lambda kv: kv[1])
                if order[0][1] < 0.35 and order[1][1] > order[0][1] + 0.15:
                    sl.kind = order[0][0]
        return sl.kind

    def _match_shape(self, p, c):
        """Cost of "c is the earlier slot p again (same text, maybe dimmer)": 0 same, 1.5 doubtful, 3 different.
        Two bright looks are compared pixel by pixel on the brightness-free shape (a different digit shows in
        the number part); dim ones by containment of the colour mask."""
        dl = abs(p.left - c.left)
        if dl > LEFT_FAR:                                  # the left edge of a line is only as good as its first letters'
            return 3                                       # mask (busy backgrounds eat them): the shape test decides the rest
        if p.bright >= B_SHAPE and c.bright >= B_SHAPE:
            lo = max(0, min(p.left, c.left) - 2)
            keep = np.ones(p.bits.shape[1], dtype=bool)
            for r in (p.rope, c.rope):
                if r is not None and len(r) == len(keep):
                    keep &= ~r                     # columns a rope crossed in either look say nothing
            pbits, cbits = p.bits & keep[None, :], c.bits & keep[None, :]
            pb, cb = pbits[:, lo:], cbits[:, lo:]
            d = int((pb & ~_dilate(cb)).sum() + (cb & ~_dilate(pb)).sum())     # a pixel flipping next to ink is noise
            pn, cn = self._slot_num(p), self._slot_num(c)
            if pn is None and cn is None:
                dn = 0                        # no "(+N)" tail on either (an item line): the whole-line shape decides
            elif pn is not None and cn is not None:
                dn = 0 if pn == cn else 99    # both numbers read from the picture: that decides (pixels flicker on busy backgrounds)
            else:
                dn = int((pbits[:, NUM_X:] != cbits[:, NUM_X:]).sum())      # one side unreadable: the pixels of the number part
                known = pn if pn is not None else cn
                if self._slot_num(c if pn is not None else p, True) == known:
                    dn = 0    # the fading side read with the laxer split gives the same number
                if dn > NUM_ABS and all(int((pbits[:, DIGIT_X0 - CELL_PITCH * k:DIGIT_X0 - CELL_PITCH * k + CELL_W]
                                             != cbits[:, DIGIT_X0 - CELL_PITCH * k:DIGIT_X0 - CELL_PITCH * k + CELL_W]).sum()) <= CELL_FLICKER
                                        for k in range(3)):
                    dn = 0    # the last cells (digits / "+") agree within flicker: two different digits are >= 5 px apart
            ds = min(self._sim(p, c, keep), self._sim(c, p, keep))
            if (d <= max(SHAPE_ABS, SHAPE_REL * int(pb.sum())) or ds >= SIM_SAME) and dn <= NUM_ABS:
                return 0
            return 1.5 if ((d <= 2 * SHAPE_ABS or ds >= SIM_DOUBT) and dn <= 2 * NUM_ABS) else 3
        cm = c.mask
        inter = int((cm & p.dil).sum())
        if inter >= CONTAIN * max(1, int(cm.sum())):
            return 0
        keep = np.ones(p.bits.shape[1], dtype=bool)
        for r in (p.rope, c.rope):
            if r is not None and len(r) == len(keep):
                keep &= ~r
        return 0 if min(self._sim(p, c, keep), self._sim(c, p, keep)) >= SIM_SAME else 3      # dim: the outline in the picture

    def _best_shift(self, t, prev, cur, plines):
        """n = number of new lines at the bottom: the shift of the previous stack that explains this frame."""
        nc = sum(1 for s in cur if s.present)
        best = (1e9, 0)
        # P lines that expired by age can't be there: treat as absent
        for n in range(0, SLOTS + 1):
            cost = NEW_COST * n                                  # parsimony: no more new lines than needed
            ok = True
            for j in range(min(n, SLOTS)):                      # new lines: present and bright
                c = cur[j]
                if not c.present or c.bright < B_NEW:
                    ok = False
                    break
            if not ok:
                continue
            for j in range(n, SLOTS):
                c, i = cur[j], j - n
                p = prev[i]
                lp = plines[i]
                if p.present and lp is not None and t - lp.t > KEEP_S:
                    p_present = False
                else:
                    p_present = p.present
                if c.present and p_present:
                    mc = self._match(p, c)
                    cost += mc
                    if c.peak > p.peak + (PEAK_TOL if j == 0 else PEAK_TOL_UP):
                        cost += 3
                    elif p.peak - c.peak > DIM_RATE * self._dt + DIM_SLACK:
                        cost += 3                                   # a line fades by ~45 G per second, not 100 in one look
                    elif p.peak >= 170 and c.peak >= p.peak - 1:
                        cost += NOFADE_COST
                elif c.present and not p_present:
                    cost += 1 if c.bright < B_STRONG else 4
                elif p_present and not c.present:
                    if j >= MAX_LINES:
                        pass                                    # pushed past the cap: gone at once
                    elif p.held > 0:
                        cost += HELD_GONE                       # already unseen last look: not seeing it again says little
                    elif lp is not None and t - lp.t <= YOUNG_S and p.bright >= B_YOUNG:
                        cost += YOUNG_GONE                      # a line seen last look and born moments ago cannot be gone: this
                                                                # shift would put it one slot up where nothing is (a re-read, not a new line)
                    elif p.bright >= B_STRONG:
                        cost += 3
                    elif p.bright >= B_GONE:
                        cost += 1
            # lines pushed past the game's line cap (MAX_LINES) vanish at once, but only a clearly fresh line
            # can push a bright, unfaded one off (else "everything is new" would explain any frame)
            if n > 0:
                max_new = max(cur[j].peak for j in range(min(n, SLOTS)))
                for i in range(SLOTS):
                    p = prev[i]
                    if i + n >= MAX_LINES and p.present and p.peak >= 170 and max_new < p.peak - PUSH_SLACK:
                        cost += 3
            if cost < best[0] - 1e-9:
                best = (cost, n)
        return best

    def _track(self, t, cur, gap):
        old_lines = self.lines
        new_lines = [None] * SLOTS
        if gap is None or gap > GAP_S:
            n, cost = sum(1 for s in cur if s.present and s.bright >= B_NEW), 0
            # everything on screen is new (bright) or an unknown ghost (dim): contiguous from the bottom
            n = 0
            while n < SLOTS and cur[n].present and cur[n].bright >= B_NEW:
                n += 1
            prev = [_Slot() for _ in range(SLOTS)]
            plines = [None] * SLOTS
        else:
            self._dt = t - self.t_prev if self.t_prev is not None else 0.3
            cost, n = self._best_shift(t, self.prev, cur, old_lines)
            prev, plines = self.prev, old_lines
            if cost >= 6:
                self.stats["resync"] += 1
            for j in range(n, min(SLOTS, MAX_LINES)):          # a fresh line that is not seen for a look (the mask loses it on a bright
                i = j - n                                      # background) is still there: hold it, so it does not come back as a new line
                pj = prev[i]
                if (not cur[j].present and pj.present and pj.bright >= B_STRONG and pj.held < HOLD_LOOKS
                        and plines[i] is not None and t - plines[i].t < KEEP_S):
                    h = copy.copy(pj)
                    h.held = pj.held + 1
                    cur[j] = h
        for j in range(SLOTS):
            c = cur[j]
            if not c.present:
                continue
            if j < n:
                ln = _Line(t, "pending")
                ln.overflow = n >= MAX_LINES
                self.stats["new_lines"] += 1
            else:
                i = j - n
                src = plines[i] if i < SLOTS else None
                if src is not None and prev[i].present:
                    ln = src
                    ln.frames += 1
                    ln.t_last = t
                else:                                            # no history: dim -> ghost, never counted
                    ln = _Line(t, "ghost")
                    self.stats["ghost"] += 1
                    ln.state = "ghost"
            ln.slot = j
            c.line = ln
            ln.unseen = c.held > 0
            if not ln.unseen:
                ln.last_bright = c.bright
            new_lines[j] = ln
        # lines that left the picture: flush what they still owe
        alive = {id(l) for l in new_lines if l is not None}
        for l in old_lines:
            if l is not None and id(l) not in alive:
                self._finalize(l, t)
        self.lines = new_lines

    # ================================================================ reading
    def _read_pending(self, t, a16, m, g):
        tess_budget = 3
        for j in range(SLOTS):
            ln = self.lines[j]
            if ln is None or ln.state != "pending" or ln.unseen:
                continue
            c_bright = ln.last_bright
            if c_bright < B_READ:
                ln.state = "failed"
                self.stats["unread"] += 1
                continue
            y1 = SLOT0 - PITCH * j
            gs = g[y1:y1 + H]
            if ln.own is None:                      # the first look: white = own kill, yellow = a teammate's
                ln.own = _is_white(a16[y1:y1 + H], gs, m[y1:y1 + H])
            self._gfull, self._y1 = g, y1
            tess_budget = self._try_read(t, ln, gs, m[y1:y1 + H], tess_budget)

    @staticmethod
    def _binarize(gs, ms):
        pk = float(np.percentile(gs[ms], 95)) if ms.any() else 0.0
        return (gs >= 0.78 * pk) & _dilate(ms), pk      # only near the text (bright backgrounds)

    def _find_number(self, b):
        """Digit count n from the '(' descender (rows 10-11 are inked only by parentheses); None if not
        the "(+N)" shape."""
        if b[10:12, RIGHT - 2:RIGHT + 1].sum() < 2:
            return None
        for n in range(1, 8):
            x0p = DIGIT_X0 - CELL_PITCH * n - 4
            if x0p < 4:
                return None
            if b[10:12, x0p:x0p + 3].sum() >= 2:
                if all(b[:, DIGIT_X0 - CELL_PITCH * i:DIGIT_X0 - CELL_PITCH * i + CELL_W].sum() >= 3 for i in range(n)):
                    return n
                return None
        return None

    def _cell(self, b, i):
        x0 = DIGIT_X0 - CELL_PITCH * i
        return b[:, x0:x0 + CELL_W]

    def _classify(self, cell, tol=4, margin=3):
        best = (99, None)
        second = 99
        for ch, pats in self.digits.items():
            for p in pats:
                d = int((p != cell).sum())
                if d < best[0]:
                    if best[1] != ch:
                        second = best[0]
                    best = (d, ch)
                elif ch != best[1] and d < second:
                    second = d
        if best[1] is not None and best[0] <= tol and second - best[0] >= margin:
            return best[1]
        return None

    def _label_bits(self, b, n):
        x0p = DIGIT_X0 - CELL_PITCH * n - 4
        reg = b[:, :max(0, x0p - 2)]
        cols = _text_cols(reg)
        if len(cols) < 10:
            return None
        return reg[:, cols.min():]

    def _match_label(self, bits):
        """Learned label shapes, right-aligned. A pixel flipping next to ink is noise (bright, busy backgrounds
        leave specks and take bits out of the letters), so the miss / extra counts use the dilated other side."""
        best = None
        for kind, ref in self.labels:
            if abs(ref.shape[1] - bits.shape[1]) > LABEL_W_TOL:
                continue
            w = min(ref.shape[1], bits.shape[1])
            r, q = ref[:, -w:], bits[:, -w:]
            d = int((r & ~_dilate(q)).sum() + (q & ~_dilate(r)).sum())
            d_exact = int((r != q).sum())
            if (d_exact <= max(6, 0.12 * int(ref.sum())) or d <= max(6, 0.08 * int(ref.sum()))) and (best is None or d_exact < best[0]):
                best = (d_exact, kind)
        return best[1] if best else None

    # ---- reading by template against the picture (not against a binarised copy) ----------------------
    def _ring(self, tm):
        """(ink, ring) index arrays of a template padded by 1: ring = its drop-shadow outline (always dark in the game)."""
        key = (tm.shape, tm.tobytes())
        r = self._rings.get(key)
        if r is None:
            ink = np.pad(tm, 1)
            ring = np.zeros_like(ink)                  # the outline is a drop shadow: the ink moved one down and one right
            ring[1:, 1:] = ink[:-1, :-1]
            ring &= ~ink
            r = (np.nonzero(ink), np.nonzero(ring), int(tm.sum()))
            if len(self._rings) < 400:
                self._rings[key] = r
        return r

    def _tscore(self, gp, y0, x0, tm, thr, extra=False):
        """Errors of template `tm` (bool) placed at (y0, x0) of the padded picture gp: ink pixels that are not bright
        plus outline pixels that are. The background around the glyph does not count, so bright or busy
        backgrounds do not flood the read."""
        ink, ring, _ = self._ring(tm)
        h, w = tm.shape
        if y0 < 0 or x0 < 0:
            return 99
        sub = gp[y0:y0 + h + 2, x0:x0 + w + 2]          # gp is padded by 1, so (y0, x0) here = template corner - 1
        if sub.shape != (h + 2, w + 2):
            return 99
        e = int((sub[ink] < thr).sum() + (sub[ring] >= thr).sum())
        if extra:                                        # ink-looking pixels (bright, dark shadow below right) the glyph does not have:
            seen = (sub[1:-1, 1:-1] >= thr) & (sub[2:, 2:] <= EXTRA_DARK * thr)      # "0" is part of "8", it must not pass for it
            e += int((seen & ~tm).sum())
        return e

    def _stack(self):
        """All digit templates as stacked arrays (padded by 1) for one vectorised score; rebuilt when the set changes."""
        key = sum(len(v) for v in self.digits.values()) + 1000 * len(self.digits)
        if self._stack_key != key:
            chars, inkm, ringm, tmm, ninks = [], [], [], [], []
            for ch, pats in self.digits.items():
                for tm in pats:
                    if tm.shape != (CELL_H, CELL_W):
                        continue
                    ink = np.pad(tm, 1)
                    ring = np.zeros_like(ink)
                    ring[1:, 1:] = ink[:-1, :-1]
                    ring &= ~ink
                    chars.append(ch)
                    inkm.append(ink)
                    ringm.append(ring)
                    tmm.append(tm)
                    ninks.append(int(tm.sum()))
            self._stack_data = (chars, np.array(inkm).reshape(-1, CELL_H + 2, CELL_W + 2) if inkm else np.zeros((0, CELL_H + 2, CELL_W + 2), bool),
                                np.array(ringm).reshape(-1, CELL_H + 2, CELL_W + 2) if ringm else np.zeros((0, CELL_H + 2, CELL_W + 2), bool),
                                np.array(tmm).reshape(-1, CELL_H, CELL_W) if tmm else np.zeros((0, CELL_H, CELL_W), bool))
            self._stack_ink = ninks
            self._stack_key = key
        return self._stack_data

    def _read_digits(self, gp, y1, thr):
        """Digit chars (last digit first) of a "(+N)" tail by templates against the padded picture gp, or None. Cells
        are tried from the right until the "+" cell comes out."""
        def cell(i):
            x0 = DIGIT_X0 - CELL_PITCH * i
            chars_, inkm, ringm, tmm = self._stack()
            if len(chars_) == 0 or x0 < 0 or y1 < 0:
                return None
            sub = gp[y1:y1 + CELL_H + 2, x0:x0 + CELL_W + 2]
            if sub.shape != (CELL_H + 2, CELL_W + 2):
                return None
            seen = (sub[1:-1, 1:-1] >= thr) & (sub[2:, 2:] <= EXTRA_DARK * thr)
            err = (((sub < thr)[None] & inkm).sum((1, 2)) + ((sub >= thr)[None] & ringm).sum((1, 2))
                   + (seen[None] & ~tmm).sum((1, 2)))
            cand = [(int(e), -int(n), ch) for e, n, ch in zip(err, self._stack_ink, chars_)]
            cand.sort()
            e0, ni0, c0 = cand[0]
            # a template that is part of a bigger one ("3" inside "8") scores as well on it: the bigger one wins ties
            if e0 > 3 or any(e <= e0 + 1 and ch != c0 and ni <= ni0 for e, ni, ch in cand[1:]):
                return None
            return c0
        chars = []
        for i in range(0, 5):
            c = cell(i)
            if c is None:
                return None
            if c == "+":
                return chars if chars else None
            chars.append(c)
        return None

    def _score_read(self, g, y1, pk):
        """(kind, number) of the slot at rows y1.. by templates against the picture, or None: the digits, then the label
        right-aligned at the number (+-1 px)."""
        if not self.digits or not self.labels or pk < 120:
            return None
        thr = READ_THR * pk
        gp = np.pad(g, 1, mode="edge")
        chars = self._read_digits(gp, y1, thr)
        if chars is None:
            return None
        n = len(chars)
        x_end = DIGIT_X0 - CELL_PITCH * n - 4 - 2             # right end of the label region
        best = None
        for kind, ref in self.labels:
            if kind not in LABELS:
                continue
            w = ref.shape[1]
            _, _, ninks = self._ring(ref)
            for dx in (-1, 0, 1):
                x0 = x_end - w + dx
                e = self._tscore(gp, y1, x0, ref, thr)
                if best is None or e < best[0]:
                    best = (e, kind, ninks)
        if best is None or best[0] > max(8, 0.10 * best[2]):
            return None
        return best[1], int("".join(reversed(chars)))

    def _try_read(self, t, ln, gs, ms, tess_budget):
        b, pk = self._binarize(gs, ms)
        hit = self._score_read(self._gfull, self._y1, pk)
        if hit is not None:
            return self._accept(t, ln, hit[0], hit[1], True, tess_budget)
        n = self._find_number(b)
        if n is None:
            return self._try_text(t, ln, b, tess_budget)
        bits = self._label_bits(b, n)
        if bits is None:
            ln.state = "unknown"
            self.stats["unknown"] += 1
            self.unknown_lines.append((t, "no label"))
            return tess_budget
        kind = self._match_label(bits)
        chars = [self._classify(self._cell(b, i)) for i in range(n)]
        plus = self._classify(b[:, DIGIT_X0 - CELL_PITCH * n:DIGIT_X0 - CELL_PITCH * n + CELL_W])
        if kind is not None and None not in chars and plus == "+":
            return self._accept(t, ln, kind, int("".join(reversed(chars))), True, tess_budget)
        # something is not learned yet: Tesseract, two agreeing reads before anything is learned
        if tess_budget <= 0:
            return tess_budget
        ln.tries += 1
        tess_budget -= 1
        text = _ocr_number(b, n, self.stats)
        lab = None
        if kind is None:
            lab = _ocr_label(b, bits, n, self.stats)
        key = (kind or lab, text)
        if text is None or key[0] is None:
            if ln.tries >= 6:
                ln.state = "failed"
                self.stats["unread"] += 1
            return tess_budget
        ln.votes[key] = ln.votes.get(key, 0) + 1
        top = max(ln.votes.items(), key=lambda kv: kv[1])
        others = [v for k, v in ln.votes.items() if k != top[0]]
        if top[1] >= 2 and (not others or top[1] > max(others)):
            kind2, num = top[0]
            self._learn(b, n, num, kind2, bits, kind)
            return self._accept(t, ln, kind2, int(num), False, tess_budget)
        if ln.tries >= 8:
            ln.state = "failed"
            self.stats["unread"] += 1
        return tess_budget

    def _try_text(self, t, ln, b, tess_budget):
        """A line without the "(+N)" tail: an item pickup (or something new). Whole-line Tesseract, two agreeing
        reads (the text is cached by the line's pixels, so a repeat of the same item costs nothing)."""
        cols = _text_cols(b)
        if len(cols) < 10:
            ln.state = "unknown"
            self.stats["unknown"] += 1
            return tess_budget
        bits = b[:, cols.min():]
        key = bits.tobytes()
        text = self.text_cache.get(key)
        if text is None:
            if tess_budget <= 0:
                return tess_budget
            ln.tries += 1
            tess_budget -= 1
            text = _ocr_text(bits, self.stats)
            if text is None:
                if ln.tries >= 6:
                    ln.state = "failed"
                    self.stats["unread"] += 1
                return tess_budget
            ln.tvotes[text] = ln.tvotes.get(text, 0) + 1
            top = max(ln.tvotes.items(), key=lambda kv: kv[1])
            others = [v for k, v in ln.tvotes.items() if k != top[0]]
            if not (top[1] >= 2 and (not others or top[1] > max(others))) and ln.tries < 6:
                return tess_budget
            text = top[0]
            if _label_like(text) and _read_item(text) is None:
                # a "(+N)" line whose number part was spoiled (`Toukavegpyed mesos (+] 4)`): not a new format. Look again on the
                # next looks (the templates read it when the picture is cleaner), give up after a few tries without a fact
                ln.tvotes = {}
                if ln.tries >= 12:
                    ln.state = "unknown"
                    self.stats["unknown"] += 1
                    self.unknown_lines.append((t, "label-like text " + text))
                return tess_budget
            if len(self.text_cache) < 400:
                self.text_cache[key] = text
        item = _read_item(text)
        if item is not None:
            ln.state, ln.kind, ln.item = "item", "item", item
            self.stats["items"] += 1
        else:
            ln.state, ln.kind, ln.item = "other", "other", text
            self.stats["other_text"] += 1
        return tess_budget

    def _learn(self, b, n, num, kind, bits, known_kind):
        s = str(num)
        if len(s) != n:
            return
        pats = {s[n - 1 - i]: self._cell(b, i) for i in range(n)}      # cell i = i-th digit from the right
        pats["+"] = b[:, DIGIT_X0 - CELL_PITCH * n:DIGIT_X0 - CELL_PITCH * n + CELL_W]
        for ch, cell in pats.items():
            have = self.digits.setdefault(ch, [])
            if any((p == cell).all() for p in have):
                continue
            clash = [c2 for c2, ps in self.digits.items() if c2 != ch and any(int((p != cell).sum()) <= 1 for p in ps)]
            if clash:
                self.stats["conflict"] += 1
                continue
            have.append(cell.copy())
            self.stats["learned_digits"] += 1
            self._dirty = True
        if known_kind is None and kind in LABELS and not any(k == kind and ref.shape == bits.shape and (ref == bits).all()
                                                              for k, ref in self.labels):
            self.labels.append((kind, bits.copy()))
            self._dirty = True
        elif known_kind is None and kind == "other":
            self.labels.append(("other", bits.copy()))
            self._dirty = True

    def _accept(self, t, ln, kind, num, by_template, tess_budget):
        if by_template:
            self.stats["template_reads"] += 1
        else:
            self.stats["tess_reads"] += 1
        if kind == "other":
            ln.state = "unknown"
            self.stats["unknown"] += 1
            self.unknown_lines.append((t, "label not known"))
            return tess_budget
        ln.state, ln.kind, ln.num = "read", kind, num
        return tess_budget

    # ================================================================ pairing / emission
    def _schedule(self, t):
        """Emit what is certain. Lines run oldest (top) -> newest (bottom); a Bonus line right below an
        EXP line belongs to it."""
        for j in range(SLOTS - 1, -1, -1):
            ln = self.lines[j]
            if ln is None or ln.emitted:
                continue
            if ln.state == "read":
                if ln.kind == "meso":
                    ln.emitted = True
                    self.out.append((ln.t, "meso_gain", {"mesos": ln.num, "overflow": ln.overflow}))
                elif ln.kind == "exp":
                    nxt = self.lines[j - 1] if j > 0 else None
                    if nxt is not None and nxt.state in ("pending",) and nxt.last_bright >= B_READ:
                        continue                                    # the line below may be its bonus
                    if nxt is not None and nxt.state == "read" and nxt.kind == "bonus" and not nxt.emitted:
                        ln.emitted = nxt.emitted = True
                        self.out.append((ln.t, "exp_gain", {"base": ln.num, "party_bonus": nxt.num,
                                                                  "overflow": ln.overflow or nxt.overflow,
                                                                  "own": bool(ln.own)}))
                    elif nxt is None and ln.frames < 2:
                        continue                                    # nothing below yet: wait one more look
                    else:
                        ln.emitted = True
                        self.out.append((ln.t, "exp_gain", {"base": ln.num, "party_bonus": 0, "overflow": ln.overflow,
                                                                  "own": bool(ln.own)}))
                elif ln.kind == "bonus":
                    older = self.lines[j + 1] if j + 1 < SLOTS else None
                    if older is not None and older.state == "pending":
                        continue                                    # its EXP line is not read yet
                    if older is not None and older.kind == "exp" and not older.emitted:
                        continue                                    # the EXP line takes it when it is emitted
                    ln.emitted = True                               # no EXP line to belong to (or it went out without)
                    self.stats["orphan_bonus"] += 1
                    self.unknown_lines.append((ln.t, f"bonus without exp line (+{ln.num})"))
            elif ln.state == "item":
                ln.emitted = True
                name, count, tab = ln.item
                self.out.append((ln.t, "item_gain", {"name": name, "count": count, "tab": tab, "overflow": ln.overflow}))
            elif ln.state == "other":
                ln.emitted = True
                self.out.append((ln.t, "msg_other", {"text": ln.item, "overflow": ln.overflow}))
            elif ln.state in ("unknown", "ghost", "failed"):
                ln.emitted = True

    def _finalize(self, ln, t):
        """A line left the picture. Flush an EXP line that was still waiting for its bonus."""
        if ln.emitted:
            return
        if ln.state == "read" and ln.kind == "exp":
            ln.emitted = True
            self.out.append((ln.t, "exp_gain", {"base": ln.num, "party_bonus": 0, "overflow": ln.overflow,
                                                                  "own": bool(ln.own)}))
        elif ln.state == "read" and ln.kind == "meso":
            ln.emitted = True
            self.out.append((ln.t, "meso_gain", {"mesos": ln.num, "overflow": ln.overflow}))
        elif ln.state == "item":
            ln.emitted = True
            name, count, tab = ln.item
            self.out.append((ln.t, "item_gain", {"name": name, "count": count, "tab": tab, "overflow": ln.overflow}))
        elif ln.state == "other":
            ln.emitted = True
            self.out.append((ln.t, "msg_other", {"text": ln.item, "overflow": ln.overflow}))
        elif ln.state == "pending":
            self.stats["unread"] += 1
        ln.emitted = True


def _text_cols(b, gap=GAP_COLS):
    """Columns with ink, counted from the right while the gaps stay word-sized: speckles of background far to the
    left of the line (rock, clouds) are cut off (a space is <= 6 px)."""
    cols = np.where(b.any(0))[0]
    if len(cols) < 2:
        return cols
    gaps = np.where(np.diff(cols) > gap)[0]
    return cols[gaps[-1] + 1:] if len(gaps) else cols


def _is_white(rgb, gs, ms):
    """First look of an EXP line: white (True) or yellow (False). Blue/red over the brightest 30% of the mask
    pixels (G at or above the 70th percentile): white 0.87-0.96, yellow 0.54-0.59 (NOTES.md 2026-11-01)."""
    if not ms.any():
        return False
    sel = ms & (gs >= np.percentile(gs[ms], 70))
    red = float(rgb[..., 0][sel].sum())
    return red > 0 and float(rgb[..., 2][sel].sum()) / red >= OWN_RATIO


# ====================================================================== Tesseract (bootstrap only)
_ready = None


def _tess_ready():
    global _ready
    if _ready is None:
        from engine import ocr
        _ready = ocr.READY or ocr.setup(None)
        if not _ready:
            try:
                from base.paths import load_config
                _ready = ocr.setup(load_config().get("tesseract"))
            except Exception:
                _ready = False
    return _ready


def _render(bits, scale=6):
    from PIL import Image, ImageOps
    im = Image.fromarray(np.where(bits, 0, 255).astype("uint8"))
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    return ImageOps.expand(im, border=16, fill=255)


def _ocr_number(b, n, stats):
    if not _tess_ready():
        return None
    import pytesseract
    x0p = DIGIT_X0 - CELL_PITCH * n - 4
    img = _render(b[:, x0p - 1:RIGHT + 2])
    stats["tess_calls"] += 1
    txt = pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789+()").strip()
    m = re.fullmatch(r"\(?\+?(\d+)\)?", txt.replace(" ", ""))
    if m and len(m.group(1)) == n:
        return m.group(1)
    return None


def _ocr_label(b, bits, n, stats):
    if not _tess_ready():
        return None
    import pytesseract
    stats["tess_calls"] += 1
    txt = pytesseract.image_to_string(_render(bits, 5), config="--psm 7").strip()
    best = max(LABELS.items(), key=lambda kv: SequenceMatcher(None, txt.lower(), kv[1].lower()).ratio())
    if SequenceMatcher(None, txt.lower(), best[1].lower()).ratio() >= LABEL_FUZZ:
        return best[0]
    return "other"


def _ocr_text(bits, stats):
    """Whole-line Tesseract of a message line (proportional text). None when it cannot run or read nothing."""
    if not _tess_ready():
        return None
    import pytesseract
    stats["tess_calls"] += 1
    txt = pytesseract.image_to_string(_render(bits, 5), config="--psm 7").strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt or None


def _label_like(text):
    """OCR text that is a garbled EXP / bonus / meso label."""
    t = text.lower()
    return any(SequenceMatcher(None, t[:len(v) + 6], v.lower()).ratio() >= 0.5 or SequenceMatcher(None, t, v.lower()).ratio() >= 0.5
               for v in LABELS.values()) or "mesos" in t


def _read_item(text):
    """Item pickup text -> (name, count, tab) or None."""
    for pat, name_g, count_g, tab_g in ITEM_PATTERNS:
        m = re.fullmatch(pat, text.strip())
        if m:
            return (m.group(name_g).strip(), int(m.group(count_g)) if count_g else 1,
                    m.group(tab_g) if tab_g else "")
    return None


# ====================================================================== the check
def _test(folder, store, cold):
    import csv
    import os
    import statistics
    import time
    from pathlib import Path

    from PIL import Image
    d = Path(folder)
    if cold and os.path.exists(store):
        os.remove(store)
    frames = sorted(p for p in d.glob("*.jpg") if re.match(r"\d{4}\.\d\d_", p.name))
    has_vitals = (d / "vitals.csv").exists()          # without it the gains are only listed (no EXP-bar comparison)
    rows = list(csv.DictReader(open(d / "vitals.csv", newline=""))) if has_vitals else []
    vit = []
    if rows:
        t0 = float(rows[0]["t"])
        vit = [(float(r["t"]) - t0, int(r["exp"]), float(r["exp_pct"])) for r in rows]

    mc = MsgCorner(store)
    facts, ms = [], []
    for p in frames:
        sec = float(p.name[:7])
        img = Image.open(p).convert("RGB")
        w0 = time.perf_counter()
        facts += mc.feed(sec, img)
        ms.append(1000 * (time.perf_counter() - w0))
    gains = [(t, v["base"] + v["party_bonus"], v) for t, k, v in facts if k == "exp_gain"]
    mesos = [(t, v["mesos"], v["overflow"]) for t, k, v in facts if k == "meso_gain"]

    if not vit:
        print(f"{d.name}: {len(frames)} frames, no vitals.csv; {len(gains)} exp_gain, {len(mesos)} meso_gain")
        for t, tot, v in gains:
            print(f"  {t:6.2f}s +{v['base']}/{v['party_bonus']}{' own' if v['own'] else ''}{' (overflow)' if v['overflow'] else ''}")
        print(f"own {sum(1 for g in gains if g[2]['own'])} of {len(gains)} gains")
        print("mesos:", ", ".join(f"+{v}@{t:.2f}{'!' if o else ''}" for t, v, o in mesos) or "none", f"(sum {sum(v for _, v, _ in mesos)})")
        print("items:", ", ".join(f"{v['name']} x{v['count']}@{t:.2f}" for t, k, v in facts if k == "item_gain") or "none")
        print("stats:", mc.stats)
        busy = [x for x in ms if x > 0.4]
        print(f"ms/frame median {statistics.median(ms):.2f}  busy median {statistics.median(busy or [0]):.2f}  "
              f"p95 {sorted(ms)[int(0.95 * (len(ms) - 1))]:.2f}  max {max(ms):.2f}  tesseract calls {mc.stats['tess_calls']}")
        return 0, 0, 0, 0
    # EXP rises (consecutive increasing rows merge into one event); a level-up (exp drops) is flagged
    events, prev, cur = [], vit[0], None
    for row in vit[1:]:
        if row[1] > prev[1]:
            if cur and cur["t1"] == prev[0]:
                cur["d"] += row[1] - prev[1]
                cur["t1"] = row[0]
            else:
                cur = {"t0": row[0], "t1": row[0], "d": row[1] - prev[1], "lvl": False}
                events.append(cur)
        elif row[1] < prev[1] or row[2] < prev[2] - 30:
            events.append({"t0": row[0], "t1": row[0], "d": 0, "lvl": True})
            cur = None
        prev = row
    sure = [i for i, g in enumerate(gains) if not g[2]["overflow"]]       # gains that are exact
    over = [i for i, g in enumerate(gains) if g[2]["overflow"]]           # born in a full all-new stack
    free = list(sure)
    lines, unmatched, matched = [], [], 0
    for ev in events:
        if ev["lvl"]:
            lines.append(f"  {ev['t0']:6.2f}s LEVEL-UP (exp reset), skipped")
            continue
        cand = [i for i in free if ev["t0"] - 2.5 <= gains[i][0] <= ev["t1"] + 1.5]   # the gain shows -1.5..+2.5 s after the rise
        pick = _subset(cand, gains, ev["d"], ev["t0"])
        if pick is None:
            unmatched.append(ev)
        else:
            matched += 1
            for i in pick:
                free.remove(i)
            lines.append(f"  {ev['t0']:6.2f}s +{ev['d']:<5d} ok       <- " + " + ".join(
                f"{gains[i][2]['base']}/{gains[i][2]['party_bonus']}{' own' if gains[i][2]['own'] else ''}@{gains[i][0]:.2f}" for i in pick))
    # unmatched rises next to overflow gains form one overflow group (the corner holds 6 lines: kills were missed)
    groups, free_over = [], list(over)
    for ev in unmatched:
        near = [i for i in over if ev["t0"] - 2.5 <= gains[i][0] <= ev["t1"] + 1.5]
        if near and groups and ev["t0"] - groups[-1]["t1"] <= 2.0:
            groups[-1]["t1"], groups[-1]["Y"] = ev["t1"], groups[-1]["Y"] + ev["d"]
            groups[-1]["ids"] |= set(near)
        elif near:
            groups.append({"t0": ev["t0"], "t1": ev["t1"], "Y": ev["d"], "ids": set(near)})
        else:
            lines.append(f"  {ev['t0']:6.2f}s +{ev['d']:<5d} MISSED   (no line explains it; candidates "
                         f"{[(round(gains[i][0], 2), gains[i][1]) for i in free]})")
    missed = sum(1 for ev in unmatched if not any(ev["t0"] - 2.5 <= gains[i][0] <= ev["t1"] + 1.5 for i in over))
    for g in groups:
        x = sum(gains[i][1] for i in g["ids"])
        for i in g["ids"]:
            free_over.remove(i)
        lines.append(f"  {g['t0']:6.2f}-{g['t1']:.2f}s OVERFLOW (read {x} of {g['Y']}): "
                     + " + ".join(f"{gains[i][2]['base']}/{gains[i][2]['party_bonus']}@{gains[i][0]:.2f}" for i in sorted(g["ids"])))
    levelup_times = [e["t0"] for e in events if e["lvl"]]
    extras = [i for i in free + free_over if not any(abs(gains[i][0] - lt) < 3 for lt in levelup_times)]
    print(f"{d.name} [{'cold' if cold else 'warm'}]: {len(frames)} frames, {len(gains)} exp_gain "
          f"({len(over)} flagged overflow), {len(mesos)} meso_gain")
    print("\n".join(lines))
    own_n = sum(1 for g in gains if g[2]["own"])
    print("own gains (white lines):", ", ".join(f"{g[2]['base']}/{g[2]['party_bonus']} own@{g[0]:.2f}" for g in gains if g[2]["own"]) or "none")
    print(f"own {own_n} of {len(gains)} gains")
    print(f"matched {matched}  missed {missed}  overflow groups {len(groups)}  extra {len(extras)}")
    for i in extras:
        print(f"  EXTRA exp_gain {gains[i][2]} at {gains[i][0]:.2f}s")
    print("mesos:", ", ".join(f"+{v}@{t:.2f}{'!' if o else ''}" for t, v, o in mesos) or "none")
    print("unknown lines:", mc.unknown_lines or "none")
    print("stats:", mc.stats)
    busy = [x for x in ms if x > 0.4]
    print(f"ms/frame median {statistics.median(ms):.2f}  busy median {statistics.median(busy or [0]):.2f}  "
          f"p95 {sorted(ms)[int(0.95 * (len(ms) - 1))]:.2f}  max {max(ms):.2f}  tesseract calls {mc.stats['tess_calls']}")
    return matched, missed, len(groups), len(extras)


def _subset(cand, gains, d, t):
    """Gains (indices) from cand whose sums equal d: earliest prefix first, else any small subset."""
    cand = sorted(cand, key=lambda i: gains[i][0])
    acc = 0
    for k, i in enumerate(cand):
        acc += gains[i][1]
        if acc == d:
            return cand[:k + 1]
    from itertools import combinations
    for r in range(1, min(len(cand), 5) + 1):
        for combo in combinations(cand, r):
            if sum(gains[i][1] for i in combo) == d:
                return list(combo)
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "test":
        import os
        import tempfile
        os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")
        cold = "--cold" in args
        store = os.path.join(tempfile.gettempdir(), "msgcorner_test_templates.json")   # never the real data folder
        for a in args:
            if a.startswith("--store="):
                store = a.split("=", 1)[1]
        for folder in [a for a in args[1:] if not a.startswith("--")]:
            _test(folder, store, cold)
            cold = False                       # later folders reuse what the first one learned
    else:
        print("usage: python -m engine.msgcorner test DIR [DIR...] [--cold] [--store=FILE]\n"
              "  frames SSSS.ss_HHMMSS.jpg + vitals.csv; --cold wipes the learned templates first (default: reuse the temp store)")
