"""perf.py - the tracker measures itself while it runs, for capacity decisions.

One row a minute in history/perf.csv (rotated at 2 MB to perf.1.csv): the process's CPU and RAM,
the "Screen checks per second" setting and what was achieved, and the cost of each named step
(grab, death box, map check, EXP read, Tesseract, ...). Plus how long things stay on screen
(e.g. the "You have died." box), which tells how fast we really need to look.

Cheap by design: timers add two clock reads, counters live in memory, one small write a minute.
Per-step CPU uses the thread clock, which ticks in ~15.6 ms steps on Windows: single calls read 0
or 15.6, averages over many calls are right.
Nothing here leaves the PC; no pictures, only numbers.

In code (anywhere, no plumbing; does nothing until the tracker calls start()):
    with perf.timed("deathbox"):  ...          # count + wall ms + this thread's CPU ms
    perf.count("map_change")                   # plain counter
    perf.sample("death_box_visible_s", 2.4)    # a measured value (min / avg / max per row)

Report (for the capture-rate decision, and the /release review):
    python perf.py report [DAYS]
"""
import csv
import ctypes
import json
import os
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

ROTATE_BYTES = 2_000_000
FIELDS = ["time", "state", "fps_setting", "checks_per_s", "reads", "cpu_core_pct", "ram_mb",
          "private_mb", "steps", "counts", "samples"]

_on = False
_path = None
_every = 60.0
_last = None              # (wall, process cpu) at the last row
_steps = {}               # name -> [n, wall ms, cpu ms]
_counts = {}
_samples = {}             # name -> [values]


# ------------------------------------------------------------------ recording
def start(path, every_s=60.0):
    global _on, _path, _every, _last
    _path, _every, _on = Path(path), float(every_s), True
    _last = (time.time(), time.process_time())


@contextmanager
def timed(name):
    if not _on:
        yield
        return
    w, c = time.perf_counter(), time.thread_time()
    try:
        yield
    finally:
        s = _steps.setdefault(name, [0, 0.0, 0.0])
        s[0] += 1
        s[1] += 1000 * (time.perf_counter() - w)
        s[2] += 1000 * (time.thread_time() - c)


def count(name, n=1):
    if _on:
        _counts[name] = _counts.get(name, 0) + n


def sample(name, value):
    if _on and value is not None:
        _samples.setdefault(name, []).append(float(value))


def _mem():
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
                   [(n, ctypes.c_size_t) for n in (
                       "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                       "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                       "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                       "PrivateUsage")]
    try:
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb)
        return m.WorkingSetSize / 2**20, m.PrivateUsage / 2**20
    except Exception:
        return None, None


def tick(state, fps_setting=None, force=False):
    """Called by the service loop; writes a row once a minute (or now with force)."""
    global _last
    if not _on:
        return
    now, cpu = time.time(), time.process_time()
    if not force and now - _last[0] < _every:
        return
    span = max(1e-6, now - _last[0])
    ws, priv = _mem()
    polls = _steps.get("poll", [0])[0]
    row = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "state": state or "",
        "fps_setting": fps_setting if fps_setting is not None else "",
        "checks_per_s": round(polls / span, 2),
        "reads": _steps.get("read", [0])[0],
        "cpu_core_pct": round(100 * (cpu - _last[1]) / span, 2),
        "ram_mb": round(ws, 1) if ws else "",
        "private_mb": round(priv, 1) if priv else "",
        "steps": json.dumps({k: [n, round(w / n, 2), round(c / n, 2)]
                             for k, (n, w, c) in sorted(_steps.items()) if n}),
        "counts": json.dumps(dict(sorted(_counts.items()))),
        "samples": json.dumps({k: [len(v), round(min(v), 3), round(sum(v) / len(v), 3),
                                   round(max(v), 3)] for k, v in sorted(_samples.items()) if v}),
    }
    _steps.clear()
    _counts.clear()
    _samples.clear()
    _last = (now, cpu)
    try:
        _path.parent.mkdir(parents=True, exist_ok=True)
        if _path.exists() and _path.stat().st_size > ROTATE_BYTES:
            os.replace(_path, _path.with_name(_path.stem + ".1.csv"))
        new = not _path.exists()
        with open(_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    except OSError:
        pass


# ------------------------------------------------------------------ report
def rows(path, days=None):
    out = []
    for p in (path.with_name(path.stem + ".1.csv"), path):
        if p.exists():
            with open(p, encoding="utf-8") as f:
                out += list(csv.DictReader(f))
    if days:
        cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
        out = [r for r in out if r["time"] >= cut]
    return out


def report(path, days=None):
    """Plain-text summary: per capture setting while playing, and what's been seen on screen."""
    rs = rows(path, days)
    if not rs:
        return "no perf data yet (history/perf.csv is written once a minute while the tracker runs)"
    lines = [f"perf data: {len(rs)} rows (one a minute), {rs[0]['time']} .. {rs[-1]['time']}"]
    play = [r for r in rs if r["state"] == "playing"]
    lines.append(f"playing: {len(play)} rows, other: {len(rs) - len(play)} rows")
    lines.append("(cpu = this process only; Tesseract runs as its own program, ~105 ms CPU a call)")

    def avg(xs):
        xs = [float(x) for x in xs if x not in ("", None)]
        return sum(xs) / len(xs) if xs else float("nan")

    def mx(xs):
        xs = [float(x) for x in xs if x not in ("", None)]
        return max(xs) if xs else float("nan")

    lines.append("\nwhile playing, by 'Screen checks per second' setting:")
    lines.append(f"  {'setting':>7} {'minutes':>7} {'checks/s':>8} {'cpu %core':>9} {'max':>6} "
                 f"{'RAM MB':>7} {'max':>6}")
    for fps in sorted({r["fps_setting"] for r in play}, key=lambda v: float(v or 0)):
        g = [r for r in play if r["fps_setting"] == fps]
        lines.append(f"  {fps:>7} {len(g):>7} {avg(r['checks_per_s'] for r in g):>8.2f} "
                     f"{avg(r['cpu_core_pct'] for r in g):>9.2f} {mx(r['cpu_core_pct'] for r in g):>6.1f} "
                     f"{avg(r['ram_mb'] for r in g):>7.0f} {mx(r['ram_mb'] for r in g):>6.0f}")
    idle = [r for r in rs if r["state"] != "playing"]
    if idle:
        lines.append(f"  not playing: cpu {avg(r['cpu_core_pct'] for r in idle):.2f} %core, "
                     f"RAM {avg(r['ram_mb'] for r in idle):.0f} MB")

    steps, counts, samples = {}, {}, {}
    for r in rs:
        for k, (n, w, c) in json.loads(r["steps"] or "{}").items():
            s = steps.setdefault(k, [0, 0.0, 0.0])
            s[0] += n
            s[1] += n * w
            s[2] += n * c
        for k, n in json.loads(r["counts"] or "{}").items():
            counts[k] = counts.get(k, 0) + n
        for k, (n, lo, av, hi) in json.loads(r["samples"] or "{}").items():
            s = samples.setdefault(k, [0, float("inf"), 0.0, float("-inf")])
            s[0] += n
            s[1] = min(s[1], lo)
            s[2] += n * av
            s[3] = max(s[3], hi)
    lines.append("\ncost per step (all minutes):")
    lines.append(f"  {'step':<22} {'calls':>8} {'wall ms':>8} {'cpu ms':>7}")
    for k, (n, w, c) in sorted(steps.items(), key=lambda kv: -kv[1][2]):
        lines.append(f"  {k:<22} {n:>8} {w / n:>8.2f} {c / n:>7.2f}")
    if "map_check" in steps:
        mc, oc = steps["map_check"][0], steps.get("ocr_map", [0])[0]
        lines.append(f"  -> Tesseract ran on {oc} of {mc} map checks ({100 * oc / mc:.0f}%; "
                     f"the rest reused the cached name because the title crop was unchanged)")
    if counts:
        lines.append("\ncounts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    lines.append("\nhow long things stayed on screen (decides how often we must look):")
    if samples:
        for k, (n, lo, tot, hi) in sorted(samples.items()):
            lines.append(f"  {k:<22} {n:>4}x  min {lo:.2f}  avg {tot / n:.2f}  max {hi:.2f}")
        vis = samples.get("death_box_visible_s")
        if vis:
            lo = vis[1]
            lines.append(f"  -> shortest death box seen: {lo:.2f} s. Looking every {lo / 2:.2f} s "
                         f"(~{max(1, round(2 / lo)) if lo else '?'}/s) would catch one that short twice.")
    else:
        lines.append("  nothing measured yet (no death box seen)")
    return "\n".join(lines)


if __name__ == "__main__":
    if sys.argv[1:2] == ["report"]:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import DATA
        print(report(DATA / "history" / "perf.csv", float(sys.argv[2]) if len(sys.argv) > 2 else None))
    else:
        print(__doc__)
