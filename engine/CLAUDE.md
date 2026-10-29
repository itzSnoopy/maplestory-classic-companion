# engine/: frames of the game window -> raw readings

Turns the game window's pixels into readings: the HUD numbers (EXP, level, HP/MP), the map title, the
death box, the Stat/Skill/Channel windows, the party box, the character's name tag. Runs in the
**tracker** process only (the helper imports `engine.calibrate` / `engine.capture` for setup and the slider).

**Memory (local):** `.claude/handoff/components/engine.md`: status, open OCR issues, the OCR review checklist. Read it before changing anything here.
**May import:** `base` only. **Never:** `gamedata`, `hub`, `components`, `launcher`, `asset("gamedata/...")`.
Classic knowledge (map/job/skill snapping, EXP table) arrives as a **translator injected by
`launcher/compose.py`** (the `gamedata/translate.py` module): `make_source(cfg, translate)`,
`ScreenSource(cfg, frames, translate)` (calls `panels.use(translate)`), `JobWatcher(translate, ...)`,
`layout.record(img, cfg, translate, save)`, `calibrate.check_typed(..., exp_needed)`.

Passive only: frames of the game window. Never game memory, network traffic or input.

## Files
| File | Role |
|---|---|
| `sources/__init__.py` | Registry: `REGISTRY` {`wgc`, `csv_replay`}, `pick_type(cfg)`, `make_source(cfg, translate)` |
| `sources/base.py` | Re-exports `base/contract.py` (Observation, Source, OK/ABSENT/UNAVAILABLE) |
| `sources/wgc.py` | `WgcFrames`: Windows Graphics Capture of window class `MapleStoryClass`; `grab(box=, center=)`, `grab_parts` (1/4-scale overview), black frame = ABSENT |
| `sources/screen.py` | `ScreenSource` (859 lines, the hub of the engine): `read(hints)` (EXP/level/map/job/HP, ~every 2-30 s), `poll(t)` (quick look, `checks_per_s`), `quick_vitals`, `take_facts`, `snapshot`, `album_shot`, `after_step` (glyph learning, level-up frames). `ImageFrames` = saved frames for tests |
| `sources/csv_replay.py` | Replays a recorded `exp_log.csv` as Observations (regression tests) |
| `capture.py` | "Screen checks per second": `MIN_FPS 1, MAX_FPS 5, DEFAULT_FPS 2`, `HARD_MAX_FPS 60`, `boost()/live_fps()` for bursts, `describe()` for the slider |
| `layout.py` + `layouts.json` | `place/crop`: HUD boxes at any window size (recorded per-size boxes, else anchor math: bottom-center / top-left / minimap) |
| `glyphs.py` | Exact 5x7 pixel-font reader; `segment/read/learn`; tables `glyphs.json`, `glyphs_level.json` (DATA) |
| `hudtext.py` | HUD text format: `parse_exp` (`1,234[56.78%]`), `parse_level` |
| `ocr.py` | Tesseract wrapper: `prep` (white_min mask / threshold, scale), `read`, `trim_at_border`; falls back to the bundled copy |
| `minimap.py` / `mapwatch.py` | Find the minimap by its WORLD button (full or collapsed) and read the title / when to check it (1 s after a change, slowing to 15 s) |
| `deathbox.py`, `hpbar.py` | "You have died." box (shape + colour, no stored art) / HP % from the red bar fill |
| `vitals.py` | Exact HP/MP numbers from the status bar (own digit table `glyphs_hud.json`); the regen model is NOT here (`components/skillbar/meter.py`) |
| `panels.py` | Character Stat, Skill, Change Channel windows: `scan` on the overview, `read`/`read_at` at full size; digits `glyphs_ui.json` |
| `jobread.py`, `charname.py` | Job from the status bar (`JobWatcher`, reads only when due) / who is playing (pixel fingerprint of the status-bar name) |
| `party.py` | Party box under the minimap: `Finder` (remembers the spot), `read`, `Names` (Tesseract once per row) |
| `portrait.py` | Character close-up: `NameTag` finds the tag by correlation with the status-bar name |
| `levelup_shot.py` | `Timer`: level-up picture at EXP reset + 1.75 s |
| `calibrate.py` | First-run helpers for `launcher/setup_ui.py`: cut WORLD/header templates, learn typed digits |

## The readers (input -> output, cost, learning, evidence)
| Reader | Input (1920x1080 boxes; layout moves them) | Output | perf step | Learns / needs (DATA) | Confirmed in |
|---|---|---|---|---|---|
| EXP bar | [1023,1048,1120,1063], white_min 230 | `Observation.exp_cur/exp_pct`, `raw.exp`; fact `exp` | `exp_read` (readings), `exp_quick` (looks) | `glyphs.json`: typed at setup, `solve_unknowns` from EXP needed, Tesseract only if it agrees with known glyphs; unknowns -> `glyphs/unknown/` | NOTES "switched to 1920x1080", "first live level-ups"; 05 table |
| Level badge | [608,1054,642,1070], white_min 240 | `Observation.level` | (in `exp_read` pass) | `glyphs_level.json`: learned within 60 s of a level-up | NOTES "adaptive capture", "first live level-ups"; 05 |
| Map title | minimap found by WORLD button, title box [62,39,560,68]; collapsed bar via `to_two_lines` | `Observation.map` (+`map_confirmed`, `raw.map_id`), fact `map` | `map_check`, `ocr_map`, count `map_change` | `templates/minimap_world.png`, `minimap_header.png` (cut at setup); md5 crop cache; snaps via `translate.map_name` | NOTES "map title cut off", "direct window capture", 19:00 sticky; 05 minimap sections |
| Job | [643,1049,775,1062], white_min 220, scale 5, psm 7 | `Observation.job` | none (rare; ~160 ms Tesseract) | `translate.job_name`; read at start, retried every 60 s, every 5 min while an advancement is possible | NOTES "job name on the status bar" |
| Death box | centre square 276x276 (`deathbox.PAD`) every quick look | `poll()` True/False | `deathbox` | none (colour/shape numbers) | NOTES 17:27, 17:31, 17:40 |
| HP bar % | interior x 780..884, y 1063..1076 | `Observation.hp_pct`, `raw.hp` (logged, unused for decisions) | `hp_bar` | none | NOTES 17:47 |
| HP/MP numbers | `vitals.STRIP` (780,1046,998,1071) | fact `vitals` {hp, mp: [cur, max]} | `vitals`, `grab_strip` (bursts) | `glyphs_hud.json`: from HUD EXP digits + Stat window, bars as check | NOTES 22:00, 22:15, 2026-10-24 01:15 |
| Stat/Skill/Channel | 1/4-scale overview each look, full read when a window is spotted; remembered spots | facts `stat`, `skills`, `channel` | `panels_scan`, `panels_read` | `glyphs_ui.json` (taught from HUD EXP/level/HP/MP), `history/panel_spots.json`, `templates/skill_icons/` | NOTES 20:40, 20:55, 21:05, 21:15; 05 "Game windows" |
| Who | status-bar name line below the job | fact `who` (10-hex fingerprint) | `charname` | pixel hash, no learning | NOTES 20:55 |
| Party box | `party.search_box` top-left, found by the bar outline | fact `party` rows | `party` | `Names` caches Tesseract per row mask | NOTES 23:50; 05 |
| Name tag | whole frame, template = status-bar name | `snapshot()` close-up | `nametag` | `templates/nametag.png` (exact tag learned from matches >= 0.80) | NOTES 23:40, 23:59, 00:05 |
| Level-up shot | EXP % drop >= 40 points | picture 1.75 s after the reset | - | - | NOTES 22:55 |
| Album keeper | middle 720x720 of the last looks | `album_shot()` | `album` | - | NOTES 03:40 |

Cost rules: new readers go on the frame the tracker already grabs, never a second capture session;
Tesseract only behind a crop-hash cache (a glyph read is ~1000x cheaper). Cost table: NOTES 18:45.

## Interfaces
- **Implements:** `base.contract.Source` (`read, poll, quick_vitals, take_facts, snapshot, album_shot, after_step, checks_per_s, boost, party_name, party_reset, wake_at`).
- **Facts emitted** (via `take_facts`, dealt by `hub.feed_facts`): `vitals`, `away`, `map`, `exp`, `party`, `who`, `stat`, `skills`, `channel`.
- **Settings read** (helper_settings.json, re-read by file time): `capture.fps`, `map_check.{min_s,max_s,step_s,every_s}`; config.json `regions`, `source_resolution`, `job_read`, `death_box`.
- **Writes (DATA):** `glyphs*.json`, `glyphs/unknown/`, `templates/*`, `history/panel_spots.json`, `layouts.json` additions via `record`.
- **Reads (shipped):** `engine/layouts.json` only.

## Test it
Offline first (set `MAPLE_COMPANION_OFFLINE=1`; outputs go to a scratch folder, never the real `history/`):
- `python dev/release_check.py`: compile, layers, data files, privacy, `svc_sim` baseline, **fresh install** (cut templates + typed EXP on `dev/fixtures/levelup_lv5`, no wrong digits learned), **fresh windows** (HP/MP read from the first frame, one Stat window teaches every window digit; `dev/fixtures/windows`), killers audit.
- `python dev/svc_sim.py OUT frames` (play, level-up, source down, game gone/back) or `csv PATH` (replay `sessions/<ts>/exp_log.csv`).
- `python dev/engine_tools.py record [FRAME]` (measure a window size into layouts.json), `... panels FRAME...` / `panels learn FRAME field=text`.
- `python -m engine.deathbox|hpbar|vitals|charname|party FRAME...`, `python -m engine.portrait test FRAME...`, `python -m engine.calibrate check FRAME`, `python -m engine.minimap learn-world FRAME x1 y1 x2 y2`, `python -m engine.layout learn-minimap FRAME`, `python -m engine.sources.wgc out.png` (live grab). Some module docstrings still say `python deathbox.py ...`: use `-m`.
- Cost: `python dev/bench.py stages [N]` / `where` / `tracker [S]` / `idle` (live game), `python -m base.perf report [DAYS]`, `dev/minimized_check.py` (does minimized capture give frames: no), `dev/burst.py [s] [fps]` (records frames for a study), `dev/vitals_log.py` + `dev/regen_sim.py` (HP/MP recordings).
- Fixtures (`dev/fixtures/*`: burst, levelup, levelup_lv5, party, review, settings_runs, windows) are **Nexon imagery: local only, git-ignored**.
Live check list: level-up picture + badge read, a map change, a death, open Stat/Skill/Channel, party box, at the player's window size/UI Size.

## Out of scope / don't read
`hub/`, `components/`, `launcher/`, `gamedata/` (ask the translator's API instead), `relay/`, `packaging/`.

## Rules that bite here
- **Read NOTES.md before changing OCR or parsing, and add a dated entry after.** Exact pixels: JPEG frames break glyph/colour readers (test on lossless PNG).
- Every per-frame reader wraps its work in `perf.timed("name")`.
- Frames, templates and glyph tables are game imagery: never commit, upload or ship (`FORBIDDEN` in `packaging/build.py`, `PRIVATE` in release_check).
- Never send input or read game memory/traffic.
- Search every pixel for pixel-font templates (a 2 px grid misses; NOTES "direct window capture").
