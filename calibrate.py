"""First-run calibration: cut the screen templates from the player's own game frame.

The templates (WORLD button, "MINI MAP" header, name tag) and the digit glyphs come from the game's
own pixels, so they are never shipped (Nexon imagery). This module finds them in a live frame.

- WORLD button: exactly 34x10 px whose main color is (221, 153, 17) (measured 2026-10-23 on 47
  frames: the button's bounding box in that color is the template's box).
- "MINI MAP" header: 50x16, at (left border + 2, button y - 4). The minimap window's left border on
  the button's row is white followed by (85,119,153), (153,187,204) (same on every frame/map).
- Name tag: the player clicks their own name; the tag's text box is grown from the click.
- EXP / level digits: the player types what the bar shows (glyphs.learn). Missing digits are
  learned later by the tracker on its own (sources/screen.py solve_unknowns, level-up badge).

Command line (development):
  python calibrate.py check FRAME.png     report what would be found, save nothing
"""
import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
from pathlib import Path

import numpy as np
from PIL import Image

import layout
import minimap
import portrait

WORLD_RGB = (221, 153, 17)
WORLD_SIZE = (34, 10)
HEADER_SIZE = (50, 16)
HEADER_DY = -4
BORDER = [(255, 255, 255), (85, 119, 153), (153, 187, 204)]
SEARCH = (0, 0, 900, 400)            # where the minimap is expected during calibration
BACKING_MAX = 130                    # name tag's dark backing: row/col median gray at most this
TAG_TEXT_H = 15                      # name tag text height incl. descenders (measured 2026-10-23)
TAG_GAP = 6                          # widest blank gap between letters of one name, px


def find_world(img, area=SEARCH):
    """(x, y) of the WORLD button's top-left, found by its color, or None."""
    a = np.asarray(img.convert("RGB"))
    x1, y1, x2, y2 = area
    sub = a[y1:min(y2, a.shape[0]), x1:min(x2, a.shape[1])]
    m = (sub == WORLD_RGB).all(axis=2)
    w, h = WORLD_SIZE
    # candidate top-left corners: a mask pixel whose row run and column run start there
    ys, xs = np.nonzero(m)
    best = None
    for y, x in zip(ys, xs):
        if (x > 0 and m[y, x - 1]) or (y > 0 and m[y - 1, x]):
            continue
        box = m[y:y + h, x:x + w]
        if box.shape != (h, w):
            continue
        fill = box.mean()
        outside = m[max(0, y - 1):y + h + 1, max(0, x - 1):x + w + 1].sum() - box.sum()
        if fill > 0.6 and outside == 0 and (best is None or fill > best[0]):
            best = (fill, int(x) + x1, int(y) + y1)
    return (best[1], best[2]) if best else None


def find_header(img, wx, wy):
    """(x, y) of the "MINI MAP" header's top-left (full minimap), or None."""
    a = np.asarray(img.convert("RGB")).astype(int)
    y = wy + 4
    if not 0 <= y < a.shape[0]:
        return None
    row = a[y]
    for x in range(max(0, wx - 900), wx - 3):
        if all((row[x + i] == c).all() for i, c in enumerate(BORDER)):
            hx, hy = x + 2, wy + HEADER_DY
            if hy < 0 or hx + HEADER_SIZE[0] > a.shape[1]:
                return None
            return hx, hy
    return None


def nametag_box(img, drag):
    """Tight box around the name's text pixels inside a box the player dragged, padded by 1 px
    (the same shape `portrait.learn` cuts by hand). None if there's no text in it."""
    x1, y1, x2, y2 = (int(v) for v in drag)
    x1, x2 = sorted((max(0, x1), min(img.width, x2)))
    y1, y2 = sorted((max(0, y1), min(img.height, y2)))
    g = np.asarray(img.convert("L").crop((x1, y1, x2, y2))).astype(int)
    # a loose box catches bright background: trim edge rows/cols that aren't on the tag's dark
    # backing (their median is bright). On dark maps nothing is trimmed, and nothing needs to be.
    rows_ok = np.nonzero(np.median(g, axis=1) <= BACKING_MAX)[0]
    if len(rows_ok):
        g, y1 = g[rows_ok[0]:rows_ok[-1] + 1], y1 + rows_ok[0]
    cols_ok = np.nonzero(np.median(g, axis=0) <= BACKING_MAX)[0]
    if len(cols_ok):
        g, x1 = g[:, cols_ok[0]:cols_ok[-1] + 1], x1 + cols_ok[0]
    bright = g > portrait.THRESHOLD
    if bright.sum() < 20:
        return None
    # the name is one line of text at most TAG_TEXT_H rows tall: keep the band with the most
    # text pixels (drops bits of the character sprite or other tags caught in the box)
    if bright.shape[0] > TAG_TEXT_H:
        per_row = bright.sum(axis=1)
        sums = np.convolve(per_row, np.ones(TAG_TEXT_H, int), "valid")
        top = int(np.argmax(sums))
        bright = bright[top:top + TAG_TEXT_H]
        y1 += top
    # names have no spaces: split text columns at gaps wider than TAG_GAP, keep the biggest run
    cols = np.nonzero(bright.any(axis=0))[0]
    if not len(cols):
        return None
    runs, start = [], cols[0]
    for a, b in zip(cols, cols[1:]):
        if b - a > TAG_GAP:
            runs.append((start, a))
            start = b
    runs.append((start, cols[-1]))
    c0, c1 = max(runs, key=lambda r: bright[:, r[0]:r[1] + 1].sum())
    bright = bright[:, c0:c1 + 1]
    x1 += int(c0)
    rows = np.nonzero(bright.any(axis=1))[0]
    cols = np.nonzero(bright.any(axis=0))[0]
    box = (x1 + cols[0] - 1, y1 + rows[0] - 1, x1 + cols[-1] + 2, y1 + rows[-1] + 2)
    if box[2] - box[0] < 10 or box[3] - box[1] < 6:
        return None
    return box


def nametag_preview(img, box, scale=3):
    """The text pixels that would be saved, enlarged, for the player to confirm."""
    g = np.asarray(img.convert("L").crop(box))
    t = Image.fromarray(((g > portrait.THRESHOLD) * 255).astype(np.uint8))
    return t.resize((t.width * scale, t.height * scale), Image.NEAREST)


def cut_templates(img, save=True):
    """Find the WORLD button and header in this frame and save both templates.
    Returns {"world": (x, y) | None, "header": (x, y) | None}."""
    out = {"world": None, "header": None}
    w = find_world(img)
    if not w:
        return out
    out["world"] = w
    h = find_header(img, *w)
    out["header"] = h
    if save:
        g = img.convert("L")
        minimap.WORLD_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
        g.crop((w[0], w[1], w[0] + WORLD_SIZE[0], w[1] + WORLD_SIZE[1])).save(minimap.WORLD_TEMPLATE)
        minimap._world = None
        if h:
            g.crop((h[0], h[1], h[0] + HEADER_SIZE[0], h[1] + HEADER_SIZE[1])).save(layout.MINIMAP_TEMPLATE)
            layout._tpl = None
    return out


def save_nametag(img, box):
    g = np.asarray(img.convert("L").crop(box))
    t = (g > portrait.THRESHOLD).astype(np.uint8) * 255
    portrait.TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(t).save(portrait.TEMPLATE)


def templates_ready():
    return minimap.WORLD_TEMPLATE.exists() and layout.MINIMAP_TEMPLATE.exists()


def needs_setup():
    """True until the minimap templates and some EXP digits exist (first run)."""
    import glyphs
    return not templates_ready() or len(glyphs.load()) < 3


# ------------------------------------------------------------------ status bar digits
def hud_crops(img, cfg):
    """{"exp": crop, "level": crop} at the spots the tracker reads, for this frame's size."""
    ref = tuple(cfg.get("source_resolution") or (1920, 1080))
    out = {}
    for name in ("exp", "level"):
        rc = cfg["regions"][name]
        box = layout.place(img, rc["box"], ref, layout.anchor_for(name, rc), name=name)
        out[name] = img.crop(tuple(box))
    return out


def exp_table():
    import json
    from paths import APP
    t = json.loads((APP / "exp_table_classic.json").read_text(encoding="utf-8"))
    return {int(k): v for k, v in t.items() if not k.startswith("_")}


def check_typed(crops, cfg, exp_text, level_text):
    """Check what the player typed against the crops and the EXP table.
    Returns (ok, message). Saves nothing."""
    import glyphs
    import parse
    exp_text = "".join(exp_text.split())
    try:
        level = int(level_text.strip())
    except ValueError:
        return False, "Type your level as a number, like 12."
    need = exp_table().get(level)
    if not need:
        return False, f"Level {level} isn't in the EXP table (1-99)."
    cur, _, pct = parse.parse_exp(exp_text)
    if cur is None or pct is None:
        return False, "Type the EXP like it's shown, for example 1,234[56.78%]."
    if abs(cur * 100 / need - pct) > 0.0101:
        return False, (f"At level {level}, {cur:,} EXP would be {cur * 100 / need:.2f}%, not "
                       f"{pct:.2f}%. Check the numbers and the level.")
    rc = cfg["regions"]
    n_exp = sum(k == "glyph" for k, _ in glyphs.segment(crops["exp"], rc["exp"].get("white_min", 230)))
    n_typed = len([c for c in exp_text if c not in "[] "])
    if n_exp != n_typed:
        return False, (f"The bar shows {n_exp} characters but you typed {n_typed}. "
                       "Include commas, the dot and the % sign.")
    n_lv = sum(k == "glyph" for k, _ in glyphs.segment(crops["level"], rc["level"].get("white_min", 240)))
    if n_lv != len(str(level)):
        return False, "The level badge doesn't match that level. Is the status bar fully visible?"
    return True, "Looks right."


def learn_typed(crops, cfg, exp_text, level_text):
    """Teach the digit shapes from the checked text. Returns how many new shapes were learned."""
    import glyphs
    rc = cfg["regions"]
    exp_text = "".join(exp_text.split())
    new = glyphs.learn(crops["exp"], exp_text, rc["exp"].get("white_min", 230))
    new_lv = glyphs.learn(crops["level"], str(int(level_text)), rc["level"].get("white_min", 240),
                          rc["level"].get("templates"))
    return len(new) + len(new_lv)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "check":
        im = Image.open(sys.argv[2]).convert("RGB")
        print(cut_templates(im, save=False))
    else:
        print(__doc__)
