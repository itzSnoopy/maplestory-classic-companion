"""vitals.py - exact HP / MP from the status bar.

Reading (screen side). The status bar shows `HP[408/408]` and `MP[606/606]` in a crisp white
pixel font with green brackets, like the EXP bar but narrower digits (own templates:
glyphs_hud.json, learned locally). Only the glyphs between the brackets are read. Unknown digits
are taught from the Character Stat window, which shows the same current/max values in the same
frame (screen.py calls try_learn). Measured 2026-10-23 at 1920x1080: HP text x 790..880, MP text
x 882..998, y 1046..1060; the status bar moves with the window size ("bottom-center").

The regen model (Meter: tick size, period, phase, the burst-look schedule) is not screen reading:
it lives in components/skillbar/meter.py; mpuse.py explains every other change.

  python -m engine.vitals FRAME [FRAME...]      read HP/MP of saved frames (dev check)
"""
import sys

from engine import glyphs
from engine.layout import place

TEMPLATES = "glyphs_hud.json"
BOXES = {"hp": (790, 1046, 880, 1060), "mp": (882, 1046, 998, 1060)}
REF = (1920, 1080)
# the bars' fill (interior rows with the strongest colour; hpbar.py measured the red one):
# a rough value that settles unknown digits when only one digit fits ("3?6/606" at 52% -> 316)
BARS = {"hp": (780, 1066, 884, 1070), "mp": (888, 1066, 992, 1070)}
STRIP = (780, 1046, 998, 1071)          # text + bars, for a frame provider that grabs just this part
FILL_TOL = 0.025                        # bar fill vs value: about 2.5 px of the 105 px bar


# ------------------------------------------------------------------ reading
def strip_box(img_size, ref=REF):
    """Where the HP+MP text strip is in a frame of this size (for WgcFrames grab_parts)."""
    class _S:
        size = img_size
    return place(_S(), list(STRIP), ref, "bottom-center", "vitals_strip")


def _keys(crop):
    """Glyph keys between the first two green brackets ('[' ... ']')."""
    seg = glyphs.segment(crop)
    greens = [i for i, (k, _) in enumerate(seg) if k == "green"]
    if len(greens) < 2:
        return None
    return [v for k, v in seg[greens[0] + 1: greens[1]] if k == "glyph"]


def _crop(img, name, ref=REF, origin=None):
    """Crop `name`'s text from a full frame, or from a strip grabbed at `origin` (x, y)."""
    return _crop_box(img, BOXES[name], name + "_text", ref, origin)


def _crop_box(img, box, name, ref=REF, origin=None):
    if origin is None:
        return img.crop(tuple(place(img, list(box), ref, "bottom-center", name)))
    sx, sy = STRIP[0], STRIP[1]
    x0, y0, x1, y1 = box
    return img.crop((x0 - sx, y0 - sy, x1 - sx, y1 - sy))


def fill(img, name, ref=REF, origin=None):
    """How full the HP (red) / MP (blue) bar is, 0-1; None if the bar isn't there."""
    import numpy as np
    a = np.asarray(_crop_box(img, BARS[name], name + "_bar", ref, origin).convert("RGB"),
                   dtype=np.int16)
    if not a.size:
        return None
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    if name == "hp":
        on = (r > 150) & (g < 70) & (b < 50)
    else:
        on = (b > 180) & (r < 80) & (g > 80) & (g < 190)
    grey = (abs(r - g) < 20) & (abs(g - b) < 20) & (r > 120)
    col_on = on.mean(0) > 0.5
    if (col_on | (grey.mean(0) > 0.5)).mean() < 0.9:
        return None                         # something over the bar
    cols = np.nonzero(col_on)[0]
    return (int(cols[-1]) + 1 if cols.size else 0) / len(col_on)


def _solve(text, frac):
    """Fill the '?' in 'cur/max' so cur/max matches the bar. -> all candidate texts."""
    import itertools
    q = [i for i, ch in enumerate(text) if ch == "?"]
    if not q or len(q) > 3 or frac is None:
        return []
    out = []
    for combo in itertools.product("0123456789", repeat=len(q)):
        t = list(text)
        for i, d in zip(q, combo):
            t[i] = d
        t = "".join(t)
        v = parse(t)
        if v and not any(part.startswith("0") and len(part) > 1 for part in t.split("/")) \
                and abs(v[0] / v[1] - frac) <= FILL_TOL:
            out.append(t)
    return out


def _trim(key):
    """A glyph key without its empty top/bottom rows (the HUD crop sits 2 rows lower than EXP's)."""
    rows = key.split("/")
    while rows and "#" not in rows[0]:
        rows.pop(0)
    while rows and "#" not in rows[-1]:
        rows.pop()
    return "/".join(rows)


def _is_slash(key):
    """'/' by shape: one thin diagonal from top-right to bottom-left (2 px per column step)."""
    rows = _trim(key).split("/")
    if len(rows) < 5 or len(rows[0]) > 4:
        return False
    cols = []
    for r in rows:
        on = [i for i, c in enumerate(r) if c == "#"]
        if not on or on[-1] - on[0] > 1:
            return False
        cols.append(on[0])
    return (cols[0] == len(rows[0]) - 1 and cols[-1] == 0
            and all(b <= a for a, b in zip(cols, cols[1:])))


def _guess(keys):
    """Unknown HUD glyphs from what a fresh install already knows (2026-10-24): the HP/MP digits
    are the same shapes as the EXP bar's (glyphs.json, learned within minutes of play), just
    cropped 2 rows lower; '/' is recognised by its shape. -> {key: char}"""
    exp = {_trim(k): c for k, c in glyphs.load().items() if c.isdigit()}
    out = {}
    for k in keys:
        c = exp.get(_trim(k))
        if c is None and _is_slash(k):
            c = "/"
        if c is not None:
            out[k] = c
    return out


def read_text(img, name, ref=REF, origin=None):
    """'408/408', with '?' for unknown glyphs; None if the bracketed text isn't there.
    Unknown glyphs that match an EXP digit (or the '/' shape) are learned when the whole
    value then reads as a valid cur/max."""
    keys = _keys(_crop(img, name, ref, origin))
    if keys is None:
        return None
    t = glyphs.load(TEMPLATES)
    text = "".join(t.get(k, "?") for k in keys)
    if "?" in text:
        g = _guess([k for k in keys if k not in t])
        filled = "".join(t.get(k) or g.get(k, "?") for k in keys)
        if parse(filled):
            t.update(g)
            glyphs.save(t, TEMPLATES)
            text = filled
    return text


def parse(text):
    """'408/408' -> [408, 408] (current <= max), else None."""
    if not text or "?" in text or text.count("/") != 1:
        return None
    a, b = text.split("/")
    if not (a.isdigit() and b.isdigit()) or int(a) > int(b) or int(b) == 0:
        return None
    return [int(a), int(b)]


def read(img, ref=REF, origin=None, learn=True):
    """{'hp': [cur, max] | None, 'mp': ..., 'raw': {'hp': text, 'mp': text}, 'learned': {...}}.
    Unknown digits: the bar's fill picks the value; a glyph is learned only when every value
    that fits the bar gives it the same digit, and the reading is used only when one value fits."""
    raw = {n: read_text(img, n, ref, origin) for n in BOXES}
    out = {"raw": raw, "learned": {}}
    for n in BOXES:
        text = raw[n]
        if text and "?" in text:
            frac = fill(img, n, ref, origin)
            cands = _solve(text, frac)
            # learn only from a clear case: one value fits, and the bar isn't at an end (it lags
            # behind the text when it refills at a level-up, and a sliver can measure as empty)
            if len(cands) == 1 and learn and 0.03 < frac < 0.97:
                keys = _keys(_crop(img, n, ref, origin))
                agreed = {keys[i]: cands[0][i] for i, ch in enumerate(text) if ch == "?"}
                if agreed:
                    t = glyphs.load(TEMPLATES)
                    new = {k: c for k, c in agreed.items() if k not in t}
                    if new:
                        t.update(new)
                        glyphs.save(t, TEMPLATES)
                        out["learned"].update({c: k for k, c in new.items()})
            if len(cands) == 1:
                text = cands[0]
        out[n] = parse(text)
    return out


def try_learn(img, name, value, ref=REF, origin=None):
    """Teach the digits of `name` from a known [cur, max] (the Stat window, same frame).
    Only when every already-known glyph agrees. Returns the newly learned characters."""
    keys = _keys(_crop(img, name, ref, origin))
    text = f"{value[0]}/{value[1]}"
    if keys is None or len(keys) != len(text):
        return {}
    t = glyphs.load(TEMPLATES)
    if any(k in t and t[k] != c for k, c in zip(keys, text)):
        return {}
    new = {k: c for k, c in zip(keys, text) if k not in t}
    if new:
        t.update(new)
        glyphs.save(t, TEMPLATES)
    return {c: k for k, c in new.items()}


if __name__ == "__main__":
    from PIL import Image
    for p in sys.argv[1:]:
        r = read(Image.open(p).convert("RGB"))
        print(p, r["raw"], r["hp"], r["mp"])
