"""panels.py - read short-lived game windows that show long-lived character facts.

  Character Stat window  -> name, job, level, HP/MP max, fame, STR/DEX/INT/LUK, ability points
  Skill window           -> each visible skill's level (names snapped to skills_classic.json), SP
  Change Channel window  -> world and the highlighted channel (no text read: grid position)

The windows can be dragged anywhere, but the game draws them at a fixed pixel size (they don't
scale with the window size), so everything inside sits at fixed offsets from a few exact palette
colors. Measured 2026-10-23 at 1920x1080 (NOTES.md "UI windows"):

  stat:    pink labels (203,79,130) 44 px wide at ly+0,18,46,64,82,100,118 (NAME..FAME, JOB is 24 tall),
           green labels (138,149,58) 48 wide at lx-2, ly+141+18k (STR, DEX, INT, LUK);
           values at lx+47..lx+134; ABILITY POINT box (white 29x15) at lx+78, ly+225
  skills:  white title bar (187 wide); rows 125x35 at tx+39, ty+94+40k (k=0..5), bg (205,217,227),
           selected row (235,235,235); name text at row top, level 18 px lower; SKILL POINT box
           (white 29x15) at tx+78, ty+344
  channel: cells 66x16 gray (177,177,177) on a 70x22 grid, 8 per row; the highlighted one is
           (54,170,221); world name in the blue header 35 px above the grid

Two stages, so the cost stays near zero while no window is open:
  scan(small)       a 1/4-scale frame (WgcFrames grab step=4, ~0.5 MB): exact palette colors
                    counted in numpy, ~1 ms. Says which windows *might* be open.
  read(frame, ...)  full-resolution frame: verify the geometry exactly, then read the values.

Text in these windows is a pure-black pixel font (every text pixel is (0,0,0), no smoothing), so
numbers are read exactly by glyph lookup (glyphs_ui.json, learned locally like glyphs.json:
taught from the EXP and level the tracker already knows; `python panels.py learn` for a frame
whose values you can see). Words (name, job, skill names, world) go through Tesseract once per
distinct crop and are snapped to known lists. No game art is stored or shipped: only colors,
offsets, and locally learned glyph bitmaps.

  python panels.py FRAME [FRAME...]          what's open and what it says (dev check)
  python panels.py learn FRAME FIELD=TEXT..  teach digit glyphs, e.g. int=77 hp=408/408
"""
import difflib
import functools
import hashlib
import json
import re
import sys

import numpy as np
from PIL import Image, ImageOps

from base.paths import DATA, asset

GLYPHS = DATA / "glyphs_ui.json"
SKILLS_FILE = asset("gamedata/skills_classic.json")
SMALL = 4                      # scale of the scan frame (WgcFrames grab step)

PINK = (203, 79, 130)
GREEN = (138, 149, 58)
ROW = (205, 217, 227)
ROW_SEL = (235, 235, 235)
CELL = (177, 177, 177)
CELL_SEL = (54, 170, 221)

# stat window, relative to the NAME label's top-left
STAT_PINK = [("name", 0, 14), ("job", 18, 24), ("level", 46, 14), ("hp", 64, 14), ("mp", 82, 14),
             ("exp", 100, 14), ("fame", 118, 14)]
STAT_GREEN = [("str", 141), ("dex", 159), ("int", 177), ("luk", 195)]
STAT_VAL_X = (47, 134)
STAT_AP = (78, 225, 107, 240)
# skill window, relative to its title bar's top-left
SKILL_W = 187
SKILL_ROW = (39, 94, 125, 35, 40)   # x, y, w, h, pitch
SKILL_ROWS = 6
SKILL_SP = (78, 344, 107, 359)
ICON_X = (3, 37)                    # the skill's icon box, left of its row (same rows)
# channel window
CELL_W, CELL_H, CELL_DX, CELL_DY, PER_ROW = 66, 16, 70, 22, 8
WORLD_BOX = (28, -37, 200, -19)     # relative to the grid's top-left


# ------------------------------------------------------------------ helpers
def _eq(a, rgb):
    return (a[..., 0] == rgb[0]) & (a[..., 1] == rgb[1]) & (a[..., 2] == rgb[2])


def _arr(img):
    return np.asarray(img.convert("RGB")) if hasattr(img, "convert") else img


def small_of(img, step=SMALL):
    """The scan frame from a full frame (same pixels a step=4 grab copies)."""
    a = _arr(img)
    return a[::step, ::step]


def _runs(v):
    """[(start, end)] of True runs in a 1-D bool array."""
    d = np.diff(np.concatenate(([0], v.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


# ------------------------------------------------------------------ stage 1: scan
def scan(small):
    """Which windows might be open: {'stat'|'skills'|'channel': (x0, y0, x1, y1) full-frame box
    around the colors seen}. Cheap; false hits only cost a full-resolution check."""
    a = _arr(small)
    out = {}
    pink = _eq(a, PINK)
    if pink.sum() >= 40:
        col = pink.sum(0).argmax()
        if len(_runs(pink[:, col])) >= 4:            # several labels stacked in one column
            ys = np.flatnonzero(pink[:, col])
            out["stat"] = _box(col - 8, ys.min() - 10, col + 45, ys.max() + 40, pad=16)
    row = _eq(a, ROW)
    if row.sum() >= 250:
        cols = row.sum(0)
        col = cols.argmax()
        if cols[col] >= 16:                          # at least two skill rows in one column
            ys = np.flatnonzero(row[:, col])
            # `col` can be anywhere across the 125 px rows: cover the whole window either way
            out["skills"] = _box(col - 45, ys.min() - 30, col + 45, ys.max() + 30, pad=20)
    sel = _eq(a, CELL_SEL)
    if 20 <= sel.sum() <= 200:
        cells = _eq(a, CELL)
        if cells.sum() >= 400:
            ys, xs = np.nonzero(sel)
            out["channel"] = _box(xs.min() - 150, ys.min() - 60, xs.max() + 150, ys.max() + 60, pad=0)
    return out


def _box(x0, y0, x1, y1, pad):
    s = SMALL
    return (int(x0 * s - pad), int(y0 * s - pad), int(x1 * s + pad), int(y1 * s + pad))


# ------------------------------------------------------------------ black pixel-font glyphs
def _load_glyphs():
    try:
        return json.loads(GLYPHS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_glyphs = {"t": None}


def glyph_table():
    if _glyphs["t"] is None:
        _glyphs["t"] = _load_glyphs()
    return _glyphs["t"]


def segments(a):
    """Glyph keys left to right in an RGB crop: text = pure-black pixels, glyphs split on empty
    columns, each trimmed to its own rows (key = height + row patterns)."""
    m = a.max(-1) < 40 if a.ndim == 3 else a
    keys = []
    for x0, x1 in _runs(m.any(0)):
        g = m[:, x0:x1]
        ys = np.flatnonzero(g.any(1))
        g = g[ys[0]: ys[-1] + 1]
        keys.append((int(x0), f"{ys[0]}:" + "/".join("".join("#" if v else "." for v in r) for r in g)))
    return keys


def _shape(key):
    return key.split(":", 1)[1]


def read_text(a):
    """Exact read of black text, spaces where the gap is wide. Unknown glyphs -> '?'."""
    t = glyph_table()
    out, last_end = [], None
    for x0, key in segments(a):
        k = _shape(key)
        w = len(k.split("/")[0])
        if last_end is not None and x0 - last_end >= 5:      # digit gap is 4 ("1" then "4"), a space 5+
            out.append(" ")
        out.append(t.get(k, "?"))
        last_end = x0 + w
    return "".join(out)


def learn(a, text):
    """Label the glyphs in crop `a` with `text` (spaces ignored). Returns {char: shape} added."""
    keys = [_shape(k) for _, k in segments(a)]
    chars = [c for c in text if c != " "]
    if len(keys) != len(chars):
        raise ValueError(f"{len(keys)} glyphs but {len(chars)} characters in {text!r}")
    t, new = glyph_table(), {}
    for k, c in zip(keys, chars):
        if t.get(k, c) != c:
            raise ValueError(f"glyph already labeled {t[k]!r}, not {c!r}")
        if k not in t:
            new[k] = c
    if new:
        t.update(new)
        GLYPHS.write_text(json.dumps(t, indent=1, sort_keys=True), encoding="utf-8")
    return {c: k for k, c in new.items()}


def try_learn(a, truth):
    """Teach unknown glyphs from a value known elsewhere (the HUD's EXP/level). Only when every
    glyph that's already known agrees with `truth`. Returns True if something new was learned."""
    got = read_text(a).replace(" ", "")
    want = truth.replace(" ", "")
    if "?" not in got or len(got) != len(want):
        return False
    if any(g != "?" and g != w for g, w in zip(got, want)):
        return False
    try:
        return bool(learn(a, truth))
    except ValueError:
        return False


# ------------------------------------------------------------------ words (Tesseract, cached)
_ocr_cache = {}


def _ocr_words(img_rgb, white=False, psm=7):
    """Tesseract on a black-on-light (or white-on-dark, white=True) crop; cached by crop hash."""
    h = hashlib.md5(img_rgb.tobytes() + bytes([white, psm])).hexdigest()
    if h in _ocr_cache:
        return _ocr_cache[h]
    try:
        from engine import ocr
        import pytesseract
        if not ocr.READY:
            return ""
        a = np.asarray(img_rgb)
        m = a.min(-1) >= 225 if white else a.max(-1) < 40
        g = Image.fromarray(np.where(m, 0, 255).astype(np.uint8))
        g = g.resize((g.width * 4, g.height * 4), Image.NEAREST)
        g = ImageOps.expand(g, border=12, fill=255)
        text = pytesseract.image_to_string(g, config=f"--psm {psm}").strip()
    except Exception:
        text = ""
    if len(_ocr_cache) > 500:
        _ocr_cache.clear()
    _ocr_cache[h] = text
    return text


# ------------------------------------------------------------------ skill names
_skills = {"list": None}


def skills():
    if _skills["list"] is None:
        try:
            d = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))["skills"]
        except (OSError, ValueError, KeyError):
            d = {}
        _skills["list"] = [{"id": k, **v} for k, v in d.items()]
    return _skills["list"]


def _sq(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


@functools.lru_cache(maxsize=512)
def _snap_cached(text):
    return tuple(_snap(text))


def snap_skill(text):
    """See _snap; cached (the same names are read every look while the window is open)."""
    return list(_snap_cached(text or ""))


def _snap(text):
    """OCR'd skill name ('Improved WP Reco...', 'hax MP Increase') -> the candidate skills: every
    skill scoring within 0.05 of the best (>= 0.8). Truncated names ('...') match by prefix.
    Several candidates = a tie (same name in several jobs, or 'WP' fitting HP and MP); the
    caller breaks it with the other skills on the same tab."""
    if not text:
        return []
    trunc = "..." in text or "…" in text
    q = _sq(text.split("...")[0].split("…")[0])
    if len(q) < 3:
        return []
    scored = []
    for s in skills():
        n = _sq(s["name"])
        scored.append((difflib.SequenceMatcher(None, q, n[:len(q)] if trunc else n).ratio(), s))
    best = max((r for r, _ in scored), default=0)
    if best < 0.8:
        return []
    return [s for r, s in scored if r >= best - 0.05 and (r == best or best < 0.999)]


# ------------------------------------------------------------------ stage 2: read
def read(frame, cands=None):
    """Full-resolution frame -> {'stat': {...}, 'skills': {...}, 'channel': {...}} for the windows
    that are really there. `cands` = scan() result (where to look); None = scan this frame."""
    a = _arr(frame)
    if cands is None:
        cands = scan(small_of(a))
    out = {}
    for kind, box in cands.items():
        try:
            r = READERS[kind](a, box)
        except Exception as e:                      # a reader must never break the tracker
            r = None
            print(f"panels: {kind} read failed: {type(e).__name__}: {e}")
        if r:
            out[kind] = r
    return out


# whole window around a reader's "box" point (stat: NAME label, skills: title bar, channel: grid),
# measured at 1920x1080, + a few px margin. The game reopens a window where it was last closed,
# so the screen source grabs these spots at full size on every look (panel_spots.json).
EXTENT = {"stat": (-9, -32, 174, 247), "skills": (-4, -4, 191, 365), "channel": (-6, -66, 562, 144)}
MARGIN = 6


def window_box(kind, r):
    """Full-frame box of the window a read came from."""
    x, y = r["box"]
    e = EXTENT[kind]
    return [x + e[0] - MARGIN, y + e[1] - MARGIN, x + e[2] + MARGIN, y + e[3] + MARGIN]


def read_at(kind, img, origin):
    """Read one window from a crop that starts at `origin` (x, y) in the frame. The result's
    box is in frame coordinates. None if the window isn't (wholly) in the crop."""
    a = _arr(img)
    r = READERS[kind](a, (0, 0, a.shape[1], a.shape[0]))
    if r:
        r["box"] = [r["box"][0] + origin[0], r["box"][1] + origin[1]]
    return r


def _clip(a, box):
    h, w = a.shape[:2]
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    return a[y0: max(y0, min(h, y1)), x0: max(x0, min(w, x1))], x0, y0


def _num(s):
    return int(s) if s and s.isdigit() else None


def read_stat(a, box):
    sub, ox, oy = _clip(a, box)
    pink = _eq(sub, PINK)
    cols = np.flatnonzero(pink.sum(0) >= 30)
    if not len(cols):
        return None
    lx = cols[0]
    rows = np.flatnonzero(pink[:, lx:lx + 44].sum(1) >= 30)
    if not len(rows):
        return None
    ly = rows[0]
    # verify every label where it should be (exact layout); the weakest one is the "fit"
    fracs = []
    for _, dy, h in STAT_PINK:
        f = pink[ly + dy: ly + dy + h, lx: lx + 44]
        fracs.append(float(f.mean()) if f.size else 0.0)
        if fracs[-1] < 0.4:
            return None
    green = (np.abs(sub.astype(np.int16) - GREEN) <= 14).all(-1)     # labels are patterned
    for _, dy in STAT_GREEN:
        g = green[ly + dy: ly + dy + 16, lx - 2: lx + 46]
        if not g.size or g.mean() < 0.08:
            return None
    X, Y = ox + lx, oy + ly
    v0, v1 = X + STAT_VAL_X[0], X + STAT_VAL_X[1]
    raw, crops = {}, {}
    for name, dy, h in STAT_PINK:
        crops[name] = a[Y + dy: Y + dy + (14 if name == "job" else h), v0: v1]
    for name, dy in STAT_GREEN:
        crops[name] = a[Y + dy: Y + dy + 16, v0: v1]
    crops["ap"] = a[Y + STAT_AP[1]: Y + STAT_AP[3], X + STAT_AP[0]: X + STAT_AP[2]]
    for name, c in crops.items():
        if name in ("name", "job"):
            raw[name] = _ocr_words(Image.fromarray(np.ascontiguousarray(c)))
        else:
            raw[name] = read_text(c)
    r = {"raw": raw, "box": [int(X), int(Y)], "_crops": crops, "fit": round(min(fracs), 2)}
    r["name"] = raw["name"] or None
    r["job"] = _snap_job(raw["job"])
    for k in ("level", "fame", "str", "dex", "int", "luk", "ap"):
        r[k] = _num(raw[k].replace(" ", ""))
    for k in ("hp", "mp"):
        m = re.fullmatch(r"(\d+)/(\d+)", raw[k].replace(" ", ""))
        r[k] = [int(m.group(1)), int(m.group(2))] if m else None
    m = re.fullmatch(r"(\d+)\((\d+)%\)", raw["exp"].replace(" ", ""))
    r["exp"] = [int(m.group(1)), int(m.group(2))] if m else None
    return r


def _snap_job(text):
    try:
        from engine import jobread
        return jobread.snap(text)
    except Exception:
        return None


def read_skills(a, box):
    sub, ox, oy = _clip(a, box)
    rowm = _eq(sub, ROW) | _eq(sub, ROW_SEL)
    cols = np.flatnonzero(_eq(sub, ROW).sum(0) >= 30)
    if not len(cols):
        return None
    rx = cols[0]
    # the white title bar above: 187 wide, starts 39 px left of the rows
    tx = rx - SKILL_ROW[0]
    if tx < 0:
        return None
    # rows start exactly 94 + 40k px below the title bar's top (the window's outer white frame
    # sits 4 px above it, so match on row tops, not just "some white line")
    row_tops = {int(y) for y, _ in _runs(rowm[:, rx: rx + SKILL_ROW[2]].mean(1) >= 0.6)}
    white = (sub[:, tx: tx + SKILL_W] == 255).all(-1).mean(1) >= 0.85
    ty = None
    for y in np.flatnonzero(white):
        if any(int(y) + SKILL_ROW[1] + SKILL_ROW[4] * k in row_tops for k in range(SKILL_ROWS)):
            ty = int(y)
            break
    if ty is None:
        return None
    X, Y = ox + tx, oy + ty
    _, ry, rw, rh, pitch = SKILL_ROW
    rows, names = [], []
    for k in range(SKILL_ROWS):
        y = Y + ry + pitch * k
        x = X + SKILL_ROW[0]
        bg = a[y + 2: y + rh - 2, x + 2: x + rw - 2]
        if not (len(bg) and ((_eq(bg, ROW) | _eq(bg, ROW_SEL)).mean() >= 0.5)):
            continue
        name_c = a[y + 1: y + 17, x + 2: x + rw]
        lvl_c = a[y + 18: y + 34, x + 2: x + 60]
        if not (name_c.max(-1) < 40).any():
            continue
        rows.append((k, name_c, lvl_c, a[y: y + rh, X + ICON_X[0]: X + ICON_X[1]]))
    if not rows:
        return None
    out = []
    for k, name_c, lvl_c, icon in rows:
        text = _ocr_words(Image.fromarray(np.ascontiguousarray(name_c)))
        lvl = read_text(lvl_c).replace(" ", "")
        out.append({"row": k, "raw": text, "level": _num(lvl), "raw_level": lvl,
                    "_snap": snap_skill(text), "_icon": icon})
    # one tab = one job: a name several jobs share (Improved MP Recovery: Magician and Crusader)
    # goes to the job most of the tab's other skills belong to
    classes = [r["_snap"][0]["class"] for r in out
               if r["_snap"] and len({c["class"] for c in r["_snap"]}) == 1]
    for r in out:
        cands = r.pop("_snap")
        s = None
        if cands:
            top = max(classes.count(c["class"]) for c in cands)
            pick = [c for c in cands if classes.count(c["class"]) == top]
            if len({c["name"] for c in pick}) == 1:    # else still ambiguous: leave unnamed
                s = pick[0]
        r.update(id=s["id"] if s else None, name=s["name"] if s else None,
                 max=s.get("max") if s else None, job=s.get("class") if s else None)
    sp_c = a[Y + SKILL_SP[1]: Y + SKILL_SP[3], X + SKILL_SP[0]: X + SKILL_SP[2]]
    sp = read_text(sp_c).replace(" ", "")
    return {"skills": out, "sp": _num(sp), "raw_sp": sp, "box": [int(X), int(Y)]}


def read_channel(a, box):
    sub, ox, oy = _clip(a, box)
    sel = _eq(sub, CELL_SEL)                         # the cell has "CH.31" in white on it
    runs_y = _runs(sel.sum(1) >= CELL_W // 3)
    runs_x = _runs(sel.sum(0) >= 3)
    if len(runs_y) != 1 or len(runs_x) != 1:
        return None
    (sy0, sy1), (sx0, sx1) = runs_y[0], runs_x[0]
    if abs(sy1 - sy0 - CELL_H) > 1 or abs(sx1 - sx0 - CELL_W) > 1:
        return None
    cell = _eq(sub, CELL) | sel
    # grid origin: the left-most / top-most cells on the selected cell's row / column lattice
    gx = sx0
    while gx - CELL_DX >= 0 and cell[sy0 + 2: sy0 + CELL_H - 2, gx - CELL_DX + 2: gx - CELL_DX + 20].mean() >= 0.5:
        gx -= CELL_DX
    gy = sy0
    while gy - CELL_DY >= 0 and cell[gy - CELL_DY + 2: gy - CELL_DY + CELL_H - 2, sx0 + 2: sx0 + 20].mean() >= 0.5:
        gy -= CELL_DY
    col, row = (sx0 - gx) // CELL_DX, (sy0 - gy) // CELL_DY
    # it's a grid only if there are cells around (a lone blue rectangle isn't)
    n = sum(cell[gy + r * CELL_DY + 2: gy + r * CELL_DY + CELL_H - 2,
                 gx + c * CELL_DX + 2: gx + c * CELL_DX + 20].mean() >= 0.5
            for r in range(5) for c in range(PER_ROW)
            if gy + r * CELL_DY + CELL_H <= sub.shape[0] and gx + c * CELL_DX + 20 <= sub.shape[1])
    if n < 8 or col >= PER_ROW:
        return None
    GX, GY = ox + gx, oy + gy
    wc = a[max(0, GY + WORLD_BOX[1]): GY + WORLD_BOX[3], GX + WORLD_BOX[0]: GX + WORLD_BOX[2]]
    world = _ocr_words(Image.fromarray(np.ascontiguousarray(wc)), white=True) if wc.size else ""
    world = re.sub(r"[^A-Za-z]", "", world).title() or None
    return {"channel": int(row * PER_ROW + col + 1), "world": world, "cells": int(n),
            "box": [int(GX), int(GY)]}


READERS = {"stat": read_stat, "skills": read_skills, "channel": read_channel}


# ------------------------------------------------------------------ dev CLI
def _clean(r):
    if isinstance(r, dict):
        return {k: _clean(v) for k, v in r.items() if not k.startswith("_")}
    if isinstance(r, list):
        return [_clean(v) for v in r]
    return r


def _main(argv):
    import time
    try:
        from engine import ocr
        from base.paths import CONFIG
        ocr.setup(json.loads(CONFIG.read_text(encoding="utf-8")).get("tesseract"))
    except Exception:
        pass
    if argv and argv[0] == "learn":
        a = np.asarray(Image.open(argv[1]).convert("RGB"))
        st = read_stat(a, (0, 0, a.shape[1], a.shape[0]))
        sk = read_skills(a, (0, 0, a.shape[1], a.shape[0]))
        for kv in argv[2:]:
            field, text = kv.split("=", 1)
            if field.startswith("skill"):         # skill0=5 (row index)
                k = int(field[5:])
                y0, x0 = sk["box"][1] + SKILL_ROW[1] + SKILL_ROW[4] * k, sk["box"][0] + SKILL_ROW[0]
                c = a[y0 + 18: y0 + 34, x0 + 2: x0 + 60]
            elif field == "sp":
                X, Y = sk["box"]
                c = a[Y + SKILL_SP[1]: Y + SKILL_SP[3], X + SKILL_SP[0]: X + SKILL_SP[2]]
            else:
                c = st["_crops"][field]
            print(field, text, "->", list(learn(c, text)) or "known")
        return
    for p in argv:
        a = np.asarray(Image.open(p).convert("RGB"))
        t0 = time.perf_counter()
        c = scan(small_of(a))
        t1 = time.perf_counter()
        r = read(a, c)
        t2 = time.perf_counter()
        print(f"{p}: scan {1000 * (t1 - t0):.1f} ms -> {sorted(c)}; read {1000 * (t2 - t1):.0f} ms")
        print(json.dumps(_clean(r), indent=1))


if __name__ == "__main__":
    _main(sys.argv[1:])
