"""charname.py - which character is playing, from the name on the status bar.

The name sits one text line (14 px; the UI doesn't scale) below the job name, in the same crisp
white pixel font. Tesseract garbles it ("SnoopySparda" -> "SsnhoopySparda") and names can't be
snapped to a list, so the character is identified by the exact pixel pattern of that text instead:
a short hash of the white-pixel mask, trimmed to the text. The same character gives the same
fingerprint every frame; another character gives another one. The readable name comes from the
Character Stat window (panels.py, read well), with Tesseract here only as a first guess.

  python charname.py FRAME [FRAME...]     fingerprint + OCR guess (dev check)
"""
import hashlib
import sys

import numpy as np

import jobread
import ocr
from layout import anchor_for, place

LINE = 14            # px from the job line to the name line
WHITE_MIN = 220


def name_box(frame, res=(1920, 1080), rc=None):
    rc = {**jobread.DEFAULT_RC, **(rc or {})}
    x0, y0, x1, y1 = place(frame, rc["box"], res, anchor_for("job", rc), "job")
    return x0, y0 + LINE, x1, y1 + LINE


MAX_TEXT_H = 11      # tallest name text (letters with descenders: "p", "y" in SnoopySparda = 10)


def fingerprint(frame, res=(1920, 1080), rc=None):
    """(fingerprint or None, crop, clean). None when there's no readable name (black frame
    etc.). clean=False when the text doesn't look like a lone name line (too tall, or touching
    the crop's edge): something (a tooltip, a dragged window) is over it."""
    c = frame.crop(name_box(frame, res, rc))
    m = np.asarray(c.convert("RGB")).min(-1) >= WHITE_MIN
    if m.sum() < 15:
        return None, c, False
    ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    clean = (ys[-1] - ys[0] + 1 <= MAX_TEXT_H and ys[0] > 0 and ys[-1] < m.shape[0] - 1
             and xs[-1] < m.shape[1] - 1)
    m = m[ys[0]: ys[-1] + 1, xs[0]: xs[-1] + 1]
    h = hashlib.sha1(np.packbits(m).tobytes() + bytes(m.shape)).hexdigest()[:10]
    return h, c, bool(clean)


def guess(crop):
    """Tesseract's reading of the name crop (may be garbled); '' without OCR."""
    if not ocr.READY:
        return ""
    try:
        return ocr._tess(crop, {**jobread.DEFAULT_RC}).strip()
    except Exception:
        return ""


if __name__ == "__main__":
    import json
    from PIL import Image
    from paths import CONFIG
    try:
        ocr.setup(json.loads(CONFIG.read_text(encoding="utf-8")).get("tesseract"))
    except Exception:
        pass
    for p in sys.argv[1:]:
        fp, c, clean = fingerprint(Image.open(p).convert("RGB"))
        print(p, fp, "clean" if clean else "COVERED?", repr(guess(c)))
