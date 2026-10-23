# Classic World EXP Tracker — working notes

Passive EXP tracker for **MapleStory Classic World** (Nexon's official pre-Big Bang server).
It grabs frames from OBS over WebSocket, OCRs fixed crop regions, logs per-session CSV and
per-map stats, writes a live `status.json`, and can update an OBS text overlay.

It never reads game memory, never touches the game process, and never sends input.

Game-data sources (Classic values only): MeowDB/NiaDB (meowdb.com/msclassic),
classic.maplestorywiki.net, OSMS Data Explorer, Henesys.GG, Metaroad. **Don't use** modern GMS or
private-server wikis (including the v83 MoopleDEV `ExpTable` in the sibling server project).
Their values differ.

## Files

| File | Role |
|---|---|
| `config.json` | OBS connection, source name, crop regions (in OBS source pixels), feature toggles |
| `capture_obs.py` | OBS WebSocket client: version, list inputs, source screenshot, set text |
| `ocr.py` | Tesseract wrapper and per-region preprocessing |
| `parse.py` | Text → numbers: EXP bar, level, map name, chat gain lines, chat dedupe |
| `tracker.py` | Main loop: stats, CSV, summary, status.json, overlay, notifications |
| `obs_test.py` | Connection test (grabs `frame.png`), offline crop/OCR check, `--learn` for EXP glyphs |
| `glyphs.py` / `glyphs.json` / `glyphs_level.json` | Exact pixel-font reader; learned templates for the EXP bar and the level badge |
| `history/` | Long-term: `levels.csv` (time per level), `exp_table.json` (EXP needed per level, learned), `state.json` (resume) |

## Setup

```
pip install -r requirements.txt
```
Install Tesseract (UB Mannheim build): https://github.com/UB-Mannheim/tesseract/wiki
(default path `C:\Program Files\Tesseract-OCR\tesseract.exe`; otherwise set `tesseract` in config).

In OBS: Tools → WebSocket Server Settings → Enable, note port and password.

## Status of requirements

| # | Requirement | State |
|---|---|---|
| 1 | EXP bar: cur/needed/%, level-up detection | Coded, untested on real frames |
| 2 | Map name above minimap, tag rows and sessions | Coded (2-frame confirmation before switching map), untested |
| 3 | Chat EXP per kill, party bonus separate, dedupe across frames | Coded (overlap-based dedupe), wording unconfirmed |
| 4a | CSV per session and `summary.json` with per-map stats | Coded |
| 4b | Live `status.json` | Coded |
| 4c | OBS Text overlay via `SetInputSettings` | Coded, off until the source name is set |
| 4d | Windows notifications (90%, ~10 min left, level up) | Coded, **off** until the player confirms they want them |

## Need from the player

- [ ] OBS **Game Capture source name** (exact, case-sensitive)
- [ ] WebSocket **port** (default 4455) and **password**
- [ ] A **full-resolution Classic World screenshot** (ideally straight from OBS via `python obs_test.py`)
      with the EXP bar, minimap title, and a few EXP gain lines visible, in solo and in party if possible
- [ ] Current **level** (or crop the level number on the status bar so it's read automatically)
- [ ] Overlay, notifications, or both? If overlay: name of the OBS Text (GDI+) source to update
- [ ] OBS canvas/source resolution and whether the game runs windowed at a fixed size

## Unknowns to confirm from real frames

- EXP bar text format: `1234/5678 [21.73%]`? `1234[21.73%]`? Is the needed value shown at all?
- EXP gain message wording and party bonus format (handoff guess: `+12 (+3)`)
- Whether gains show in the chat box, the side system-message area, or both
- Minimap title font, color, position; does it truncate long map names?
- Does `GetSourceScreenshot` work when the Game Capture source isn't in the active scene?

## Design decisions

- **Capture via OBS `GetSourceScreenshot`** (not mss): immune to window moves, overlap, and DPI;
  crops are in game pixels. Calls go through `ReqClient.send(..., raw=True)` to avoid wrapper drift.
  If the source resolution differs from `source_resolution` in config, crops are scaled and a warning is printed.
- **EXP bar is the source of truth for totals.** Chat is only for per-kill and party breakdown.
- **Chat dedupe:** treat each frame's chat box as a scrolling list. Find the longest overlap between the
  end of the previous frame's lines and the start of the current one; only lines after it are new.
  (v1 used "line not seen recently", which drops repeated identical kills like five `+12`s.)
  Limitation: if more lines scroll past than the box holds between frames, they're missed. Use a shorter
  `chat_interval` if per-kill stats matter.
- **ETA** uses a rolling 10-minute window so a map change shows up quickly, not the whole-session average.
- **Map name** switches only after the same new name is read on 2 consecutive frames (OCR noise guard).

## OCR / screenshot findings log

Record each finding: date, region, what was read vs. reality, and what changed (regex, crop, psm,
whitelist, threshold).

### 2026-10-23: first OBS frame (1366x768, Lv 1 Beginner, Mushroom Town)
- OBS 32.0.4 / websocket 5.6.3. Source is `Maplestory Game Capture` (lowercase s), a **Window Capture**.
  Overlay text source `Maple Tracker Text` exists. WebSocket listens on all interfaces, but the
  existing Windows Firewall inbound Block rules for obs64.exe (Private and Public) keep it local only.
- **EXP bar** format confirmed: `EXP. 0[0.00%]`, current EXP then `[pct%]`. **No "needed" value is shown**,
  so EXP totals across a level-up need either an EXP table (Classic sources only) or %-based math.
  White digits on a dark bar, green brackets. Crop [746,735,842,751]; the left edge must clear the `.` after "EXP".
- **Level**: white digit on an orange badge after `LV.`, crop [328,735,362,753]. Stylized font, so OCR may
  be unreliable. Fallback is `--level` plus level-up counting.
- **Map**: minimap title is two lines, area (`Maple Road`) then map (`Mushroom Town - West Entrance`).
  White text with a dark outline on light blue. `parse_map` keeps the last line. The minimap window
  can be moved or collapsed, which breaks the crop. Keep it in the top-left and open.
- **Chat** box [288,634,832,699] is full of megaphone/trade spam on the All tab. Still unknown where
  EXP gain messages appear (chat vs. the bottom-right side notices). Need a frame right after a kill.
- Tesseract isn't installed yet, so no OCR results so far.

### 2026-10-23: switched to 1920x1080, Tesseract installed
- Game now runs at **1920x1080**. UI elements stay the same pixel size and are anchored at the bottom
  center (status bar x≈562–1358) and top-left (minimap). `source_resolution` is now [1920, 1080].
  Crops: EXP [1023,1048,1120,1063], level [608,1054,642,1070], map [62,39,258,68].
- **EXP bar: Tesseract is unreliable.** The same crop read `42`, `12`, `412`, or `2` depending on scale/psm,
  and the green `[` reads as `1` (`2[13.33%]` -> `2113.33%`). Switched to **exact glyph matching**
  (`glyphs.py`). The font is a clean 5x7 pure-white (255,255,255) pixel font with 1px gaps, and brackets
  are solid green (153,204,51), used as separators. Background is (60,68,73).
  Known glyphs so far: `0 1 2 8 . %`. **Still need 3–7, 9, and `,`**: unknowns are read as `?`
  (falling back to Tesseract) and saved to `glyphs/unknown/`. Teach them with
  `python obs_test.py --learn "<what the bar shows>"`.
- `parse_exp` now matches the confirmed `cur[pct]` form strictly first, so a misread `%` can't leak digits.
- **Level** badge (white on orange): Tesseract `--psm 7`, scale 4, white_min 240 reads `1` correctly.
  Untested for 2-digit levels.
- **Map**: scale 4, threshold 180 reads the title perfectly ("Mushroom Town - West Entrance",
  "East Entrance to Mushroom Town"). The area line sometimes garbles, but we only use the last line.
- **EXP gains are NOT in chat.** They show as fading grey side notices in the **bottom-right corner**
  (around x 1790–1915, y ~915–930, stacking upward): `You received EXP (+1)`. Faint grey text with a
  dark outline over busy background art, so OCR will be harder than the HUD. Chat region is disabled
  (box null). Still need a frame with several notices stacked, and one in a party to see the bonus format.
- Unconfirmed: whether quest rewards use the same notice wording as kills.

### 2026-10-23: adaptive capture, level-up capture, long-term history
- **Capture rate follows time-to-level, not %.** ETA = (EXP needed − current) ÷ recent EXP/hr
  (10-min window). Next frame = ETA ÷ `frames_before_level` (15), clamped to
  [`fast_interval` 2s, `interval` 30s]. While warming up (first ~90s, no rate yet) it uses 5s; with no ETA
  and ≥95% it also uses 5s. Simulation: at Lv 1–5 with snails it runs at ~2–5s nearly constantly. At
  a "Lv 30" pace (10% per 30 min) it stays at 30s until ~2 min out, then speeds up to 2s
  (137 frames for 45 min vs 90 at a fixed 30s).
- **EXP needed is derived from the bar**: displayed pct p (2 decimals) bounds needed EXP to
  (cur·100/(p+0.01), cur·100/(p−0.005)]; bounds are intersected across frames. It's stored per level in
  `history/exp_table.json` when the bound is tight. Confirmed so far: Lv 2 needs **34** (30 at 88.24%).
- **Level-up detection**: badge reads level+1 and EXP dropped, or pct dropped more than 5 and the new
  reading doesn't fit the old EXP needed. A drop that still fits the old EXP needed counts as a **death**
  (EXP lost, logged separately). Unexplained small drops, or cur/pct that disagree, are **rejected** as misreads.
- **Level-up frames**: the last 3 full frames, the detecting frame, and 2 after are saved to
  `sessions/<ts>/levelups/Lv<N>_<time>/`.
- **Level badge** is the same kind of crisp white pixel font (bolder: 7 wide). Tesseract read nothing,
  so it now uses glyph matching too (`glyphs_level.json`, seeded with `2`). The tracker learns new level
  digits itself after each detected level-up, since it knows the new level then.
- **Misread found live:** with `3` and `4` unknown, the Tesseract fallback read `30[88.24%]` as
  `30[83.24%]`. Auto-learn correctly refused, but the value was still used. Fixed: a Tesseract result
  that contradicts any known glyph is now discarded (frame rejected). Taught `3` and `4`.
  Known EXP glyphs: `0 1 2 3 4 8 . %`. Still need `5 6 7 9 ,`.
- Run in the background: `python -u tracker.py > tracker_live.log`. Stop cleanly by creating a file
  named `stop` in the project folder (writes summary.json).

### 2026-10-23: first live level-ups, Classic EXP table
- **EXP table**: `exp_table_classic.json`, from MeowDB (https://meowdb.com/msclassic/guides/exp-table-level-1-to-100).
  Lv 1–4 (15, 34, 57, 92) match values measured from the bar. Oddity: from Lv 90 the requirement roughly
  doubles every level (11.9M at 90, up to 9.48B at 99). Verify if we ever get close.
  The tracker prefers a measured value (`history/exp_table.json`), then the reference unless the bar
  contradicts it (prints a WARNING), then the bar estimate.
- **Level-up misread as death (fixed):** `32[94.12%]` -> `0[0.00%]` with the badge still showing `2`
  (the **badge lags the EXP bar by a frame**), and `0 @ 0.00%` fits any EXP needed. Now any drop over
  12 points is a level-up whatever the badge says; deaths need cur > 0, a drop ≤ 12 points that fits the
  old EXP needed, and Lv ≥ 10 (beginners lose no EXP).
- **Level correction from the table:** when the bar pins EXP needed tightly and it matches exactly one
  level in the reference table, the tracker sets that level (caught a Lv 3 -> 4 level-up that happened
  while the tracker was stopped) and learns the badge glyph.
- **Unknown EXP digits are now solved arithmetically**: with EXP needed known, try 0–9 and `,` for each
  `?` and keep the unique combination where cur/need matches the displayed %. Learned `6` this way.
  Tesseract is no longer needed for the EXP bar once EXP needed is known. Known: `0–8 . %`, plus `9`.
- **Map names**: the minimap is narrower on some maps, so the crop caught its right border (read as
  `|`). `trim_border` cuts the crop at the first column with no background pixels. Trailing roman
  numerals misread with l/1 are normalized (`Ground Il` -> `Ground II`). OCR'd names snap to names seen
  in ≥3 frames (`history/maps.json`), but never across a different trailing numeral (I vs II are different maps).
- Memory: one run showed ~790 MB working set and I couldn't reproduce it. Later runs hold steady at ~67 MB. Keep an eye on it.
- **First live level-up captured** (Lv 4 -> 5, 02:12:25, A Split Road). Frames 2s apart at 96.74% then
  `1[0.74%]`; the badge already showed 5 and its glyph was learned. The "at" frame shows the level-up
  light columns. The "LEVEL UP" text had faded within ~2s. Every level-up's "at" frame is now also
  copied to `screenshots/levelups/Lv<N>_<date>_<time>.png`. Same frame shows the bottom-right notices
  stacking: `You received EXP (+4)` and `Mushroom Spore x1 earned. (Etc)` lines.

### 2026-10-23: runs on its own (service mode), journal + digest
- `tracker.py` is now a service: retries OBS every 20s, treats "EXP bar not visible" (no green
  brackets) as away, closes a session after 10 min away or 30 min with no EXP (AFK), and reopens
  when the bar returns and EXP moves. A session is only recorded once EXP moves. Errors in the loop
  are logged and the loop continues. Only one copy runs (`history/tracker.lock`).
- **Active time is AFK-aware**: a frame counts only if EXP moved within `afk_after_s` (300s), so
  level times and EXP/hr exclude standing in town.
- Map OCR only runs when the title crop's pixels change (md5 cache): far fewer Tesseract calls.
- New outputs: `journal.md`, `history/sessions.csv`, `history/map_stats.json` (map stays ≥ 30s),
  `digest.json` (rebuilt every 5 min, on level-up and on session end). Old session `frames/` and
  `levelups/` are deleted after 14 days; `screenshots/levelups/` is kept.
- Started at logon by the Startup-folder shortcut "Maple Tracker" (pythonw, no window). pytesseract
  already runs Tesseract with SW_HIDE, so no console flashes.
- First standalone run caught Lv 6 -> 7 from a 120 EXP quest turn-in at Southperry
  (288/372 -> 36/560, exact).

### 2026-10-23: source-agnostic refactor
- All OBS/OCR code moved to `sources/screen.py`. The tracker consumes `Observation`s
  (`sources/base.py`). The overlay moved to `sinks.py`. `exp_log.csv` now logs `source` plus `raw` JSON.
- Replaying a real `exp_log.csv` through the new `csv_replay` source found a bug: the AFK check used
  wall time, so sources with their own timestamps looked permanently AFK. It now uses `obs.t`.

### 2026-10-23: job name on the status bar
- Box [643,1049,775,1062] at 1920x1080. Tesseract `white_min 220, scale 5, psm 7` -> `Beginner` on 8/8 level-up frames.
  A wider box [641,1047,780,1063] picks up junk (`Beginner ,`, `Beginner i`), so keep it tight. Reads snap to a job list (difflib cutoff 0.8).

### 2026-10-23: diary + character close-ups
- The name tag can be found by template (gray > 150 text mask, FFT correlation). True tag 0.46–1.0,
  false ≤ 0.14 on 5 frames. Close-ups are good even with a touching tag (`dingdingSnoopySparda`)
  and the LEVEL UP banner.
- Quest-reward EXP prints in the chat box too (`You received EXP (+120)`), with item gained/lost lines.
- Long minimap titles get truncated: `Hunting Ground Middle of the Fore`.

### 2026-10-23: map title cut off + map dictionary
- "Hunting Ground Middle of the Fore" was our crop (x 62..258), not the game: the minimap widens for long names. Box now [62,39,560,68].
- Wider box broke `trim_at_border` (most common color = game scene). Fix: background color from the left 80 px. Re-checked 11 frames: all correct.
- Tesseract glues the roman numeral: `Forestl`. `maplist.py` snaps to the 453 MeowDB map names (`Forestl` -> Forest I, `Il` -> II).

### 2026-10-23: direct window capture, window sizes, minimap states
- PrintWindow(PW_RENDERFULLCONTENT) on MapleStoryClass returns 0 and a black bitmap. GetWindowDisplayAffinity = 0.
  Windows Graphics Capture (`windows-capture` 2.0.1) works: first frame ~240 ms, later ~40 ms per read.
- Status bar at 800x600 matched the 1920x1080 one with a (-560,-480) shift, mean diff 0.0 (EXP, level, job).
- Template search on a 2 px grid missed the minimap header at (9,5): pixel fonts mismatch badly 1 px off.
  Always search every pixel (`layout.best_match`) or pre-filter on exact pixels (`minimap._prefiltered`).
- Collapsed minimap: scan for the bar's left edge on the row *above* the text (wy-3); the row
  below hits descenders ("y" in Henesys). Tesseract read `Victoria Road :-Henesys`: strip ` -.` after the split.
