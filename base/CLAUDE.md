# base/: shared basics every layer may use

The bottom of every dependency chain: the shared types and events (`contract.py`), where files live
(`paths.py`), process diet (`lean.py`), self-measurement (`perf.py`), the internet gate (`internet.py`)
and the app version. Used by the tracker and the helper alike.

**Memory (local):** `.claude/handoff/components/hub.md` (hub + base share one memory): read it before changing anything here.
**May import:** nothing from this project (stdlib and third-party only). Everyone may import `base`.
Enforced by `dev/check_layers.py --strict` (part of `dev/release_check.py`). **Never:** game knowledge (-> `gamedata/`), screen reading (-> `engine/`).

## Files
| File | Role |
|---|---|
| `contract.py` | `Observation` (t, status, exp_cur, exp_pct, level, map, map_confirmed, exp_needed, job, hp_pct, gains, suspect, raw, source, note), `Source` (+ optional hooks: begin_session, after_step, snapshot, album_shot (+ `album_watch` flag), poll, quick_vitals, take_facts, checks_per_s, boost, party_name/party_reset, end_session, close), `OK/ABSENT/UNAVAILABLE`, `describe`, **`EVENTS`**, **`SERVICES`** (tuples of (name, payload text)), `EVENT_NAMES`, `Component` (`name`, `settings_defaults`, `attach(hub)` tracker side, `ui(shell)` helper side). The live.json section list is documented there too |
| `paths.py` | `APP` (shipped, read-only; the PyInstaller bundle when frozen), `DATA` (player files), `asset("<layer>/<file>")`, `CONFIG`, `BUNDLED_TESSERACT`, `load_config()` (creates config.json from `config.example.json`; frozen: always the running version's Tesseract), `self_command(*args)`, `FROZEN` |
| `lean.py` | Import **first** in every entry point: 1 math thread (OPENBLAS/OMP/MKL), OpenCV stubbed (`MAPLE_COMPANION_REAL_CV2=1` loads the real one) |
| `version.py` | `VERSION` (last real release, now 0.7.1), `TEST_BASE` (set only during a `/testbuild` build) |
| `perf.py` | `start(path)`, `timed(name)`, `count`, `sample`, `tick`, `report`; history/perf.csv one row a minute, rotated at 2 MB. `python -m base.perf report [DAYS]` |
| `internet.py` | `FEATURES` (updates, party, icons, mobs: label, what, hosts), `state()`, `allowed(name)`; reads helper_settings.json `internet` (cached by file time) |

## Interfaces
- **DATA vs APP:** a file the player writes (config, history, glyphs, templates, logs) = `DATA / ...`, never `Path(__file__).parent`. A file that ships (json, layouts, art, tesseract) = `asset("layer/file")` and must be in `packaging/build.py` DATA_FILES. From source both are the repo root; frozen: DATA = `%LOCALAPPDATA%\MapleClassicCompanion`; `MAPLE_COMPANION_DATA` overrides DATA (tests, portable).
- **Offline switch:** `MAPLE_COMPANION_OFFLINE=1` makes `internet.allowed()` False for every feature; set by every `dev/*_sim.py` and `dev/release_check.py`.
- **New online feature** = a `FEATURES` entry (with every host) + `if internet.allowed("x"):` right before connecting; Settings > Internet lists it by itself. Local IPC (Discord, 127.0.0.1) is not "internet".
- **New event/service** = a tuple in `EVENTS` / `SERVICES` with its payload text first, then code.
- **perf.timed("name")** around every per-frame piece of work; names show up as `steps` in perf.csv (`bus.<event>` for subscribers).

## Test it
`python dev/release_check.py`; `python dev/check_layers.py --strict`; `python -m base.perf report`; `python dev/update_sim.py OUT` covers `paths`/`internet` use by the updater.

## Out of scope / don't read
Everything else: base must stay tiny and ignorant of the rest.

## Rules that bite here
- Importing anything internal here is a layer violation. Keep imports inside functions only for stdlib/third-party.
- `version.py` holds the last release; `/testbuild` rewrites it only during the build, `/release` bumps it.
- Never print or commit `config.json` (may hold an old OBS password).
