# classic-helper: MapleStory Classic World EXP tracker

A passive leveling tracker for **Nexon's official MapleStory Classic World** (pre-Big Bang
server). The player is a Magician heading for Cleric. This is **not** the v83 private-server
project in the parent folder. Don't use its `reference/` data (EXP tables, mob stats); Classic values differ.

`tracker.py` runs on its own (no AI) as a background service. Data comes from a pluggable
*source* (today: frames of the game window, or OBS frames, + OCR of the EXP bar / level badge / map title), normalized into
`Observation`s, so sources can be swapped later (APIs etc.) without touching the rest. Claude is only needed
for questions, recommendations, and changing the code.

## Handoff

**Development sessions: read `.claude/handoff/README.md` first.** It has the current status, the index
of topic files (overview, architecture, data files, operations, UI facts, roadmap, decisions, session
log), and the rules for parallel sessions. Before ending a session, append to
`.claude/handoff/08-session-log.md` and update the README status block. Quick check-ins
("how am I doing") don't need the handoff; use the order below.

## Answering questions cheaply (read in this order, stop when answered)

1. **`digest.json`** (~1–2 KB): current level/%/ETA/EXP-hr/map, last 7 days, time per level,
   best maps by EXP/hr, projection for the next levels, recent sessions. Most questions end here.
2. **`status.json`**: live snapshot. `state` is `playing` / `idle` / `waiting_for_obs` / `stopped`.
3. **`diary.md`** (milestones: level-ups, deaths, job advancements, first visits, notes) or
   **`journal.md`**: readable event log (sessions, level-ups, map changes, deaths). Read the tail.
4. Only if needed: `history/levels.csv`, `history/sessions.csv`, `history/map_stats.json`,
   `sessions/<ts>/exp_log.csv` (every frame, large), `sessions/<ts>/summary.json`.

Don't read the PNGs unless asked. They're big, and the numbers are already in the CSVs.

## Game data (Classic values only)

- EXP per level: `exp_table_classic.json` (from MeowDB, matches values measured from the bar).
  Measured values: `history/exp_table.json`.
- For mobs, maps, drops, quests use **MeowDB/NiaDB** (meowdb.com/msclassic),
  classic.maplestorywiki.net, OSMS Data Explorer, Henesys.GG, Metaroad.
  **Never** use modern GMS or private-server wikis.
- For recommendations, combine the player's measured EXP/hr per map (`best_maps`,
  `map_stats.json`) with mob level and EXP from MeowDB.

## Running it

- Starts at logon: Startup-folder shortcut "Maple Tracker" runs
  `pythonw.exe tracker.py --service`. `start_tracker.cmd` and `stop_tracker.cmd` do the same by hand.
  Only one copy runs at a time (`history/tracker.lock`).
- It captures the game window directly (default) or reads OBS: Settings > General in the helper
  (`helper_settings.json` capture.source; config.json `source.type` overrides). It opens a session
  when the EXP bar is on screen and EXP moves, and closes it
  after 10 min without the game on screen or 30 min AFK. Settings are in `config.json` → `service`.
- Log: `tracker_live.log` (one line per minute plus events; rotated at 5 MB).
- To restart after code changes, run `stop_tracker.cmd`, wait for `tracker service stopped` in
  the log, then run `start_tracker.cmd`.

## Code map (for development sessions)

| File | Role |
|---|---|
| `tracker.py` | Tracker (EXP/level/death logic, ETA, adaptive interval), Session, service loop. Consumes `Observation`s only |
| `sources/` | Pluggable data sources: `base.py` (Observation, Source), `screen.py` (OBS frames → glyph/Tesseract reading), `wgc.py` (game window frames without OBS, type `wgc`), `csv_replay.py`. Select in config `source.type` |
| `layout.py` / `layouts.json` | where HUD boxes sit at any window size (anchors + recorded per-size boxes; `python layout.py record`) |
| `minimap.py` | map title from the full or collapsed minimap, found anywhere by its WORLD button |
| `sinks.py` | Live outputs (OBS overlay) |
| `diary.py` / `diary_html.py` / `portrait.py` | Diary timeline (`history/timeline.jsonl` → `diary.md` + `diary.html` viewer, screenshots in `screenshots/diary/`) / character close-up from the name tag |
| `history.py` | journal.md, sessions.csv, map_stats.json, digest.json, cleanup |
| `glyphs.py` | exact pixel-font reader for EXP bar and level badge; learns new glyphs |
| `ocr.py` / `parse.py` | Tesseract wrapper (map title) / text → numbers |
| `capture_obs.py` | OBS WebSocket: screenshot, set overlay text |
| `helper_ui.py` | floating info window + tray icon (read-only on status/digest; `start_helper.cmd`) |
| `settings_ui.py` / `helper_settings.py` / `discord_rpc.py` | helper settings window, `helper_settings.json`, Discord Rich Presence (off by default) |
| `jobread.py` | job name from the status bar, read only at start / when an advancement is possible |
| `maplist.py` / `maps_classic.json` | snaps map reads to the 453 real Classic map names (NiaMeowDB list) |
| `obs_test.py` | connection test, crop/OCR check, `--learn` glyphs by hand |
| `dev/svc_sim.py` | offline end-to-end test of the service with saved frames |
| `NOTES.md` | **running log of formats, OCR findings, design decisions. Read before changing OCR/parsing, and add to it after.** |

Test changes offline before restarting the live tracker. Save simulation output to the scratchpad,
not the real `history/`, `journal.md` or `sessions/`.

## Rules

- **Screenshots and frames are Nexon game imagery.** Keep them local. Never upload, commit to a
  public repo, or publish them. (Root project rule 1 applies.)
- `config.json` holds the OBS WebSocket password. Never share or print it.
- Data sources are pluggable (`sources/`), but the default stays passive (screen frames only: the game window or OBS).
  Reading game memory or intercepting game traffic violates Nexon's terms on the official server
  (anti-cheat ban risk): don't build such a source unless the player explicitly decides to accept
  that risk, and flag it when asked. Never send input or automate gameplay.
