"""Replay an exp_log.csv through the composed hub and print the KPQ runs it produces.

  python dev/kpq_sim.py CSV OUT_DIR [--expect s1+b1,s2+b2,...]

Everything goes to OUT_DIR (compose.make_hub(OUT_DIR) points the tracker's history/journal there),
never the real data folder. With --expect, exits 1 unless the runs' stage+bonus EXP match exactly.
"""
import json
from datetime import datetime
import os
import sys
import types
from pathlib import Path
os.environ["MAPLE_COMPANION_OFFLINE"] = "1"      # simulations never go online

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from base import lean  # noqa: F401,I001,E402
from hub import tracker as T  # noqa: E402
from launcher import compose  # noqa: E402
from engine.sources import make_source  # noqa: E402


def main():
    argv = sys.argv[1:]
    expect = None
    if "--expect" in argv:
        i = argv.index("--expect")
        expect = argv[i + 1].split(",")
        del argv[i:i + 2]
    csv_path, out = argv[0], Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    hub = compose.make_hub(out)
    cfg = json.loads((CODE / "config.json").read_text(encoding="utf-8")) if (CODE / "config.json").exists() \
        else json.loads((CODE / "config.example.json").read_text(encoding="utf-8"))
    cfg.update(interval=0, fast_interval=0)
    cfg.setdefault("service", {}).update(close_after_absent_s=1, idle_poll_s=0.15, close_after_afk_s=1800,
                                         source_retry_s=0.1)
    cfg["source"] = {"type": "csv_replay", "path": csv_path}
    comp = next(c for c in hub.components if c.name == "kpq")
    life, state = [], {"view": None, "rate": False}

    def watch(**_):                      # after the component's own progress handler (registered later)
        if comp.runs is None or comp.last[0] is None:
            return
        t, m = comp.last
        v = comp.runs.view()
        hhmm = datetime.fromtimestamp(t).strftime("%H:%M:%S")
        if v and v["bonus"] and v["bonus"]["rate"] and not state["rate"]:
            state["rate"] = True
            life.append(f"  {hhmm} bonus rate shows: {v['bonus']['kills']} kills, {v['bonus']['rate']:,}/hr")
        if v is None and state["view"] is not None:
            life.append(f"  {hhmm} view clears on {m}")
        if v and (state["view"] is None or (state["view"]["since"] != v["since"])):
            state["rate"] = False
            life.append(f"  {hhmm} view opens ({v['pq']})")
        state["view"] = v
    hub.on("progress", watch)
    args = types.SimpleNamespace(level=None, interval=None, service=False)
    T.run(cfg, args, source=make_source(cfg, None), hub=hub)

    print("view lifecycle:")
    print(chr(10).join(life) or "  (none)")
    d = json.loads((out / "history" / "kpq_runs.json").read_text(encoding="utf-8")) \
        if (out / "history" / "kpq_runs.json").exists() else {"runs": [], "open": None}
    got = []
    for i, r in enumerate(d["runs"], 1):
        a, b = (datetime.fromtimestamp(r[k]).strftime("%H:%M") for k in ("start", "end"))
        print(f"run {i}: {a}-{b}  {r['stage_exp']:,} + {r['bonus_exp']:,} = {r['total_exp']:,} EXP  "
              f"{r['minutes']} min  {r['exp_per_hour']:,}/hr  Lv {r['level_start']}->{r['level_end']}  "
              f"bonus {r['bonus_kills']} kills rate {r['bonus_rate']}  "
              f"completed={r['completed']} reason={r['reason']}")
        print("   " + "  ".join(f"{s['label']} {int(s['minutes'] * 60) // 60}:{int(s['minutes'] * 60) % 60:02d}/{s['exp']}/{s['state']}" for s in r["stages"]))
        got.append(f"{r['stage_exp']}+{r['bonus_exp']}")
    print("best:", json.dumps(d.get("best")))
    if d.get("open"):
        print("open run left:", d["open"]["total_exp"])
    if expect is not None:
        ok = got == expect
        print("EXPECT", "OK" if ok else f"MISMATCH: got {got}, want {expect}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
