# base/: shared basics every layer may use

**May import:** nothing from this project (stdlib and third-party only). Everyone may import `base`.
Enforced by `dev/check_layers.py --strict` (part of `dev/release_check.py`).

| File | Role |
|---|---|
| `contract.py` | The shared types: `Observation`, `Source` (+ optional hooks: snapshot, poll, quick_vitals, take_facts, checks_per_s, boost, party_name/party_reset), `OK/ABSENT/UNAVAILABLE`, `describe`, **`EVENTS`** (every bus event with its payload keys), **`SERVICES`** (hub.provide/ask names), `Component` (`attach(hub)` tracker side, `ui(shell)` helper side). Add an event or service here first, with its payload text |
| `paths.py` | Where shipped files (`asset("<layer>/<file>")`) vs player data (DATA: `%LOCALAPPDATA%\MapleClassicCompanion` when packaged, the repo root from source) live; `load_config`, `self_command` |
| `lean.py` | Import first in every entry point: 1 math thread, OpenCV stubbed (~900 MB less RAM) |
| `version.py` | App version (last release; `/testbuild` rewrites it only during the build) |
| `perf.py` | Self-measurement: `perf.timed("name")` around per-frame work, history/perf.csv, `python -m base.perf report` |
| `internet.py` | The one gate for internet use: `FEATURES`, `allowed("name")` right before connecting. `MAPLE_COMPANION_OFFLINE=1` (set by every `dev/*_sim.py` and release_check) forces it off |

Keep this folder small: it is the bottom of every dependency chain. Game knowledge belongs in
`gamedata/`, screen reading in `engine/`.
