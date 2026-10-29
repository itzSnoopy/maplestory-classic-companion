"""capture.py - how many frames a second the tracker looks at the game (Settings > General slider).

helper_settings.json "capture": {"fps": 1..5}. Default 2 (since 0.4.0); cap 5 (player, 2026-10-24).
Bursts (boost) may go up to HARD_MAX_FPS = 60: MapleStory doesn't draw more than 60 frames a second,
so more would only re-read the same picture.

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
HARD_MAX_FPS = 60        # the game draws <= 60 fps; MAX_FPS is the player-facing cap (decided 2026-10-24)

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
    """Frames a second from helper_settings.json (MIN_FPS..MAX_FPS)."""
    if path is None:
        from base.paths import DATA
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


_boost = {"fps": 0, "until": 0.0}


def boost(f, seconds):
    """Let Windows deliver up to `f` frames a second for the next `seconds` (the regen clock's
    bursts of HP/MP looks, the skill bar's regen clock). Ends by itself; the slider's rate applies again."""
    import time
    _boost.update(fps=min(HARD_MAX_FPS, max(1, int(f))), until=time.time() + seconds)


def live_fps():
    """The rate Windows should deliver frames at right now: the slider, or a burst's boost."""
    import time
    f = fps()
    return max(f, _boost["fps"]) if time.time() < _boost["until"] else f


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
