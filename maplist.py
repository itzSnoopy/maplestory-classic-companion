"""Snap OCR'd minimap titles to the real Classic World map list (maps_classic.json).

The minimap title has two lines: street ("Rainbow Street") and map name
("Hunting Ground Middle of the Forest I"). OCR gets the map line mostly right but makes small
mistakes: a lost space before a roman numeral ("Forestl"), l/1/| for I, a dropped letter. Matching
against the 453 known names turns those into the exact name, with its street and map id.

Rules, from strict to loose:
  1. exact match after squashing case/spaces/punctuation (and a trailing l/1/| read as I)
  1b. exact match after folding letters Tesseract mixes up in this font (f/l/i/1/t/j all the
     same, "rn" = "m"), only if exactly one map folds to it ("Eflinia" -> Ellinia; difflib
     scores short names too low, 0.86, for one wrong letter). No two of the 453 names collide.
  2. close fuzzy match (difflib >= 0.88), but the roman numeral suffix must agree exactly,
     so "... Ground I" never becomes "... Ground II"
  3. unique prefix (a cut-off read that only one map name starts with)
Several maps share a name (e.g. "Mushroom Garden" is a field and a hidden-street town); then the
street line breaks the tie when it's readable, else the first one wins (same name either way).
No match -> None, and the caller keeps the raw read.
"""
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from paths import APP as ROOT
MAPS_FILE = ROOT / "maps_classic.json"
ROMAN_RE = re.compile(r"\s(I{1,3}|IV|V|VI{1,3}|IX)$")
FUZZY_CUTOFF = 0.88


def squash(s):
    return re.sub(r"[^a-z0-9<>]", "", (s or "").lower())


def roman_suffix(name):
    m = ROMAN_RE.search(name or "")
    return m.group(1).lower() if m else ""


@lru_cache(maxsize=1)
def _index():
    try:
        maps = json.loads(MAPS_FILE.read_text(encoding="utf-8"))["maps"]
    except Exception:
        maps = []
    idx = {}
    for m in maps:
        idx.setdefault(squash(m["name"]), []).append(m)
    return idx


_FOLD = str.maketrans("fli1tj", "######")


def fold(s):
    """Squashed name with OCR look-alikes merged, so 'eflinia' and 'ellinia' compare equal."""
    return s.replace("rn", "m").translate(_FOLD)


@lru_cache(maxsize=1)
def _fold_index():
    out = {}
    for k in _index():
        out.setdefault(fold(k), []).append(k)
    return out


def _variants(name):
    """Squashed forms of an OCR'd map line: as read, and with a trailing l/1/| run as I's."""
    s = squash(name)
    out = [s]
    m = re.search(r"[l1|]+$", (name or "").strip())
    if m:
        fixed = (name.strip()[:m.start()] + "i" * len(m.group()))
        out.insert(0, squash(fixed))
    return out


def _pick(cands, street):
    if len(cands) > 1 and street:
        ss = squash(street)
        score = lambda m: difflib.SequenceMatcher(None, ss, squash(m["street"])).ratio()
        best = max(cands, key=score)
        if score(best) >= 0.7:                 # only trust a readable street line
            return best
    return cands[0]


def lookup(name, street=None):
    """OCR'd map line (+ optional street line) -> map dict {id, region, street, name, town} or None."""
    idx = _index()
    if not idx or not name:
        return None
    variants = _variants(name)
    for v in variants:                                   # 1. exact
        if v in idx:
            return _pick(idx[v], street)
    fidx = _fold_index()
    for v in variants:                                   # 1b. exact after folding look-alikes
        ks = fidx.get(fold(v), [])
        if len(ks) == 1:
            return _pick(idx[ks[0]], street)
    keys = list(idx)
    for v in variants:                                   # 2. fuzzy, roman suffix must agree
        for k in difflib.get_close_matches(v, keys, n=5, cutoff=FUZZY_CUTOFF):
            cand = idx[k][0]
            want = roman_suffix(cand["name"])
            tail = re.search(r"[ivx]*$", v).group()
            if want and not tail.endswith(want):
                continue
            if want and len(tail) > len(want) and tail[-len(want) - 1] in "ivx":
                continue                                 # read II, candidate I
            if not want and roman_suffix(name):
                continue                                 # read has a numeral, candidate doesn't
            return _pick(idx[k], street)
    v = variants[-1]                                     # 3. unique prefix of a cut-off read
    if len(v) >= 10:
        hits = [k for k in keys if k.startswith(v)]
        if len(hits) == 1:
            return _pick(idx[hits[0]], street)
    return None


def snap(raw_title):
    """Two-line OCR text of the minimap title -> map dict or None."""
    lines = [ln.strip() for ln in (raw_title or "").splitlines() if ln.strip()]
    if not lines:
        return None
    name = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 :'&,<>\-|]", " ", lines[-1])).strip()
    street = lines[0] if len(lines) > 1 else None
    return lookup(name, street)
