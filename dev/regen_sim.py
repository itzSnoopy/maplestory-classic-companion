"""dev/regen_sim.py - replay a dev/vitals_log.py recording (8-10 looks/s) the way the tracker
looks live: normal looks at `fps` (Settings > General), plus bursts at burst_fps when the regen
clock asks for them (vitals.Meter.wants_fast). Reports how many looks it took, how well the
clock's next tick matched the real ticks, the checks, and what mpuse.Usage made of the changes.

  python dev/regen_sim.py LOG.csv [fps=2] [skill_id=level ...]
"""
import bisect
import csv
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
import mpuse  # noqa: E402
import vitals  # noqa: E402


def main():
    path = sys.argv[1]
    fps = float(sys.argv[2]) if len(sys.argv) > 2 and "=" not in sys.argv[2] else 2.0
    levels = {k: int(v) for k, v in (a.split("=") for a in sys.argv[2:] if "=" in a)}
    rows = [r for r in csv.DictReader(open(path)) if r["mp"]]
    T = [float(r["t"]) for r in rows]
    mp = [int(r["mp"]) for r in rows]
    truth = [(T[i - 1] + T[i]) / 2 for i in range(1, len(rows))
             if mp[i] - mp[i - 1] in (9, 10, 11, 12, 13) or
             (mp[i] - mp[i - 1] < 0 and (mp[i] - mp[i - 1]) % 17 in (10,))]
    s = dict(vitals.REGEN_DEFAULTS)
    meters = {k: vitals.Meter(prior=10.0, settings_fn=lambda: s) for k in ("hp", "mp")}
    u = mpuse.Usage(meters)
    u.set_context(levels=levels)
    t, looks, fast_looks, errs = T[0], 0, 0, []
    while t <= T[-1]:
        i = bisect.bisect_right(T, t) - 1
        r = rows[i]
        u.look(t, [int(r["hp"]), int(r["hp_max"])] if r["hp"] else None,
               [int(r["mp"]), int(r["mp_max"])])
        looks += 1
        m = meters["mp"]
        nxt = m.next_tick(t)
        if nxt:                                 # how far is the shown next tick from the real one
            real = [x for x in truth if x > t - 0.05]
            if real and real[0] - t < 10.5:
                errs.append(abs(nxt - real[0]))
        fast = any(mm.wants_fast(t) for mm in meters.values())
        fast_looks += fast
        step = 1 / s["burst_fps"] if fast else 1 / fps
        due = [mm.burst_in(t) for mm in meters.values()]
        due = [d for d in due if d is not None and d > 0]
        if not fast and due:
            step = min(step, max(0.05, min(due)))
        t += step
    mins = (T[-1] - T[0]) / 60
    errs.sort()
    st = meters["mp"].state(t)
    print(f"{path.split('/')[-1]}: {mins:.1f} min, {looks} looks ({looks / mins:.0f}/min; "
          f"{fast_looks} at burst rate), plain {fps:g}/s would be {mins * 60 * fps:.0f}")
    if errs:
        print(f"  next-tick error: median {errs[len(errs) // 2]:.2f} s, 90% {errs[int(len(errs) * .9)]:.2f} s, "
              f"worst {errs[-1]:.2f} s ({len(errs)} looks)")
    print(f"  clock {st['clock']}, check every {st['check_every']} ticks, checks {st['checks']}, "
          f"restarts {st['restarts']}, ticks seen {st['ticks_seen']} (real {len(truth)})")
    sm = u.summary()
    print("  MP:", {k: {n: v["n"] for n, v in d.items()} for k, d in sm["mp"].items() if isinstance(d, dict)})
    print("  HP:", {k: {n: v["n"] for n, v in d.items()} for k, d in sm["hp"].items() if isinstance(d, dict)})
    print("  levels seen:", sm["levels_seen"])


if __name__ == "__main__":
    main()
