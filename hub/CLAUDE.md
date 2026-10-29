# hub/: the companion core (tracker, bus, history, helper shell)

The core that turns Observations into EXP/level/ETA/session logic, files and bus events, and the
helper window that shows it. Two processes talk through files (status.json, live.json, digest.json,
party.json, helper_settings.json, the `stop` file):
- **tracker** (`companion.py --service`): `tracker.py` + `bus.py` + `live.py` + the components' `attach(hub)`.
- **helper** (`companion.py`): `helper_ui.py` + `settings_ui.py` + `ui/shell.py` + the components' `ui(shell)`.

**Memory (local):** `.claude/handoff/components/hub.md` (also covers `base/`): read it before changing anything here.
**May import:** `gamedata`, `base`. **Never:** `engine`, `components`, `launcher`. When it must reach
upward it loads a module by name with `importlib` (`tracker.py __main__` -> `launcher.compose`, `pages.py`, `tools.py`).
The hub sees the engine only through `base/contract.py` (`Observation`, `Source`) and components only through the bus.

## Files
| File | Role |
|---|---|
| `tracker.py` (987 lines) | `Tracker` (EXP needed, level-up/death classification, ETA, adaptive `next_interval`, `status()` -> status.json), `MapStats`, `Session` (CSV row, journal, events), `run(cfg, args, source, hub)` the service loop (lock, read, poll, AFK/absent closing, stop file). **Most contended file: small changes only** |
| `bus.py` | `Hub` + `State`. `on/emit`, `feed_facts`, `publish/published`, `provide/ask`, `settings`, `state`, `wake/take_wake`, `snapshot`, `album_shot`, `tell_source`, `journal`, `log`, `on_idle/idle`, `shots_dir`. Each subscriber timed as `bus.<event>`; failures logged `bus: <event> -> <handler> failed` |
| `live.py` | `LiveFile`: live.json = `t`, `character`, then published sections in `KEYS` order (hp, mp, regen, skills, usage, training, party); written after a facts batch holding a `vitals` fact, at most every 0.9 s |
| `history.py` | `write_json` (atomic, retries `os.replace` on PermissionError), `read_json`, csv helpers, `Journal`, `add_session`, `add_map_stint`, `build_digest()` (digest.json), `cleanup(retention_days=14)` |
| `character.py` | `Profile`: what to believe from the game windows, per character fingerprint -> history/characters.json (stats, skills, channel, regen, change log); `score_stat/skills/channel/who` (0-10 rule); `summary()` for the digest |
| `exprate.py` | `ExpRate`: EXP/hr per map visit (active clock, 120 s break, first number after 3 gains, "~" until 5 active min, lumps/party apart) |
| `helper_settings.py` | helper_settings.json `DEFAULTS` (keys: character, discord, diary, capture, internet, updates, skillbar, party, trainingpanel, partypanel, map_check, regen_check), `load/save/job`. Order of keys = JSON bytes: don't reorder |
| `helper_ui.py` | The helper window + tray (`App`, `Tray`, `Snapshot`, `menu_items`), `QUIET_RESTART_S`, `main(argv, services)`; reads status/digest/live files |
| `settings_ui.py` | `SettingsWindow` (General, Character, Discord, Diary, Internet, About pages; components add pages/extras) |
| `ui/shell.py` | `Shell`: the only API a component's `ui()` sees (add_menu, add_command, add_settings_page, add_settings_extra, on_settings_save, on_refresh, on_exit, add_page, add_api, set_camera, settings/all_settings/set_setting/save_settings, post, notify, root, k, px, at, demo, visible, headless, log_error) |
| `ui/overlay.py` | `PanelHost` (create/remove/toggle/lock for panels over the game), `font`, `text` |
| `ui/theme.py` | Colours and font |
| `pages.py` | Local page registry (`register_page/page/paths/open_page/serve`, `register_api/api` = local POST endpoints, `set_host`); the diary server (127.0.0.1:8737) hosts the pages |
| `tools.py` | `portrait_maker()` hook, loaded by name from the launcher |

## Interfaces
- **Emits (tracker):** `service_start/stop, loop, poll, facts_begin, fact, facts, session_start, observation, progress, moment, session_end, levelup, death_guess, death, job_change, map_enter, map_change`. Component-emitted: `exp_rise` (training), `first_kill` (album). **Payload text: `base/contract.py` EVENTS** (add events/services there first).
- **Services:** `exp_rate, visit_map, seen_places, usage, mp_price, price_of, party_on_map` (`base/contract.py` SERVICES). Tracker asks `exp_rate` for the visit rate.
- **Source hooks used:** `read(hints)`, `poll`, `take_facts`, `checks_per_s`, `boost`, `wake_at`, `snapshot`, `album_shot`, `after_step`, `begin_session/end_session`, `close`.
- **Writes (DATA, root):** `status.json` (every reading), `digest.json` (every 5 min, level-up, session end), `journal.md`, `live.json`, `stop` (read), `tracker_live.log`, `crash.log` (launcher).
- **Writes (history/):** `levels.csv, sessions.csv, map_stats.json, maps.json, exp_table.json (measured), state.json, characters.json, perf.csv, tracker.lock, helper.lock, ui_state.json`; per session `sessions/<ts>/{exp_log.csv, summary.json, frames/, levelups/}`. Formats: handoff `03-data-files.md`.
- **Reads (shipped):** `gamedata/exp_table_classic.json` (tracker, history: `REF_TABLE`).
- **Settings owned:** all of helper_settings.json DEFAULTS; components point at their slice via `settings_defaults`.

## Test it
- `python dev/release_check.py` (compile, `check_layers --strict`, sims). `python dev/check_layers.py [--strict|--max N|--json P]` (git-tracked files only: `git add -N` new ones first).
- `python dev/svc_sim.py OUT frames|csv PATH`: tracker end to end (level-up, source down, game gone/back). Baseline line: `Lv 4 84.78% -> Lv 5 20.74%, +42 EXP, 1 level-up(s)`.
- `python dev/album_sim.py OUT` (bus, facts, training -> album), `python dev/regen_sim.py LOG`, `python dev/training_sim.py LOG "Map"` (need a `dev/vitals_log.py` recording).
- Helper UI on the left monitor: `python companion.py --at=-2400,40` (never saves settings in `--at` runs). Source `python -m hub.helper_ui --at=...` / `--demo`.
- Build a hub in a test: `from launcher import compose; hub = compose.make_hub(root=scratch)`.
Live check list: status.json updates, level-up journal line, digest after a session, no `bus:` lines in `tracker_live.log`, live.json sections present.

## Out of scope / don't read
`engine/` pixel code, `components/*` internals (use the bus), `launcher/updater.py`, `packaging/`.

## Rules that bite here
- Keep `tracker.py` edits small; new behavior = a component on the bus, not a new call in the loop.
- Handlers end with `**_`; never import another component; reach the screen only via `hub.source`.
- Never write the player's files from a test: use `tracker.set_root` / `make_hub(root=...)` and `MAPLE_COMPANION_OFFLINE=1`.
- No player data in git (digest.json, status.json, journal.md are local).
