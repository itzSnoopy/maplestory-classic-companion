"""Exact-match reader for Maple's white pixel font (EXP bar digits).

Tesseract misreads this 5x7 font (e.g. '12' -> '42'), but every glyph is pure white
with a blank column between characters, so we segment by columns and look each glyph
up by its exact pixel pattern. Green columns are the EXP bar's '[' and ']'.

Unknown glyphs are read as '?' and saved under glyphs/unknown/. The first-run setup teaches
the digits on screen (calibrate.learn_typed); the tracker solves the rest from EXP needed.
The level badge uses the same approach with its own templates (glyphs_level.json); the tracker
teaches itself new level digits whenever it detects a level-up.
"""
import hashlib
import json
from pathlib import Path

from paths import DATA as ROOT
TEMPLATES = ROOT / "glyphs.json"
UNKNOWN = ROOT / "glyphs" / "unknown"
BAND = 9          # rows kept from the top of the text: 7 for digits + room for ',' descender


def _is_white(p, wm):
    return min(p) >= wm


def _is_green(p):
    r, g, b = p
    return g > r + 30 and g > b + 60


def segment(img, white_min=230):
    """Return [(kind, key)] left to right; kind is 'glyph' or 'green'."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    # ignore near-full-width white rows (the bar's border line)
    rows = [y for y in range(h)
            if sum(_is_white(px[x, y], white_min) for x in range(w)) < 0.6 * w]
    white_rows = [y for y in rows if any(_is_white(px[x, y], white_min) for x in range(w))]
    if not white_rows:
        return []
    top = white_rows[0]
    band = [y for y in rows if top <= y < top + BAND]

    out, cur = [], []
    for x in range(w + 1):
        col_white = x < w and any(_is_white(px[x, y], white_min) for y in band)
        col_green = x < w and any(_is_green(px[x, y]) for y in band)
        if col_white:
            cur.append(x)
            continue
        if cur:
            key = "/".join("".join("#" if _is_white(px[c, y], white_min) else "."
                                   for c in cur) for y in band)
            out.append(("glyph", key))
            cur = []
        if col_green and not (out and out[-1][0] == "green" and out[-1][1] == x - 1):
            out.append(("green", x))
        elif col_green:
            out[-1] = ("green", x)       # extend the current green run
    return out


def _path(templates):
    return ROOT / templates if templates else TEMPLATES


def load(templates=None):
    p = _path(templates)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save(t, templates=None):
    _path(templates).write_text(json.dumps(t, indent=1, sort_keys=True), encoding="utf-8")


def _save_unknown(key):
    UNKNOWN.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(key.encode()).hexdigest()[:10]
    p = UNKNOWN / f"{h}.txt"
    if not p.exists():
        p.write_text(key.replace("/", "\n"), encoding="utf-8")


def read(img, white_min=230, templates=None):
    """Text like '12[80.00%]'. Unknown glyphs come back as '?'."""
    t = load(templates)
    s, brackets = [], 0
    for kind, val in segment(img, white_min):
        if kind == "green":
            s.append("[" if brackets == 0 else "]")
            brackets += 1
        elif val in t:
            s.append(t[val])
        else:
            _save_unknown(val)
            s.append("?")
    return "".join(s)


def learn(img, text, white_min=230, templates=None):
    """Label this crop's glyphs with `text` (brackets are skipped). Returns new labels."""
    glyphs = [k for kind, k in segment(img, white_min) if kind == "glyph"]
    chars = [c for c in text if c not in "[] "]
    if len(glyphs) != len(chars):
        raise ValueError(f"{len(glyphs)} glyphs in image but {len(chars)} characters in {text!r}")
    t, new = load(templates), {}
    for k, c in zip(glyphs, chars):
        if t.get(k) not in (None, c):
            raise ValueError(f"glyph already labeled {t[k]!r}, not {c!r}")
        if k not in t:
            new[k] = c
    t.update(new)
    save(t, templates)
    return new
