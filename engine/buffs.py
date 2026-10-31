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
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from base import perf

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
