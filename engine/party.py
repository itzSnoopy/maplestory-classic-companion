"""party.py - the party box under the minimap: who's in the party, their HP %, who's on your map.

The game shows a small translucent box with one row per party member (up to 6), measured
2026-10-23 at 1920x1080 (box at x 218-410, y 218-252 with the minimap at the top-left):

  [icon] Name ............ [HP bar]
  icon   star = party leader, small sun = member
  name   black text = on your map, grey text (darkest pixel ~55) = elsewhere / offline
  bar    black outline, white border, 63x7 px interior; red fill = HP, dark grey = empty.
         Members on another map get an empty bar (the game doesn't send their HP).
  rows   17 px apart

The UI is drawn at a fixed pixel size (layout.py), so the bar shape is the same at every window
size; it's *found* by its outline anywhere in the top-left area, not at a fixed spot.
HP % comes from the fill (1 px = 1.6 %); names from Tesseract once per row, snapped to names we
know (your own from the Stat window, partners' from the party link: components/party/partylink.py).

  python -m engine.party FRAME [FRAME...]     print the rows of saved frames (dev check)
"""
import difflib
import sys
import time

import numpy as np
from PIL import Image

SEARCH = (0, 0, 900, 600)       # top-left area searched for the box (game pixels)
INNER_W = 63                    # bar interior width (white border row length)
INNER_H = 7                     # rows between the white top and bottom border lines
PITCH = 17                      # px between rows
MAX_ROWS = 6
NAME_DX = (-104, -6)            # name text, relative to the bar interior's left x
NAME_DY = (-3, 11)
ICON_DX = (-122, -105)


def search_box(size):
    W, H = size
    return (0, 0, min(W, SEARCH[2]), min(H, SEARCH[3]))


def _white(a):
    return a.min(axis=-1) >= 200 if a.size else np.zeros(a.shape[:2], bool)


def _runs(row):
    """(start, end) of runs of True in a 1-D bool array."""
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1))


def find_bars(a):
    """[(x0, y0)] of party HP bars in RGB array `a`: x0 = first interior column, y0 = the white
    top border row. Checks the white bottom border 8 rows down and the black outline around."""
    H, W = a.shape[:2]
    # cheap first pass on the red channel: only rows with a long bright line can be a border;
    # the full white mask is built just around those rows (the whole area costs ~6 ms)
    red = (a[..., 0] >= 200).sum(axis=1)
    cand = np.nonzero(red >= INNER_W - 2)[0]
    wh = np.zeros((H, W), bool)
    need = sorted({r for y in cand for r in range(y - 1, y + INNER_H + 3) if 0 <= r < H})
    if need:
        wh[need] = _white(a[need])
    out = []
    counts = wh.sum(axis=1)
    for y in np.nonzero(counts >= INNER_W - 2)[0]:
        y = int(y)
        if not 1 <= y < H - INNER_H - 2:
            continue
        for x0, x1 in _runs(wh[y]):
            n = x1 - x0 + 1
            if not (INNER_W - 2 <= n <= INNER_W + 1) or x0 < 1 or x1 + 1 >= W:
                continue                            # wrong length, or the side border is cut off
            yb = y + INNER_H + 1
            if wh[yb, x0:x1 + 1].mean() < 0.9:              # bottom border
                continue
            if not (wh[y + 1:yb, x0 - 1].mean() > 0.8 and wh[y + 1:yb, x1 + 1].mean() > 0.8):
                continue                                     # side borders
            dark = a[[y - 1, yb + 1], x0:x1 + 1].astype(np.int16).max(-1)
            if dark.mean() > 60:                             # black outline above / below
                continue
            out.append((int(x0), int(y)))
    return out


def fill_pct(a, x0, y0):
    """HP % from the red fill of the bar at (x0, y0); 0 for an empty bar."""
    seg = a[y0 + 3:y0 + 7, x0:x0 + INNER_W].astype(np.int16)
    r, g, b = seg[..., 0], seg[..., 1], seg[..., 2]
    red = ((r > 140) & (g < 70) & (b < 70)).mean(axis=0) > 0.5
    reds = np.nonzero(red)[0]
    n = int(reds[-1]) + 1 if reds.size else 0
    return round(100.0 * n / INNER_W, 1)


def _name_area(a, x0, y0):
    H, W = a.shape[:2]
    xa, xb = max(0, x0 + NAME_DX[0]), max(0, x0 + NAME_DX[1])
    ya, yb = max(0, y0 + NAME_DY[0]), min(H, y0 + NAME_DY[1])
    return a[ya:yb, xa:xb]


def name_ink(a, x0, y0):
    """(on_map, mask). Black text (on your map) has pixels near 0; grey text bottoms out ~50."""
    c = _name_area(a, x0, y0).astype(np.int16)
    if c.size == 0:
        return False, None
    lum = c.mean(-1)
    sat = c.max(-1) - c.min(-1)
    black = (lum < 32) & (sat < 40)
    if black.sum() >= 12:
        return True, black
    grey = (lum < 100) & (sat < 35)
    return False, grey if grey.sum() >= 12 else None


def leader(a, x0, y0):
    """The star icon (leader) is bigger and more orange than the member's small sun."""
    H, W = a.shape[:2]
    c = a[max(0, y0 - 2):min(H, y0 + 11), max(0, x0 + ICON_DX[0]):max(0, x0 + ICON_DX[1])]
    c = c.astype(np.int16)
    if c.size == 0:
        return False
    gold = (c[..., 0] > 200) & (c[..., 1] > 110) & (c[..., 0] - c[..., 2] > 100)
    return int(gold.sum()) >= 25        # star ~37 px, member sun ~14


def group_rows(bars):
    """The longest column of bars with the same x and a 17 px pitch (the party box)."""
    best = []
    for x0, y0 in bars:
        rows = [(x0, y0)]
        y = y0
        while len(rows) < MAX_ROWS:
            nxt = [b for b in bars if abs(b[0] - x0) <= 1 and b[1] == y + PITCH]
            if not nxt:
                break
            rows.append(nxt[0])
            y += PITCH
        if len(rows) > len(best):
            best = rows
    return best


def read(img, origin=(0, 0)):
    """Rows of the party box in a PIL image (a crop of the frame at `origin`), top to bottom:
    [{x, y, on_map, hp (None if not on your map), leader, mask}] or [] when there's no box.
    x/y are frame coordinates of the bar."""
    a = np.asarray(img.convert("RGB"))
    rows = []
    for x0, y0 in group_rows(find_bars(a)):
        on_map, mask = name_ink(a, x0, y0)
        hp = fill_pct(a, x0, y0)
        rows.append({"x": x0 + origin[0], "y": y0 + origin[1], "on_map": on_map,
                     "hp": hp if on_map else None, "leader": leader(a, x0, y0), "mask": mask})
    return rows


class Finder:
    """read() with memory: once the box is found, each look checks only that spot (~1 ms); the
    whole top-left area (~8 ms) is searched when the box isn't there, at most every FULL_EVERY_S
    while no box is seen (most of the time when you're not in a party)."""
    FULL_EVERY_S = 5.0

    def __init__(self):
        self.last = None            # frame (x, y) of the first bar
        self.full_t = 0.0

    def read(self, img, origin=(0, 0), now=None):
        now = now or time.time()
        if self.last is not None:
            x, y = self.last[0] - origin[0], self.last[1] - origin[1]
            box = (max(0, x + ICON_DX[0] - 4), max(0, y - 6),
                   min(img.width, x + INNER_W + 6), min(img.height, y + PITCH * MAX_ROWS + 6))
            if box[2] > box[0] and box[3] > box[1]:
                rows = read(img.crop(box), (origin[0] + box[0], origin[1] + box[1]))
                if rows and (rows[0]["x"], rows[0]["y"]) == self.last:
                    return rows
            self.last = None
        elif now - self.full_t < self.FULL_EVERY_S:
            return []
        self.full_t = now
        rows = read(img, origin)
        self.last = (rows[0]["x"], rows[0]["y"]) if rows else None
        return rows


# ---------------------------------------------------------------- names
def _ocr(mask):
    """Tesseract on the text mask (black on white, x4). '' when OCR isn't set up."""
    try:
        from engine import ocr
        if not ocr.READY:
            return ""
        import pytesseract
        g = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8))
        g = g.resize((g.width * 4, g.height * 4), Image.NEAREST)
        from PIL import ImageOps
        g = ImageOps.expand(g, border=12, fill=255)
        cfg = "--psm 7 -c tessedit_char_whitelist=" \
              "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        return pytesseract.image_to_string(g, config=cfg).strip()
    except Exception:
        return ""


def snap(text, known):
    """The known name closest to an OCR read (case-insensitive), or None."""
    if not text or not known:
        return None
    low = {k.lower(): k for k in known if k}
    m = difflib.get_close_matches(text.lower(), list(low), n=1, cutoff=0.6)
    return low[m[0]] if m else None


class Names:
    """Row masks -> names. Tesseract runs only when a row's text changed (the box is
    translucent, so a few background pixels flicker: under 15 % of the text pixels counts as the
    same), at most once per row every RETRY_S."""
    RETRY_S = 5.0

    def __init__(self):
        self.rows = {}          # row index -> {"mask", "raw", "t"}

    def name(self, i, mask, known=(), now=None):
        now = now or time.time()
        if mask is None:
            return None
        c = self.rows.get(i)
        same = (c is not None and c["mask"].shape == mask.shape
                and (c["mask"] ^ mask).sum() <= 0.15 * max(1, c["mask"].sum()))
        if not same and (c is None or now - c["t"] >= self.RETRY_S or not c["raw"]):
            c = {"mask": mask, "raw": _ocr(mask), "t": now, "good": (c or {}).get("good")}
            self.rows[i] = c
        if c is None:
            return None
        got = snap(c["raw"], known)
        if got:
            c["good"] = got
        # grey text (member on another map) reads worse: keep the name this row last had
        return got or c.get("good") or c["raw"] or None

    def reset(self):
        """The party changed (row count): row names no longer line up."""
        self.rows = {}


if __name__ == "__main__":
    from engine import ocr
    from base.paths import load_config
    ocr.setup(load_config().get("tesseract"))
    names = Names()
    for p in sys.argv[1:]:
        im = Image.open(p)
        box = search_box(im.size)
        rows = read(im.crop(box), box[:2])
        print(p)
        for i, r in enumerate(rows):
            nm = names.name(i, r["mask"], known=["SnoopySparda", "DSius"])
            print(f"  {nm or '?':16} hp={r['hp']} on_map={r['on_map']} leader={r['leader']} "
                  f"at=({r['x']},{r['y']})")
        if not rows:
            print("  no party box")
