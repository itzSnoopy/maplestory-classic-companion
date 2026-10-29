# classic-helper: MapleStory Classic World EXP tracker

A passive leveling tracker for **Nexon's official MapleStory Classic World** (pre-Big Bang
server). The player is a Magician heading for Cleric. This is **not** the v83 private-server
project in the parent folder. Don't use its `reference/` data (EXP tables, mob stats); Classic values differ.

The tracker (`hub/tracker.py`) runs on its own (no AI) as a background service. Data comes from a pluggable
*source* (today: frames of the game window + OCR of the EXP bar / level badge / map title), normalized into
`Observation`s, so sources can be swapped later (APIs etc.) without touching the rest. Claude is only needed
for questions, recommendations, and changing the code.

## Handoff

**Development sessions: read `.claude/handoff/README.md` first.** It has the current status, the index
of topic files (overview, architecture, data files, operations, UI facts, roadmap, decisions, session
log), and the rules for parallel sessions. Before ending a session, append to
`.claude/handoff/08-session-log.md` and update the README status block. Quick check-ins
("how am I doing") don't need the handoff; use the order below.

## Answering questions cheaply (read in this order, stop when answered)

**Where the live data is (since 2026-10-24):** the player plays with the **installed app**, so its
data folder `%LOCALAPPDATA%\MapleClassicCompanion` (`C:\Users\snoopy\AppData\Local\MapleClassicCompanion`)
has the current copies of the files below. The copies in this code folder stopped on 2026-10-24
around 21:55 (the source tracker no longer runs). Read only those files there, nothing else on the PC.

1. **`digest.json`** (~1–2 KB): current level/%/ETA/EXP-hr/map, last 7 days, time per level,
   best maps by EXP/hr, projection for the next levels, recent sessions. Most questions end here.
2. **`status.json`**: live snapshot. `state` is `playing` / `idle` / `waiting_for_source` / `stopped`.
3. **`diary.md`** (milestones: level-ups, deaths, job advancements, first visits, notes) or
   **`journal.md`**: readable event log (sessions, level-ups, map changes, deaths). Read the tail.
4. Only if needed: `history/levels.csv`, `history/sessions.csv`, `history/map_stats.json`,
   `sessions/<ts>/exp_log.csv` (every frame, large), `sessions/<ts>/summary.json`.
   Stats / skill levels / channel: `digest.json` "character", full detail per character in `history/characters.json`
   (only as fresh as the last time the player opened those windows: see the `seen` times).

Don't read the PNGs unless asked. They're big, and the numbers are already in the CSVs.

## Game data (Classic values only)

- EXP per level: `gamedata/exp_table_classic.json` (from MeowDB, matches values measured from the bar).
  Measured values: `history/exp_table.json`.
- For mobs, maps, drops, quests use **MeowDB/NiaDB** (meowdb.com/msclassic),
  classic.maplestorywiki.net, OSMS Data Explorer, Henesys.GG, Metaroad.
  **Never** use modern GMS or private-server wikis.
- For recommendations, combine the player's measured EXP/hr per map (`best_maps`,
  `map_stats.json`) with mob level and EXP from MeowDB.

## Running it

- **The player's tracker is the installed app** (`%LOCALAPPDATA%\Programs\MapleClassicCompanion`,
  desktop shortcut; its helper starts its tracker). New code reaches it through **/testbuild**
  (`dev/testbuild.py`: local versions M.m.90+, never on GitHub) and **/release** (GitHub).
- The source tracker here (`start_tracker.cmd` / `stop_tracker.cmd`, `pythonw companion.py --service`)
  is for development only and no longer starts at logon (its Startup shortcut was moved to
  `build/startup_backup/` on 2026-10-24). Don't run it while the installed app tracks: both would
  capture the game. Only one copy per data folder runs at a time (`history/tracker.lock`).
- It captures the game window (Windows Graphics Capture; config.json `source.type` can pick the
  csv replay for tests). It opens a session when the EXP bar is on screen and EXP moves, and closes it
  after 10 min without the game on screen or 30 min AFK. Settings are in `config.json` → `service`.
- Log: `tracker_live.log` (one line per minute plus events; rotated at 5 MB).
- To restart after code changes, run `stop_tracker.cmd`, wait for `tracker service stopped` in
  the log, then run `start_tracker.cmd`.

## Code map (for development sessions)

Since 2026-10-28 the code is split into layers, one top-level folder each, with enforced import rules
(`dev/check_layers.py --strict`, part of `dev/release_check.py`). **Each folder has its own CLAUDE.md**
listing its files; read the one for the folder you touch. Full picture: `.claude/handoff/02-architecture.md`.

| Folder | What lives there | May import |
|---|---|---|
| `base/` | `contract.py` (Observation, Source, **EVENTS**, **SERVICES**, Component), `paths.py`, `lean.py` (import first in every entry point), `version.py`, `perf.py` (**wrap new per-frame work in `perf.timed("name")`**), `internet.py` (the one gate for internet use) | nothing internal |
| `launcher/` | `companion.py` (entry: helper, `--service` = tracker), **`compose.py`** (the one place that wires hub + components + source, injects the translator), `updater.py`, `setup_ui.py`, `whats_new.py/.json` | everything |
| `engine/` | Frames -> raw readings: `sources/` (`wgc` default, `screen` = ScreenSource, `csv_replay`), glyphs, OCR, layout, minimap, map watch, death box, HP/MP, game windows (panels), job, name, party box, close-up, level-up shot timing, capture rate | `base` only (Classic knowledge comes injected: `gamedata/translate.py`) |
| `gamedata/` | Classic facts: `translate.py` (map/job/skill snapping, EXP table), `maplist.py`, `parse.py`, `game_refs.py`, every `*_classic.json`, `map_mobs.json`, `important_maps.json` | `base` |
| `hub/` | `tracker.py` (Tracker, Session, loop: emits events), `bus.py` (Hub: events, services, live.json sections), `live.py`, `history.py`, `character.py`, `exprate.py`, `helper_settings.py`; helper shell `helper_ui.py`, `settings_ui.py`, `ui/` (Shell, PanelHost, theme), `pages.py` | `gamedata`, `base` |
| `components/` | One folder per feature, each with `component.py` (`attach(hub)` tracker side, `ui(shell)` helper side), registry in `__init__.py`: killers, diary, skillbar, album, party, training, discord | `hub`, `gamedata`, `base`; **never another component** |
| `relay/` | Party relay server the player hosts (stdlib + Docker); first to get its own repo (Phase 7) | nothing |
| `packaging/` | `build.py` (PyInstaller zip, bundled Tesseract; `HIDDEN` for lazily loaded modules, `DATA_FILES` for shipped json), START_HERE.txt, THIRD_PARTY.txt | - |
| `dev/` | Offline checks and tools: `release_check.py`, `check_layers.py`, `svc_sim.py`, `album_sim.py`, `regen_sim.py`, `training_sim.py`, `update_sim.py`, `bench.py`, `engine_tools.py` (`record`, `panels`), `testbuild.py`, `fetch_*.py` | - |
| `NOTES.md` | **Running log of formats, OCR findings, design decisions. Read before changing OCR/parsing, and add to it after.** | - |

Adding a feature = a new `components/<name>/` that subscribes to bus events; the tracker core doesn't change.
Component handoffs (local only): `.claude/handoff/components/` (diary, skillbar, album, party, installer).
Sims set `MAPLE_COMPANION_OFFLINE=1` so they never go online.

Test changes offline before restarting the live tracker. Save simulation output to the scratchpad,
not the real `history/`, `journal.md` or `sessions/`.

## Rules

- **Screenshots and frames are Nexon game imagery.** Keep them local. Never upload, commit to a
  public repo, or publish them. (Root project rule 1 applies.)
- `config.json` is local (may still hold an old OBS password from before 2026-10-23). Never share or print it.
- Data sources are pluggable (`sources/`), but the default stays passive (frames of the game window only).
  Reading game memory or intercepting game traffic violates Nexon's terms on the official server
  (anti-cheat ban risk): don't build such a source unless the player explicitly decides to accept
  that risk, and flag it when asked. Never send input or automate gameplay.
- **One exception (player's decision, 2026-10-24, ban risk understood):** `dev/local/game_click.py`
  (gitignored, dev only, never in the app) may click through the options menus only (ESC > Game
  Options > Graphics: Resolution, UI Size) to test window/UI sizes. Run it **only after the player
  OKs that specific run in chat**, only when a task needs it, short (its limits: 15 inputs, 90 s).
  Never extend it to gameplay, never run it on your own, never loosen its limits without asking.
