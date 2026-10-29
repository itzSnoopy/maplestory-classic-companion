# launcher/: entry, composition, install and update

The top of the stack: the program's `main`, the one place that wires every layer together
(`compose.py`), self-install and self-update (`updater.py`), first-run setup, the "what's new" card.
`packaging/` (repo root) builds the release zip from it; the `/testbuild` and `/release` skills drive that.

**Memory (local):** `.claude/handoff/components/installer.md` (the launcher memory; filename kept because the skills link to it): release history, updater facts, installer plan, open items.
**May import:** every layer (base, engine, gamedata, hub, components). Nothing imports the launcher
statically (hub code that needs it loads it by name with `importlib`: `hub/tracker.py __main__`, `hub/pages.py`, `hub/tools.py`).
`components/__init__.py` (the registry) counts as launcher.

## Files
| File | Role |
|---|---|
| `companion.py` | `main()`: `import lean` first, `updater.adopt(args)` (hand off to the newest installed version), then `compose.service_main` (`--service`) or `compose.helper_main`; any crash -> `<DATA>/crash.log`. The **root `companion.py` is a shim** (PyInstaller script, `start_*.cmd`, `paths.self_command`) |
| `compose.py` | **The one composition path.** `make_hub(root=None, components=None)` (Hub + every registered component attached; tests pass a scratch root), `build_source(cfg, log)` (Tesseract set up, `pick_type`, `gamedata.translate` injected), `screen_source(cfg, frames)` (saved frames: sims, bench, release_check), `run_service`, `service_main(argv)`, `HelperServices` (`components()`, `updater`, `setup_window`, `whats_new`, `needs_setup`, `capture`; all lazy), `helper_main`, `portrait_maker`, `page_host` |
| `updater.py` | Packaged only (`paths.FROZEN`). `adopt` (install own copy into `versions\<ver>`, point `latest`, start the real folder's exe, exit), `Updater` thread (feed poll + `check_local`), `feed_versions` (`releases.atom`, ETag), `install` (zip + `.sha256`, unpack, swap), `rank/test_base` (test builds sit just above their base), `cleanup` (keep `KEEP = 2` + the running one), `ensure_shortcuts` (Start menu + desktop, once per PC via `history/shortcuts.json`) |
| `setup_ui.py` | `SetupWindow`: welcome + name, grab the game window and cut the minimap templates, type what the EXP bar and badge show, done; hands `translate.exp_needed` to `engine.calibrate.check_typed` |
| `whats_new.py` / `whats_new.json` | "Updated to x.y.z" card after a self-update (information only: no diary/journal); remembers `history/app_version.json` |

## Interfaces
- **Processes:** `companion.exe` (helper + tray, starts the tracker via `paths.self_command("--service")`) and `companion.exe --service` (tracker). Flags: `--at=X,Y` (dev window spot), `--demo`, `--setup`, `--settings [page]`, `--details`, `--no-handoff` (stay on this copy; release_check), `--after-update` (set by the updater's restart).
- **Env (tests):** `MAPLE_COMPANION_DATA` (data folder), `MAPLE_COMPANION_PROGRAMS` (install root; also blocks shortcuts), `MAPLE_COMPANION_UPDATE_BASE` (fake GitHub), `MAPLE_COMPANION_OFFLINE`.
- **Updater facts:** layout `%LOCALAPPDATA%\Programs\MapleClassicCompanion\versions\<ver>` + `latest` junction; processes always run from the real version folder, never via the junction. Poll 30 s after start, doubling to 5 min (`helper_settings.json updates.min_s/max_s`); `LOCAL_S = 15` s local check (no network) for a newer folder installed by `/testbuild`. A release without a `.sha256`, a bad hash or `../` paths is refused. Restart when status.json `state` is not `playing`, after `QUIET_RESTART_S = 60` s of no play (constant lives in `hub/helper_ui.py`) or at once from tray > Update; if pystray is missing the window loop pumps the same commands (tray fallback). Needs `internet.allowed("updates")`.
- **Ranks:** release `(v, 0, v)`; test build `(base, 1, v)` (`TESTBUILD.txt` in its folder names the base). 0.6.90 (on 0.6.4) beats 0.6.4 and older 0.6.9x, any real 0.6.5 replaces it.
- **Writes (DATA):** `updater.log`, `crash.log`, `history/app_version.json`, `history/shortcuts.json`, `config.json` (first run, from `config.example.json`), templates/glyphs (via setup).
- **Reads (shipped):** `launcher/whats_new.json`.
- **whats_new.json rule:** every release adds 1-3 player-facing lines under its version (`"0.7.1": [...]`); no entry = no card. Plain language, no developer talk.
- **packaging/build.py rules:** every shipped json read via `asset()` goes in `DATA_FILES` (repo-relative path; lands at the same relative path in the bundle); every lazily imported module (each `components.<x>.component`, `hub.*`, `engine.*` loaded by name) goes in `HIDDEN` (dotted names); `EXCLUDE` = `cv2` (stubbed by `rthook_cv2.py`), `pkg_resources`, `setuptools`; `FORBIDDEN` file names must never be in the zip (config, helper_settings, glyph tables, templates, party/live json). PyInstaller 6.21.0, one-folder, windowed; Tesseract trimmed (exe + the DLLs it loads, `eng` only) from `TESSERACT_DIR`. Output `dist/MapleClassicCompanion-<ver>-win64.zip` + `.sha256`.

## Test it
- `python dev/update_sim.py OUT`: install/hand-off, junction path, feed 200/304, bad hash, no hash, zip slip, cleanup with a file in use (fake GitHub on localhost).
- `python dev/release_check.py` (code checks) and `--build` (build, zip audit: no player/game files, top level exe + START HERE + `_internal`, size < 80 MB, matching `.sha256`; start the exe's tracker and helper on throwaway folders, the helper's setup window must open on an empty data folder; windows go on the left monitor).
- `python dev/testbuild.py [--dry-run|--no-build]` installs a local `M.m.90+` build on this PC only. **Skills** (`.claude/skills/`): `/testbuild` (quick checks, packaging gaps, build, install, hand over, session log) and `/release` (checks, version + whats_new, `--build`, handoff update, commit + push, `gh release create` as itzSnoopy with BOTH zip and `.sha256`, `--prerelease`). Never `taskkill /IM`; kill test processes by PID.
- Live: after an install, the helper offers the switch within ~15 s; verify `updater.log`, the version in Settings > About, the "Updated to" card.

## Out of scope / don't read
`engine/` pixel code, `hub/tracker.py` internals, `components/*`, `relay/`.

## Rules that bite here
- Never ship Nexon imagery or player data (templates, glyphs, screenshots, `art/placeholder_source.jpg`); the release calibrates on the player's own screen.
- Never re-upload a different zip under an existing tag; publish a new version. Test-build numbers (`M.m.90+`) are never published.
- Anything that launches `python.exe` won't work in the exe: use `paths.self_command`.
- No AI-generated art (the artist friend's icons are in `art/icon.png`).
