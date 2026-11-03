"""art.py - small pictures for the windows over the game, shared by components (relic toast, rooms panel).

Pictures live in <data>/assets/art/<name>.png|gif. assets/ is git-ignored and never packaged: a placeholder
taken from game art stays on this PC only. When the file is missing, a code-drawn stand-in is used, so the
app works the same without it.

    piece(size) -> RGBA PIL image of the relic puzzle piece, size x size

Imports PIL only when called (the helper imports components' ui() at start).
"""
from base.paths import DATA

_cache = {}


def _load(name):
    if name in _cache:
        return _cache[name]
    from PIL import Image
    im = None
    for ext in ("png", "gif"):
        p = DATA / "assets" / "art" / f"{name}.{ext}"
        if p.exists():
            try:
                with Image.open(p) as src:
                    src.seek(0)
                    im = src.convert("RGBA")
                break
            except (OSError, ValueError):
                im = None
    _cache[name] = im
    return im


def _drawn_piece(size):
    """A jigsaw piece in Maple gold: square body, a knob on top and on the right, a notch at the left."""
    from PIL import Image, ImageDraw
    s = 64
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    body, edge, shine = (232, 178, 64, 255), (110, 70, 20, 255), (255, 232, 150, 255)
    x0, y0, x1, y1 = 10, 16, 50, 56
    r = 8
    for dx in (0, 1, -1):                       # outline: draw the shape a bit bigger in the edge colour
        for dy in (0, 1, -1):
            d.rounded_rectangle((x0 - 2 + dx, y0 - 2 + dy, x1 + 2 + dx, y1 + 2 + dy), radius=5, fill=edge)
            d.ellipse((30 - r - 2 + dx, y0 - 2 * r - 2 + dy + 4, 30 + r + 2 + dx, y0 + 2 + dy + 4), fill=edge)
            d.ellipse((x1 - 4 - 2 + dx, 36 - r - 2 + dy, x1 - 4 + 2 * r + 2 + dx, 36 + r + 2 + dy), fill=edge)
    d.rounded_rectangle((x0, y0, x1, y1), radius=4, fill=body)
    d.ellipse((30 - r, y0 - 2 * r + 4, 30 + r, y0 + 4), fill=body)
    d.ellipse((x1 - 4, 36 - r, x1 - 4 + 2 * r, 36 + r), fill=body)
    d.ellipse((x0 - r + 2, 36 - r + 2, x0 + r - 2, 36 + r - 2), fill=edge)        # notch
    d.ellipse((x0 - r + 4, 36 - r + 4, x0 + r - 4, 36 + r - 4), fill=(0, 0, 0, 0))
    d.line((x0 + 4, y0 + 4, x0 + 4, y1 - 8), fill=shine, width=3)                 # shine
    return im.resize((size, size), Image.LANCZOS)


def piece(size):
    """The relic puzzle piece, size x size (the placeholder picture when present, else drawn)."""
    from PIL import Image
    im = _load("relic_piece")
    if im is None:
        return _drawn_piece(size)
    # pixel art: whole-number scale with NEAREST keeps it crisp, then fit
    f = max(1, size // max(im.size))
    big = im.resize((im.width * f, im.height * f), Image.NEAREST)
    if big.size != (size, size):
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        big.thumbnail((size, size), Image.LANCZOS)
        out.paste(big, ((size - big.width) // 2, (size - big.height) // 2), big)
        big = out
    return big
