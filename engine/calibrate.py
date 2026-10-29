"""First-run calibration: cut the screen templates from the player's own game frame.

The templates (WORLD button, "MINI MAP" header) and the digit glyphs come from the game's
own pixels, so they are never shipped (Nexon imagery). This module finds them in a live frame.

- WORLD button: exactly 34x10 px whose main color is (221, 153, 17) (measured 2026-10-23 on 47
  frames: the button's bounding box in that color is the template's box).
- "MINI MAP" header: 50x16, at (left border + 2, button y - 4). The minimap window's left border on
  the button's row is white followed by (85,119,153), (153,187,204) (same on every frame/map).
- EXP / level digits: the player types what the bar shows (glyphs.learn). Missing digits are
  learned later by the tracker on its own (sources/screen.py solve_unknowns, level-up badge).

Command line (development):
  python -m engine.calibrate check FRAME.png     report what would be found, save nothing
"""
from base import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
from pathlib import Path

import numpy as np
from PIL import Image

from engine import layout
from engine import minimap

WORLD_RGB = (221, 153, 17)
WORLD_SIZE = (34, 10)
HEADER_SIZE = (50, 16)
HEADER_DY = -4
BORDER = [(255, 255, 255), (85, 119, 153), (153, 187, 204)]
SEARCH = (0, 0, 900, 400)            # where the minimap is expected during calibration


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


def templates_ready():
    return minimap.WORLD_TEMPLATE.exists() and layout.MINIMAP_TEMPLATE.exists()


def needs_setup():
    """True until the minimap templates and some EXP digits exist (first run)."""
    from engine import glyphs
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


def check_typed(crops, cfg, exp_text, level_text, exp_needed):
    """Check what the player typed against the crops and the EXP table.
    exp_needed(level) -> EXP needed at that level, or None (gamedata.translate.exp_needed).
    Returns (ok, message). Saves nothing."""
    from engine import glyphs
    from engine import hudtext
    exp_text = "".join(exp_text.split())
    try:
        level = int(level_text.strip())
    except ValueError:
        return False, "Type your level as a number, like 12."
    need = exp_needed(level)
    if not need:
        return False, f"Level {level} isn't in the EXP table (1-99)."
    cur, _, pct = hudtext.parse_exp(exp_text)
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
    from engine import glyphs
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
