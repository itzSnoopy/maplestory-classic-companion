"""seed.py - pre-fill the HUD glyph tables from the player's own game install, at tracker start.

The EXP bar, HP/MP and level badge digits are bitmap fonts stored in the game's UI package. Decoding
them (through the injected translator: translate.game_canvas) gives the exact shapes the readers look
for, so the tables are right from the first frame. Learning (glyphs.learn, vitals, level-up) stays as the
fallback when the game is not found or a shape is missing.

Nexon material: the masks are written only to the player's DATA folder (glyphs*.json, glyph_seed.json,
history/glyph_sources.jsonl), never to the repo. Key formats are exactly the ones glyphs.segment makes:
ink columns only, rows '/'-joined, the set's common top ink row, padded to glyphs.BAND (9) rows.

  hud_sets(translate)        -> {"glyphs.json": {key: char}, "glyphs_hud.json": {...}, "glyphs_level.json": {...}}
  merge(path, seeded, src)   -> {"added": n, "dropped": [{key, char}]}  (learned mis-reads of a seeded char go)
  seed_hud(translate, force) -> short summary dict; never raises; nearly free when the game files are unchanged

Arial 12 (independent of the game): the Stat/Skill window text and the message-corner text are Arial, 12 px, no
smoothing. Rendering it with Pillow (fontmode "1") from the system's own arial.ttf (never copied or shipped)
reproduces the learned shapes exactly:
  ui_set()     -> {panels key: char} for 0-9 % ( ) / , . : - +   (keys built THROUGH panels.segments)
  seed_ui()    -> merge into DATA/glyphs_ui.json
  seed_msg()   -> digits 0-9, '+' as 13x5 cells and the three labels into DATA/glyphs_msg.json (msgcorner's store)
  seed_arial() -> both; MAPLE_NO_ARIAL=1 skips (tests of the learning path)
"""
import json
import os
import time
from pathlib import Path

from base import paths

PACK, STATUS, BASIC = "UI/UI_000.wz", "StatusBar.img", "Basic.img"
ROWS = 7                                  # height of the digits
# table -> (chars, white_min as the reader uses it, blank rows on top, source image, path template)
TABLES = {
    "glyphs.json": ("0123456789,.%", 230, 0),
    "glyphs_hud.json": ("0123456789/", 230, 2),
    "glyphs_level.json": ("0123456789", 240, 0),
}
BAND = 9


def _canvas_path(table, ch):
    if table == "glyphs_level.json":
        return BASIC, "LevelNo/" + ch
    return STATUS, "gauge/number/" + ("slash" if ch == "/" else ch)


def _ink(rows, white_min):
    return [[bool(p[3] > 128 and min(p[:3]) >= white_min) for p in r] for r in rows]


def _key(ink, top, lead):
    """Key of one glyph: columns with ink in rows top..top+7, `lead` blank rows on top, padded to BAND."""
    band = ink[top:top + ROWS]
    band = band + [[False] * len(ink[0])] * (ROWS - len(band))
    cols = [x for x in range(len(ink[0])) if any(r[x] for r in band)]
    if not cols:
        return None
    x0, x1 = cols[0], cols[-1]
    out = [[False] * (x1 - x0 + 1)] * lead
    out += [r[x0:x1 + 1] for r in band]
    w = x1 - x0 + 1
    out += [[False] * w] * (BAND - len(out))
    return "/".join("".join("#" if v else "." for v in r) for r in out[:BAND])


def hud_sets(translate):
    """{table: {key: char}} decoded from the game install, or {} when it isn't found / unreadable."""
    out = {}
    try:
        for table, (chars, wm, lead) in TABLES.items():
            inks = {}
            for ch in chars:
                img, path = _canvas_path(table, ch)
                rows = translate.game_canvas(PACK, img, path)
                if not rows:
                    return {}
                inks[ch] = _ink(rows, wm)
            tops = [next((y for y, r in enumerate(ink) if any(r)), None) for ink in inks.values()]
            tops = [t for t in tops if t is not None]
            if not tops:
                return {}
            top = min(tops)
            keys = {}
            for ch, ink in inks.items():
                k = _key(ink, top, lead)
                if k:
                    keys[k] = ch
            out[table] = keys
    except Exception:
        return {}
    return out


def _load(path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, obj, indent=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=indent, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _dims(key):
    rows = key.split("/")
    return len(rows), len(rows[0])


def merge(path, seeded, source):
    """Add the seeded keys to the table at `path`. A learned key goes when
    - the key is a seeded shape under another char, or
    - its char is seeded and it has the SAME height and width as that char's seeded key (a same-size mis-learn;
      at another UI Size the same char legitimately has a different-size shape, so those stay), or
    - it is clearly not a glyph of this table (wider than 2x the widest seeded one).
    Learned keys for chars the seed doesn't cover stay. Logs one line."""
    t = _load(path)
    size = {}
    for k, c in seeded.items():
        size.setdefault(c, set()).add(_dims(k))
    wmax = max((w for ds in size.values() for _, w in ds), default=0)
    dropped = []
    for k in list(t):
        c = t[k]
        h, w = _dims(k)
        if (k in seeded and seeded[k] != c) or (k not in seeded and c in size and (h, w) in size[c]) \
                or (wmax and w > 2 * wmax):
            dropped.append({"key": k, "char": c})
            del t[k]
    added = [k for k in seeded if t.get(k) != seeded[k]]
    t.update(seeded)
    if added or dropped:
        _write(path, t)
        _log({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "table": path.name, "source": source,
              "added": len(added), "dropped": dropped})
    return {"added": len(added), "dropped": dropped}


def _log(row):
    try:
        p = paths.DATA / "history" / "glyph_sources.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def seed_hud(translate, force=False):
    """Seed the three tables (see the module doc). -> {"status": "unchanged"|"seeded"|"no game"|"error", ...}"""
    try:
        data = paths.DATA
        side = data / "glyph_seed.json"
        stamp = json.loads(json.dumps(translate.game_stamp()))
        if not stamp:
            return {"status": "no game"}
        if not force:
            old = _load(side)
            if old.get("stamp") == stamp and all(
                    set(keys) <= set(_load(data / name)) for name, keys in (old.get("tables") or {}).items()) \
                    and set(old.get("tables") or {}) == set(TABLES):
                return {"status": "unchanged"}
        sets = hud_sets(translate)
        if not sets:
            return {"status": "no game"}
        inst = translate.game_install() or {}
        res = {"status": "seeded"}
        for name, seeded in sets.items():
            r = merge(data / name, seeded, "game " + str(inst.get("how") or "install"))
            res[name] = {"seeded": len(seeded), "added": r["added"], "dropped": len(r["dropped"])}
        side_obj = _load(side)
        side_obj.update({"stamp": stamp, "tables": {n: sorted(s) for n, s in sets.items()}})
        _write(side, side_obj)
        return res
    except Exception as e:
        return {"status": "error", "error": type(e).__name__}


# ====================================================================== Arial 12 (window text, message corner)
UI_CHARS = "0123456789%()/,.:-+"
MSG_DIGITS = "0123456789+"
MSG_LABELS = {"exp": "You received EXP", "bonus": "Bonus EXP for PARTY", "meso": "You have gained mesos"}
MSG_TRAIL = {"exp": 3, "bonus": 2, "meso": 3}     # blank columns after the label text up to the "(" (measured on clean learned labels)
_CELL_ROWS, _CELL_W, _TOP = 13, 5, 1              # msgcorner cell: 13 rows x 5, glyph top at row 1 (default)


def _font_path():
    return os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")


def arial(size=12):
    """Pillow font Arial `size` from the system fonts; None when missing (or MAPLE_NO_ARIAL is set)."""
    if os.environ.get("MAPLE_NO_ARIAL"):
        return None
    try:
        from PIL import ImageFont
        return ImageFont.truetype(_font_path(), size)
    except Exception:
        return None


def _arial_stamp():
    try:
        st = os.stat(_font_path())
        return [st.st_size, int(st.st_mtime)]
    except OSError:
        return None


def _render(font, text, pad=3):
    """Bool ink array (True = ink) of `text` on a 30 px high canvas, text origin at (pad, 5)."""
    from PIL import Image, ImageDraw
    import numpy as np
    im = Image.new("1", (int(font.getlength(text)) + 2 * pad + 4, 30), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    d.text((pad, 5), text, font=font, fill=1)
    return np.array(im, dtype=bool)


def _trim(a):
    import numpy as np
    ys, xs = np.flatnonzero(a.any(1)), np.flatnonzero(a.any(0))
    if not len(ys):
        return None, 0, 0
    return a[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1], int(ys[0]), int(xs[0])


def ui_set(font=None):
    """{panels shape key: char}, built through panels.segments so the format can't drift. Chars that don't render as
    exactly one glyph, or whose shape another char already has, are left out."""
    font = font or arial(12)
    if font is None:
        return {}
    from engine import panels
    out, bad = {}, set()
    for ch in UI_CHARS:
        segs = panels.segments(_render(font, ch))
        if len(segs) != 1:
            continue
        k = panels._shape(segs[0][1])
        if k in out and out[k] != ch:
            bad.add(k)
        out[k] = ch
    return {k: c for k, c in out.items() if k not in bad}


def seed_ui(force=False):
    """Merge ui_set() into DATA/glyphs_ui.json. -> {"status": "unchanged"|"seeded"|"no arial"|"error", ...}"""
    try:
        stamp = _arial_stamp()
        font = arial(12)
        if font is None or not stamp:
            return {"status": "no arial"}
        data = paths.DATA
        side = data / "glyph_seed.json"
        old = (_load(side).get("ui") or {})
        cur = _load(data / "glyphs_ui.json")
        if not force and old.get("stamp") == stamp and old.get("keys") and all(cur.get(k) == c for k, c in old["keys"].items()) \
                and not any(len(k.split("/")[0]) > 24 for k in cur):
            return {"status": "unchanged"}
        sets = ui_set(font)
        if not sets:
            return {"status": "no arial"}
        r = merge(data / "glyphs_ui.json", sets, "arial12")
        side_obj = _load(side)
        side_obj["ui"] = {"stamp": stamp, "keys": sets}
        _write(side, side_obj)
        return {"status": "seeded", "seeded": len(sets), "added": r["added"], "dropped": len(r["dropped"])}
    except Exception as e:
        return {"status": "error", "error": type(e).__name__}


def _dec(rows):
    import numpy as np
    return np.array([[c == "1" for c in r] for r in rows], dtype=bool)


def _enc(a):
    return ["".join("1" if c else "0" for c in row) for row in a]


def _clean_top(learned_digits, ref):
    """Row where clean learned samples of the digit sit in the cell (the sample, trimmed, equals the render)."""
    from collections import Counter
    c = Counter()
    for ch, pats in learned_digits.items():
        r = ref.get(ch)
        if r is None:
            continue
        for p in pats:
            t, y0, x0 = _trim(p)
            if t is not None and t.shape == r.shape and (t == r).all() and x0 == 0:
                c[y0] += 1
    return c.most_common(1)[0][0] if c else _TOP


def msg_set(font=None, learned_digits=None):
    """({char: 13x5 bool cell}, [(kind, 13xw bool bits)]) rendered from Arial 12. The row of the glyph top is read
    from clean learned samples when there are some, else 1; labels share the digits' baseline and carry the measured
    trailing blank columns (msgcorner cuts the label region just before the '(')."""
    import numpy as np
    font = font or arial(12)
    if font is None:
        return {}, []
    cells, raw, tops = {}, {}, {}
    for ch in MSG_DIGITS:
        t, y0, x0 = _trim(_render(font, ch))
        if t is not None:
            raw[ch], tops[ch] = t, y0
    top = _clean_top({c: p for c, p in (learned_digits or {}).items() if c != "+"}, raw)
    base = tops.get("7", tops.get("0", 7))            # canvas row of a digit's top = the cap line
    for ch, t in raw.items():
        cell = np.zeros((_CELL_ROWS, _CELL_W), dtype=bool)
        h, w = t.shape
        r0 = top + tops[ch] - base                    # '+' sits lower than the cap line
        if r0 >= 0 and r0 + h <= _CELL_ROWS and w <= _CELL_W:
            cell[r0:r0 + h, :w] = t
            cells[ch] = cell
    labels = []
    for kind, text in MSG_LABELS.items():
        a = _render(font, text)
        ys, xs = np.flatnonzero(a.any(1)), np.flatnonzero(a.any(0))
        if not len(ys):
            continue
        w = int(xs[-1] - xs[0] + 1)
        bits = np.zeros((_CELL_ROWS, w + MSG_TRAIL[kind]), dtype=bool)
        for y in ys:
            r = int(y) - base + top
            if 0 <= r < _CELL_ROWS:
                bits[r, :w] = a[y, xs[0]:xs[-1] + 1]
        labels.append((kind, bits))
    return cells, labels


def seed_msg(store=None, force=False):
    """Put the Arial digits / '+' / labels FIRST in msgcorner's template store (DATA/glyphs_msg.json), learned
    samples stay as variants except digit samples that are closer to another char's seeded shape than to their own
    (or have < 3 ink pixels). -> {"status": ..., ...}"""
    try:
        stamp = _arial_stamp()
        font = arial(12)
        if font is None or not stamp:
            return {"status": "no arial"}
        default = store is None
        store = Path(store) if store else paths.DATA / "glyphs_msg.json"
        side = paths.DATA / "glyph_seed.json"
        d = _load(store)
        if d.get("v") != 1:
            d = {"v": 1, "digits": {}, "labels": []}
        ld = {ch: [_dec(p) for p in pats] for ch, pats in (d.get("digits") or {}).items()}
        ll = [(k, _dec(rows)) for k, rows in (d.get("labels") or [])]
        cells, labels = msg_set(font, ld)
        if not cells:
            return {"status": "no arial"}
        if not force and default and (_load(side).get("msg") or {}).get("stamp") == stamp \
                and all(ld.get(ch) and (ld[ch][0] == c).all() for ch, c in cells.items()) \
                and all(i < len(ll) and ll[i][0] == k and ll[i][1].shape == b.shape and (ll[i][1] == b).all()
                        for i, (k, b) in enumerate(labels)):
            return {"status": "unchanged"}
        same = dropped = 0
        new = {}
        for ch, c in cells.items():
            keep = [c]
            for p in ld.get(ch, []):
                if p.shape != c.shape or int(p.sum()) < 3:
                    dropped += 1
                    continue
                if (p == c).all():
                    same += 1
                    continue
                d_own = int((p != c).sum())
                d_oth = min(int((p != o).sum()) for oc, o in cells.items() if oc != ch)
                if d_oth < d_own:
                    dropped += 1
                    continue
                keep.append(p)
            new[ch] = keep
        for ch, pats in ld.items():               # chars the seed doesn't cover stay as they are
            if ch not in cells:
                new[ch] = pats
        rest = [(k, b) for k, b in ll if not any(k == sk and b.shape == sb.shape and (b == sb).all() for sk, sb in labels)]
        d["digits"] = {ch: [_enc(p) for p in pats] for ch, pats in new.items()}
        d["labels"] = [[k, _enc(b)] for k, b in labels + rest]
        d["note"] = "learned at run time from the player's own screen (+ Arial 12 seed); local only, never ship or commit"
        _write(store, d, indent=None)
        if default:
            so = _load(side)
            so["msg"] = {"stamp": stamp}
            _write(side, so)
            _log({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "table": "glyphs_msg.json", "source": "arial12",
                  "added": len(cells) + len(labels), "same": same, "dropped_digits": dropped})
        return {"status": "seeded", "digits": len(cells), "labels": len(labels), "learned_equal": same, "dropped": dropped}
    except Exception as e:
        return {"status": "error", "error": type(e).__name__ + ": " + str(e)}


def seed_arial():
    """seed_ui + seed_msg (one call at ScreenSource start). Never raises."""
    return {"ui": seed_ui(), "msg": seed_msg()}
