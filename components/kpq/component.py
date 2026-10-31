"""kpq component: summarizes each party quest run (EXP and time per stage, bonus kills / EXP per hour).

Subscribes: service_start (loads history/kpq_runs.json), session_start, observation (map as read),
progress (the reading is done: feed Runs with hub.state.exp_total), map_enter (a confirmed non-PQ map
ends the run), session_end / service_stop (close the open run, reason "session").
Writes: history/kpq_runs.json {"runs": [last 3 finished], "best": {pq: fastest per stage / run}, "open": run
or null, "view": live view or null}, one journal line per run, and live.json "kpq" (Runs.view(); None = nothing
to show).
Helper side (ui): the main view that replaces the stat grid of the helper window while a PQ is on
(shell.add_main_view; setting kpq.pq_view): the run's stages with time and EXP, arrows through the stored
runs, a "best" toggle. It reads live.json "kpq" and history/kpq_runs.json (by mtime).
"""
import traceback

from base import perf
from base.contract import Component
from hub import helper_settings as HS

STALE_S = 30            # live.json older than this: the tracker isn't writing, show the normal view
SEP = {"sep": True}


class KpqComponent(Component):
    name = "kpq"
    settings_defaults = {"kpq": HS.DEFAULTS["kpq"]}

    def ui(self, shell):
        self.shell = shell
        self._cache = {}                        # path -> (mtime, parsed)
        self.nav = {"idx": None, "best": False, "seen": None}
        shell.add_main_view(self.main_view)
        shell.add_settings_extra("General", self.settings_extra, order=25)

    def settings_extra(self, win, f):
        win.s.setdefault("kpq", dict(self.settings_defaults["kpq"]))
        win.check(f, "kpq.pq_view", "Party quest view instead of the EXP details while in a PQ"
                  ).pack(anchor="w", pady=(0, win.px(2)))
        win.label(f, "In a party quest (Kerning City) the window shows the time and EXP per stage, then "
                     "the Bonus EXP per hour once 10 mobs' worth are killed. Arrows step through your last "
                     "runs, 'best' shows your fastest. Back to normal on the next EXP outside the PQ.",
                  muted=True, wrap=win.px(380)).pack(anchor="w", pady=(0, win.px(6)))

    # -- the helper's main view
    def _read(self, path):
        import json
        try:
            m = path.stat().st_mtime
            if self._cache.get(path, (None,))[0] != m:
                self._cache[path] = (m, json.loads(path.read_text(encoding="utf-8")))
            return self._cache[path][1]
        except (OSError, ValueError):
            return None

    def _sources(self):
        """(live view or None, stored runs, best) from live.json / kpq_runs.json, or the demo's."""
        import os
        import time
        from base.paths import DATA
        if self.shell.demo and os.environ.get("KPQ_DEMO"):
            if self._cache.get("demo") is None:
                self._cache["demo"] = demo_data()          # once: its times are "now"
            return self._cache["demo"]
        live = self._read(DATA / "live.json")
        if not isinstance(live, dict) or time.time() - (live.get("t") or 0) > STALE_S:
            return None, [], {}
        cur = live.get("kpq")
        if not cur:
            return None, [], {}
        d = self._read(DATA / "history" / "kpq_runs.json")
        d = d if isinstance(d, dict) else {}
        return cur, d.get("runs") or [], d.get("best")

    def main_view(self, snap):
        """The helper's main view (shell.add_main_view): the PQ run as rows, or None."""
        if not self.shell.settings("kpq").get("pq_view", True):
            return None
        from components.kpq import kpq
        cur, runs, best = self._sources()
        if cur is None:
            return None
        pqs = kpq.load_pqs()
        runs = [kpq.normalize(r, pqs) for r in runs]
        if not isinstance(best, dict):
            best = kpq.compute_best(runs, pqs)
        items = [item_from_run(r) for r in runs if r["start"] != cur["since"]]
        items.append(item_from_view(cur))
        items.sort(key=lambda i: i["start"])
        nav = self.nav
        if nav["seen"] != items[-1]["start"]:           # a new run: jump to it, best off
            nav.update(idx=len(items) - 1, best=False, seen=items[-1]["start"])
        idx = max(0, min(nav["idx"] if nav["idx"] is not None else len(items) - 1, len(items) - 1))
        it = items[idx]

        def go(step):
            def f():
                nav["idx"] = idx + step
            return f

        def flip():
            nav["best"] = not nav["best"]
        out = best_view(it, (best.get(cur["key"]) or {})) if nav["best"] else run_view(it)
        out["title"] = cur["pq"]
        out["prev"] = go(-1) if idx > 0 and not nav["best"] else None
        out["next"] = go(1) if idx < len(items) - 1 and not nav["best"] else None
        out["toggle"] = ("best", nav["best"], flip)
        return out

    # -- the tracker process
    def attach(self, hub):
        self.hub = hub
        self.runs = None
        self.last = (None, None)            # (t, map as read) of the reading being processed
        for event, fn in (("service_start", self.on_start), ("session_start", self.on_session_start),
                          ("observation", self.on_observation), ("progress", self.on_progress),
                          ("map_enter", self.on_enter), ("session_end", self.on_session_end),
                          ("service_stop", self.on_session_end)):
            hub.on(event, fn)

    @property
    def path(self):
        return self.hub.root / "history" / "kpq_runs.json"

    def on_start(self, **_):
        from components.kpq import kpq
        from hub import history
        d = history.read_json(self.path, {})
        d = d if isinstance(d, dict) else {}
        o = d.get("open")
        runs = list(d.get("runs") or [])
        if o:                                # the tracker died mid-run: keep what was seen
            o["reason"] = "session"
            runs.append(o)
        self.runs = kpq.Runs(runs, on_save=self.save, best=d.get("best"))
        if o and d.get("best") is not None:  # (a missing best was computed from the runs, open one included)
            kpq.update_best(self.runs.best, kpq.normalize(o, self.runs.pqs))
        self.hub.publish("kpq", lambda now, prof: self.runs.view() if self.runs is not None else None)
        self.save(self.runs)

    def save(self, runs):
        from hub import history
        try:
            history.write_json(self.path, runs.snapshot())
        except Exception:
            self.hub.log("kpq: " + traceback.format_exc())

    def on_session_start(self, **_):
        self.last = (None, None)
        if self.runs is not None:
            self.runs.new_session()             # exp_total starts over

    def on_observation(self, obs, **_):
        self.last = (obs.t, obs.map)

    def on_progress(self, level=None, **_):
        t, name = self.last
        if self.runs is None or t is None:
            return
        try:
            with perf.timed("kpq"):
                self.finish(self.runs.feed(t, name, self.hub.state.exp_total, level))
        except Exception:
            self.hub.log("kpq: " + traceback.format_exc())

    def on_enter(self, map=None, **_):
        if self.runs is not None:
            try:
                self.runs.on_left(map)
            except Exception:
                self.hub.log("kpq: " + traceback.format_exc())

    def on_session_end(self, **_):
        if self.runs is not None:
            try:
                self.finish(self.runs.close("session"))
            except Exception:
                self.hub.log("kpq: " + traceback.format_exc())

    def finish(self, rec):
        if rec:
            self.hub.journal(summary_line(rec))


def summary_line(r):
    return (f"KPQ run: {r['stage_exp']:,} stages + {r['bonus_exp']:,} bonus = {r['total_exp']:,} EXP "
            f"in {r['minutes']:.0f} min ({r['exp_per_hour']:,}/hr)")


# ------------------------------------------------------------------------------------ the view's rows
def mmss(minutes):
    s = int(round((minutes or 0) * 60))
    return f"{s // 60}:{s % 60:02d}"


def item_from_run(r):
    """A stored run record -> what the view shows."""
    b = {"exp": r["bonus_exp"], "minutes": r["bonus_minutes"], "kills": r.get("bonus_kills", 0),
         "rate": r.get("bonus_rate")} if r.get("completed") else None
    return {"start": r["start"], "stages": r["stages"], "stage_exp": r["stage_exp"],
            "stage_minutes": r["stage_minutes"], "bonus": b,
            "state": "done" if r.get("completed") else "left early"}


def item_from_view(v):
    """live.json "kpq" -> the same."""
    state = "in progress" if not v.get("done") else ("done" if v.get("completed") else "left early")
    return {"start": v["since"], "stages": v["stages"], "stage_exp": v["stage_exp"],
            "stage_minutes": v.get("stage_minutes", 0), "bonus": v.get("bonus"), "state": state}


def run_view(it):
    from datetime import datetime
    rows = []
    for s in it["stages"]:
        st = s["state"]
        if st == "todo":
            rows.append((s["label"], "", "", "", "muted"))
        else:
            rows.append((s["label"], {"done": "✓", "now": "▸"}.get(st, ""), mmss(s["minutes"]),
                         f"{s['exp']:,} xp", "gold" if st == "now" else ""))
    rows += [SEP, ("Total", "", mmss(it["stage_minutes"]), f"{it['stage_exp']:,} xp", "bold"), SEP]
    b = it["bonus"]
    if b and b.get("rate"):
        rows.append(("Bonus Stage", "", "", f"{b['rate']:,}/hr", "gold"))
    elif b:
        rows.append(("Bonus Stage", "", "", f"{b['kills']} kills", "muted"))
    else:
        rows.append(("Bonus Stage", "", "", "", "muted"))
    when = datetime.fromtimestamp(it["start"]).strftime("%b %d %H:%M")
    return {"sub": f"{when} · {it['state']}", "rows": rows}


def best_view(it, best):
    """The fastest recorded time per stage and per completed run (stage time; the bonus isn't counted)."""
    rows = []
    for s in it["stages"]:
        b = (best.get("stages") or {}).get(s["label"])
        rows.append((s["label"], "✓", mmss(b["minutes"]), f"{b['exp']:,} xp", "")
                    if b else (s["label"], "", "", "", "muted"))
    r = best.get("run")
    rows += [SEP, ("Total", "", mmss(r["stage_minutes"]), f"{r['stage_exp']:,} xp", "bold") if r
             else ("Total", "", "", "", "muted"), SEP, ("Bonus Stage", "", "", "", "muted")]
    return {"sub": "Best runs (fastest)", "rows": rows}


def demo_data():
    """Fake live view + 3 stored runs + best for KPQ_DEMO=1 (hub.helper_ui --demo)."""
    import time
    labels = ["Stage 1", "Stage 2", "Stage 3", "Stage 4", "King Slime"]

    def run(start, mins, exps, completed=True):
        st = [{"label": l, "exp": e, "minutes": m, "state": "done"} for l, m, e in zip(labels, mins, exps)]
        return {"pq": "kpq", "start": start, "stages": st, "stage_exp": sum(exps), "stage_minutes": sum(mins),
                "bonus_exp": 14000, "bonus_minutes": 9.0, "bonus_kills": 390, "bonus_rate": 93000,
                "completed": completed}
    now = time.time()
    runs = [run(now - 9000, [3.1, 1.2, 1.5, 2.0, 5.3], [1400, 270, 275, 270, 2000]),
            run(now - 6000, [2.8, 1.1, 1.6, 1.8, 4.9], [1500, 280, 270, 275, 2100]),
            run(now - 3000, [3.4, 1.0, 1.4, 2.2, 5.0], [1450, 275, 270, 270, 2050], False)]
    cur = {"pq": "Kerning Party Quest", "key": "kpq", "done": False, "since": now - 1200,
           "stages": [{"label": "Stage 1", "exp": 1413, "minutes": 3.2, "state": "done"},
                      {"label": "Stage 2", "exp": 274, "minutes": 1.1, "state": "done"},
                      {"label": "Stage 3", "exp": 274, "minutes": 1.5, "state": "now"},
                      {"label": "Stage 4", "exp": 0, "minutes": 0, "state": "todo"},
                      {"label": "King Slime", "exp": 0, "minutes": 0, "state": "todo"}],
           "stage_exp": 1961, "stage_minutes": 5.8, "bonus": None, "total": 1961, "minutes": 5.8}
    best = {"kpq": {"stages": {"Stage 1": {"minutes": 2.8, "exp": 1500, "start": 0},
                               "Stage 2": {"minutes": 1.0, "exp": 275, "start": 0},
                               "Stage 3": {"minutes": 1.4, "exp": 270, "start": 0}},
                    "run": {"stage_minutes": 11.0, "stage_exp": 4000, "start": 0}}}
    return cur, runs, best
