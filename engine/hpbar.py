"""hpbar.py - HP as a percentage, from the red HP bar in the status bar.

The bar is a red fill over grey, inside a white border (measured 2026-10-23 at 1920x1080):
interior x 780..884, y 1063..1076. The fill is read along rows 1065-1070, where the red is
brightest (R 226-243, G ~10, B 0). No digits or font involved, so nothing to learn or calibrate.
The status bar moves with the window size like the EXP text ("bottom-center" anchor, layout.py).

Used to tell deaths from level-ups (tracker.py): HP 0 + EXP drop = death; pre-Big Bang level-ups
refill HP, so a full bar contradicts a death.

  python hpbar.py FRAME [FRAME...]      print HP % of saved frames (dev check)
"""
import sys

import numpy as np

from engine.layout import place

BOX = (780, 1065, 884, 1070)        # interior rows with the strongest red, 1920x1080 pixels
REF = (1920, 1080)


def hp_pct(img, ref=REF):
    """HP 0-100 (1 decimal), or None when the bar isn't there (no red and no grey interior)."""
    x0, y0, x1, y1 = place(img, list(BOX), ref, "bottom-center", "hp")
    a = np.asarray(img.crop((x0, y0, x1 + 1, y1 + 1)).convert("RGB"), dtype=np.int16)
    if a.size == 0:
        return None
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    red = (r > 150) & (g < 70) & (b < 50)
    grey = (abs(r - g) < 20) & (abs(g - b) < 20) & (r > 120)
    col_red = red.mean(axis=0) > 0.5
    col_any = col_red | (grey.mean(axis=0) > 0.5)
    if col_any.mean() < 0.9:              # not the bar (menu over it, other screen)
        return None
    # the fill runs from the left edge to the last red column (tick marks break it into segments)
    reds = np.nonzero(col_red)[0]
    n = int(reds[-1]) + 1 if reds.size else 0
    return round(100.0 * n / len(col_red), 1)


if __name__ == "__main__":
    from PIL import Image
    for p in sys.argv[1:]:
        print(f"{p}: {hp_pct(Image.open(p))}")
