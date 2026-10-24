"""capture.py - how many frames a second the tracker looks at the game (Settings > General slider).

helper_settings.json "capture": {"fps": 1..60}. Default 2 (since 0.4.0). MapleStory doesn't draw more than 60
frames a second, so more would only re-read the same picture.

What the rate drives, while playing:
  - Windows Graphics Capture delivers at most `fps` frames a second (sources/wgc.py)
  - the quick look between EXP readings runs `fps` times a second (ScreenSource.poll: the
    "You have died." box, map checks when due, and future per-frame readers)
EXP readings keep their own schedule (every 2-30 s by time to level), and while not playing the
tracker looks every 10 s whatever this says.

Re-read on every call (cached by file time): the tracker follows a change within a second.
"""
import json
import os

MIN_FPS, MAX_FPS, DEFAULT_FPS = 1, 5, 2
HARD_MAX_FPS = 60        # the game draws <= 60 fps; MAX_FPS is the player-facing cap (decide by 0.3)

# measured 2026-10-23 (dev/bench.py + a poll loop, 1920x1080, death box + center grab per frame):
# 1 fps 1.1%, 5 1.7%, 10 3.0%, 20 7.2%, 30 8.4%, 60 20.6% of one core (Windows delivered ~48/s)
_COST = [(1, 1.1), (5, 1.7), (10, 3.0), (20, 7.2), (30, 8.4), (60, 20.6)]
_cache = {"key": None, "fps": DEFAULT_FPS}


def clamp(v):
    try:
        return max(MIN_FPS, min(MAX_FPS, int(round(float(v)))))
    except (TypeError, ValueError):
        return DEFAULT_FPS


def fps(path=None):
    """Frames a second from helper_settings.json (1-60)."""
    if path is None:
        from paths import DATA
        path = DATA / "helper_settings.json"
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        return DEFAULT_FPS
    if key != _cache["key"]:
        try:
            v = (json.loads(open(path, encoding="utf-8").read()).get("capture") or {}).get("fps")
        except (OSError, ValueError, AttributeError):
            v = None
        _cache.update(key=key, fps=clamp(v) if v is not None else DEFAULT_FPS)
    return _cache["fps"]


def cpu_estimate(f):
    """About how much of one CPU core the tracker uses at `f` frames a second (measured table,
    straight lines in between). The PC's other cores are untouched."""
    f = clamp(f)
    for (a, ca), (b, cb) in zip(_COST, _COST[1:]):
        if f <= b:
            return ca + (cb - ca) * (f - a) / (b - a)
    return _COST[-1][1]


def describe(f):
    """One line for the Settings slider."""
    f = clamp(f)
    core = cpu_estimate(f)
    cores = os.cpu_count() or 1
    whole = core / cores
    share = f"{whole:.1f}%" if whole >= 0.1 else "under 0.1%"
    gap = "a second" if f == 1 else f"{1000 // f} ms"
    return (f"{f} per second: about {core:.0f}% of one CPU core ({share} of this whole "
            f"{cores}-thread CPU). A death is seen within {gap}.")
