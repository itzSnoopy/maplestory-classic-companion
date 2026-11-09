"""Buff icon checks (engine/buffs.py + the skill bar's deferred cast decision):

    python dev/buffs_check.py [--frames DIR] [--icons DIR]

1. frames: Magic Guard's icon is found in dev/fixtures/burst/20261031_142412/ exactly when it was on
   (12.5-27.5 s and 35.5-59 s), and no other icon is found anywhere. Needs the local frames (Nexon imagery,
   git-ignored) and the player's skill icons (templates/skill_icons); SKIPS (counts as pass) without them.
2. logic: components/skillbar/feed.py with synthetic facts: 8 MP drop + icon flip 0.5 s later = Magic Guard
   cast; drop without a flip = Energy Bolt after ~1.4 s (no attack pulse before); Guard off without an MP drop =
   state change only.
3. haste (run_haste): a party member's Haste (4101001 / 4201001, one picture) is read from the buff list with the
   MeowDB picture (assets/skill_icons, not the player's own cuts) in dev/fixtures/burst/20261107_215426: found on
   every non-faded frame, countdown digits read exactly at verified frames (59 ... 28 s), never found in any other
   fixture folder under dev/fixtures/burst (every n-th frame, <= 150 per folder). SKIPS without frames / picture.
4. party logic (run_party): feed facts {on, left} -> usage.party_buffs; partyfx slots (lit, exact vs estimated end,
   recast restart, not in a party = not watched).
Used by dev/release_check.py (run_frames / run_logic / run_haste / run_party return (ok, note, skipped)).
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FRAMES = ROOT / "dev" / "fixtures" / "burst" / "20261031_142412"
GUARD = "2001000"
ON = ((12.5, 27.5), (35.5, 59.0))
OFF = ((0.0, 11.5), (28.5, 34.5))


def icons_dir(given=None):
    from base import paths
    cands = [Path(given)] if given else [
        Path(os.environ.get("MAPLE_BUFF_ICONS", "")) if os.environ.get("MAPLE_BUFF_ICONS") else None,
        paths.DATA / "templates" / "skill_icons",
        Path(os.environ.get("LOCALAPPDATA", "")) / "MapleClassicCompanion" / "templates" / "skill_icons"]
    for c in cands:
        if c and c.is_dir() and any(c.glob("*.png")):
            return c
    return None


def run_frames(frames=None, icons=None):
    frames = Path(frames) if frames else FRAMES
    icons = icons_dir(icons)
    files = sorted(frames.glob("*.jpg")) if frames.is_dir() else []
    if not files or icons is None or not (icons / f"{GUARD}.png").exists():
        return True, "skipped: needs dev/fixtures/burst/20261031_142412 and the player's skill icons", True
    from PIL import Image
    from engine import buffs
    ids = sorted(p.stem for p in icons.glob("*.png"))
    ic = buffs.load_icons(icons, ids)
    import numpy as np
    from engine import vitals
    lum = {}
    for f in files:       # map-change fade frames (HUD dimmed): the source skips those looks (screen._buffs)
        im = Image.open(f).convert("L")
        lum[f] = float(np.asarray(im.crop(vitals.strip_box(im.size))).mean())
    med = float(np.median(list(lum.values())))
    bad, total, n, faded = [], 0.0, 0, 0
    for f in files:
        t = float(f.name.split("_")[0])
        if lum[f] < 0.95 * med:
            faded += 1
            continue
        im = Image.open(f).convert("RGB")
        strip = im.crop(buffs.strip_box(im.size))
        t0 = time.perf_counter()
        got = buffs.find(strip, ic)
        total += time.perf_counter() - t0
        n += 1
        if got - {GUARD}:
            bad.append(f"{t}: other {sorted(got - {GUARD})}")
        if any(a <= t <= b for a, b in ON) and GUARD not in got:
            bad.append(f"{t}: Guard missed")
        if any(a <= t <= b for a, b in OFF) and GUARD in got:
            bad.append(f"{t}: Guard false hit")
    return (not bad), (f"{n} frames (+{faded} faded skipped), {len(ic)} icons, {total / n * 1000:.1f} ms/look" if not bad
                       else "; ".join(bad[:6])), False


class _Prof:
    who = "tester"

    def __init__(self):
        self.log = print

    def char(self):
        return {"skills": {"2001000": {"value": 2}, "2001002": {"value": 4}}}

    def regen(self):
        return {}

    def base_max(self, k):
        return None

    def learn_base_max(self, *a, **k):
        pass

    def learn_skill_level(self, *a, **k):
        return False

    def save_regen(self, d):
        pass


class _Src:
    def __init__(self):
        self.watched = None

    def watch_buffs(self, ids, folder=None, digits=(), prefer_loose=()):
        self.prefer = list(prefer_loose)
        self.watched = list(ids)
        self.folder = folder
        self.digits = list(digits)


class _Hub:
    def __init__(self):
        self.source = _Src()

    def ask(self, name, default=None):
        return default


def _feed():
    from components.skillbar import feed
    f = feed.Feed(hub=_Hub())
    f._watch_ids = lambda prof: [GUARD]                 # no icon files needed
    f.casts_written = []
    f._write_casts = lambda: f.casts_written.append(dict(f.shown_counts()))
    f._log_events = lambda ev: f.casts_written.append(("log", [e["name"] for e in ev if e["kind"] == "cast"]))
    prof = _Prof()
    f.begin(prof, 0.0)
    return f, prof


def _mp(f, t, mp):
    f.fact(t, "vitals", {"hp": [600, 600], "mp": [mp, 100]}, None)


def _casts(f):
    return {k: v["n"] for k, v in f.usage.totals["mp"].get("cast", {}).items()}


def run_logic():
    bad = []
    # A: drop, icon turns on 0.5 s later -> Magic Guard
    f, _ = _feed()
    f.fact(1.0, "buffs", {"on": []}, None)
    _mp(f, 1.5, 100)
    _mp(f, 2.0, 92)
    if f.hub.source.watched != [GUARD]:
        bad.append("A: watch_buffs not called")
    if _casts(f) or f.shown_counts():
        pass
    pend = len(f.pending)
    f.fact(2.5, "buffs", {"on": [GUARD]}, None)
    if pend != 1 or _casts(f) != {"Magic Guard": 1} or f.usage.cast_count.get("2001002"):
        bad.append(f"A: pending {pend}, casts {_casts(f)}, count {dict(f.usage.cast_count)}")
    if any(isinstance(w, dict) and w.get("2001002") for w in f.casts_written):
        bad.append("A: attack pulse fired for the buff cast")
    # B: drop, no flip -> Energy Bolt only after 1 s
    f, _ = _feed()
    f.fact(1.0, "buffs", {"on": []}, None)
    _mp(f, 1.5, 100)
    _mp(f, 2.0, 92)
    early = list(f.casts_written)
    _mp(f, 3.0, 92)
    mid = _casts(f), f.shown_counts()
    _mp(f, 3.5, 92)
    if any(isinstance(w, dict) and w for w in early) or mid[1]:
        bad.append(f"B: pulse before the decision {early} {mid}")
    if _casts(f) != {"Energy Bolt": 1} or f.shown_counts() != {"2001002": 1} or f.pending:
        bad.append(f"B: casts {_casts(f)} shown {f.shown_counts()}")
    # B2: same drop with Guard already on, icon turns off 0.4 s later -> Magic Guard (re-cast = off)
    f, _ = _feed()
    f.fact(1.0, "buffs", {"on": [GUARD]}, None)
    _mp(f, 1.5, 100)
    _mp(f, 2.0, 92)
    f.fact(2.4, "buffs", {"on": []}, None)
    if _casts(f) != {"Magic Guard": 1}:
        bad.append(f"B2: casts {_casts(f)}")
    # C: Guard off, no MP change -> state only
    f, _ = _feed()
    f.fact(1.0, "buffs", {"on": [GUARD]}, None)
    _mp(f, 1.5, 100)
    f.fact(2.0, "buffs", {"on": []}, None)
    _mp(f, 2.5, 100)
    _mp(f, 4.0, 100)
    if _casts(f) or f.pending or f.buff_on.get(GUARD) is not False:
        bad.append(f"C: casts {_casts(f)} state {f.buff_on}")
    # D: no buffs facts at all (source can't read) -> immediate attack, old behaviour
    f, _ = _feed()
    _mp(f, 1.5, 100)
    _mp(f, 2.0, 92)
    if _casts(f) != {"Energy Bolt": 1} or f.pending:
        bad.append(f"D: casts {_casts(f)}")
    return (not bad), ("A-D ok" if not bad else "; ".join(bad)), False


HASTE_FRAMES = ROOT / "dev" / "fixtures" / "burst" / "20261107_215426"
HASTE_FRAMES2 = ROOT / "dev" / "fixtures" / "burst" / "20261107_221755"     # 240 s, Haste on throughout, recast twice, no big digits
HASTE_IDS = ("4101001", "4201001")
# frame index (sorted by name) -> seconds left read by eye from the crops (57 at 30 s, 47 at 40 s, 32 at 55 s ...)
HASTE_LEFT = {56: 59, 60: 57, 64: 55, 68: 53, 70: 53, 72: 51, 76: 49, 80: 47, 84: 45, 88: 43, 90: 42, 92: 41, 96: 39,
              100: 37, 104: 35, 108: 33, 110: 32, 116: 29, 118: 28}


def haste_icons(given=None):
    from base import paths
    for c in (Path(given) if given else None, paths.DATA / "assets" / "skill_icons"):
        if c and any((c / f"{i}.png").exists() for i in HASTE_IDS):
            return c
    return None


def _file_t(path):
    import re
    return float(re.match(r"(\d+\.\d+)_", path.name).group(1))


def _walk(files, ic, ids, fn, lum_guard=True):
    """Feed the frames of a recording in order the way the source does: find the icon (last spot as a hint), skip faded
    frames, `fn(k, t, strip, x, y)` for each frame with the icon, `fn(k, t, None, 0, 0)` when it is not found."""
    import numpy as np
    from PIL import Image
    from engine import buffs, vitals
    lum = [float(np.asarray(Image.open(f).convert("L").crop(vitals.strip_box((1920, 1080)))).mean()) for f in files]
    med = float(np.median(lum))
    hint = {}
    for k, f in enumerate(files):
        if lum_guard and lum[k] < 0.95 * med:
            hint = {}
            continue
        st = Image.open(f).convert("RGB").crop(buffs.strip_box((1920, 1080)))
        got = buffs.find_loose(st, ic, hints=hint)
        hint = dict(got)
        if set(got) == set(ids):
            fn(k, _file_t(f), st, *got[ids[0]])
        else:
            fn(k, _file_t(f), None, 0, 0)


def run_haste(frames=None, icons=None):
    frames = Path(frames) if frames else HASTE_FRAMES
    folder = haste_icons(icons)
    files = sorted(frames.glob("*.jpg")) if frames.is_dir() else []
    if not files or folder is None:
        return True, "skipped: needs dev/fixtures/burst/20261107_215426 and assets/skill_icons/4101001.png", True
    from PIL import Image
    from engine import buffs
    ids = [i for i in HASTE_IDS if (folder / f"{i}.png").exists()]
    ic = buffs.load_loose(folder, ids)
    bad = []
    st_ = {"found": 0, "n": 0, "ms": 0.0}

    def one(k, t, st, x, y):
        st_["n"] += 1
        if st is not None:
            st_["found"] += 1
        else:
            bad.append(f"frame {k}: not found")
    _walk(files, ic, ids, one)
    # nothing found anywhere else (those folders have no Haste; the 1366x768 one at its own scale)
    f2 = sorted(HASTE_FRAMES2.glob("*.jpg")) if HASTE_FRAMES2.is_dir() else []
    n2 = []
    if f2:
        _walk(f2, ic, ids, lambda k, t, st, x, y: n2.append(st is not None))
        if sum(n2) < 0.99 * len(n2):
            bad.append(f"221755: found on {sum(n2)} of {len(n2)} frames")
    others, n_other = [], 0
    for d in sorted((ROOT / "dev" / "fixtures" / "burst").iterdir()):
        fs = sorted(d.glob("*.jpg"))
        if d == frames or d == HASTE_FRAMES2 or not fs:
            continue
        size = Image.open(fs[0]).size
        icl = buffs.load_loose(folder, ids, size[0] / buffs.REF_W)
        for f in fs[::max(1, len(fs) // 150)]:
            im = Image.open(f).convert("RGB")
            n_other += 1
            if buffs.find_loose(im.crop(buffs.strip_box(im.size)), icl):
                others.append(f"{d.name}/{f.name}")
    if others:
        bad.append("Haste found in other recordings: " + ", ".join(others[:4]))
    return (not bad), (f"215426: {st_['found']}/{st_['n']} non-faded frames found, 221755: {sum(n2)}/{len(n2)}, "
                       f"{n_other} frames of other recordings clean" if not bad else "; ".join(bad[:6])), False


_GLYPH_CACHE = {}


def _scratch():
    import tempfile
    d = Path(tempfile.gettempdir()) / "maple_buffs_check"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _learn_from_scratch(folder):
    """Learn the countdown digits from nothing on the two recordings, in order, into a scratch file. Returns
    (path, info) with info = {"pass1": [(k, left, mins)], "learned": [(t, kind, digit)], "t_big": first big-digit
    time, "t_rec2": ...}; cached for the other checks of this run."""
    if "x" in _GLYPH_CACHE:
        return _GLYPH_CACHE["x"]
    from engine import buffs
    path = _scratch() / "glyphs_buff.json"
    if path.exists():
        path.unlink()
    ids = [i for i in HASTE_IDS if (folder / f"{i}.png").exists()]
    ic = buffs.load_loose(folder, ids)
    g = buffs.BuffGlyphs(path)
    sid = ids[0]
    info = {"pass1": [], "t_big": None, "rec2_start": None}
    files1 = sorted(HASTE_FRAMES.glob("*.jpg"))

    def one1(k, t, st, x, y):
        if st is None:
            g.lost(sid, t)
            return
        l, m = g.look(sid, t, st, x, y)
        info["pass1"].append((k, l, m))
        if info["t_big"] is None and k >= 56:
            info["t_big"] = t
    _walk(files1, ic, ids, one1)
    g.lost(sid, 1e6)
    n1 = len(g.learned)
    files2 = sorted(HASTE_FRAMES2.glob("*.jpg"))

    def one2(k, t, st, x, y):
        if info["rec2_start"] is None:
            info["rec2_start"] = t
        if st is None:
            g.lost(sid, 1000.0 + t)
            return
        g.look(sid, 1000.0 + t, st, x, y)           # later in time than the first recording
    _walk(files2, ic, ids, one2)
    g.lost(sid, 1e6)
    info["learned"], info["n1"] = list(g.learned), n1
    _GLYPH_CACHE["x"] = (path, info)
    return path, info


def run_glyphs(icons=None):
    """The countdown digits are LEARNED (engine/buffs.py BuffGlyphs -> glyphs_buff.json, never shipped): from nothing on
    20261107_215426 (+ 221755 for the small minutes digit 2) into a scratch file. Checks: reads before a digit is
    learned are None and never wrong; after learning, the 19 frames verified by eye read exactly, from the saved
    file, in a fresh run; a store holding only digits 4 and 5 never reads a wrong number. Reports learning times."""
    folder = haste_icons(icons)
    f1 = sorted(HASTE_FRAMES.glob("*.jpg")) if HASTE_FRAMES.is_dir() else []
    f2 = sorted(HASTE_FRAMES2.glob("*.jpg")) if HASTE_FRAMES2.is_dir() else []
    if not f1 or not f2 or folder is None:
        return True, "skipped: needs the 20261107_215426 / 221755 frames and assets/skill_icons/4101001.png", True
    from engine import buffs
    path, info = _learn_from_scratch(folder)
    bad = []
    learned = info["learned"]
    secs = {d for _, kind, d in learned if kind == "seconds"}
    mins = {d for _, kind, d in learned if kind == "minutes"}
    if secs != set("0123456789"):
        bad.append(f"seconds digits learned {sorted(secs)} (expected 0-9)")
    if mins != {"1", "2"}:
        bad.append(f"minutes digits learned {sorted(mins)} (expected 1, 2)")
    # pass 1: while learning, a read is None or right
    first_read = None
    for k, l, m in info["pass1"]:
        if l is not None:
            first_read = first_read if first_read is not None else k
            if k in HASTE_LEFT and l != HASTE_LEFT[k]:
                bad.append(f"learning pass: frame {k} read {l} expected {HASTE_LEFT[k]}")
    reads = [(k, l) for k, l, m in info["pass1"] if l is not None]
    if any(b[1] > a[1] for a, b in zip(reads, reads[1:])) or any(a[1] - b[1] > 2 for a, b in zip(reads, reads[1:])):
        bad.append("learning pass: reads not a steady countdown")
    if first_read is None or first_read < 80:
        bad.append(f"first seconds read at frame {first_read}: digits cannot be known before the first tens change")
    # pass 2: a fresh run loading the saved file
    g = buffs.BuffGlyphs(path)
    ic = buffs.load_loose(folder, [i for i in HASTE_IDS if (folder / f"{i}.png").exists()])
    ids = [i for i in HASTE_IDS if (folder / f"{i}.png").exists()]
    got2, mins1 = {}, []

    def two(k, t, st, x, y):
        if st is not None:
            l, m = g.look(ids[0], 5000.0 + t, st, x, y)
            got2[k] = l
            if k < 56 and m is not None:
                mins1.append(m)
    _walk(f1, ic, ids, two)
    wrong = {k: (got2.get(k), v) for k, v in HASTE_LEFT.items() if got2.get(k) != v}
    if wrong:
        bad.append(f"after learning: {len(wrong)} of {len(HASTE_LEFT)} verified frames wrong/None {dict(list(wrong.items())[:4])}")
    if any(m != 1 for m in mins1) or len(mins1) < 40:
        bad.append(f"minutes digit on 215426 before the last minute: {len(mins1)} reads, values {sorted(set(mins1))}")
    # the minutes digit sequence on 221755
    seq = ""

    def three(k, t, st, x, y):
        nonlocal seq
        if st is not None:
            _, m = g.look(ids[0], 9000.0 + t, st, x, y)
            if m and (not seq or seq[-1] != str(m)):
                seq += str(m)
    _walk(f2, ic, ids, three)
    if seq != "121212":
        bad.append(f"221755 minutes digit runs {seq} (expected 121212)")
    # a store with only 4 and 5 learned: unlearned digits read None, never a wrong number
    part = buffs.BuffGlyphs(_scratch() / "glyphs_part.json")
    part.t["seconds"] = {k: v for k, v in g.t["seconds"].items() if v in ("4", "5")}
    part.t["minutes"] = {}
    seen = []

    def four(k, t, st, x, y):
        if st is not None and k >= 56:
            l, m = part.look(ids[0], 7000.0 + t, st, x, y)
            seen.append(l)
    _walk(f1, ic, ids, four)
    ok_vals = {44, 45, 54, 55}
    if any(l is not None and l not in ok_vals for l in seen) or not any(l is not None for l in seen):
        bad.append(f"partial store (4, 5 only) read {sorted({l for l in seen if l is not None})}")
    # when each digit became readable (looks are fed in file time; the recording's big digits start at t_big)
    tb = info["t_big"]
    when = {}
    for t, kind, d in learned:
        if kind == "seconds":
            when.setdefault(d, t - tb)
        elif t < 1000:
            when.setdefault("m" + d, t - tb)
        else:
            when.setdefault("m" + d, t - 1000.0 - info["rec2_start"])
    order = ", ".join(f"{d} {v:.0f}s" for d, v in sorted(when.items(), key=lambda kv: kv[1]))
    return (not bad), (f"learned from scratch, seconds into the countdown: {order} (m1 = minutes 1, m2 = minutes 2 counted "
                       f"from the start of 221755); first seconds read at frame {first_read}; 19 verified frames exact after "
                       f"learning, none wrong before; partial store reads None" if not bad else "; ".join(bad[:6])), False


def run_learn(frames=None, icons=None):
    """Replay 20261107_221755 through ScreenSource._buffs + Feed (what the tracker does) with the countdown digits
    learned from scratch (run_glyphs): the icon is on at frame 0 (mid-buff: nothing learned from it), the first recast
    (minutes digit up) is a fresh cast whose minutes-digit sync at 2 -> 1 gives its end: 160 s = Haste level 7, stored
    for the caster "?"; the next cast starts from it."""
    frames = Path(frames) if frames else HASTE_FRAMES2
    folder = haste_icons(icons)
    files = sorted(frames.glob("*.jpg")) if frames.is_dir() else []
    if not files or folder is None or not HASTE_FRAMES.is_dir():
        return True, "skipped: needs dev/fixtures/burst/20261107_221755 + 215426 and assets/skill_icons/4101001.png", True
    from PIL import Image
    from base import paths
    from launcher import compose
    from engine import buffs, vitals
    from engine.sources.screen import ImageFrames
    gpath, _info = _learn_from_scratch(folder)
    src = compose.screen_source(paths.load_config(), ImageFrames([]))
    src.bglyphs = buffs.BuffGlyphs(gpath)           # the digits learned from scratch (a scratch file, not the player's)
    src.watch_buffs(["4101001"], folder=str(folder))
    f, _ = _feed()
    f.party_n = 3
    f.sync_watch(_prof_who())
    bad, seen = [], []
    t0 = 1000.0
    for k, fl in enumerate(files):
        im = Image.open(fl).convert("RGB")
        src._buffs(t0 + k * 0.5, im.crop(buffs.strip_box(im.size)), im.crop(tuple(vitals.strip_box(im.size, (1920, 1080)))))
        for tt, kind, v in src.take_facts():
            f.fact(tt, kind, v, None)
        pb = f.section_usage(0, None)["party_buffs"].get("4101001")
        if pb:
            seen.append((k, pb["level"], pb["src"], pb["est"]))
    lv = ((f.model.data.get("party_levels") or {}).get("4101001") or {}).get("?", {}).get("level")
    if lv != 7:
        bad.append(f"learned level {lv} (expected 7 = 160 s)")
    first = next((x for x in seen if x[2] == "learned"), None)
    if first is None or first[0] < 100:
        bad.append(f"first learned at frame {first} (expected after the first 2 -> 1 sync, frame ~126)")
    if any(x[2] == "learned" for x in seen if x[0] < 120):
        bad.append("learned from the mid-buff start")
    return (not bad), (f"level 7 learned at frame {first[0]} (t {first[0] / 2:.0f} s), stored for \"?\"" if not bad
                       else "; ".join(bad)), False


def _prof_who():
    p = _Prof()
    p.who = "tester"
    return p


def _cast_run(f, level, start=10.0, err=0.0, mid_way=False, off_first=True, sid="4101001"):
    """Feed one Haste at `level` (duration from the skill table + err) as the source would: an off fact, the icon on at
    `start`, a fact each second with the small minutes digit (60 s or more left) or the big digits (under 60 s).
    mid_way = the app starts with the icon already on (no off fact first, and the cast began before `start`)."""
    from components.skillbar import partyfx
    D = partyfx.duration(sid, level) + err
    if off_first and not mid_way:
        f.fact(start - 5.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)     # off long enough (PARTY_OFF_S)
    k = 0
    skip = 25 if mid_way else 0                      # mid-way: the first look is 25 s into the buff
    while D - k - skip > 0:
        left = D - k - skip
        t = start + k
        if left >= 60:
            f.fact(t, "buffs", {"on": [sid], "left": {}, "mins": {sid: int(left // 60)}}, None)
        else:
            f.fact(t, "buffs", {"on": [sid], "left": {sid: int(left)}, "mins": {}}, None)
        k += 1
    f.fact(start + k, "buffs", {"on": [], "left": {}, "mins": {}}, None)


def _learned(f, sid="4101001"):
    return {k: v["level"] for k, v in ((f.model.data.get("party_levels") or {}).get(sid) or {}).items()}


def run_party():
    from components.skillbar import feed, partyfx
    bad = []
    if [partyfx.speed("4101001", lv) for lv in (1, 7, 19, 20)] != [10, 16, 28, 30]:
        bad.append("Haste speed per level")
    sl = partyfx.party_slots({}, [], 100.0, buffs={"4101001": {"since": 90.0, "ends": 250.0, "est": True, "level": 7,
                                                                "src": "assumed"}})
    if not sl or sl[0].get("cap") != "+16" or sl[0].get("mark") != "boot" or partyfx.speed("4101001", 7) != 16:
        bad.append(f"Haste slot speed: {sl}")
    f, _ = _feed()
    p = _prof_who()
    f.party_n = 1                                       # alone: nothing asked for
    f.sync_watch(p)
    if f.hub.source.watched != [GUARD] or f.party_watch:
        bad.append(f"solo: watched {f.hub.source.watched} party {f.party_watch}")
    f.party_n = 3
    f.sync_watch(p)
    if f.hub.source.watched != [GUARD, "1101004", "2301003", "4101001"] or f.party_watch != ["1101004", "2301003", "4101001"]:   # one id per picture
        bad.append(f"party: watched {f.hub.source.watched}")
    # --- first sight (the app starts with the icon on: no t0), exact digits
    f.fact(10.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {}}, None)
    pb = f.section_usage(10.0, None)["party_buffs"]
    if pb.get("4101001") != {"since": 10.0, "ends": 170.0, "est": True, "mins": None, "level": 7, "src": "assumed",
                              "mine": False}:
        bad.append(f"first sight (level 7 = 160 s): {pb}")
    f.fact(40.0, "buffs", {"on": ["4101001"], "left": {"4101001": 57}}, None)
    f.fact(41.0, "buffs", {"on": ["4101001"], "left": {"4101001": 56}}, None)
    pb = f.section_usage(41.0, None)["party_buffs"]["4101001"]
    if pb["ends"] != 97.0 or pb["est"] or _learned(f):
        bad.append(f"digits / mid-way start learned something: {pb} {_learned(f)}")
    # --- slots
    party = {"ok": True, "t": 50.0, "members": [{"name": "Sin", "job": "Assassin", "map": "A"}]}
    pbs = f.section_usage(41.0, None)["party_buffs"]
    sl = partyfx.party_slots(party, [], 50.0, "A", buffs=pbs)
    h = sl[0] if sl else {}
    if len(sl) != 1 or h["id"] != "4101001" or h["from"] != ["Sin"] or not h["on"] or h["ends"] != 97.0 or h["est"] or h["dim"]:
        bad.append(f"slot exact (one caster): {sl}")
    sl = partyfx.party_slots(None, [], 50.0, "A", buffs=pbs)          # no party link at all: still a slot
    if len(sl) != 1 or sl[0]["from"] != ["Haste"] or not sl[0]["on"]:
        bad.append(f"slot without the relay: {sl}")
    two = {"ok": True, "t": 50.0, "members": [{"name": "Sin", "job": "Assassin"}, {"name": "Bo", "job": "Bandit"}]}
    sl = partyfx.party_slots(two, [], 50.0, "A", buffs=pbs)
    if len(sl) != 1 or sl[0]["from"] != ["Haste"]:
        bad.append(f"two casters = one Haste slot: {sl}")
    bandit = {"ok": True, "t": 50.0, "members": [{"name": "Bo", "job": "Bandit", "map": "A"}]}
    sl = partyfx.party_slots(bandit, [], 50.0, "A", buffs=pbs)
    if len(sl) != 1 or sl[0]["id"] != "4101001" or sl[0]["from"] != ["Bo"]:
        bad.append(f"a Bandit's Haste is the same slot: {sl}")
    sl = partyfx.party_slots(bandit, [], 50.0, "A", buffs={})
    if len(sl) != 1 or sl[0]["on"] or sl[0]["ends"] is not None:
        bad.append(f"caster but no icon = off slot: {sl}")
    if partyfx.party_slots(None, [], 50.0, "A", buffs={}) or partyfx.party_slots({"ok": True, "t": 50.0, "members": []}, [], 50.0, "A", buffs=None):
        bad.append("nothing known and no icon must give no slot")
    if partyfx.casters(bandit, "4201001", 50.0) != ["Bo"] or partyfx.casters(bandit, "4101001", 50.0) != ["Bo"]:
        bad.append("casters of the twin ids")
    est = {"4101001": {"since": 10.0, "ends": None}}
    h = partyfx.party_slots(party, [], 50.0, "A", buffs=est)[0]
    if not h["on"] or not h["est"] or h["ends"] != 10.0 + 160 or partyfx.duration("4101001", 7) != 160:
        bad.append(f"slot estimate: {h}")
    # --- learning the level from a fresh cast, every table end of the range
    for lv in (1, 7, 19, 20):
        g, _ = _feed()
        g.party_n = 3
        g.sync_watch(_prof_who())
        _cast_run(g, lv)
        want = {"?": lv}
        if _learned(g) != want:
            bad.append(f"learn level {lv}: stored {_learned(g)}")
    g, _ = _feed()
    g.party_n = 3
    g.sync_watch(_prof_who())
    _cast_run(g, 7, err=5.0)                         # 165 s is 5 s from both 160 and 170 (+-4): unknown
    if _learned(g):
        bad.append(f"5 s off the table was learned: {_learned(g)}")
    _cast_run(g, 7, err=3.0, start=300.0)            # 163 s: within +-4
    if _learned(g) != {"?": 7}:
        bad.append(f"3 s off the table not snapped: {_learned(g)}")
    g, _ = _feed()
    g.party_n = 3
    g.sync_watch(_prof_who())
    _cast_run(g, 7, mid_way=True)                    # app started mid-buff: no t0, nothing learned
    if _learned(g):
        bad.append(f"mid-way start learned {_learned(g)}")
    # a missed look mid-buff (icon off for under PARTY_OFF_S) is not a fresh cast: nothing learned from what is left
    g, _ = _feed()
    g.party_n = 3
    g.sync_watch(_prof_who())
    g.fact(10.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 2}}, None)
    g.fact(40.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)
    g.fact(41.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 2}}, None)
    g.fact(142.0, "buffs", {"on": ["4101001"], "left": {"4101001": 59}, "mins": {}}, None)   # end 201 = 160 s after the false t0: would read as L7
    if _learned(g):
        bad.append(f"a missed look was taken for a fresh cast: {_learned(g)}")
    # the estimate before the end is known: m0 narrows the level; a learned level is the prior of the next cast
    g, _ = _feed()
    g.party_n = 3
    g.sync_watch(_prof_who())
    g.fact(5.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)
    g.fact(10.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 4}}, None)      # 240-299 s: L15-19
    pb = g.section_usage(10.0, None)["party_buffs"]["4101001"]
    if pb["level"] != 17 or pb["src"] != "range" or pb["ends"] != 10.0 + 260 or not pb["est"]:
        bad.append(f"minutes digit 4 at the cast: {pb}")
    g.model.data["party_levels"] = {"4101001": {"?": {"level": 19, "t": 1.0}}}
    g.fact(296.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)
    g.fact(301.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 4}}, None)
    pb = g.section_usage(301.0, None)["party_buffs"]["4101001"]
    if pb["level"] != 19 or pb["src"] != "learned" or pb["ends"] != 301.0 + 280:
        bad.append(f"stored level 19 + minutes digit 4: {pb}")
    g.fact(396.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)
    g.fact(401.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 2}}, None)     # 19 does not fit 120-179
    pb = g.section_usage(401.0, None)["party_buffs"]["4101001"]
    if pb["src"] != "range" or pb["level"] != 6 or pb["ends"] != 401.0 + 150:
        bad.append(f"stored level outside the digit's range: {pb}")
    # --- recasts and the icon going off
    h = _prof_who()
    f, _ = _feed()
    f.party_n = 3
    f.sync_watch(h)
    f.fact(42.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)
    if f.party_buff:
        bad.append(f"off: {f.party_buff}")
    f.fact(100.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 2}}, None)
    f.fact(101.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 2}}, None)
    pb = f.section_usage(101.0, None)["party_buffs"]["4101001"]
    if not pb["est"] or pb["mins"] != 2 or not 120 <= pb["ends"] - 101.0 <= 179:
        bad.append(f"minutes 2 estimate: {pb}")
    f.fact(102.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 1}}, None)
    pb = f.section_usage(102.0, None)["party_buffs"]["4101001"]
    if pb["est"] or abs(pb["ends"] - (101.5 + 120)) > 1e-6:
        bad.append(f"minutes 2 -> 1 sync: {pb}")
    f.fact(170.0, "buffs", {"on": ["4101001"], "left": {"4101001": 53}, "mins": {}}, None)
    pb = f.section_usage(170.0, None)["party_buffs"]["4101001"]
    if pb["est"] or pb["ends"] != 223.0:
        bad.append(f"digits after sync: {pb}")
    f.fact(171.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 2}}, None)
    pb = f.section_usage(171.0, None)["party_buffs"]["4101001"]
    if pb["since"] != 171.0 or not pb["est"] or pb["ends"] != 171.0 + 120 or pb["level"] != 3 or pb["src"] != "learned":
        bad.append(f"recast (minutes digit while < 60 s were left; level 3 was learned from the first cast): {pb}")
    f.fact(172.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 2}}, None)
    f.fact(173.0, "buffs", {"on": ["4101001"], "left": {}, "mins": {"4101001": 3}}, None)      # digit up = cast again
    pb = f.section_usage(173.0, None)["party_buffs"]["4101001"]
    if pb["since"] != 173.0:
        bad.append(f"recast (minutes digit up): {pb}")
    # digits unreadable PARTY_MISS facts in a row with time left (after exact digits) = cast again, not fresh
    f.fact(200.0, "buffs", {"on": ["4101001"], "left": {"4101001": 40}, "mins": {}}, None)
    for i in range(feed.PARTY_MISS):
        f.fact(201.0 + i, "buffs", {"on": ["4101001"], "left": {}, "mins": {}}, None)
    d = f.party_buff["4101001"]
    if d["since"] != 201.0 + feed.PARTY_MISS - 1 or d["ends"] is not None or d["fresh"]:
        bad.append(f"recast (digits gone): {d}")
    f.party_n = 1                                       # left the party: forgotten
    f.sync_watch(h)
    if f.party_buff or f.party_watch:
        bad.append(f"left party: {f.party_buff}")
    return (not bad), ("party buff logic ok" if not bad else "; ".join(bad)), False


BLESS = "2301003"


def bless_icons(given=None):
    """Folder with the MeowDB Bless picture (skillicons' assets/skill_icons), or None."""
    from base import paths
    for c in (Path(given) if given else None,
              Path(os.environ["MAPLE_BLESS_ICONS"]) if os.environ.get("MAPLE_BLESS_ICONS") else None,
              paths.DATA / "assets" / "skill_icons",
              Path(os.environ.get("LOCALAPPDATA", "")) / "MapleClassicCompanion" / "assets" / "skill_icons"):
        if c and (c / f"{BLESS}.png").exists():
            return c
    return None


def run_bless_frames(icons=None):
    """Negative set: the Bless picture (searched together with Haste) over every burst fixture: no frame may show it
    (none of the recordings has a Bless; the Haste ones show only Haste). Reports the best score seen (margin to
    LOOSE_MIN) and, informationally, a synthetic positive (the picture pasted translucent over a strip). Skips without
    fixtures / the picture."""
    folder = bless_icons(icons)
    root = ROOT / "dev" / "fixtures" / "burst"
    dirs = [d for d in sorted(root.iterdir()) if any(d.glob("*.jpg"))] if root.is_dir() else []
    if not dirs or folder is None:
        return True, "skipped: needs dev/fixtures/burst and assets/skill_icons/2301003.png (MAPLE_BLESS_ICONS)", True
    from PIL import Image
    from engine import buffs
    hf = haste_icons(icons)
    n, hits, best, haste_n, haste_found = 0, [], (-1.0, ""), 0, 0
    for d in dirs:
        fs = sorted(d.glob("*.jpg"))
        size = Image.open(fs[0]).size
        sc = size[0] / buffs.REF_W
        ic = buffs.load_loose(folder, [BLESS], sc)
        both = {**ic, **(buffs.load_loose(hf, list(HASTE_IDS), sc) if hf else {})}
        for f in fs[::max(1, len(fs) // 150)]:
            im = Image.open(f).convert("RGB")
            st = im.crop(buffs.strip_box(im.size))
            det = {}
            got = buffs.find_loose(st, both, detail=det)
            n += 1
            if BLESS in got:
                hits.append(f"{d.name}/{f.name}")
            if det[BLESS][0] > best[0]:
                best = (det[BLESS][0], f"{d.name}/{f.name}")
            if d.name in (HASTE_FRAMES.name, HASTE_FRAMES2.name):
                haste_n += 1
                haste_found += any(h in got for h in HASTE_IDS)
    bad = []
    if hits:
        bad.append("Bless found in: " + ", ".join(hits[:4]))
    if haste_n and haste_found < 0.95 * haste_n:
        bad.append(f"Haste found on {haste_found}/{haste_n} frames of its recordings when searched with Bless")
    # informational positive: the picture pasted at 85 % over a strip (the game's own rendering differs)
    f0 = sorted(dirs[0].glob("*.jpg"))[0]
    im = Image.open(f0).convert("RGB")
    st = im.crop(buffs.strip_box(im.size)).copy()
    pic = Image.open(folder / f"{BLESS}.png").convert("RGBA")
    pic.putalpha(pic.getchannel("A").point(lambda v: int(v * 0.85)))
    st.paste(pic, (150 + 3, 3 + 3), pic)
    det = {}
    got = buffs.find_loose(st, buffs.load_loose(folder, [BLESS], im.size[0] / buffs.REF_W), detail=det)
    pos = f"synthetic positive {'found' if BLESS in got else 'NOT found'} (score {det[BLESS][0]})"
    return (not bad), (f"{n} frames of {len(dirs)} recordings: no Bless hit, best score {best[0]:.2f} at {best[1]} "
                       f"(needs {buffs.LOOSE_MIN['full']}/{buffs.LOOSE_MIN['outer']}); {pos}" if not bad else "; ".join(bad)), False


class _BlessProf(_Prof):
    def char(self):
        return {"skills": {"2001000": {"value": 2}, BLESS: {"value": 5}}}


def _bless_feed(own=True, party_n=1):
    from components.skillbar import feed
    f = feed.Feed(hub=_Hub())
    f._watch_ids = lambda prof: [GUARD, BLESS] if own else [GUARD]
    f._write_casts = lambda: None
    f._log_events = lambda ev: None
    f.party_n = party_n
    p = _BlessProf() if own else _Prof()
    f.begin(p, 0.0)
    f.sync_watch(p)
    return f, p


def _pb(f, t=0.0):
    return f.section_usage(t, None)["party_buffs"].get(BLESS)


def _heal_n(f):
    return sum(v.get("n", 0) for v in (f.model.data.get("heal") or {}).values())


def run_bless():
    """Bless as a party buff (Cleric/Priest/Bishop) and as my own skill: captions, party slot, level learning, the own-vs-party
    rule, own timer, Heal average kept free of Bless, the engine's `digits` rule."""
    from components.skillbar import partyfx
    bad = []
    # captions / details at levels 1, 5, 20 (the skill text), Haste unchanged
    want = {1: ("-5%", "damage taken -5%, Heal +1%"), 5: ("-7%", "damage taken -7%, Heal +3%"),
            20: ("-15%", "damage taken -15%, Heal +10%")}
    for lv, (c, dt) in want.items():
        got = partyfx.caption(BLESS, lv)
        if got != (c, "shield") or partyfx.details(BLESS, lv) != dt:
            bad.append(f"caption L{lv}: {got} / {partyfx.details(BLESS, lv)}")
    if [partyfx.duration(BLESS, lv) for lv in (1, 5, 20)] != [100, 140, 300] or partyfx.caption("4101001", 7) != ("+16", "boot"):
        bad.append("durations / Haste caption")
    # --- party Bless: slot from the icon alone (no relay), assumed level 10 = 190 s, "-10%"
    sl = partyfx.party_slots({"ok": True, "t": 50.0, "members": [{"name": "Cler", "job": "Cleric", "map": "A"}]}, [], 50.0, "A",
                             buffs={BLESS: {"since": 40.0, "ends": None, "est": True}})
    sl = [x for x in sl if x["id"] == BLESS]            # (a Cleric in the party also gives the Heal slot)
    h = sl[0] if sl else {}
    if len(sl) != 1 or h["id"] != BLESS or h["from"] != ["Cler"] or not h["on"] or h["level"] != 10 or h["dur"] != 190 \
            or h["cap"] != "-10%" or h["mark"] != "shield" or h["ends"] != 230.0 or not h["est"] or h["lvl_src"] != "assumed":
        bad.append(f"party Bless slot: {sl}")
    sl = partyfx.party_slots(None, [], 50.0, "A", buffs={BLESS: {"since": 40.0, "ends": 300.0, "est": False, "level": 5, "src": "learned"}})
    if not sl or sl[0]["cap"] != "-7%" or sl[0]["lvl_src"] != "learned" or sl[0]["ends"] != 300.0 or sl[0]["from"] != ["Bless"]:
        bad.append(f"party Bless slot, learned level 5: {sl}")
    if partyfx.party_slots(None, [], 50.0, "A", buffs={BLESS: {"since": 40.0, "mine": True}}):
        bad.append("my own Bless must not be a party slot")
    # --- level learning on a fresh party cast, like Haste
    for lv in (1, 5, 20):
        g, _ = _bless_feed(own=False, party_n=3)
        if BLESS not in g.party_watch:
            bad.append(f"Bless not watched in a party: {g.party_watch}")
            break
        _cast_run(g, lv, sid=BLESS)
        if _learned(g, BLESS) != {"?": lv}:
            bad.append(f"learn Bless L{lv}: {_learned(g, BLESS)}")
    g, _ = _bless_feed(own=False, party_n=3)
    g.fact(5.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)
    g.fact(10.0, "buffs", {"on": [BLESS], "left": {}, "mins": {BLESS: 2}}, None)           # 120-179 s: L3-8 -> a middle one
    pb = _pb(g, 10.0)
    if pb["mine"] or pb["src"] != "range" or pb["level"] not in (5, 6):
        bad.append(f"party Bless minutes 2 at the cast: {pb}")
    # --- own Bless (level 5 = 140 s, 21 MP)
    f, p = _bless_feed()
    if BLESS not in (f.hub.source.watched or []) or f.hub.source.digits != [BLESS]:
        bad.append(f"own Bless watch: {f.hub.source.watched} digits {getattr(f.hub.source, 'digits', None)}")
    f.fact(1.0, "buffs", {"on": []}, None)
    f.fact(1.5, "vitals", {"hp": [600, 600], "mp": [100, 100]}, None)
    f.fact(2.0, "vitals", {"hp": [600, 600], "mp": [79, 100]}, None)                         # -21: Bless L5
    if f.usage.last_cast.get(BLESS) != 2.0:
        bad.append(f"Bless cast not booked: {f.usage.last_cast} {_casts(f)}")
    f.fact(2.5, "buffs", {"on": [BLESS]}, None)                                                # icon 0.5 s later: mine
    pb = _pb(f, 2.5)
    if not pb or not pb["mine"] or pb["level"] != 5 or pb["src"] != "own" or not pb["est"] or pb["ends"] != 142.0:
        bad.append(f"own Bless from a booked cast + icon: {pb}")
    sk = next((x for x in f.section_skills(3.0, p) if x["id"] == BLESS), None)
    tm = (sk or {}).get("timer") or {}
    if tm.get("ends") != 142.0 or tm.get("cap") != "-7%" or tm.get("dur") != 140 or tm.get("level") != 5 or "Heal +3%" not in tm.get("detail", ""):
        bad.append(f"own timer: {sk and sk.get('timer')}")
    if partyfx.party_slots(None, [], 3.0, "A", buffs=f.section_usage(3.0, None)["party_buffs"]):
        bad.append("own Bless showed in the party slot")
    f.fact(20.0, "buffs", {"on": [BLESS], "left": {}, "mins": {BLESS: 2}}, None)
    f.fact(120.0, "buffs", {"on": [BLESS], "left": {BLESS: 22}, "mins": {}}, None)            # the digits make the end exact
    pb = _pb(f, 120.0)
    if pb["est"] or pb["ends"] != 142.0 or not pb["mine"]:
        bad.append(f"own Bless exact end from the digits: {pb}")
    # a cast booked 0.4 s AFTER the icon was first seen still makes it mine
    f2, _p2 = _bless_feed()
    f2.fact(1.0, "buffs", {"on": []}, None)
    f2.fact(4.5, "vitals", {"hp": [600, 600], "mp": [100, 100]}, None)
    f2.fact(5.0, "buffs", {"on": [BLESS]}, None)
    pb = dict(_pb(f2, 5.0))
    f2.fact(5.4, "vitals", {"hp": [600, 600], "mp": [79, 100]}, None)
    f2.fact(6.0, "buffs", {"on": [BLESS], "left": {}, "mins": {}}, None)
    pb2 = _pb(f2, 6.0)
    if pb["mine"] or not pb2["mine"] or pb2["ends"] != 5.4 + 140:
        bad.append(f"cast booked just after the icon: {pb} -> {pb2}")
    # a refresh (own cast again, icon stays) restarts the timer
    f.fact(125.0, "vitals", {"hp": [600, 600], "mp": [100, 100]}, None)
    f.fact(126.0, "vitals", {"hp": [600, 600], "mp": [79, 100]}, None)
    f.fact(127.0, "buffs", {"on": [BLESS]}, None)
    pb = _pb(f, 127.0)
    if not pb["mine"] or pb["since"] != 126.0 or pb["ends"] != 126.0 + 140 or not pb["est"]:
        bad.append(f"refresh by my own cast: {pb}")
    # a party recast over my Bless (digit up, no cast of mine near it) is a party Bless from then on
    f.fact(130.0, "buffs", {"on": [BLESS], "left": {}, "mins": {BLESS: 1}}, None)
    f.fact(135.0, "buffs", {"on": [BLESS], "left": {}, "mins": {BLESS: 2}}, None)
    pb = _pb(f, 135.0)
    if pb["mine"]:
        bad.append(f"a party recast over my Bless stayed mine: {pb}")
    # a party Bless while I own the skill: icon on with no booked cast near it -> party slot, no own timer
    f3, p3 = _bless_feed()
    f3.fact(1.0, "buffs", {"on": []}, None)
    f3.fact(300.0, "buffs", {"on": [BLESS], "left": {}, "mins": {BLESS: 3}}, None)
    pb = _pb(f3, 300.0)
    sk = next((x for x in f3.section_skills(300.0, p3) if x["id"] == BLESS), None)
    sl = partyfx.party_slots(None, [], 300.0, "A", buffs=f3.section_usage(300.0, None)["party_buffs"])
    if pb["mine"] or (sk or {}).get("timer") or len(sl) != 1 or not sl[0]["on"]:
        bad.append(f"party Bless while I own it: {pb} timer {sk and sk.get('timer')} slots {sl}")
    # --- Heal average free of Bless
    h = {"kind": "heal", "name": "Heal", "amount": 100, "t": 1.0}
    f4, _ = _bless_feed()
    f4._heal_stats([h])
    n1 = _heal_n(f4)
    f4.fact(2.0, "buffs", {"on": [BLESS]}, None)
    f4._heal_stats([h])
    n2 = _heal_n(f4)
    f4.fact(3.0, "buffs", {"on": []}, None)
    f4._heal_stats([h])
    n3 = _heal_n(f4)
    if (n1, n2, n3) != (1, 1, 2):
        bad.append(f"Heal average with Bless on: {(n1, n2, n3)} (want 1, 1, 2)")
    # --- engine: `digits` ids (owned: a cut exists) go through the loose path when their picture is there
    import shutil
    import tempfile
    from PIL import Image
    from base import paths
    from launcher import compose
    from engine.sources.screen import ImageFrames
    tmp = Path(tempfile.mkdtemp(prefix="bless_digits_"))
    try:
        (tmp / "templates" / "skill_icons").mkdir(parents=True)
        (tmp / "pics").mkdir()
        for q in (tmp / "templates" / "skill_icons" / f"{BLESS}.png", tmp / "templates" / "skill_icons" / f"{GUARD}.png",
                  tmp / "pics" / f"{BLESS}.png"):
            Image.new("RGBA", (34, 35), (90, 90, 160, 255)).save(q)         # dummy pictures, not game art
        old = paths.DATA
        paths.DATA = tmp
        try:
            src = compose.screen_source(paths.load_config(), ImageFrames([]))
            src.watch_buffs([GUARD, BLESS], folder=str(tmp / "pics"), digits=[BLESS])
            a = (src.buff_ids, src.loose_ids)
            src.watch_buffs([GUARD, BLESS], folder=str(tmp / "pics"))
            b = (src.buff_ids, src.loose_ids)
            src.watch_buffs([GUARD, BLESS], folder=str(tmp / "nopics"), digits=[BLESS])
            c = (src.buff_ids, src.loose_ids)
        finally:
            paths.DATA = old
        if a != ([GUARD], [BLESS]) or b != ([GUARD, BLESS], []) or c != ([GUARD, BLESS], []):
            bad.append(f"engine digits rule: {a} {b} {c}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (not bad), ("Bless logic ok (captions, party slot, learning, own vs party, own timer, Heal, engine rule)" if not bad
                       else "; ".join(bad)), False


RAGE = "1101004"
INV = "2301002"


class _InvProf(_Prof):
    def char(self):
        return {"skills": {"2001000": {"value": 2}, INV: {"value": 3}}}


def run_invincible():
    """Invincible (Cleric, self only): own timer at my level, never a party slot, always mine (no booked cast needed),
    does not keep Heal rises out of the average (only Bless boosts Heal)."""
    from components.skillbar import feed, partyfx
    bad = []
    if partyfx.caption(INV, 1) != ("-1%", "shield") or partyfx.caption(INV, 20) != ("-20%", "shield"):
        bad.append(f"captions {partyfx.caption(INV, 1)} {partyfx.caption(INV, 20)}")
    if partyfx.self_ids() != [INV] or INV not in partyfx.own_ids() or partyfx.heal_boost_ids() != [BLESS]:
        bad.append(f"ids self {partyfx.self_ids()} own {partyfx.own_ids()} heal_boost {partyfx.heal_boost_ids()}")
    party = {"ok": True, "t": 100.0, "members": [{"name": "Cler", "job": "Cleric", "map": "A"}]}
    sl = partyfx.party_slots(party, [], 100.0, "A", buffs={INV: {"since": 90.0, "ends": 200.0, "est": True, "level": 3,
                                                                    "src": "assumed"}})
    if any(s["id"] == INV for s in sl):
        bad.append(f"Invincible got a party slot: {sl}")
    for party_n in (1, 3):
        f = feed.Feed(hub=_Hub())
        f._watch_ids = lambda prof: [GUARD, INV]
        f._write_casts = lambda: None
        f._log_events = lambda ev: None
        f.party_n = party_n
        p = _InvProf()
        f.begin(p, 0.0)
        f.sync_watch(p)
        f.fact(5.0, "buffs", {"on": [], "left": {}, "mins": {}}, None)
        f.fact(10.0, "buffs", {"on": [INV], "left": {}, "mins": {INV: 2}}, None)     # no booked cast: still mine
        tm = f.own_timer(INV)
        if not tm or tm["level"] != 3 or tm["dur"] != 120 or tm["cap"] != "-3%" or abs(tm["ends"] - 130.0) > 0.01 \
                or not tm["est"]:
            bad.append(f"own timer (party_n {party_n}): {tm}")
        pb = f.section_usage(10.0, None)["party_buffs"].get(INV) or {}
        if not pb.get("mine"):
            bad.append(f"not mine (party_n {party_n}): {pb}")
        f.fact(80.0, "buffs", {"on": [INV], "left": {INV: 50}, "mins": {}}, None)   # digits: exact end
        tm = f.own_timer(INV)
        if not tm or tm["est"] or abs(tm["ends"] - 130.0) > 0.01:
            bad.append(f"own timer from digits (party_n {party_n}): {tm}")
    return not bad, "Invincible logic ok (captions, no party slot, always mine, own timer, Heal untouched)" \
        if not bad else "; ".join(bad), False
RAGE_FRAMES = ROOT / "dev" / "fixtures" / "burst" / "20261108_224714"      # a party Fighter's Rage (L5, 170 s), cast fresh at ~6 s
RAGE_ON = (6.0, 178.1)         # the icon shows from the first frame at 6.18 to the last at 177.97 (judged on crops)


def _pic_dir(sid, given=None):
    from base import paths
    for c in (Path(given) if given else None,
              Path(os.environ["MAPLE_BLESS_ICONS"]) if os.environ.get("MAPLE_BLESS_ICONS") else None,
              paths.DATA / "assets" / "skill_icons",
              Path(os.environ.get("LOCALAPPDATA", "")) / "MapleClassicCompanion" / "assets" / "skill_icons"):
        if c and (c / f"{sid}.png").exists():
            return c
    return None


def run_rage_frames(icons=None):
    """Positive + negative set for Rage (MeowDB 1101004 picture): found on every frame of 20261108_224714 inside RAGE_ON (the icon
    is translucent, greyer as its time runs down, and under the last minute's digits) and on none outside; never found in any
    other burst recording (searched together with Haste and Bless). Reports the lowest positive and the best negative score."""
    folder = _pic_dir(RAGE, icons)
    root = ROOT / "dev" / "fixtures" / "burst"
    files = sorted(RAGE_FRAMES.glob("*.jpg")) if RAGE_FRAMES.is_dir() else []
    if not files or folder is None:
        return True, "skipped: needs dev/fixtures/burst/20261108_224714 and assets/skill_icons/1101004.png (MAPLE_BLESS_ICONS)", True
    from PIL import Image
    from engine import buffs
    ic = buffs.load_loose(folder, [RAGE])
    bad, pos, neg_in, n_on, n_off = [], [], [], 0, 0
    for f in files:
        t = _file_t(f)
        im = Image.open(f).convert("RGB")
        det = {}
        got = buffs.find_loose(im.crop(buffs.strip_box(im.size)), ic, detail=det)
        if RAGE_ON[0] <= t <= RAGE_ON[1]:
            n_on += 1
            pos.append(det[RAGE][0])
            if RAGE not in got:
                bad.append(f"missed at {t}")
        else:
            n_off += 1
            neg_in.append(det[RAGE][0])
            if RAGE in got:
                bad.append(f"false hit at {t}")
    hf, bf = _pic_dir(HASTE_IDS[0], icons), _pic_dir(BLESS, icons)
    n, best, hits = 0, (-1.0, ""), []
    for d in sorted(root.iterdir()):
        fs = sorted(d.glob("*.jpg"))
        if d == RAGE_FRAMES or not fs:
            continue
        sc = Image.open(fs[0]).size[0] / buffs.REF_W
        both = buffs.load_loose(folder, [RAGE], sc)
        if hf:
            both.update(buffs.load_loose(hf, list(HASTE_IDS), sc))
        if bf:
            both.update(buffs.load_loose(bf, [BLESS], sc))
        for f in fs[::max(1, len(fs) // 150)]:
            im = Image.open(f).convert("RGB")
            det = {}
            got = buffs.find_loose(im.crop(buffs.strip_box(im.size)), both, detail=det)
            n += 1
            if RAGE in got:
                hits.append(f"{d.name}/{f.name}")
            if det[RAGE][0] > best[0]:
                best = (det[RAGE][0], f"{d.name}/{f.name}")
    if hits:
        bad.append("Rage found in other recordings: " + ", ".join(hits[:4]))
    return (not bad), (f"{n_on} frames with Rage all found (lowest score {min(pos):.2f}), {n_off} without it clean (best {max(neg_in):.2f}); "
                       f"{n} frames of the other recordings clean (best {best[0]:.2f} at {best[1]}; needs {buffs.LOOSE_MIN['full']}/{buffs.LOOSE_MIN['outer']})"
                       if not bad else "; ".join(bad[:6])), False


def _rage_replay(folder, glyph_path, keep=False):
    """20261108_224714 through ScreenSource._buffs + Feed as the tracker does, Rage watched as a party buff (digits wanted: this
    dev folder may hold a cut of it). Returns (feed, log [(t, on, left, mins, party_buffs entry)], source)."""
    from PIL import Image
    from base import paths
    from launcher import compose
    from engine import buffs, vitals
    from engine.sources.screen import ImageFrames
    if glyph_path.exists() and not keep:
        glyph_path.unlink()
    src = compose.screen_source(paths.load_config(), ImageFrames([]))
    src.bglyphs = buffs.BuffGlyphs(glyph_path)
    src.watch_buffs([RAGE], folder=str(folder), digits=[RAGE])
    f, _ = _feed()
    f.party_n = 3
    f.sync_watch(_prof_who())
    log = []
    for fl in sorted(RAGE_FRAMES.glob("*.jpg")):
        t = _file_t(fl)
        im = Image.open(fl).convert("RGB")
        src._buffs(1000.0 + t, im.crop(buffs.strip_box(im.size)), im.crop(tuple(vitals.strip_box(im.size, (1920, 1080)))))
        for tt, kind, v in src.take_facts():
            if kind != "buffs":
                continue
            f.fact(tt, kind, v, None)
            pb = f.section_usage(0, None)["party_buffs"].get(RAGE)
            log.append((tt - 1000.0, v.get("on"), (v.get("left") or {}).get(RAGE), (v.get("mins") or {}).get(RAGE), dict(pb) if pb else None))
    return f, log, src


def run_rage_learn(icons=None):
    """Rage replay with the countdown digits learned from scratch on this one recording: (1) the fresh cast is seen at ~6.2 s (level 5
    assumed), (2) the seconds reads all agree on one end (175.4 +- 1) and the level learned from the full run is 5 (duration end - cast
    ~169.2 s, table 170, tolerance +-2 for 5 s steps), (3) only minutes digits 1 and 2 are learned (no 3 / 4 from jitter variants of the
    2), none wrong, (4) a second pass with that store reads the minutes digit and learns level 5 from the 2 -> 1 sync before the big
    digits (end within 1.5 s of the exact one)."""
    folder = _pic_dir(RAGE, icons)
    if not RAGE_FRAMES.is_dir() or folder is None:
        return True, "skipped: needs dev/fixtures/burst/20261108_224714 and assets/skill_icons/1101004.png", True
    from components.skillbar import partyfx
    bad = []
    gp = _scratch() / "glyphs_rage.json"
    f, log, src = _rage_replay(folder, gp)
    first = next((x for x in log if x[4]), None)
    if first is None or not 5.9 <= first[0] <= 6.6 or first[4]["level"] != 5 or first[4]["src"] != "assumed":
        bad.append(f"fresh cast at the start: {first}")
    reads = [round(t + left, 1) for t, on, left, mins, pb in log if left]
    if len(reads) < 30 or max(reads) - min(reads) > 1.0 or not 174.4 <= reads[0] <= 176.4:
        bad.append(f"seconds reads: {len(reads)} reads, ends {min(reads, default=None)}-{max(reads, default=None)}")
    lv = ((f.model.data.get("party_levels") or {}).get(RAGE) or {}).get("?", {}).get("level")
    if lv != 5:
        bad.append(f"learned level {lv} (want 5)")
    dur = (reads[0] - first[0]) if reads and first else None
    mins = sorted(src.bglyphs.known("minutes"))
    secs = sorted(src.bglyphs.known("seconds"))
    if mins != ["1", "2"]:
        bad.append(f"minutes digits learned {mins} (want only 1, 2)")
    if len(secs) < 8:
        bad.append(f"seconds digits learned {secs}")
    wrong = [x for x in log if x[2] and not 0 < x[2] < 60]
    if wrong:
        bad.append(f"out-of-range seconds read {wrong[:2]}")
    # second pass with the stored digits: the minutes digit is read from ~30 s and the 2 -> 1 step gives an end
    f2, log2, _ = _rage_replay(folder, gp, keep=True)
    m = [(t, mins_) for t, on, left, mins_, pb in log2 if mins_]
    steps = [i for i in range(1, len(m)) if m[i][1] == 1 and m[i - 1][1] == 2]
    synced = next((x for x in log2 if x[4] and not x[4]["est"] and x[0] < 116), None)
    if not m or any(v not in (1, 2) for _t, v in m) or len(steps) != 1:
        bad.append(f"minutes reads on the second pass: {m[:2]} ... steps {len(steps)}")
    if synced is None or abs(synced[4]["ends"] - 1000.0 - 175.4) > 1.5:
        bad.append(f"2 -> 1 sync end: {synced and synced[4]}")
    lv2 = ((f2.model.data.get("party_levels") or {}).get(RAGE) or {}).get("?", {}).get("level")
    if lv2 != 5:
        bad.append(f"level from the minutes sync: {lv2}")
    # per-skill tolerance: Rage's 5 s steps get +-2, Haste / Bless keep +-4
    if (partyfx.level_tol(RAGE), partyfx.level_tol("4101001"), partyfx.level_tol("2301003")) != (2.0, 4.0, 4.0) \
            or partyfx.level_for_duration(RAGE, 172.6) is not None or partyfx.level_for_duration(RAGE, 171.5) != 5 \
            or partyfx.level_for_duration("4101001", 163.9) != 7:
        bad.append("per-skill level tolerance")
    return (not bad), (f"fresh cast at {first[0]:.1f} s, end {reads[0]} -> duration {dur:.1f} s = level {lv} (170 s), seconds learned {''.join(secs)}, "
                       f"minutes {''.join(mins)}; second pass: 2 -> 1 sync end {synced[4]['ends'] - 1000.0:.1f}, level {lv2}" if not bad
                       else "; ".join(bad)), False


def run_rage():
    """Rage as a party buff (Fighter / Crusader / Hero): caption and detail from the text, slot, durations from the level texts when the
    kinds file has none, jobs."""
    import unittest.mock as mock
    from components.skillbar import partyfx
    from gamedata import translate
    bad = []
    if partyfx.caption(RAGE, 1) != ("+10", None) or partyfx.caption(RAGE, 5) != ("+14", None) or partyfx.caption(RAGE, 30) != ("+40", None):
        bad.append(f"Rage captions {[partyfx.caption(RAGE, lv) for lv in (1, 5, 30)]}")
    if partyfx.details(RAGE, 5) != "Attack +14, Weapon Def. -14":
        bad.append(f"Rage detail {partyfx.details(RAGE, 5)}")
    if [partyfx.duration(RAGE, lv) for lv in (1, 5, 30)] != [150, 170, 300]:
        bad.append("Rage durations")
    with mock.patch.object(translate, "skill_kind", lambda s: None):          # no kinds entry: the level texts say "for N sec"
        if [partyfx.duration(RAGE, lv) for lv in (1, 5, 30)] != [150, 170, 300] or partyfx.duration("2301003", 20) != 300 \
                or partyfx.level_tol(RAGE) != 2.0:
            bad.append("durations from the level texts")
    for job in ("Fighter", "Crusader", "Hero"):
        sl = partyfx.party_slots({"ok": True, "t": 50.0, "members": [{"name": "Fi", "job": job, "map": "A"}]}, [], 50.0, "A", buffs={})
        if [s["id"] for s in sl] != [RAGE] or sl[0]["on"] or sl[0]["from"] != ["Fi"]:
            bad.append(f"{job}: {sl}")
    sl = partyfx.party_slots(None, [], 100.0, "A", buffs={RAGE: {"since": 90.0, "ends": None, "est": True}})
    h = sl[0] if sl else {}
    if len(sl) != 1 or h["level"] != 5 or h["dur"] != 170 or h["cap"] != "+14" or h["mark"] is not None or h["ends"] != 260.0 or not h["on"] \
            or h["from"] != ["Rage"] or "Weapon Def. -14" not in h["detail"]:
        bad.append(f"Rage slot from the icon alone: {sl}")
    # level learning, like Haste (170 s = L5, 300 s = L30; 5 s steps)
    for lv in (1, 5, 30):
        g, _ = _bless_feed(own=False, party_n=3)
        _cast_run(g, lv, sid=RAGE)
        if _learned(g, RAGE) != {"?": lv}:
            bad.append(f"learn Rage L{lv}: {_learned(g, RAGE)}")
    g, _ = _bless_feed(own=False, party_n=3)
    _cast_run(g, 5, err=2.6, sid=RAGE)                    # 172.6 s is 2.4 / 2.6 from two levels: unknown
    if _learned(g, RAGE):
        bad.append(f"2.6 s off a 5 s step was learned: {_learned(g, RAGE)}")
    return (not bad), ("Rage logic ok (captions, durations from texts, slots, jobs, learning L1/L5/L30, tolerance)" if not bad else "; ".join(bad)), False


if __name__ == "__main__":
    a = sys.argv[1:]
    kw = {}
    if "--frames" in a:
        kw["frames"] = a[a.index("--frames") + 1]
    if "--icons" in a:
        kw["icons"] = a[a.index("--icons") + 1]
    for name, fn in (("frames", lambda: run_frames(**kw)), ("logic", run_logic), ("haste", run_haste), ("glyphs", run_glyphs), ("learn", run_learn), ("party", run_party), ("bless", run_bless), ("bless_frames", run_bless_frames), ("rage", run_rage), ("rage_frames", run_rage_frames), ("rage_learn", run_rage_learn), ("invincible", run_invincible)):
        ok, note, skipped = fn()
        print(f"{'SKIP' if skipped else 'PASS' if ok else 'FAIL'}  {name}: {note}")
        if not ok:
            sys.exit(1)
