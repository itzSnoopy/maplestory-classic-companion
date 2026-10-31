"""bulbs.py - a 5x7 bulb-board font and the drawing helpers for it (generic: the relic hunt's pieces window reuses them).

A character is a list of 7 strings of "0"/"1" (its width is the string length); text is laid out with one
unlit column between characters. `draw_text` paints round bulbs (lit = colour with a highlight, unlit = a dim
dot) on a PIL ImageDraw: give it sizes already multiplied by the render scale. Needs PIL, imported by the caller.
"""

AMBER = (255, 176, 32)
RED = (255, 54, 44)
GREEN = (70, 226, 96)
OFF = (46, 36, 22)              # an unlit bulb on the black housing

_G = {
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 11111 10001 10001 10001",
    "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111",
    "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111",
    "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110",
    "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001",
    "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001",
    "N": "10001 11001 10101 10011 10001 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110",
    "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101",
    "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110",
    "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 11011 10001",
    "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100",
    "Z": "11111 00001 00010 00100 01000 10000 11111",
    ":": "0 0 1 0 1 0 0",
    ";": "0 0 0 0 0 0 0",          # a colon that is switched off (the timer blinks it)
    "-": "000 000 000 111 000 000 000",
    ".": "0 0 0 0 0 0 1",
    "!": "1 1 1 1 1 0 1",
    " ": "000 000 000 000 000 000 000",
}
FONT = {k: v.split() for k, v in _G.items()}


def glyph(ch):
    return FONT.get(ch.upper(), FONT[" "])


def cols(text):
    """Columns the text takes (one blank column between characters)."""
    return sum(len(glyph(c)[0]) for c in text) + max(0, len(text) - 1)


def width(text, pitch):
    return cols(text) * pitch


def height(pitch):
    return 7 * pitch


def _bulb(d, cx, cy, r, color, lit):
    if not lit:
        d.ellipse((cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55), fill=OFF)
        return
    glow = tuple(int(c * 0.45) for c in color)
    d.ellipse((cx - r * 1.12, cy - r * 1.12, cx + r * 1.12, cy + r * 1.12), fill=glow)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    hi = tuple(min(255, int(c + (255 - c) * 0.65)) for c in color)
    d.ellipse((cx - r * 0.45, cy - r * 0.5, cx + r * 0.15, cy + r * 0.1), fill=hi)


def draw_text(d, x, y, text, pitch, color=AMBER, unlit=True, anchor="l"):
    """Bulbs for `text` with its top-left at (x, y); anchor "l" / "m" / "r" says what x means. `unlit`
    also paints the dark bulbs of every cell (a real board shows them). Returns the width used."""
    w = width(text, pitch)
    if anchor == "m":
        x -= w / 2
    elif anchor == "r":
        x -= w
    r = pitch * 0.4
    cx = x
    for i, ch in enumerate(text):
        g = glyph(ch)
        for row, line in enumerate(g):
            for col, v in enumerate(line):
                if v == "1" or unlit:
                    _bulb(d, cx + (col + 0.5) * pitch, y + (row + 0.5) * pitch, r, color, v == "1")
        cx += (len(g[0]) + 1) * pitch
    return w


def draw_digits(d, x, y, n, count, pitch, color=AMBER):
    """A zero-padded counter of `count` digits (a whole housing's width): 7 -> "007"."""
    return draw_text(d, x, y, str(max(0, int(n))).zfill(count)[-count:], pitch, color)
