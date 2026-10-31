# components/kpq: party quest runs (Kerning PQ first) and the live PQ view

Summarizes each party quest run: time and EXP per stage (stage-clear rewards and the boss kill land there),
Bonus EXP, total, EXP/hr. While in a PQ (and after, until the next EXP outside the PQ) the helper
window shows the run per stage instead of the stat grid (header unchanged), with the Bonus EXP/hr once 10 mobs'
worth are killed, arrows through the last 3 runs and a "best" toggle (fastest per stage / per completed run).
Tracker process computes; the helper draws the view. No separate page.

**Memory (local):** `.claude/handoff/components/kpq.md`: read it before changing anything here.
**May import:** `hub`, `gamedata`, `base`. **Never:** another component, `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `KpqComponent`: wires events -> `Runs`, saves `history/kpq_runs.json`, journal line per run, publishes live.json `kpq`; `ui(shell)`: Settings > General checkbox, `shell.add_main_view` (`main_view` reads live.json `kpq` + `history/kpq_runs.json`; `run_view` / `best_view` build the rows; arrows / best state in `self.nav`; `demo_data` for KPQ_DEMO=1) |
| `kpq.py` | Plain logic, no hub: `load_pqs()` (gamedata/pq_classic.json, KPQ fallback), `pq_maps`, `bonus_averages` (Bonus map's mob EXP from map_mobs.json), `stages_from` (per stage exp / minutes / state done|now|left|todo), `normalize` (older records), `update_best` / `compute_best`, `Runs` (feed / on_left / close / view / snapshot) |
| `__init__.py` | docstring only |

## Rules of a run (kpq.py)
- Starts on the first reading on the PQ's `first` map. Phase "stages" until the first reading on `bonus`, then "bonus". Only exact map names move phases (OCR garbles never do); `other` maps (Exit) belong to the current phase. Joining mid-run = not tracked.
- EXP = `hub.state.exp_total` (tracker, per session, across level-ups) at first readings. Stage rows sum every visit to that stage's map.
- Ends when the tracker's `map_enter` confirms a non-PQ map (closed on the next reading, so that "tail" reading's EXP still counts: the last bonus kills often show there), or on `session_end` / `service_stop` (reason "session"). End time = last PQ reading.
- Entry reward: the first rise within 20 s of entering the Bonus, if >= 500, goes to the Last stage row.
- Bonus kills = Bonus EXP / the Bonus map's spawn-weighted average mob EXP (same "mobs' worth" as training's kills). Bonus EXP/hr only once kills >= 10.
- Stored: the last 3 finished runs (`KEEP`) + `best` {pq: {stages {label: {minutes, exp, start}}, run {stage_minutes, stage_exp, start}}} = FASTEST cleared stage ("done": a later PQ map was read) and fastest completed run's stage time (bonus not counted); updated before the trim; a file without `best` gets it computed from its runs on load.
- View: the open run; after it ends the finished run (`done`) until a non-PQ reading gains EXP (the tail reading never clears it), or a new run replaces it.

## Interfaces
- **Listens to (events):** `service_start`, `session_start`, `observation` (`obs.t`, `obs.map`), `progress` (`level`; then reads `hub.state.exp_total`), `map_enter` (`map`), `session_end`, `service_stop`.
- **Emits:** nothing. **Services:** none provided or asked.
- **Source hooks used:** none.
- **Settings:** `kpq` {`pq_view`: True} (DEFAULTS in `hub/helper_settings.py`).
- **Writes (data folder):** `history/kpq_runs.json` {runs [last 3], best, open, view}; live.json section `kpq` (-> `main_view` in the helper); one journal line per run.
- **Reads (shipped data):** `gamedata/pq_classic.json` (PQ definitions: add a PQ there, no code), `gamedata/map_mobs.json` (Bonus mobs).
- **Helper side (`ui(shell)`):** `shell.add_main_view(main_view)` (replaces the stat grid; live.json older than 30 s = normal grid), Settings > General checkbox (order 25).

## Test it
- `python dev/kpq_sim.py CSV OUT_DIR [--expect s1+b1,...]`: replays an exp_log.csv (copy it to a scratch folder first) through the composed hub, prints runs (per-stage times), the view's lifecycle and the best record. Reference: the 2026-10-30 session (3 runs) gives `4347+15096,4598+14630,4246+17072`.
- `python dev/release_check.py`: `check_pq_data`, the synthetic CSV run (level-up, garbled map), `check_kpq_logic` (entry reward, 10-kill gate, view lifecycle), `check_kpq_best` (fastest, trim to 3, migration).
- View look: `KPQ_DEMO=1 python -m hub.helper_ui --demo --at=-2400,40`.
Live: do a KPQ: the window switches to the PQ view at 1st Stage, rows fill per stage, Bonus kills count up, EXP/hr appears after 10; back in town it stays until you gain EXP outside; arrows / best work; journal has the line.

## Out of scope / don't read
Other components, `engine/`, `hub/tracker.py` internals (only `exp_total` and the events above matter).

## Rules that bite here
- Map names come from Classic data only (`pq_classic.json` names must exist in `maps_classic.json`; release_check checks it).
- Per-reading work is under `perf.timed("kpq")`. Tests never write the real data folder (`make_hub(root=scratch)`).
