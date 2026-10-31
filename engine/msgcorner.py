"""msgcorner.py - the gain messages in the bottom-right corner of the game window.

`You received EXP (+755)` then (in a party) `Bonus EXP for PARTY (+227)`, `You have gained mesos (+3)`:
each line is read exactly once and emitted as a fact

    (t, "exp_gain",  {"base": 755, "party_bonus": 227})   # party_bonus 0 without a bonus line
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
CELL_W, CELL_PITCH = 5, 7

# ---- tracking numbers ------------------------------------------------------------------------
MIN_PIXELS = 100                      # colour-mask pixels for a line to count as "present" (full line ~300)
MAX_PIXELS = 900
B_NEW = 165                           # a new line is at least this bright (measured 180-250 on its first look)
B_STRONG = 200                        # clearly fresh: a line this bright can't vanish, can't appear unexplained
NEW_COST = 0.7                        # cost of one more new line (an old, dimmed line also looks "bright enough")
PEAK_TOL = 7                          # a continuing line may not get brighter (98th percentile of G) than this (JPEG noise ~3)
NOFADE_COST = 1.0                     # ... but a bright line that did not fade at all is a little more likely a NEW identical one
PUSH_SLACK = 6                        # new lines may push a bright line off the top only if they are about as fresh as it
MAX_LINES = 6                         # the game shows at most 6 lines; older ones are pushed out at once (more
                                      # than 6 kills in one tick are partly never shown)
B_GONE = 160                          # below this a line may vanish
B_READ = 150                          # still readable
GAP_S = 5.0                           # no look for this long: every old line is gone
KEEP_S = 6.0                          # a tracked line older than this can't still be on screen
LEFT_TOL = 3
CONTAIN = 0.70
B_SHAPE = 140                         # both looks at least this bright: compare exact shapes
NUM_X, NUM_ABS = 290, 5               # the number part of a line (digit cells, "+", parentheses) must agree almost exactly
SHAPE_ABS, SHAPE_REL = 10, 0.03       # allowed differing pixels (JPEG noise) for the same line

LABELS = {"exp": "You received EXP", "bonus": "Bonus EXP for PARTY", "meso": "You have gained mesos"}
LABEL_FUZZ = 0.78

# Item pickups: NOT confirmed, so nothing is emitted (such lines are dropped as "unknown"). Seen on the fixture
# burst/20261030_222236 (frames 0063.1-0066.1), for whoever fills this in: `Squishy Liquid x1 earned. (Etc)`
# (name, "x<count>", "earned.", "(<tab>)"; proportional text with no "(+N)" tail, so the digit-cell reader does
# not apply and a name needs OCR). `Snail's Closeness has increased (+1)` is NOT an item (pet closeness).
# Fill with (regex on the OCR text of the whole line, name_group, count_group_or_None) once the format is
# confirmed and OCR of names is proven, then call `_read_item` from `_try_read` for lines without a "(+N)" shape.
ITEM_PATTERNS = []                    # TODO(item format)


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


def _dilate(m):
    p = np.pad(m, 1)
    d = np.zeros_like(m)
    for dy in range(3):
        for dx in range(3):
            d |= p[dy:dy + m.shape[0], dx:dx + m.shape[1]]
    return d


class _Slot:
    __slots__ = ("present", "left", "bright", "mask", "dil", "rows", "bits", "peak")

    def __init__(self):
        self.present = False
        self.left = 0
        self.bright = 0.0
        self.mask = self.dil = self.bits = None
        self.peak = 0.0
        self.rows = 0


class _Line:
    """One message line being tracked (follows its text up the stack)."""
    __slots__ = ("t", "frames", "state", "kind", "num", "votes", "emitted", "slot", "last_bright", "t_last", "tries", "overflow")

    def __init__(self, t, state):
        self.t, self.t_last, self.frames = t, t, 1
        self.state = state          # pending | read | unknown | ghost | failed
        self.kind = self.num = None
        self.votes = {}
        self.emitted = False
        self.slot = None
        self.last_bright = 0.0
        self.tries = 0
        self.overflow = False       # born in a frame whose whole stack was new: more lines may have been missed


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
                      "template_reads": 0, "tess_reads": 0}
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
        m = _colour_mask(a16)
        g = a16[..., 1]
        cur = [self._slot(m, g, k) for k in range(SLOTS)]
        self.out = []
        gap = None if self.t_prev is None else t - self.t_prev
        self._track(t, cur, gap)
        self._read_pending(t, a16, m, g)
        self._schedule(t)
        if self._dirty:
            self._dirty = False
            self._save()
        self.prev, self.t_prev = cur, t
        return self.out

    def _slot(self, m, g, k):
        s = _Slot()
        y1 = SLOT0 - PITCH * k
        if y1 < 0:
            return s
        sl = m[y1:y1 + H]
        n = int(sl.sum())
        if n < MIN_PIXELS or n > MAX_PIXELS or sl[:, RIGHT - 3:RIGHT + 1].sum() < 4:
            return s
        colsum = sl.sum(0)
        run = np.convolve(colsum, np.ones(8, dtype=int), "valid")
        left = int(np.argmax(run >= 8))
        s.present, s.left = True, left
        s.bright = float(g[y1:y1 + H][sl].mean())
        s.peak = float(np.percentile(g[y1:y1 + H][sl], 98))
        s.mask = sl.copy()
        s.dil = _dilate(sl)
        gs = g[y1:y1 + H]
        s.bits = sl & (gs >= 0.7 * float(np.percentile(gs[sl], 95)))      # brightness-free shape of the line
        s.rows = y1
        return s

    # ================================================================ stack tracking
    def _match(self, p, c):
        """Cost of "c is the earlier slot p again (same text, maybe dimmer)": 0 same, 1.5 doubtful, 3 different.
        Two bright looks are compared pixel by pixel on the brightness-free shape (a different digit shows in
        the number part); dim ones by containment of the colour mask."""
        if abs(p.left - c.left) > LEFT_TOL:
            return 3
        if p.bright >= B_SHAPE and c.bright >= B_SHAPE:
            lo = max(0, min(p.left, c.left) - 2)
            d = int((p.bits[:, lo:] != c.bits[:, lo:]).sum())
            dn = int((p.bits[:, NUM_X:] != c.bits[:, NUM_X:]).sum())      # the number: a different digit shows here
            if d <= max(SHAPE_ABS, SHAPE_REL * int(p.bits[:, lo:].sum())) and dn <= NUM_ABS:
                return 0
            return 1.5 if (d <= 2 * SHAPE_ABS and dn <= 2 * NUM_ABS) else 3
        cm = c.mask
        inter = int((cm & p.dil).sum())
        return 0 if inter >= CONTAIN * max(1, int(cm.sum())) else 3

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
                    if c.peak > p.peak + PEAK_TOL:
                        cost += 3
                    elif p.peak >= 170 and c.peak >= p.peak - 1:
                        cost += NOFADE_COST
                elif c.present and not p_present:
                    cost += 1 if c.bright < B_STRONG else 4
                elif p_present and not c.present:
                    if j >= MAX_LINES:
                        pass                                    # pushed past the cap: gone at once
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
            cost, n = self._best_shift(t, self.prev, cur, old_lines)
            prev, plines = self.prev, old_lines
            if cost >= 6:
                self.stats["resync"] += 1
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
            if ln is None or ln.state != "pending":
                continue
            c_bright = ln.last_bright
            if c_bright < B_READ:
                ln.state = "failed"
                self.stats["unread"] += 1
                continue
            y1 = SLOT0 - PITCH * j
            gs = g[y1:y1 + H]
            tess_budget = self._try_read(t, ln, gs, m[y1:y1 + H], tess_budget)

    @staticmethod
    def _binarize(gs, ms):
        pk = float(np.percentile(gs[ms], 95)) if ms.any() else 0.0
        return gs >= 0.78 * pk, pk

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

    def _classify(self, cell):
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
        if best[1] is not None and best[0] <= 4 and second - best[0] >= 3:
            return best[1]
        return None

    def _label_bits(self, b, n):
        x0p = DIGIT_X0 - CELL_PITCH * n - 4
        reg = b[:, :max(0, x0p - 2)]
        cols = np.where(reg.any(0))[0]
        if len(cols) < 10:
            return None
        return reg[:, cols.min():]

    def _match_label(self, bits):
        best = None
        for kind, ref in self.labels:
            w = min(ref.shape[1], bits.shape[1])
            if abs(ref.shape[1] - bits.shape[1]) > 1:
                continue
            d = int((ref[:, -w:] != bits[:, -w:]).sum())
            if d <= max(6, 0.12 * int(ref.sum())) and (best is None or d < best[0]):
                best = (d, kind)
        return best[1] if best else None

    def _try_read(self, t, ln, gs, ms, tess_budget):
        b, pk = self._binarize(gs, ms)
        n = self._find_number(b)
        if n is None:
            ln.state = "unknown"
            self.stats["unknown"] += 1
            self.unknown_lines.append((t, "no (+N) shape"))
            return tess_budget
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
                                                                  "overflow": ln.overflow or nxt.overflow}))
                    elif nxt is None and ln.frames < 2:
                        continue                                    # nothing below yet: wait one more look
                    else:
                        ln.emitted = True
                        self.out.append((ln.t, "exp_gain", {"base": ln.num, "party_bonus": 0, "overflow": ln.overflow}))
                elif ln.kind == "bonus":
                    older = self.lines[j + 1] if j + 1 < SLOTS else None
                    if older is not None and older.state == "pending":
                        continue                                    # its EXP line is not read yet
                    if older is not None and older.kind == "exp" and not older.emitted:
                        continue                                    # the EXP line takes it when it is emitted
                    ln.emitted = True                               # no EXP line to belong to (or it went out without)
                    self.stats["orphan_bonus"] += 1
                    self.unknown_lines.append((ln.t, f"bonus without exp line (+{ln.num})"))
            elif ln.state in ("unknown", "ghost", "failed"):
                ln.emitted = True

    def _finalize(self, ln, t):
        """A line left the picture. Flush an EXP line that was still waiting for its bonus."""
        if ln.emitted:
            return
        if ln.state == "read" and ln.kind == "exp":
            ln.emitted = True
            self.out.append((ln.t, "exp_gain", {"base": ln.num, "party_bonus": 0, "overflow": ln.overflow}))
        elif ln.state == "read" and ln.kind == "meso":
            ln.emitted = True
            self.out.append((ln.t, "meso_gain", {"mesos": ln.num, "overflow": ln.overflow}))
        elif ln.state == "pending":
            self.stats["unread"] += 1
        ln.emitted = True


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


def _read_item(text):
    """Item pickups: format unknown, ITEM_PATTERNS is empty, so nothing matches. -> (name, count) or None."""
    for pat, name_g, count_g in ITEM_PATTERNS:
        m = re.fullmatch(pat, text)
        if m:
            return m.group(name_g), int(m.group(count_g)) if count_g else 1
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
    rows = list(csv.DictReader(open(d / "vitals.csv", newline="")))
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
                f"{gains[i][2]['base']}/{gains[i][2]['party_bonus']}@{gains[i][0]:.2f}" for i in pick))
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
