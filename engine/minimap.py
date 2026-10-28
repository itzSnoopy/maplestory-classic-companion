"""Map name from the minimap, in either of its two states, wherever the player put it.
(measured 2026-10-23 at 800x600, 1024x768, 1920x1080)

Reference point: the orange **WORLD button** (`templates/minimap_world.png`). It is pixel-identical
in both states and sits at the right end of the minimap's top bar. Found by, in order:
  1. the spot it was at last time (instant),
  2. a search of the top-left area (where the game puts the minimap),
  3. a whole-frame search (the player dragged it away).
  Both searches only fully compare spots where a few distinctive button pixels match.
     When nothing is found, step 3 runs at most once per FULL_SEARCH_EVERY_S.

Then the state:
- **full**: the "MINI MAP" header (`templates/minimap_header.png`) is on the same row, left of the
  button. The title (two lines, street / map, white text on light blue) is config regions.map.box
  shifted by where the header is vs. where it was when the box was set (layout.MINIMAP_REF).
- **untitled** ("compact" view, or anything with the button but no readable name): no map read;
  the tracker keeps the last map it knew. Not seen yet: prepared from the user's description.
- **collapsed**: no header; a single light-gray bar "Victoria Road : Henesys" in black pixel font,
  from the bar's left border to 30 px left of the button. Read with dark-text settings and turned
  into "street\\nmap", so parse.parse_map / maplist.snap see the same two-line format.
"""
import time
from pathlib import Path

import numpy as np
from PIL import Image

from engine import layout

from base.paths import DATA
WORLD_TEMPLATE = DATA / "templates" / "minimap_world.png"
TOP_LEFT_SEARCH = (0, 0, 700, 300)   # usual home of the minimap (frame pixels)
WORLD_MAX = 8.0                      # mean abs gray difference that still counts as the button
HEADER_MAX = layout.MATCH_MAX
FULL_SEARCH_EVERY_S = 60
COLLAPSED_RC = {"psm": 7, "scale": 4, "threshold": 120}
# collapsed bar, relative to the WORLD button's top-left (wx, wy); measured at 800x600
TEXT_TOP, TEXT_BOTTOM, TEXT_RIGHT = -2, 12, -30
BAR_ROW = -3                         # row inside the bar above the text: plain light gray
                                     # (below it, descenders like the "y" in Henesys get in the way)
# full state: header row relative to the button (header y = wy - 4 at every size measured);
# the header-to-button distance follows the minimap's width, which varies by map
HEADER_DY = (-6, -2)
HEADER_DX = (-700, -40)

_world = None
_last = {}                           # frame size -> last (wx, wy)
_last_full_search = {}               # frame size -> time of the last whole-frame search


def _world_tpl():
    global _world
    if _world is None and WORLD_TEMPLATE.exists():
        _world = np.asarray(Image.open(WORLD_TEMPLATE).convert("L"), dtype=np.int16)
    return _world


def _gray(img):
    return np.asarray(img.convert("L"), dtype=np.int16)


def _diff_at(g, t, x, y):
    h, w = t.shape
    if x < 0 or y < 0 or y + h > g.shape[0] or x + w > g.shape[1]:
        return 1e9
    return float(np.abs(g[y:y + h, x:x + w] - t).mean())


def _search(g, t, box):
    x1, y1, x2, y2 = box
    x1, y1 = max(0, x1), max(0, y1)
    sub = g[y1:min(y2, g.shape[0]), x1:min(x2, g.shape[1])]
    d, x, y = layout.best_match(sub, t)
    return d, x + x1, y + y1


def _prefiltered(g, t, box=None):
    """Best spot of t in g (or in box of g): only spots where a few distinctive template pixels
    match (±3) are fully compared. (diff, x, y) or None."""
    off_x = off_y = 0
    if box:
        off_x, off_y = max(0, box[0]), max(0, box[1])
        g = g[off_y:min(box[3], g.shape[0]), off_x:min(box[2], g.shape[1])]
    h, w = t.shape
    H, W = g.shape
    if H < h or W < w:
        return None
    # probe pixels: spread over the template, picking its most unusual gray values
    ys, xs = np.mgrid[0:h, 0:w]
    order = np.argsort(np.abs(t - np.median(t)).ravel())[::-1]
    probes = [(int(ys.ravel()[i]), int(xs.ravel()[i])) for i in order[:6]]
    ok = np.ones((H - h + 1, W - w + 1), bool)
    for py, px in probes:
        ok &= np.abs(g[py:py + H - h + 1, px:px + W - w + 1] - t[py, px]) <= 3
    best = None
    for y, x in zip(*np.nonzero(ok)):
        d = _diff_at(g, t, int(x), int(y))
        if d <= WORLD_MAX and (best is None or d < best[0]):
            best = (d, int(x) + off_x, int(y) + off_y)
    return best


def find_world_button(img, g=None):
    """(x, y) of the WORLD button's top-left, or None."""
    t = _world_tpl()
    if t is None:
        return None
    g = _gray(img) if g is None else g
    last = _last.get(img.size)
    if last and _diff_at(g, t, *last) <= WORLD_MAX:
        return last
    hit = _prefiltered(g, t, TOP_LEFT_SEARCH)
    if not hit:
        now = time.time()
        if now - _last_full_search.get(img.size, 0) < FULL_SEARCH_EVERY_S:
            return None
        _last_full_search[img.size] = now
        hit = _prefiltered(g, t)
        if not hit:
            return None
    _, x, y = hit
    _last[img.size] = (x, y)
    return x, y


def header_offset(g, wx, wy):
    """Full state: (dx, dy) of the 'MINI MAP' header vs. layout.MINIMAP_REF, or None."""
    t = layout._template()
    if t is None:
        return None
    d, x, y = _search(g, t, (wx + HEADER_DX[0], wy + HEADER_DY[0],
                             wx + HEADER_DX[1], wy + HEADER_DY[1] + t.shape[0] + 1))
    if d > HEADER_MAX:
        return None
    return x - layout.MINIMAP_REF[0], y - layout.MINIMAP_REF[1]


def collapsed_box(img, wx, wy):
    """Text box of the collapsed bar, from its left border to just before the [-] button."""
    rgb = img.convert("RGB").load()
    y = max(0, min(wy + BAR_ROW, img.height - 1))
    x = wx + TEXT_RIGHT
    while x > 0 and sum(rgb[x - 1, y]) > 600:     # walk left over the light bar
        x -= 1
    box = [x + 2, max(0, wy + TEXT_TOP), wx + TEXT_RIGHT, wy + TEXT_BOTTOM]
    return box if box[2] - box[0] >= 10 else None


def locate(img, rc, res=None):
    """('full'|'collapsed', box, rc to read with); ('untitled', None, None) if the minimap is there
    but shows no name (the "compact" view); (None, None, None) if no minimap."""
    g = _gray(img)
    w = find_world_button(img, g)
    if w is None:
        return None, None, None
    off = header_offset(g, *w)
    if off is not None:
        b = rc["box"]
        box = [b[0] + off[0], b[1] + off[1], min(img.width, b[2] + off[0]), b[3] + off[1]]
        return "full", box, rc
    box = collapsed_box(img, *w)
    if box:
        return "collapsed", box, {**COLLAPSED_RC, **rc.get("collapsed", {})}
    return "untitled", None, None


def full_offset(img):
    """(dx, dy) for boxes anchored "minimap" (full state), or None. Used by layout.place."""
    g = _gray(img)
    w = find_world_button(img, g)
    return header_offset(g, *w) if w else None


def to_two_lines(text):
    """'Victoria Road : Henesys' -> 'Victoria Road\\nHenesys' (first colon splits).
    No colon -> '': the collapsed bar always reads 'Street : Map', so anything else isn't a
    title (e.g. the compact view, which has the button row but no name)."""
    line = " ".join(text.split())
    if ":" in line:
        street, name = line.split(":", 1)
        return f"{street.strip(' -.')}\n{name.strip(' -.')}"
    return ""


def make_world_template(frame_path, box):
    im = Image.open(frame_path).convert("L").crop(tuple(box))
    WORLD_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    im.save(WORLD_TEMPLATE)
    print(f"saved {WORLD_TEMPLATE} {im.size}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 7 and sys.argv[1] == "learn-world":
        make_world_template(sys.argv[2], [int(v) for v in sys.argv[3:7]])
    else:
        print("usage: python -m engine.minimap learn-world FRAME.png X1 Y1 X2 Y2")
