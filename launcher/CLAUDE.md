# launcher/: entry, composition, install and update

**May import:** every layer (base, engine, gamedata, hub, components). It is the only layer that
knows all of them; nothing imports the launcher (hub code that needs it loads it by name with
`importlib`, e.g. `hub/tracker.py __main__`, `hub/pages.py`, `hub/tools.py`).

| File | Role |
|---|---|
| `companion.py` | The program's `main`: helper (no args) or tracker (`--service`); crash.log. The root `companion.py` is a shim for PyInstaller, the `.cmd` files and `paths.self_command` |
| `compose.py` | **The one composition path.** `make_hub()` (Hub + every registered component attached), `build_source(cfg, log)` (engine source with `gamedata.translate` injected), `screen_source(cfg, frames)` (saved frames: sims, bench, release_check), `run_service` / `service_main`, `HelperServices` + `helper_main` (helper gets components, updater, setup, what's new), `portrait_maker`, `page_host` |
| `updater.py` | Packaged only: installs under `%LOCALAPPDATA%\Programs\MapleClassicCompanion` (versions/<ver> + `latest` junction), polls GitHub releases, checks `.sha256`, swaps, restarts when not playing. Test builds rank above their base. Tests: `dev/update_sim.py` |
| `setup_ui.py` | First-run setup (minimap templates, digits from typed EXP); hands `translate.exp_needed` to `engine.calibrate.check_typed` |
| `whats_new.py` / `.json` | "Updated to x.y.z" card after a self-update. Add lines for every release |

Packaging lives in `packaging/` (repo root): `build.py` lists `HIDDEN` imports (every lazily loaded
module, e.g. each `components.<x>.component`) and `DATA_FILES` (every shipped json). Handoff for the
release build, updater and parked installer: `.claude/handoff/components/installer.md` (local only).
