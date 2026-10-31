"""Buff icon checks (engine/buffs.py + the skill bar's deferred cast decision):

    python dev/buffs_check.py [--frames DIR] [--icons DIR]

1. frames: Magic Guard's icon is found in dev/fixtures/burst/20261031_142412/ exactly when it was on
   (12.5-27.5 s and 35.5-59 s), and no other icon is found anywhere. Needs the local frames (Nexon imagery,
   git-ignored) and the player's skill icons (templates/skill_icons); SKIPS (counts as pass) without them.
2. logic: components/skillbar/feed.py with synthetic facts: 8 MP drop + icon flip 0.5 s later = Magic Guard
   cast; drop without a flip = Energy Bolt after ~1.4 s (no attack pulse before); Guard off without an MP drop =
   state change only.
Used by dev/release_check.py (run_frames / run_logic return (ok, note, skipped)).
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

    def watch_buffs(self, ids):
        self.watched = list(ids)


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


if __name__ == "__main__":
    a = sys.argv[1:]
    kw = {}
    if "--frames" in a:
        kw["frames"] = a[a.index("--frames") + 1]
    if "--icons" in a:
        kw["icons"] = a[a.index("--icons") + 1]
    for name, fn in (("frames", lambda: run_frames(**kw)), ("logic", run_logic)):
        ok, note, skipped = fn()
        print(f"{'SKIP' if skipped else 'PASS' if ok else 'FAIL'}  {name}: {note}")
        if not ok:
            sys.exit(1)
