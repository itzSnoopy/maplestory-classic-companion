"""Cost of the tracker's work: CPU, time and RAM per step, against the live game window.

Answers "how many readings / extra screen readers can we afford?". Writes nothing to the
player's data (no history/, sessions/, journal.md); frames stay in memory.

  python dev/bench.py                 everything below (game must be open, not minimized)
  python dev/bench.py idle            background cost of an open capture session, per update rate
  python dev/bench.py stages [N]      time per step of one reading (grab, crops, glyphs, OCR, ...)
  python dev/bench.py tracker [S]     CPU % and RAM of the running tracker service over S seconds
  python dev/bench.py --frame PNG stages   stages on a saved frame instead of the live window

CPU % is of one core (100 = one core fully busy); RAM is the process working set.
"""
import ctypes
import json
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from base import lean  # noqa: E402,F401  (measure what the app runs: same caps)

k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
               [(n, ctypes.c_size_t) for n in (
                   "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                   "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                   "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


class Proc:
    """CPU seconds and memory of one process (this one by default)."""

    def __init__(self, pid=None):
        self.h = k32.GetCurrentProcess() if pid is None else \
            k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.h:
            raise OSError(f"can't open process {pid}")

    def cpu(self):
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        k32.GetProcessTimes(self.h, *(ctypes.byref(x) for x in (c, e, k, u)))
        f = lambda ft: (ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 1e7   # noqa: E731
        return f(k) + f(u)

    def mem(self):
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        psapi.GetProcessMemoryInfo(self.h, ctypes.byref(m), m.cb)
        return {"ws_mb": m.WorkingSetSize / 2**20, "peak_ws_mb": m.PeakWorkingSetSize / 2**20,
                "private_mb": m.PrivateUsage / 2**20}


ME = Proc()


def measure(fn, n=1):
    """(ms wall, ms cpu) per call, averaged over n calls."""
    c0, t0 = ME.cpu(), time.perf_counter()
    for _ in range(n):
        out = fn()
    return 1000 * (time.perf_counter() - t0) / n, 1000 * (ME.cpu() - c0) / n, out


def cfg():
    return json.loads((CODE / "config.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- background cost of a session
def bench_idle(secs=8):
    """An open WGC session gets frames from Windows at minimum_update_interval even when we
    don't want them. How much does just keeping it open cost?"""
    import engine.sources.wgc as W
    print(f"\n== open capture session, no readings ({secs} s each) ==")
    print(f"{'update every':>14} {'cpu %':>7} {'grab ms':>8} {'RAM +MB':>8}")
    for ms in (100, 250, 500, 1000):
        f = W.WgcFrames({"wgc": {"min_update_ms": ms}})
        m0 = ME.mem()["private_mb"]
        f.grab()                                    # opens the session
        time.sleep(1)
        c0, t0 = ME.cpu(), time.time()
        time.sleep(secs)
        cpu = 100 * (ME.cpu() - c0) / (time.time() - t0)
        g = [measure(f.grab)[0] for _ in range(5)]
        ram = ME.mem()["private_mb"] - m0
        f.close()
        time.sleep(0.5)
        print(f"{ms:>11} ms {cpu:>7.2f} {sum(g) / len(g):>8.0f} {ram:>8.1f}")


# ---------------------------------------------------------------- per-step cost of one reading
def bench_stages(n=20, frame_path=None):
    from engine import deathbox
    from engine import glyphs
    from engine import hpbar
    from engine import jobread
    from engine import minimap
    from engine import ocr
    from engine.layout import anchor_for, crop
    from engine.sources.screen import ImageFrames, ScreenSource, exp_bar_visible, read_exp
    c = cfg()
    ocr.setup(c.get("tesseract"))
    res = tuple(c.get("source_resolution") or (1920, 1080))
    regions = c["regions"]

    if frame_path:
        from PIL import Image
        frame0 = Image.open(frame_path).convert("RGB")
        frames = None
    else:
        import engine.sources.wgc as W
        frames = W.WgcFrames(c)
        frame0 = frames.grab()
        if frame0.getbbox() is None:
            print("game window not found or minimized")
            return
    print(f"\n== one reading, step by step (frame {frame0.size[0]}x{frame0.size[1]}, avg of {n}) ==")
    print(f"{'step':<34} {'wall ms':>8} {'cpu ms':>8}")
    rows = []

    def row(name, fn, k=n):
        w, cp, out = measure(fn, k)
        rows.append((name, w, cp))
        print(f"{name:<34} {w:>8.1f} {cp:>8.1f}")
        return out

    if frames:
        row("grab (wait for frame + convert)", frames.grab)
    exp_rc = regions["exp"]
    ec = row("crop exp", lambda: crop(frame0, exp_rc["box"], res, anchor_for("exp", exp_rc), "exp"))
    row("exp bar visible?", lambda: exp_bar_visible(ec, exp_rc.get("white_min", 230)))
    row("read exp (glyphs)", lambda: read_exp(ec, exp_rc, {}))
    if regions.get("level", {}).get("box"):
        lrc = regions["level"]
        lc = crop(frame0, lrc["box"], res, anchor_for("level", lrc), "level")
        row("crop + read level (glyphs)", lambda: glyphs.read(
            crop(frame0, lrc["box"], res, anchor_for("level", lrc), "level"),
            lrc.get("white_min", 230), lrc.get("templates")))
    mrc = regions.get("map", {})
    if mrc.get("box"):
        state, box, read_rc = row("find minimap", lambda: minimap.locate(frame0, mrc, res))
        if box:
            mc = frame0.crop(tuple(box))
            row("map OCR (Tesseract, uncached)", lambda: ocr.read(mc, read_rc, kind="map"), 3)
    row("hp bar", lambda: hpbar.hp_pct(frame0, res))
    row("death box check", lambda: deathbox.is_dead(frame0))
    row("job OCR (rare: start/advancement)", lambda: jobread.read_job(frame0, c.get("job_read"), res), 3)

    # the whole ScreenSource.read on this frame (map cache warm: the usual case)
    src = ScreenSource(c, frames=ImageFrames([frame0] * (n + 1)))
    src.read({})
    row("ScreenSource.read total (cached map)", lambda: src.read({}))
    if frames:
        def poll_like():
            fr = frames.grab()
            return fr.getbbox() is not None and deathbox.is_dead(fr)
        row("death poll (grab + check), 1/s", poll_like)
        frames.close()
    m = ME.mem()
    print(f"\nthis process: RAM {m['ws_mb']:.0f} MB (peak {m['peak_ws_mb']:.0f}), "
          f"private {m['private_mb']:.0f} MB")
    return rows


# ---------------------------------------------------------------- the running service
def tracker_pid():
    log = (CODE / "tracker_live.log").read_text(encoding="utf-8", errors="replace")
    started = re.findall(r"tracker service started \(pid (\d+)", log)
    stopped = log.rfind("tracker service stopped")
    if not started or stopped > log.rfind("tracker service started"):
        return None
    return int(started[-1])


def bench_tracker(secs=60):
    pid = tracker_pid()
    if not pid:
        print("\ntracker service isn't running")
        return
    p = Proc(pid)
    print(f"\n== running tracker (pid {pid}) over {secs} s ==")
    c0, t0 = p.cpu(), time.time()
    time.sleep(secs)
    cpu = 100 * (p.cpu() - c0) / (time.time() - t0)
    m = p.mem()
    print(f"cpu {cpu:.2f} % of one core; RAM {m['ws_mb']:.0f} MB "
          f"(peak {m['peak_ws_mb']:.0f}), private {m['private_mb']:.0f} MB")


if __name__ == "__main__" and sys.argv[1:2] != ["where"]:
    a = sys.argv[1:]
    frame = None
    if "--frame" in a:
        i = a.index("--frame")
        frame = a[i + 1]
        del a[i:i + 2]
    what = a[0] if a else "all"
    num = float(a[1]) if len(a) > 1 else None
    if what in ("idle", "all") and not frame:
        bench_idle()
    if what in ("stages", "all"):
        bench_stages(int(num or 20), frame)
    if what in ("tracker", "all") and not frame:
        bench_tracker(int(num or 60))


# ---------------------------------------------------------------- what fills a process's RAM
class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


def where(pid=None):
    """Resident RAM of a process split by kind: program/DLL files, mapped memory (shared with
    another process or a driver, e.g. graphics), and the process's own heap. Biggest areas too."""
    import bisect
    PROCESS_QUERY_INFORMATION = 0x0400
    h = k32.GetCurrentProcess() if pid is None else \
        k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    psapi.QueryWorkingSet.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    psapi.GetMappedFileNameW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
    regions, addr, m = [], 0, MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(m), ctypes.sizeof(m)):
        if m.State == 0x1000:                        # committed
            regions.append((m.BaseAddress or 0, m.RegionSize, m.Type, m.AllocationBase or 0))
        addr = (m.BaseAddress or 0) + m.RegionSize
        if addr >= 1 << 47:
            break
    n = 1 << 20
    while True:                                       # working-set page list
        buf = (ctypes.c_size_t * (n + 1))()
        if psapi.QueryWorkingSet(h, buf, ctypes.sizeof(buf)):
            break
        n *= 2
    pages = [buf[i + 1] for i in range(buf[0])]
    starts = [r[0] for r in regions]
    kinds = {0x1000000: "files (program, DLLs)", 0x40000: "mapped (shared/driver)", 0x20000: "own heap"}
    tot, by_alloc = {}, {}
    for e in pages:
        va = e & ~0xFFF
        i = bisect.bisect_right(starts, va) - 1
        if i < 0 or va >= regions[i][0] + regions[i][1]:
            continue
        _, _, typ, ab = regions[i]
        k = kinds.get(typ, "other")
        tot[k] = tot.get(k, 0) + 4096
        by_alloc[(k, ab)] = by_alloc.get((k, ab), 0) + 4096
    print(f"\n== resident RAM by kind (pid {pid or 'self'}) ==")
    for k, v in sorted(tot.items(), key=lambda kv: -kv[1]):
        print(f"{k:<26} {v / 2**20:8.0f} MB")
    print("biggest areas:")
    for (k, ab), v in sorted(by_alloc.items(), key=lambda kv: -kv[1])[:8]:
        name = ctypes.create_unicode_buffer(512)
        psapi.GetMappedFileNameW(h, ctypes.c_void_p(ab), name, 512)
        print(f"  {v / 2**20:7.0f} MB  {k:<24} {name.value.rsplit(chr(92), 1)[-1] if name.value else ''}")


if __name__ == "__main__" and sys.argv[1:2] == ["where"]:
    where(int(sys.argv[2]) if len(sys.argv) > 2 else tracker_pid())
