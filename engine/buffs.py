"""buffs.py - which buff icons are showing at the top right of the game window.

A buff (Magic Guard, Meditation, ...) shows as the skill's own icon in a strip at the far right
edge, stacking leftwards (NOTES "2026-10-31: buff icons"). The icon is the same art as the skill icon
the app cuts from the Skill window (templates/skill_icons/<id>.png, 34x35, 1 px frame), so the reader
slides each icon along the strip and keeps the ones that match.

  find(img, icons)   img = the strip (or a whole frame), icons = {skill id: prepared icon} -> set of ids
  prepare(icon)      an icon image -> what find() compares
  python -m engine.buffs FRAME... --icons DIR      debug: what is found in each frame

Measured at 1920x1080 (NOTES): icon at x ~1884-1916, y ~3-35; a 32 px crop from (3,3) of the skill icon;
only rows 14-30 are compared (the player's FPS counter overlay covers the top rows). Mean grey diff: icon
there 13-24, no icon 131, another skill's icon 59-82. Spacing of several icons: not measured, so the
whole band is searched. UI-size/resolution: boxes and icons scale with width/1920 (unmeasured).

Buffs the player does NOT own (a party member's Haste; NOTES "2026-11-07: party Haste in the buff list") have no
cut of their own: LooseIcon takes the MeowDB picture (32x32 RGBA, pasted at (3,3) on a 34x35 canvas) and
find_loose() finds it by masked normalized correlation, because the game draws that icon translucent (the scene shows
through), dims it and, in the last 60 s, draws big yellow seconds-left digits over it (a small white minutes digit
before). BuffGlyphs reads those digits with masks it LEARNS at run time from the countdown itself into
<data>/glyphs_buff.json (the shapes are the game's font: never shipped). The owned-buff path above is unchanged.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from base import perf
from base.paths import DATA

REF_W = 1920
BAND = 320              # px of the frame's right edge searched (at 1920 wide; ~8 icons; wider gave scenery false hits)
STRIP_H = 48            # rows kept from the top (icon y 3-35, searched y offset 0-10, plus its height)
CROP = (3, 3)           # the skill icon's frame: the buff icon is the icon from here on
ROWS = (14, 30)         # rows of the crop compared (the FPS overlay covers the rows above)
COLS = 30
DY = 10                 # the y offsets tried: 0..DY
MATCH = 32.0            # mean grey diff below this = the icon is there (NOTES 2026-10-31: on 12-24, best scenery hit 38-39, off 52+)
COARSE = 3              # the sliding search compares every 3rd pixel; the best spots are then checked in full


def strip_box(size):
    """(x0, y0, x1, y1) of the buff strip in a frame of this size."""
    w, h = size
    s = w / REF_W
    return (max(0, int(round(w - BAND * s))), 0, w, min(h, int(round(STRIP_H * s)) + 1))


def _grey(img):
    a = np.asarray(img.convert("RGB"), dtype=np.int32)
    return (a[..., 0] * 77 + a[..., 1] * 151 + a[..., 2] * 28) >> 8


def prepare(icon, scale=1.0):
    """A skill icon (PIL) -> the grey patch compared (rows 14-30 of the crop from (3,3))."""
    if scale and abs(scale - 1.0) > 0.02:
        icon = icon.resize((max(1, round(icon.width * scale)), max(1, round(icon.height * scale))), Image.BILINEAR)
    g = _grey(icon)
    return g[CROP[1] + ROWS[0]: CROP[1] + ROWS[1], CROP[0]: CROP[0] + COLS]


def _scan(g, t):
    """Best mean abs diff of patch t over grey strip g, y offsets 0..DY, every x: (diff, x, y)."""
    th, tw = t.shape
    if g.shape[0] < th or g.shape[1] < tw:
        return 1e9, 0, 0
    y0 = ROWS[0]                                        # the crop's top is at y 0..DY, so the patch's top at 14..14+DY
    y1 = min(y0 + DY, g.shape[0] - th)
    if y1 < y0:
        return 1e9, 0, 0
    rows = g[y0: y1 + th]
    # coarse: every COARSE-th pixel of the patch, at every x and y offset
    c = t[::COARSE, ::COARSE]
    w = np.lib.stride_tricks.sliding_window_view(rows, t.shape)[:, :, ::COARSE, ::COARSE]
    d = np.abs(w - c).mean(axis=(2, 3))
    k = np.argsort(d, axis=None)[:3]
    best = (1e9, 0, 0)
    for i in k:
        yy, xx = divmod(int(i), d.shape[1])
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                y, x = yy + dy, xx + dx
                if 0 <= y < w.shape[0] and 0 <= x < w.shape[1]:
                    v = float(np.abs(rows[y:y + th, x:x + tw] - t).mean())
                    if v < best[0]:
                        best = (v, x, y0 + y)
    return best


def find(img, icons, detail=None):
    """The skill ids whose icon is in the strip. `img` = the strip grabbed at strip_box(), or the whole
    frame (then it is cropped). `detail` (dict, optional) gets {id: (diff, x, y)} for every icon."""
    with perf.timed("buffs"):
        w, h = img.size
        if h > 200:                                     # a whole frame, not the strip
            img = img.crop(strip_box(img.size))
        g = _grey(img)
        out = set()
        for sid, t in icons.items():
            v, x, y = _scan(g, t)
            if detail is not None:
                detail[sid] = (round(v, 1), x, y)
            if v < MATCH:
                out.add(sid)
        return out


def load_icons(folder, ids, scale=1.0):
    """{id: prepared icon} for the ids that have <folder>/<id>.png."""
    out = {}
    for sid in ids:
        p = Path(folder) / f"{sid}.png"
        if p.exists():
            try:
                out[str(sid)] = prepare(Image.open(p), scale)
            except Exception:
                pass
    return out


# ---- buffs the player does not own: masked correlation + the countdown digits -------------------------------------
ICON_W, ICON_H = 34, 35
# masks on the 34x35 icon image: (rows, cols, hole) -> compared pixels. "full" = the lower part, "outer" = the same
# without the middle where the countdown digits sit. Measured on 20261107_215426: icon on 0.78-0.83 (full, no digits)
# / 0.87-1.00 (outer, digits); anything else in 8 other recordings <= 0.59.
LOOSE_MASKS = {"full": ((14, 34), (5, 33), None),
               "outer": ((14, 34), (6, 33), (12, 29, 8, 29))}
LOOSE_MIN = {"full": 0.68, "outer": 0.75}
HINT_DX, HINT_DY = 3, 2     # a known spot is checked this far around first...
HINT_SLACK = 0.12           # ...with this much lower thresholds (a cursor over the icon gave 0.64 once)


def _norm(v):
    v = v - v.mean(-1, keepdims=True)
    return v / (v.std(-1, keepdims=True) + 1e-3)


class LooseIcon:
    """One picture to find: grey patch + its masks (built for a frame scale)."""

    def __init__(self, img, scale=1.0):
        canvas = Image.new("RGB", (ICON_W, ICON_H), (0, 0, 0))
        icon = img.convert("RGBA")
        canvas.paste(icon, (3, 3), icon)
        if scale and abs(scale - 1.0) > 0.02:
            canvas = canvas.resize((max(1, round(ICON_W * scale)), max(1, round(ICON_H * scale))), Image.BILINEAR)
        self.scale = scale or 1.0
        T = _grey(canvas).astype(np.float32)
        self.key = T.tobytes()
        self.masks = {}
        s = self.scale
        for name, (rows, cols, hole) in LOOSE_MASKS.items():
            m = np.zeros(T.shape, bool)
            m[round(rows[0] * s): round(rows[1] * s), round(cols[0] * s): round(cols[1] * s)] = True
            if hole:
                m[round(hole[0] * s): round(hole[1] * s), round(hole[2] * s): round(hole[3] * s)] = False
            ys, xs = np.nonzero(m)
            y0, x0 = int(ys.min()), int(xs.min())
            mm = m[y0: ys.max() + 1, x0: xs.max() + 1]
            patch = T[y0: y0 + mm.shape[0], x0: x0 + mm.shape[1]]
            sub = mm.copy()                         # every 2nd pixel each way: the coarse scan
            sub[1::2, :] = False
            sub[:, 1::2] = False
            self.masks[name] = (y0, x0, mm, _norm(patch[mm]), sub, _norm(patch[sub]))


def _scores(g, M, dys, xs, coarse=False):
    """NCC of mask M over grey strip g at icon y offsets `dys` and icon left positions `xs`: array [dy, x]
    (-1 where the window does not fit)."""
    y0, x0, mm, t, sub, ts = M
    h, w = mm.shape
    use, tt = (sub, ts) if coarse else (mm, t)
    out = np.full((len(dys), len(xs)), -1.0, np.float32)
    ok = [j for j, x in enumerate(xs) if 0 <= x + x0 <= g.shape[1] - w]
    if not ok:
        return out
    cols = [xs[j] + x0 for j in ok]
    for i, dy in enumerate(dys):
        if dy + y0 + h > g.shape[0]:
            continue
        win = np.lib.stride_tricks.sliding_window_view(g[dy + y0: dy + y0 + h], (h, w))[0]   # (nx, h, w)
        out[i, ok] = (_norm(win[cols][:, use]) * tt).mean(-1)
    return out


def _best_of(g, M, dys, xs, coarse=False):
    sc = _scores(g, M, dys, xs, coarse)
    i = np.unravel_index(int(sc.argmax()), sc.shape)
    return float(sc[i]), xs[i[1]], dys[i[0]]


def _refine(g, M, sc, dys, xs):
    """The 3 best coarse spots checked in full (+-2 px, +-1 dy): (score, x, dy)."""
    best = (-1.0, 0, 0)
    for o in np.argsort(sc, axis=None)[::-1][:3]:
        di, xi = np.unravel_index(int(o), sc.shape)
        rd = [d for d in range(dys[di] - 1, dys[di] + 2) if 0 <= d <= DY]
        b = _best_of(g, M, rd, list(range(xs[xi] - 2, xs[xi] + 3)))
        if b[0] > best[0]:
            best = b
    return best


def _loose_one(g, ic, hint):
    """(score, x, y, mask, found) for icon `ic` in grey strip g: the first mask that passes (the last spot first, with
    lower thresholds; then the whole strip), else the best failing one."""
    top = (-1.0, 0, 0, "full", False)
    if hint:
        xs = list(range(hint[0] - HINT_DX, hint[0] + HINT_DX + 1))
        dys = [d for d in range(hint[1] - HINT_DY, hint[1] + HINT_DY + 1) if 0 <= d <= DY]
        for name, M in ic.masks.items():
            v, x, dy = _best_of(g, M, dys, xs)
            if v >= LOOSE_MIN[name] - HINT_SLACK:
                return v, x, dy, name, True
            if v > top[0]:
                top = (v, x, dy, name, False)
    xs = list(range(0, g.shape[1] - 8))
    dys = list(range(0, DY + 1, 3))                 # every 3rd icon y; the refine step covers +-1
    for name, M in ic.masks.items():
        v, x, dy = _refine(g, M, _scores(g, M, dys, xs, True), dys, xs)
        if v >= LOOSE_MIN[name]:
            return v, x, dy, name, True
        if v > top[0]:
            top = (v, x, dy, name, False)
    return top


def load_loose(folder, ids, scale=1.0):
    """{id: LooseIcon} for the ids that have <folder>/<id>.png (MeowDB pictures)."""
    out = {}
    for sid in ids:
        p = Path(folder) / f"{sid}.png"
        if p.exists():
            try:
                out[str(sid)] = LooseIcon(Image.open(p), scale)
            except Exception:
                pass
    return out


def find_loose(img, icons, detail=None, hints=None):
    """Which of `icons` ({id: LooseIcon}) are in the strip (or whole frame): {id: (x, y)} = where the icon image's
    top-left is in the strip. Icons with the same picture (Haste 4101001 / 4201001) are searched once and share the
    result. `hints` {id: (x, y)} = where they were last (checked first). `detail` gets {id: (score, x, y, mask)}."""
    with perf.timed("buffs_loose"):
        if img.size[1] > 200:
            img = img.crop(strip_box(img.size))
        g = _grey(img).astype(np.float32)
        done, out = {}, {}
        for sid, ic in icons.items():
            if ic.key not in done:
                done[ic.key] = _loose_one(g, ic, (hints or {}).get(sid))
            v, x, y, name, found = done[ic.key]
            if detail is not None:
                detail[sid] = (round(v, 2), x, y, name)
            if found:
                out[sid] = (x, y)
        return out


# ---- the countdown digits: segmented here, LEARNED at run time (glyphs_buff.json, never shipped) ----------------------
# The game draws, over a buff icon, big yellow seconds digits in its last 60 s and a small white minutes digit before.
# Their shapes are Nexon's font, so no mask ships: BuffGlyphs learns them from the countdown itself (see its docstring).
DIGIT_ROWS = (13, 26)       # icon rows / cols searched for the yellow digits
DIGIT_COLS = (3, 33)
DIGIT_CORE, DIGIT_CORE_N = 190, 8   # real digits have >= 8 pixels with min(r, g) >= 190 (Rage's flames: none above ~165, 2026-11-08)
MIN_ROWS, MIN_COLS = (23, 34), (3, 12)      # the small white minutes digit sits here on the icon image (bottom-left)
GLYPH_FILE = "glyphs_buff.json"
SEC_SAME, MIN_SAME = 3, 2   # two cut glyphs are the same shape when they differ in at most this many pixels
SEC_READ, MIN_READ = 3, 3   # a glyph is read as a learned digit within this many pixels...
READ_MARGIN = 3             # ...when the best other digit is at least this much further
MIN_DIGIT_S = (45.0, 95.0)    # ...and the learned digit's run lasted this many seconds (a minute, with the frame clock's jitter)
MIN_STEP_RUN = 6            # a minutes-digit step counts only between two runs of this many same-shape looks each
LEARN_LOOKS = 3             # a shape is learned only when seen on this many looks
RUN_GAP_S = 2.5             # looks further apart than this are different runs
STEP_GAP_S = 1.6            # two looks this close are consecutive (a minutes digit, the start of the last minute)
CHAIN_GAP_S = 1.2           # the big digits count down 1 per second: looks further apart may have skipped one
ANCHOR_GAP_S = 0.95         # two looks this close cannot have a whole displayed second between them
ANALYZE_EVERY_S = 4.0       # a running countdown is re-analysed this often
MAX_VARIANTS = 16           # keys kept per learned digit (jitter variants)
_arrays = {}


def _key_array(key):
    a = _arrays.get(key)
    if a is None:
        a = _arrays[key] = np.array([[c == "#" for c in r] for r in key.split("/")])
    return a


def _glyph_key(g):
    return "/".join("".join("#" if v else "." for v in r) for r in g)


def _yellow(c):
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    mn = np.minimum(r, g)
    return (mn - b >= 60) & (np.abs(r - g) < 45) & (mn >= 90)


def _glyph_dist(g, s):
    """Pixels that differ between two glyph masks, best over a 1 px shift."""
    best = 9999
    H, W = max(g.shape[0], s.shape[0]) + 2, max(g.shape[1], s.shape[1]) + 2
    a = np.zeros((H, W), bool)
    a[1: 1 + g.shape[0], 1: 1 + g.shape[1]] = g
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            b = np.zeros((H, W), bool)
            b[1 + dy: 1 + dy + s.shape[0], 1 + dx: 1 + dx + s.shape[1]] = s
            best = min(best, int((a ^ b).sum()))
    return best


def _icon_crop(img, x, y, scale):
    s = scale or 1.0
    c = img.crop((x, y, x + round(ICON_W * s), y + round(ICON_H * s))).convert("RGB")
    if abs(s - 1.0) > 0.02:
        c = c.resize((ICON_W, ICON_H), Image.BILINEAR)
    return np.asarray(c).astype(np.int32)


def cut_seconds(img, x, y, scale=1.0):
    """The big yellow digits over the icon whose image top-left is (x, y): [] = none, None = something yellow that is
    not 1-2 digit-shaped glyphs, else the glyph keys left to right ("rows/rows" of # and .)."""
    a = _icon_crop(img, x, y, scale)
    m = _yellow(a)[DIGIT_ROWS[0]: DIGIT_ROWS[1], DIGIT_COLS[0]: DIGIT_COLS[1]]
    if m.sum() < 15:
        return []
    core = np.minimum(a[..., 0], a[..., 1])[DIGIT_ROWS[0]: DIGIT_ROWS[1], DIGIT_COLS[0]: DIGIT_COLS[1]]
    if int((m & (core >= DIGIT_CORE)).sum()) < DIGIT_CORE_N:
        return []                   # the icon's own orange (Rage's flames) is yellow-ish too but never this bright
    segs, st = [], None
    for i, v in enumerate(m.any(0)):
        if v and st is None:
            st = i
        if not v and st is not None:
            segs.append((st, i))
            st = None
    if st is not None:
        segs.append((st, m.shape[1]))
    if not 1 <= len(segs) <= 2:
        return None
    out = []
    for p, q in segs:
        gl = m[:, p:q]
        rows = np.nonzero(gl.any(1))[0]
        gl = gl[rows.min(): rows.max() + 1]
        if not (8 <= gl.shape[0] <= 11 and 2 <= gl.shape[1] <= 7):
            return None
        out.append(_glyph_key(gl))
    return out


def cut_minutes(img, x, y, scale=1.0):
    """The small white digit at the icon's bottom-left (60 s or more left): its mask key, or None when there is none."""
    a = _icon_crop(img, x, y, scale)[MIN_ROWS[0]: MIN_ROWS[1], MIN_COLS[0]: MIN_COLS[1]]
    m = (a.mean(2) >= 215) & ((a.max(2) - a.min(2)) < 40)
    if m.sum() < 4:
        return None
    rows, cols = np.nonzero(m.any(1))[0], np.nonzero(m.any(0))[0]
    return _glyph_key(m[rows.min(): rows.max() + 1, cols.min(): cols.max() + 1])


def _cluster(keys, tol):
    """Group glyph keys that are the same shape: (list of class index per key, number of classes)."""
    reps, ids = [], []
    for k in keys:
        a = _key_array(k)
        for i, r in enumerate(reps):
            if _glyph_dist(a, r) <= tol:
                ids.append(i)
                break
        else:
            reps.append(a)
            ids.append(len(reps) - 1)
    return ids, len(reps)


class BuffGlyphs:
    """The countdown digits of buff icons, learned at run time into <data>/glyphs_buff.json (never shipped: the shapes
    are the game's font; engine/glyphs.py works the same way). Two tables: "seconds" (the big yellow digits) and
    "minutes" (the small white digit), each {glyph key: digit}; a digit may have a few keys (jitter).

    Reading: look() cuts the digits over a found icon and returns (seconds left, whole minutes left); a digit that is
    not learned yet reads None (never a guess), and the caller's estimate path carries on.

    Learning (self-labelled from the countdown, by order, not by clock: look() feeds the looks of one icon, a "run",
    and re-analyses it every ANALYZE_EVERY_S and when it ends; frame times of a recording may jitter):
    * seconds: needs a run that shows the small minutes digit and then the big digits (the start of the last minute).
      The countdown drops exactly 1 per displayed second, so the first big tens shape is 5 (59, 58), then 4, 3, ...;
      at a tens change seen in two looks <= ANCHOR_GAP_S apart (no whole second can hide between) the units shape before
      it is 0 and after it 9, and every further new units shape is one less, mod 10. A shape that would get two digits
      (a skipped second, garbage) drops the run; one-off garbled looks are ignored; a shape is stored when seen on
      >= LEARN_LOOKS looks. So the units digits are learned after ~2 cycles of the countdown (~20 s of big digits).
    * minutes: the shape directly before the big digits is 1 (floor of 60..119 s / 60). A shape X that changes to an
      already learned digit k (labels 1..k-1 all learned) was k+1: only a recast raises the digit, and it would have to
      come from a smaller (learned) one. So 2 is learned from a 2 -> 1 step, 3 from 3 -> 2, ...
    A shape that clashes with a stored digit is dropped. `learned` = [(t, kind, digit)] for this session."""

    def __init__(self, path=None, log=None):
        self.path = Path(path) if path else DATA / GLYPH_FILE
        self.log = log or (lambda s: None)
        self.t = {"seconds": {}, "minutes": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for kind in self.t:
                self.t[kind] = {k: str(v) for k, v in (raw.get(kind) or {}).items() if isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            pass
        self.runs = {}              # {icon id: [obs]}, obs = {"t", "sec", "min"}
        self.analyzed = {}          # {icon id: time of the last analysis}
        self.learned = []
        self._cls = {}

    # -- store ----------------------------------------------------------------------------------------------------
    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"_version": 1, **self.t}, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def known(self, kind):
        return set(self.t[kind].values())

    def classify(self, kind, key):
        """The learned digit for glyph `key`, or None (nothing close enough, or two digits about as close)."""
        ck = (kind, key)
        if ck in self._cls:
            return self._cls[ck]
        a, best = _key_array(key), {}
        for k, lab in self.t[kind].items():
            d = _glyph_dist(a, _key_array(k))
            best[lab] = min(d, best.get(lab, 9999))
        order = sorted((d, lab) for lab, d in best.items())
        lim = SEC_READ if kind == "seconds" else MIN_READ
        res = None
        if order and order[0][0] <= lim and (len(order) == 1 or order[1][0] - order[0][0] >= READ_MARGIN):
            res = order[0][1]
        self._cls[ck] = res
        return res

    def _commit(self, kind, label, keys, t):
        """Store a learned digit (all the jitter variants seen), unless a stored digit contradicts it."""
        keys = list(dict.fromkeys(keys))[:MAX_VARIANTS]
        tol = SEC_SAME if kind == "seconds" else MIN_SAME
        for k in keys:
            for k2, lab in self.t[kind].items():
                if lab != label and _glyph_dist(_key_array(k), _key_array(k2)) <= tol:
                    self.log(f"buff glyph {kind} {label}: clashes with stored {lab}, dropped")
                    return False
        new = [k for k in keys if self.t[kind].get(k) != label]
        if not new:
            return False
        for k in new:
            self.t[kind][k] = label
        self._cls = {}
        self.learned.append((t, kind, label))
        self.save()
        return True

    # -- reading + feeding the learner ---------------------------------------------------------------------------
    def look(self, sid, t, img, x, y, scale=1.0):
        """One look at icon `sid` found at (x, y): (seconds left | None, whole minutes left | None). Also feeds the
        learner (call lost() when the icon is not found)."""
        with perf.timed("buff_digits"):
            try:
                sec, mn = cut_seconds(img, x, y, scale), cut_minutes(img, x, y, scale)
            except Exception:
                return None, None
            run = self.runs.get(sid)
            if run and t - run[-1]["t"] > RUN_GAP_S:
                self._analyze(sid, t, final=True)
                run = None
            if run is None:
                run = self.runs[sid] = []
                self.analyzed[sid] = t
            run.append({"t": t, "sec": sec, "min": mn})
            if t - self.analyzed[sid] >= ANALYZE_EVERY_S:
                self._analyze(sid, t)
            left = mins = None
            if sec:                                       # big digits: read them all or nothing
                labs = [self.classify("seconds", k) for k in sec]
                if None not in labs and int("".join(labs)) > 0:
                    left = int("".join(labs))
            elif sec == [] and mn:
                m = self.classify("minutes", mn)
                mins = int(m) if m else None
            return left, mins

    def lost(self, sid, t):
        """The icon was not found: the run ends (and is analysed a last time)."""
        if sid in self.runs:
            self._analyze(sid, t, final=True)
            self.runs.pop(sid, None)

    # -- learning -------------------------------------------------------------------------------------------------
    def _analyze(self, sid, t, final=False):
        run = self.runs.get(sid) or []
        self.analyzed[sid] = t
        if len(run) < 4:
            return
        try:
            self._learn_seconds(run, t)
            self._learn_minutes(run, None, t)
        except Exception as e:
            self.log(f"buff glyph learning failed: {type(e).__name__}: {e}")
        if len(run) > 400:
            del run[:len(run) - 400]

    def _big_phase(self, run):
        """(b, B): index of the first big-digit look that follows a minutes-digit look closely (the start of the last
        minute, seen), and the looks of the contiguous big phase from there; None when that start was not seen."""
        for i in range(1, len(run)):
            o, p = run[i], run[i - 1]
            if o["sec"] and p["sec"] == [] and p["min"] and o["t"] - p["t"] <= STEP_GAP_S:
                B = [o]
                for q in run[i + 1:]:
                    if q["sec"] and q["t"] - B[-1]["t"] <= CHAIN_GAP_S:
                        B.append(q)
                    else:
                        break
                return i, B
        return None

    def _learn_seconds(self, run, t_now):
        """Label the big digits from the order they appear in: the countdown drops by exactly 1 per displayed second.
        Tens: the first shape of the last minute is 5 (59, 58), then 4, 3, ... at each change. Units: the shape just
        before a tens change is 0 and the one after it 9 (10k -> 10k-1), then every new shape is one less, mod 10;
        every look pair used is close enough (<= ANCHOR_GAP_S) that no displayed second fits between. Any shape that
        would get two digits, or a change that does not fit the chain, drops the whole run."""
        bp = self._big_phase(run)
        if bp is None:
            return
        _, B = bp
        keys = [k for o in B for k in o["sec"]]
        ids, _n = _cluster(keys, SEC_SAME)
        looks, n = [], 0                                     # (t, tens class or None, units class, [keys])
        for o in B:
            cl = ids[n: n + len(o["sec"])]
            looks.append([o["t"], cl[0] if len(cl) == 2 else None, cl[-1], o["sec"]])
            n += len(o["sec"])
        support = {}
        for _t, tc, uc, _k in looks:
            for c in (tc, uc):
                if c is not None:
                    support[c] = support.get(c, 0) + 1
        looks = [lk for lk in looks if all(c is None or support[c] >= 2 for c in lk[1:3])]    # garbled one-offs
        if len(looks) < 6:
            return
        # runs of the same units shape / the same tens shape, in time order
        uruns, truns = [], []
        for i, (t, tc, uc, _k) in enumerate(looks):
            if not uruns or uruns[-1]["c"] != uc:
                uruns.append({"c": uc, "i0": i, "i1": i})
            uruns[-1]["i1"] = i
            if tc is not None and (not truns or truns[-1]["c"] != tc):
                truns.append({"c": tc, "i0": i, "i1": i})
            if tc is not None:
                truns[-1]["i1"] = i
        lab, ok = {}, True

        def put(c, d, table):
            nonlocal ok
            if table.setdefault(c, d) != d:
                ok = False

        # tens: 5, 4, 3, ... (a tens-less look = under 10: units only)
        tl = {}
        if looks[0][1] is None or len(truns) == 0:
            return
        for k, r in enumerate(truns):
            if k + 5 - 1 < 0:
                return
            put(r["c"], str(5 - k), tl)
        # units: anchors at tens changes (both looks close together)
        anchors = []                                         # (units run index before, after)
        ridx = {}
        for ri, r in enumerate(uruns):
            for i in range(r["i0"], r["i1"] + 1):
                ridx[i] = ri
        for r0, r1 in zip(truns, truns[1:]):
            i = r1["i0"]
            if i > 0 and looks[i - 1][1] == r0["c"] and looks[i][0] - looks[i - 1][0] <= ANCHOR_GAP_S:
                anchors.append(ridx[i - 1])
        if not anchors:
            return
        ul, base = {}, anchors[0]                            # the units run `base` is a 0
        for ri, r in enumerate(uruns):
            put(r["c"], str((0 - (ri - base)) % 10), ul)
        for a_ in anchors:                                   # every anchor must agree with the chain
            if (a_ - base) % 10 != 0:
                ok = False
        if not ok:
            self.log("buff glyph seconds: the run does not fit a 1 s countdown, dropped")
            return
        rep_keys = {}
        for lk in looks:
            for c, k in zip([x for x in lk[1:3] if x is not None], lk[3]):
                rep_keys.setdefault(c, []).append(k)
        # a shape is both tens and units sometimes: the digits must agree
        digit = {}
        for table in (tl, ul):
            for c, d in table.items():
                if digit.setdefault(c, d) != d:
                    self.log("buff glyph seconds: a shape got two digits, dropped")
                    return
        for c, d in digit.items():
            if support.get(c, 0) >= LEARN_LOOKS:
                self._commit("seconds", d, rep_keys.get(c, []), t_now)

    def _learn_minutes(self, run, E, t_now):
        """The small digit. (1) The shape directly before the big digits is 1 (floor of 60..119 s / 60). (2) A shape X
        that changes to an already learned digit k, all of 1..k-1 learned, was k+1: only a recast raises the digit
        and it would have to come from a smaller (learned) one."""
        bp = self._big_phase(run)
        if bp is not None:
            j, tail = bp[0] - 1, []
            while j >= 0 and run[j]["sec"] == [] and run[j]["min"] and (not tail or tail[-1]["t"] - run[j]["t"] <= STEP_GAP_S):
                tail.append(run[j])
                j -= 1
            tail.reverse()
            if tail:
                ids, _n = _cluster([o["min"] for o in tail], MIN_SAME)
                last = ids[-1]
                same = [o for o, c in zip(tail, ids) if c == last]
                self._commit_minutes(same, [0] * len(same), {0: 1}, t_now)
        spans, cur = [], []
        for o in run:
            if o["sec"] == [] and o["min"] and (not cur or o["t"] - cur[-1]["t"] <= STEP_GAP_S):
                cur.append(o)
            else:
                if cur:
                    spans.append(cur)
                cur = [o] if (o["sec"] == [] and o["min"]) else []
        if cur:
            spans.append(cur)
        for sp in spans:
            ids, _n = _cluster([o["min"] for o in sp], MIN_SAME)
            runs = []                                        # [class, first index, last index] of consecutive same shapes
            for i, c in enumerate(ids):
                if runs and runs[-1][0] == c:
                    runs[-1][2] = i
                else:
                    runs.append([c, i, i])
            for r0, r1 in zip(runs, runs[1:]):
                # a real digit step is between two LONG steady runs (a digit lasts a minute); a brief garbled shape (a
                # translucent icon over a bright scene) next to a learned digit must not be labelled "one more"
                if r0[2] - r0[1] + 1 < MIN_STEP_RUN or r1[2] - r1[1] + 1 < MIN_STEP_RUN:
                    continue
                if not MIN_DIGIT_S[0] <= sp[r1[2]]["t"] - sp[r1[1]]["t"] <= MIN_DIGIT_S[1]:
                    continue                                 # the learned digit k must have shown for about a whole minute
                                                             # (a jitter variant of the same shape lasts a few seconds)
                k = self.classify("minutes", sp[r1[1]]["min"])
                if k is None or self.classify("minutes", sp[r0[2]]["min"]) is not None:
                    continue
                kk = int(k)
                if all(str(d) in self.known("minutes") for d in range(1, kk)):
                    self._commit_minutes(sp[r0[1]: r0[2] + 1], [0] * (r0[2] - r0[1] + 1), {0: kk + 1}, t_now)

    def _commit_minutes(self, looks, ids, lab, t_now):
        support, reps = {}, {}
        for o, c in zip(looks, ids):
            support[c] = support.get(c, 0) + 1
            reps.setdefault(c, []).append(o["min"])
        for c, v in lab.items():
            if support.get(c, 0) >= LEARN_LOOKS and v >= 1:
                self._commit("minutes", str(v), reps[c], t_now)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--icons" not in args:
        print(__doc__)
        sys.exit(1)
    i = args.index("--icons")
    folder, frames = args[i + 1], args[:i] + args[i + 2:]
    ids = sorted(p.stem for p in Path(folder).glob("*.png"))
    icons = load_icons(folder, ids)
    for f in frames:
        im = Image.open(f).convert("RGB")
        s = im.width / REF_W
        det = {}
        got = find(im.crop(strip_box(im.size)), load_icons(folder, ids, s) if abs(s - 1) > 0.02 else icons, det)
        print(Path(f).name, sorted(got), {k: v for k, v in det.items() if k in got})
