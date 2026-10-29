# hub/: the companion core (tracker, bus, history, helper shell)

**May import:** `gamedata`, `base`. Never `engine`, `components` or `launcher` (when it must reach
upward it loads a module by name with `importlib`: `tracker.py __main__`, `pages.py`, `tools.py`).
The hub sees the engine only through `base/contract.py` (`Observation`, `Source`) and components only
through the bus.

Two processes, talking through files (status.json, live.json, digest.json, party.json):
- **tracker** (`companion.py --service`): `tracker.py` + `bus.py` + the components' `attach(hub)`.
- **helper** (`companion.py`): `helper_ui.py` + `settings_ui.py` + `ui/shell.py` + the components' `ui(shell)`.

| File | Role |
|---|---|
| `tracker.py` | `Tracker` (EXP/level/death logic, ETA, adaptive interval), `Session`, service loop `run(cfg, args, source, sinks, hub)`. Consumes Observations, emits events. Most contended file: keep changes small |
| `bus.py` | `Hub`: `on/emit` (each subscriber timed as `bus.<event>`, failures logged as `bus: <event> -> <handler> failed`), `feed_facts`, `publish`, `provide/ask`, `settings`, `state`, `wake`, `snapshot`, `album_shot`, `tell_source`, `journal`, `on_idle/idle`. Event and service names: `base/contract.py` |
| `live.py` | `LiveFile`: writes live.json (`t`, `character`, then the published sections in the order hp, mp, regen, skills, usage, training, party) after a facts batch with vitals, at most every 0.9 s |
| `history.py` | journal.md, sessions.csv, map_stats.json, digest.json, cleanup |
| `character.py` | `Profile`: what to believe from the game windows, per character -> history/characters.json |
| `exprate.py` | EXP/hr on a map visit (active clock, quest/town lumps out, party share apart) |
| `helper_settings.py` | helper_settings.json DEFAULTS / load / save (one dict; components' `settings_defaults` point at their slice) |
| `helper_ui.py` / `settings_ui.py` | The helper window + tray / Settings window: a *shell* that draws what components register |
| `ui/` | `shell.py` (`Shell`: add_menu, add_settings_page, add_settings_extra, on_refresh, add_page, ...), `overlay.py` (`PanelHost` for panels over the game), `theme.py` |
| `pages.py` | Local page registry + host (diary server serves them; components register paths) |
| `tools.py` | Offline tool hooks loaded by name from the launcher (`portrait_maker`) |
