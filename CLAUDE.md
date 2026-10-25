# classic-helper: MapleStory Classic World EXP tracker

A passive leveling tracker for **Nexon's official MapleStory Classic World** (pre-Big Bang
server). The player is a Magician heading for Cleric. This is **not** the v83 private-server
project in the parent folder. Don't use its `reference/` data (EXP tables, mob stats); Classic values differ.

`tracker.py` runs on its own (no AI) as a background service. Data comes from a pluggable
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

- EXP per level: `exp_table_classic.json` (from MeowDB, matches values measured from the bar).
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
- The source tracker here (`start_tracker.cmd` / `stop_tracker.cmd`, `pythonw tracker.py --service`)
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

| File | Role |
|---|---|
| `tracker.py` | Tracker (EXP/level/death logic, ETA, adaptive interval), Session, service loop. Consumes `Observation`s only |
| `sources/` | Pluggable data sources: `base.py` (Observation, Source), `screen.py` (HUD reader: glyph/Tesseract), `wgc.py` (game window frames, type `wgc`, default), `csv_replay.py` |
| `layout.py` / `layouts.json` | where HUD boxes sit at any window size (anchors + recorded per-size boxes; `python layout.py record`) |
| `minimap.py` | map title from the full or collapsed minimap, found anywhere by its WORLD button |
| `sinks.py` | Live outputs hook (none built in today) |
| `diary.py` / `diary_html.py` / `portrait.py` | Diary timeline (`history/timeline.jsonl` → `diary.md` + `diary.html` viewer, screenshots in `screenshots/diary/`) / character close-up: finds the name tag using the status-bar name as template (no setup; both UI sizes; learns the exact tag and your colours while you play) |
| `album.py` / `album_html.py` / `mobicons.py` / `mobs_classic.json` | monster album: every mob as ? / grey (seen, boss, or <= 3 levels above you) / caught; a first kill (one EXP rise only one mob explains, via training.py `on_rise`) takes a photo (middle square from the look before) + diary beat `first_kill`; page `album.html` (also /album.html on the diary server); mob facts `dev/fetch_mobs.py`, pictures downloaded once. Handoff: `.claude/handoff/components/album.md` |
| `levelup_shot.py` | when to take the diary level-up picture: fixed 1.75 s after the EXP bar reset ("LEVEL UP" fully drawn), no screen reading |
| `exprate.py` | EXP/hr on the map visit: active clock (2 min without EXP = break, left out), first number after 3 gains ("~" for 5 active min), quest / town lumps out, party share apart; training.py feeds it, the tracker's EXP/hr and ETA use it |
| `history.py` | journal.md, sessions.csv, map_stats.json, digest.json, cleanup |
| `glyphs.py` | exact pixel-font reader for EXP bar and level badge; learns new glyphs |
| `ocr.py` / `parse.py` | Tesseract wrapper (map title) / text → numbers |
| `helper_ui.py` | floating info window + tray icon (read-only on status/digest; `start_helper.cmd`) |
| `settings_ui.py` / `helper_settings.py` / `discord_rpc.py` | helper settings window, `helper_settings.json`, Discord Rich Presence (off by default) |
| `panels.py` / `character.py` | game windows with long-lived facts: Character Stat, Skill, Change Channel (spotted in a 1/4-scale overview each quick look, read at full size; digits exact via `glyphs_ui.json`, skill names snapped to `skills_classic.json`) / what to believe, **per character** -> `history/characters.json` (stats, skills, channel, change log), `digest.json` "character" |
| `vitals.py` / `live.py` / `skillbar_ui.py` | exact HP/MP from the status bar + regen tick model / `live.json` feed (~1/s) / the movable skill bar over the game (regen wheels, casts left). Component handoff: `.claude/handoff/components/skillbar.md` |
| `party.py` / `partylink.py` / `party_ui.py` / `relay/` | party box under the minimap (HP %, on your map, leader, names) / shares your numbers with your party through the relay the player hosts (`party.json`; Settings > Internet > Party link) / party panel over the game / the relay server (stdlib + Docker). Handoff: `.claude/handoff/components/party.md` |
| `whats_new.py` / `whats_new.json` | "Updated to x.y.z" card in the helper after a self-update (information only, no diary); add lines for every release |
| `charname.py` | who is playing: pixel fingerprint of the status-bar name (OCR garbles it); the real name comes from the Stat window |
| `jobread.py` | job name from the status bar, read only at start / when an advancement is possible |
| `hpbar.py` | HP % from the red HP bar fill (no OCR); logged as raw.hp, not used for decisions yet |
| `deathbox.py` | spots the "You have died." box (shape/colour, no stored art); polled ~1/s between readings (`Source.poll`) |
| `killers.py` / `killers.json` / `map_mobs.json` | who killed the player, guessed from the map's mob list (MeowDB, `dev/fetch_map_mobs.py`) + curated boss maps; `python killers.py audit` |
| `internet.py` | the one gate for internet use: master switch + a toggle per feature (`FEATURES`), Settings > Internet. Every online feature calls `internet.allowed("name")` right before connecting |
| `updater.py` | packaged app only: installs itself under %LOCALAPPDATA%\Programs (versions/<ver> + `latest` junction), polls GitHub releases (30 s -> 5 min), downloads + checks .sha256, swaps, restarts when not playing. Tests: `dev/update_sim.py` |
| `perf.py` | the tracker measures itself: history/perf.csv one row a minute (CPU, RAM, checks/s, cost per step, death-box on-screen time). `python perf.py report`. **New per-frame features: wrap their work in `perf.timed("name")`** |
| `capture.py` | "Screen checks per second" (Settings > General slider, 1-5 for now, default 1): WGC frame rate + the tracker's quick-look loop; followed live. Measured CPU table + `describe()` for the slider |
| `mapwatch.py` | when to check the map name: every 1 s after a change, slowing to 15 s the longer you stay (Settings > General, `helper_settings.json` map_check) |
| `maplist.py` / `maps_classic.json` | snaps map reads to the 453 real Classic map names (NiaMeowDB list) |
| `dev/svc_sim.py` | offline end-to-end test of the service with saved frames |
| `dev/minimized_check.py` | does window capture still give frames while the game is minimized (asks Windows directly) |
| `lean.py` / `dev/bench.py` | import first in every entry point (1 math thread, no OpenCV: ~900 MB less RAM) / CPU, RAM and per-step cost (`tracker`, `where`, `stages`, `idle`) |
| `paths.py` / `companion.py` | where shipped files vs player data live (packaged: %LOCALAPPDATA%) / single entry point (helper, `--service` = tracker) |
| `calibrate.py` / `setup_ui.py` | first-run setup: cut minimap templates, learn digits from typed EXP (the name-tag step was removed 2026-10-24) |
| `packaging/` | `build.py` (PyInstaller release zip, bundled Tesseract), START_HERE.txt, THIRD_PARTY.txt |
| `NOTES.md` | **running log of formats, OCR findings, design decisions. Read before changing OCR/parsing, and add to it after.** |

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
