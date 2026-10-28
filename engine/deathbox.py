"""deathbox.py - spot the "You have died." box in a game frame.

When the character dies the game shows a fixed box, centered in the game window, until the
player clicks REVIVE IN TOWN (measured 2026-10-23 at 1920x1080, NOTES.md):

    white frame 258x104 px, centered on the window center
      blue panel 246x66 at the top (tombstone + halo on the left, "You have died." on the right)
      orange REVIVE IN TOWN button ~101x12 below it, slightly left of center

Detection checks those shapes and colors only (numbers, no game art is stored or shipped), so it
works for every player without calibration. The box doesn't scale with the window size; it's
searched for within a few pixels of the center to allow for an odd window size.

  python deathbox.py FRAME [FRAME...]     score saved frames (dev check)
"""
import sys

import numpy as np

# geometry, relative to the window center (cx, cy)
PANEL = (-122, -44, 123, 21)        # blue panel, inclusive box, from the 1920x1080 frames
BUTTON = (-56, 33, 44, 44)          # orange button
ICON = (-90, -38, -40, 20)          # tombstone + halo, left part of the panel
SEARCH = 8                          # px the panel may be off center
PAD = SEARCH + 130                  # half-size of the center square that's checked; a frame
                                    # provider can grab just that square (WgcFrames center=PAD)

# thresholds (a dead frame scored panel 0.84, button 0.57+, icon gray 0.25+; see NOTES)
MIN_PANEL, MIN_BUTTON, MIN_ICON = 0.70, 0.35, 0.10


def _blue(a):
    return (a[..., 2] > 170) & (a[..., 0] < 110) & (a[..., 1] > 100) & (a[..., 1] < 170)


def _orange(a):
    return (a[..., 0] > 200) & (a[..., 1] > 80) & (a[..., 1] < 170) & (a[..., 2] < 90)


def _gray(a):
    """Tombstone stone and halo: light, unsaturated pixels (the panel itself is saturated blue)."""
    return (a.min(-1) > 140) & (a.max(-1) - a.min(-1) < 40)


def _frac(mask, box, dx, dy, cx, cy):
    x0, y0, x1, y1 = box
    m = mask[cy + y0 + dy: cy + y1 + dy + 1, cx + x0 + dx: cx + x1 + dx + 1]
    return float(m.mean()) if m.size else 0.0


def score(img):
    """(panel, button, icon) fractions at the best spot near the center, or None if the frame
    is too small. `img` is a PIL image or an RGB array of the game window."""
    pad = PAD
    if hasattr(img, "crop"):                    # PIL: cut the center first, then convert
        w, h = img.size
        cx, cy = w // 2, h // 2
        if w < 2 * pad or h < 2 * pad:
            return None
        sub = np.asarray(img.crop((cx - pad, cy - pad, cx + pad, cy + pad)).convert("RGB"),
                         dtype=np.int16)
    else:
        h, w = img.shape[:2]
        cx, cy = w // 2, h // 2
        if w < 2 * pad or h < 2 * pad:
            return None
        sub = img[cy - pad: cy + pad, cx - pad: cx + pad].astype(np.int16)
    c = pad                                     # center inside `sub`
    blue = _blue(sub)
    best = (0.0, 0, 0)
    for dy in range(-SEARCH, SEARCH + 1, 2):    # coarse search for the panel
        for dx in range(-SEARCH, SEARCH + 1, 2):
            f = _frac(blue, PANEL, dx, dy, c, c)
            if f > best[0]:
                best = (f, dx, dy)
    f, dx, dy = best
    if f < MIN_PANEL:
        return (round(f, 3), 0.0, 0.0)
    return (round(f, 3), round(_frac(_orange(sub), BUTTON, dx, dy, c, c), 3),
            round(_frac(_gray(sub), ICON, dx, dy, c, c), 3))


def is_dead(img):
    s = score(img)
    return bool(s) and s[0] >= MIN_PANEL and s[1] >= MIN_BUTTON and s[2] >= MIN_ICON


if __name__ == "__main__":
    from PIL import Image
    for p in sys.argv[1:]:
        im = Image.open(p)
        print(f"{p}: {'DEAD' if is_dead(im) else '-'} {score(im)}")
