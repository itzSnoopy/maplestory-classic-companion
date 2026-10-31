# Classic World EXP Tracker — working notes

Passive EXP tracker for **MapleStory Classic World** (Nexon's official pre-Big Bang server).
It reads frames of the game window (Windows Graphics Capture), reads the EXP bar, level badge,
job and minimap title from fixed HUD spots, logs per-session CSV and per-map stats, and writes a
live `status.json` for the floating window.

It never reads game memory, never touches the game process or its files, and never sends input.

Game-data sources (Classic values only): MeowDB/NiaDB (meowdb.com/msclassic),
classic.maplestorywiki.net, OSMS Data Explorer, Henesys.GG, Metaroad. **Don't use** modern GMS or
private-server wikis (including the v83 MoopleDEV `ExpTable` in the sibling server project).
Their values differ.

The code map is in `CLAUDE.md`; this file is the lab notebook: formats, OCR findings, decisions.
Entries below are dated and kept as written, so early ones mention OBS, which was the capture
method until 2026-10-23 (removed, see the last entry).

## Setup

```
pip install -r requirements.txt
```
Install Tesseract (UB Mannheim build): https://github.com/UB-Mannheim/tesseract/wiki
(default path `C:\Program Files\Tesseract-OCR\tesseract.exe`; otherwise set `tesseract` in config).
Then run `pythonw companion.py`; the first-run setup learns the HUD pieces from your screen.

## Open questions

- EXP gain message wording and party bonus format (handoff guess: `+12 (+3)`)
- Whether gains show in the chat box, the side system-message area, or both

## Design decisions

- **Capture the game window with Windows Graphics Capture** (since 2026-10-23; before that OBS
  `GetSourceScreenshot`): works with the window covered or moved, crops are in game pixels.
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

### 2026-10-23: OBS removed
- Window capture (`sources/wgc.py`) had been the default since earlier the same day and the packaged
  app never had OBS. Removed: `capture_obs.py`, `obs_test.py`, the OBS frame source, the OBS text
  overlay sink, the Settings capture choice, config keys `obs` / `overlay`, `obsws-python`.
  `crop()` moved to `layout.py`; `service.obs_retry_s` is now `service.source_retry_s`.
  Glyphs are taught by the first-run setup (`calibrate.py`) instead of `obs_test.py --learn`.

## 2026-10-23 17:27 · Death on screen (burst capture, dev/fixtures/burst/20261023_172714, local only)

Player (Lv 13 Magician, 6 EXP) died on purpose to an **Axe Stump** at Construction Site North of
Kerning City, 1920x1080, burst at 3 fps (`python dev/burst.py`).
- Sequence: hit while touching the mob (HP 52/392 -> 0) -> same frame (~0.3 s later): **"You have
  died."** dialog with a tombstone icon and an orange **REVIVE IN TOWN** button, centered
  horizontally around (960, 540) at 1920x1080 -> character turns into a translucent **ghost** ->
  a **tombstone drops from the sky** and lands on the spot within ~1 s -> dialog stays until the
  player clicks -> fade to black (~0.5 s) -> respawn in the nearest town (Kerning City).
- Status bar HP shows 0/max while dead.
- EXP went 6 -> 0. The tracker **rejected it** (death rule required cur > 0). Fixed: 0 counts when
  the % also reads 0.
- Killer: nothing on screen names it. It's the mob touching the player in the last alive frame.
  Map-based guess: no (6 species); Axe Stump was 2nd of 5 suspects.
- Good diary shot: ~1 s after the dialog appears (ghost + landed tombstone + the killer usually still
  next to it).
- Burst capture note: `sources/wgc.py` ran fine beside the live tracker (two capture sessions).

## 2026-10-23 17:31 · Second death (burst 20261023_173048, local only)

Killed by a **Red Snail** (Lv 4), same map, crowded channel. Last hit: purple damage number **8**
above the character (damage taken is purple; the player's own hits are orange). Then the same
sequence: "You have died." box, ghost, tombstone lands on the spot, the snail still on top of it.
- **The box is pixel-identical between deaths and sits at a fixed spot** at 1920x1080: blue panel
  bbox ~(837,495)-(1082,560). Test: pixels in (700..1220, 400..700) with B>170, R<110, 100<G<170
  -> 13,632-13,634 on every dead frame, far fewer otherwise. A reliable death detector.
- Killer idea from this: the **last purple damage number** + each suspect's touch-damage range
  (MeowDB mob attack) could single out the mob. Red Snail 8 dmg vs Axe Stump etc.
- Side effect of the zero-EXP bug: after the first death the live tracker kept its old 6 EXP and
  rejected every reading (log shows `0[0.00%] ... [rejected]` each 30 s) until a restart.

## 2026-10-23 17:40 · Death box detector (deathbox.py), live

Geometry relative to the window center (1920x1080): blue panel (-122,-44)-(123,21), orange button
(-56,33)-(44,44), tombstone+halo (-90,-38)-(-40,20); searched +-8 px. Scores on the two bursts:
dead frames panel >= 0.823, button >= 0.491, icon >= 0.173; alive frames <= 0.009 / 0 / 0; 78 other
saved frames (diary, level-ups) max panel 0.235, none dead. Thresholds 0.70 / 0.35 / 0.10. ~20 ms a
frame. Unverified: other window sizes (assumed the box stays 1:1 and centered), other boxes in the
same skin (none seen yet).
The EXP-bar glyph check fails on JPEG frames (exact pixel font), so poll() only rejects black frames.

## 2026-10-23 17:47 · HP bar (hpbar.py), used for death / level-up

Read the bar, not the `HP[329/376]` digits: red fill (~206,8,0; rows 1065-1070 R 226-243) over grey,
interior x 780..884, y 1063..1076 at 1920x1080, bottom-center anchor. Tick marks split the fill,
so HP% = last red column / 105 px. Checked: 329/376 -> 92 px = 87.6%; works on JPEG too.
- Level-ups: HP 100% on 7 of 8 saved "at" frames (Lv10 read 85.7%: likely a hit right after the
  refill); "after" frames 65-100%.
- Deaths: 13.3% -> 0 at death (burst 1). **Bar is also 0 px when alive under ~1% HP** (burst 2:
  1-3 HP for 5 s before dying), so empty bar alone isn't death; empty bar + EXP drop is.
- Revive in town: **50 HP** (12.4% of 392), never full.
HP rules for death/level-up were built and then **removed the same hour** (user: too much; level +
EXP bar are enough). HP is still read and logged (raw.hp in exp_log.csv, `hp` on death events) for
later use.

## 2026-10-23 18:45 · Resource use: measured, then cut (dev/bench.py)

PC: 28 cores. "% of a core" = 100 is one core busy. Numbers from `python dev/bench.py` against the
live game at 1920x1080 (player AFK or grinding in West Domain of Perion).

**Before:** live tracker 6.4% of a core, **1,034 MB RAM** (private 898 MB).
- ~850 MB: numpy's OpenBLAS starts one thread per core (24 max), each with a 32 MB buffer; they
  fill on first use. Our arrays are tiny. `OPENBLAS_NUM_THREADS=1`: numpy import 790 -> 49 MB.
- 83 MB: `cv2.pyd`, imported by windows-capture for `save_as_image` only (never used). The
  release build already stubbed it (packaging/rthook_cv2.py); source runs didn't.
- CPU: every frame Windows delivers costs ~4 ms (the library copies it out of the GPU), wanted or
  not. At `minimum_update_interval=100` that's up to 10/s = 1-5% of a core depending on how busy
  the game is. Skipped frames cost nothing (2000 ms: 0.00%). Opening a session per grab instead:
  ~116 ms CPU each, so keep one open.
- Each grab copied the full frame 2-3 times (8 MB BGRA copy, BGRA->RGB, crop to client area).

**Changes:** `lean.py` (thread caps + cv2 stand-in), imported first by every entry point;
`wgc.min_update_ms` default 1000 (the death poll needs 1/s; the loop now subtracts the poll's own
wait so the cadence stays 1.0 s, measured); `WgcFrames.grab(box=, center=)` copies only the
requested part, one BGRX->RGB pass (`Image.frombuffer`, pixel-identical to before); the death poll
grabs only the 276x276 center (`deathbox.PAD`) unless a diary shot is due; deathbox/hpbar crop
before converting. Death box + HP results identical on all 365 fixture frames.

**After:** live tracker **0.47% of a core, 108 MB** (private 144 MB). Remaining RAM: ~40 MB
DLLs (Python, numpy, PIL, GPU driver), ~5 full frames at 8 MB each (PIL keeps RGB as 4 bytes/px):
3 kept for level-up evidence (`recent`), the current one, one transient. Compressing the kept ones
was measured and rejected: zlib/PNG ~80-110 ms CPU per reading to save ~12 MB.

**Cost table (per call, CPU):** full grab ~5-9 ms (+ up to 1 s wait for the next frame), center
grab ~1 ms, glyph read (EXP/level) <1 ms, HP bar <1 ms, death box ~1.5 ms, find minimap ~6 ms,
whole `ScreenSource.read` ~7 ms (map cached), **Tesseract ~105 ms in tesseract.exe + ~15 ms here**
(~160 ms wall; map read only when the title crop changes; job read only at start/advancement).
Tesseract thread caps made no measurable difference.

**Capacity, for new readers:** add them to the *same* frame, never a second capture session.
A pixel/glyph reader on every reading costs ~0.01-0.05% of a core; at 1/s polling ~0.1-0.2% each.
A Tesseract reader is ~1,000x a glyph reader: only behind a crop-hash cache. Faster capture: each
extra frame per second ~0.4% (delivery) + what's read from it; e.g. full readings at 5/s would be
~10% of a core, region-only checks at 5/s ~3%. Each full frame kept in memory: 8 MB.

## 2026-10-23 19:00 · Map name on its own schedule ("stickiness", mapwatch.py)

User: the longer you're in a map the less likely you leave (long grinds); check every 1 s after a
change, slowing +1 s per 5 s in the map up to 15 s; min/max as settings to play with.
- interval = min_s + step_s * (stay // every_s), capped at max_s. Defaults 1 / 15 / 1 / 5: gaps
  1,1,1,1,1,2,2,2,3,3,4,5,6,7,8,10,12,14,15... (15 s from ~70 s); 20 checks in the first 100 s.
- Live cost: a check with an unchanged title ~4 ms (full grab + find minimap + md5); Tesseract
  only on a crop change (~160 ms wall; it re-ran twice in 45 s in West Domain of Perion, the title
  crop changed without the text changing). Tracker 0.83% of a core in its first minute.
- Two checks in a row must agree (1 s apart while a candidate is pending): one garbled read
  (fade between maps) doesn't count. Confirmed names skip the tracker's 2-readings rule.
- Before: the map was read only on readings (every 2-30 s), and the tracker needed 2 of them, so a
  map passed through in under ~1 min could be missed (no first-visit diary entry, no stint).
- Settings re-read on every check (file time cached): changes in Settings > General apply without
  restarting the tracker. Range in the UI: min 1-60 s, max 1-120 s; step/every are file-only.
- Only while the death poll runs (playing, `death_box.enabled`); with it off, checks happen on
  readings only.

## 2026-10-23 19:03 · Minimized: confirmed no capture

`dev/minimized_check.py` (asks Windows Graphics Capture directly, skipping wgc.py's IsIconic
shortcut) with the game minimized: **no frame from Windows** on every try (2 s timeout each).
Windows stops drawing a minimized window, so there's nothing to copy. README's "not while it's
minimized" is right; the IsIconic shortcut stays (it answers in ~3 ms instead of waiting 2 s).
Covered / behind other windows / on another monitor still works.

## 2026-10-23 20:00 · Screen checks per second: a setting (capture.py)

User: now that the costs are known, make the capture rate a slider; MapleStory doesn't draw over
60 fps, so 60 is the top. Settings > General "Screen checks per second (while playing)", 1-60,
default 1, with a live line "about X% of one CPU core (Y% of this whole N-thread CPU)".
Measured (poll loop: center grab + death box per frame, game running, 1920x1080):

| /s | 1 | 5 | 10 | 20 | 30 | 60 |
|---|---|---|---|---|---|---|
| % of a core | 1.1 | 1.7 | 3.0 | 7.2 | 8.4 | 20.6 (Windows delivered ~48/s) |
| RAM MB | 87 | 89 | 90 | 91 | 91 | 130 |

Drives WGC `min_update_ms` (1000/fps; config wgc.min_update_ms still pins it for dev tools) and
the tracker's quick-look loop (death box; map checks when due). EXP readings keep their 2-30 s
schedule; idle stays 10 s. Read live (file time cache): a change restarts the capture session once
(~116 ms CPU). Real service loop switched 1 -> 20 -> 5: 0.8 / 17.8 / 4.8 checks/s.
`death_box.poll_s` in config.json is no longer used (`death_box.enabled` still is).

## 2026-10-23 20:15 · Self-measurement (perf.py) and the 5/s cap

Cap 5/s (user). The tracker now writes history/perf.csv once a minute. First sandbox run (L Forest
II, 5 s rows): poll 5.1 ms CPU avg, read 17.9 ms, map_check 9.6 ms CPU / 172 ms wall, deathbox
0.3 ms, grab_center 0.16 ms, grab_full 4.1 ms; process 4.9% of a core at both 1/s and 5/s over a
minute that included map checks. **Tesseract ran on 13 of 13 map checks** there (the title crop
changes between checks), so the md5 cache doesn't help on that map: ~165 ms wall + ~105 ms CPU
in tesseract.exe per check (not in the process CPU). Death box timing resolution = 1/fps.

## 2026-10-23 20:40 · Game windows: Character Stat, Skill, Change Channel (panels.py)

Goal (user): catch short-lived windows that show long-lived facts. Fixtures (local, gitignored):
`dev/fixtures/windows/` allcommitted_ch31.png (all three open, AP/SP 0, ch 31), lv12_stat.png (AP 5),
lv12_skills.png (SP 3), lv14_skills.png (SP 2, mouse tooltip over Magic Claw). The Lv 12/14 ones
were already in sessions/*/levelups/ (level-up evidence frames): the player opens these right
after leveling.

**Window facts (1920x1080; the windows don't scale, they can be dragged anywhere):**
- Every window: white outer frame, white title bar 14 px tall 4 px below it (stat 175 wide, skill
  187, channel 560, minimap 173), blue close button in the bar.
- Text inside windows is a **pure-black pixel font** (text px exactly (0,0,0), no smoothing),
  digit advance 7 px ("1" is 3 wide, gap 4), a space is a 5+ px gap. Tesseract is bad at it
  (408 -> 405, 77 -> "if", Max -> "hax", Bolt -> "Balt") -> digits by exact glyph lookup
  (glyphs_ui.json, all 0-9 / ( ) % learned from the fixtures). Words still Tesseract + snapping.
- Stat: pink labels (203,79,130) 44x14 (JOB 44x24) at ly+0/18/46/64/82/100/118 (NAME, JOB, LEVEL,
  HP, MP, EXP, FAME); green labels (patterned, ~(138,149,58)) 48x16 at lx-2, ly+141+18k (STR DEX
  INT LUK); values x lx+47..lx+134; AP box white 29x15 at (lx+78, ly+225). JOB shows "MAGICIAN" in
  black + a small pink second line (ignored). EXP shows "1328 (24%)" (whole %). The DETAIL panel
  (attack, magic, acc, avoid, speed, jump; blue labels) opens to the right: not read yet.
- Skill: rows 125x35 at (tx+39, ty+94+40k), k=0..5 visible, bg (205,217,227), selected/hovered row
  (235,235,235); name on top (long ones cut: "Improved MP Reco..."), level 18 px lower; SP box white
  29x15 at (tx+78, ty+344). Tabs: beginner (circle) and 1st job ("I", pink when active); the
  "+" buttons turn orange when SP is free. Same name in two jobs (Improved MP Recovery =
  Magician and Crusader) -> pick the job most of the tab's other skills belong to.
- Change Channel: header blue (74,131,188) with the world name ("WINDIA") in white; cells 66x16
  gray (177,177,177) with white "CH.n", 70x22 grid, 8 a row, 40 channels on Windia; the highlighted
  cell is (54,170,221). Channel = row*8 + col + 1, no text read. Assumed: the window opens with the
  current channel highlighted (ch 31 here, the channel the player said they're on).

**Cost:** each quick look (1/s) now copies a 1/4-scale overview of the same frame (step-4 slice
in the WGC callback, 480x270) next to the death-box square: copy 0.5 ms + to PIL 0.2 ms + scan
0.85 ms = ~1.6 ms (~0.16% of a core at 1/s). While a window is open: a full grab + read each look
(stat 3 ms, skills 11 ms, channel 3 ms; Tesseract ~1 s total the first time per distinct crop,
then cached). False candidates: 0 on 61 lossless full frames without windows.

**Not verified yet:** a real channel change (does the screen go black / EXP bar vanish long enough
for a look to see it? that's what confirms the new channel), the login screen's channel list,
the Beginner tab, a scrolled skill list (2nd job has more than 6 skills), other resolutions.

## 2026-10-23 20:55 · Character windows: one glance is enough, per character

User: these windows are glanced at rarely (at a level-up, or once in 30 min), maybe for a moment;
values only change at milestones, so make sure they're consistent. A player may have several
characters: keep stats/skills per character. Channel switching has no confirmation, only the screen
changing.

- **Consistency instead of "seen twice"** (character.py): one read is kept when it adds up.
  Stats: STR+DEX+INT+LUK+AP = 25 + 5 x (level-1) (Lv 12: 4+4+62+5+5 = 80; Lv 14: 4+4+77+5+0 = 90),
  or no stat went down. Skills: level not lower than before and not above the skill's max. A drop
  (reset items, or a misread) needs two matching reads. SP total looked like 1 + 3 x (level-10) for
  1st job (Lv 12: 7, Lv 14: 13) but isn't used as a rule yet (other tiers unknown).
- **Same-frame reads at remembered spots** (screen.py, panels.window_box/read_at): the game reopens
  a window where it was closed, so every quick look also copies those boxes at full size from the
  same frame (stat ~195x290, skills ~207x380, channel ~580x220; ~1 MB, ~0.2 ms) and reads a window
  there as soon as the overview says it's open. A window open for one frame is read. A window at a
  new spot needs one more look (full grab). Spots persist in history/panel_spots.json.
- **Who is playing** (charname.py): the status-bar name, one text line (14 px) below the job.
  Tesseract reads "SnoopySparda" as "SsnhoopySparda", and names can't be snapped to a list, so the
  key is a 10-hex SHA-1 of the trimmed white-pixel mask (white_min 220): the same on all frames
  Lv 5-14 (13 checked), unaffected by open windows. Checked on every reading (~0.1 ms); a new one
  becomes a "who" fact. The display name comes from the Stat window (OCR reads it right). Two
  characters with the same name on different worlds would share a key (not handled).
- history/character.json (first version) was migrated into history/characters.json (kept as
  character.json.migrated).

## 2026-10-23 21:05 · Channel switching, recorded at 10 fps (dev/fixtures/burst/20261023_205652)

The player switched channel and cancelled once while dev/burst.py recorded (JPEG, so exact-color
window detection doesn't work on these frames; channel read with a +-30 color tolerance):

| time | Change Channel window | after |
|---|---|---|
| 20:57:05 | opens on 30 (current), highlight never moves, open ~1.0 s | closes; screen dark 1.14 s -> now on 21 |
| 20:57:12 | opens on 21 (current), highlight 21 -> 17 -> 21 -> 18, open ~1.2 s | closes; dark 1.14 s -> now on 19 |
| 20:57:17 | opens on 19 (current), open ~0.5 s | Cancel: no dark |

Facts: the window **always opens on the current channel**. The picked channel is **often never on
screen** (double-click switches at once: the 1st switch went to 21 with 30 highlighted until the
window closed). A switch = window closes, then the whole screen fades to black: mean brightness
100 -> 61 -> 24 -> 0, ~0.9 s fully black, back up; < 30 for ~1.15 s, starting ~0.4 s after the
window is gone. Cancel: no fade. The EXP bar isn't visible while dark. Players start clicking
~0.6 s after opening.

Rules now (character.py): first read after opening = current channel ("sure"), except when it
contradicts a sure saved channel: then it waits (saved one highlighted while open -> keep it;
cancelled without -> take the first read). A dark look (center square mean < 30, sources/screen.py
poll) within 3 s of the window's last sighting = a switch: channel becomes "ch ? (switched from
21, maybe 18)" until the window is opened again. Replay of the recorded timeline at 1 look/s,
1000 random phases: both switches detected 1000/1000; in 29% the only look into the 2nd window
landed on a browsed cell (17/18), shown as the channel for ~1 s and then as "switched from 17"
(the real 21 was never seen; a 1/s look can't do better). Dark check on 774 recorded frames: on
only during the 2 switches + one 0.7 s fade in an older recording (portal?), harmless (only
counts within 3 s of the channel window). A portal taken within 3 s of a *cancelled* channel
window would count as a switch (rare; then "ch ?" until the next window).
The live profile said ch 21 after the test (old logic); corrected to 19 by hand from the recording.

## 2026-10-23 21:15 · Screenshot scores (user's rule: under 8 waits for the next screenshot)

Every window read and every status-bar name check gets a score 0-10 with reasons
(character.py `score_stat` / `score_skills` / `score_channel` / `score_who`). >= 8: committed at
once. 4-7: each changed value waits; the next screenshot showing the same value commits it.
< 4: never used. The last score per window is kept in characters.json `last_reads`; low scores
are logged (tracker_live.log only, not the journal).

| read | points off |
|---|---|
| stat | -1 per unreadable field (max -4); -3 STR+DEX+INT+LUK+AP != 25 + 5 x (level-1); -3 level != HUD badge; -2 EXP != HUD EXP (same frame only); -2 HP/MP current > max; -1 label fit < 0.6; -4 any base stat / level lower than saved |
| skills | -1 per unrecognized name, unreadable level, unreadable SP; -4 per level above max; -4 any skill lower than saved |
| channel | -2 fewer than 40 cells; -1 world unread; -3 contradicts a sure saved channel |
| name | 0 covered (text taller than 11 px or touching the crop edge); 10 known character; 7 new after the game left the screen (tracker start, login, fade); 3 new mid-play. A pending name is confirmed by the next clean screenshot of it; facts wait meanwhile |

Results: all 6 real window reads in dev/fixtures/windows score 10. Doctored: a box over the
second "7" of INT 77 reads "7" -> 3/10 with history (doesn't add up + lower than saved: not
used), 7/10 without (waits). A box over the status-bar name -> "covered", ignored. Edge-case
script (16 checks) in the session scratchpad. Channel replay: unchanged (both switches 1000/1000;
the ~29% "first look landed on a browsed cell" case is a 10/10 screenshot, so no score catches it).

## 2026-10-23 22:00 · HP/MP numbers, regen ticks, skill bar (vitals.py, live.py, skillbar_ui.py)

- Status bar `HP[408/408]` / `MP[606/606]`: same white pixel font family as the EXP bar with
  green brackets, but **narrower digits** (EXP templates don't match) -> own templates
  glyphs_hud.json. Text boxes at 1920x1080: HP x 790..880, MP x 882..998, y 1046..1060. Bars:
  HP red interior x 780..884, MP blue (b > 180, r < 80, 80 < g < 190) x 888..992, rows 1066..1070.
- Digit learning: from the Stat window (same frame: identical cur/max), and from the bars: fill the
  "?" so cur/max matches the fill within 2.5%; learn only when exactly one value fits and the fill
  is between 3% and 97% (the bar lags the text at a level-up refill: Lv 10 frame showed 194/194
  with the bar at 86%; a 6% MP sliver measured as empty). Saved frames: 1 and 2 learned this way
  (96/114 at 84.6%, 127/146 at 87.5%); 124 readings then all known, none disagreeing with the text.
- Regen (vitals.Meter): small rises (<= 10% of max or 30) are tick candidates; the most common
  size is the tick; period = the longest half-second step on which all (previous look, sighting]
  windows share one phase (one stray allowed) and <= shortest gap + 1.5 s; phase = middle of that
  overlap. Simulation 1200/1200. Live: MP +9 every 10.0 s at Lv 14 with Improved MP Recovery 5
  (skill text: 1% of 606 = 6; base regen makes up the rest), phase +-0.55 s after ~40 s.
- skills_classic.json per level (MeowDB level texts): Magic Claw MP 10..20 (Lv 6 = 11), Energy Bolt
  8..16, Magic Armor 300..600 s, Recovery 30 s + cooldown 2 min, Nimble Feet cooldown 3 min.

## 2026-10-23 22:15 · Regen fix: the bar said MP ticks every 2-3 s (user), real is 10 s

Recorded HP/MP at 4/s for 2 min (`dev/vitals_log.py`), standing still in town: MP +12 at 12.55,
22.83, 32.61 ... 112.88 s (10 gaps, mean 10.03 s; first tick +9), HP +50 every 10 s, ~1 s after
the MP tick (+38 to reach 408). While fighting/moving the MP tick was +9 (same 10 s beat).
Bugs: (1) the period search was capped by the *shortest* gap between rises, so one stray rise
collapsed 10 s to 2 s; short periods line up with almost anything. (2) HP +50 of 408 is over the
old 10% "potion" cut. Now: cap 25%; longest half-second period with all but 1 in 4 sightings on
one phase, P <= longest gap + 1.5; refined from the sightings' spacing; a period must explain
>= 45% of the gaps between sightings (gaps with time at full skipped); >= 4 matching ticks before
any period is shown. Recording at 1/s, 100 offsets: MP 10.0 s 100/100 (HP: only 3 ticks before
full -> "learning", correct). Synthetic 1200 with heavy same-size strays: ~98%. Live after 1 min:
MP +9 / 10.0 s.

## 2026-10-23 23:00 · Regen ticks aren't a strict clock while playing; level-up reset

User: "MP recovery crashed once I leveled up" (Lv 15, max MP 606 -> 630). Not a crash: the Meter
reset on the max change and never settled again. Recorded (dev/vitals_log.py, fighting around
Perion): MP +9 at gaps 9.8, 10.3, 9.8, **14.8, 12.3** s. Standing still it was 10.0 s every time
(22:15). So play pushes the next tick back, and a fixed phase lattice can't fit.
New Meter: the next tick = last seen tick (window midpoint) + the usual gap; the usual gap = the
cluster of gaps (+-1.5 s) most others agree with (>= 3), and until then the skill text's rhythm
("every 10 seconds", live.py passes it as `prior`). A max change keeps the rhythm (only the size
changes); the shown size is the last tick's (+12 standing, +9 moving). Overdue ticks hold the
wheel at 0 instead of wrapping. Results: steady recording 10.0 s, next tick within 0.2-0.3 s
(median); pushed-back recording 10.0 s, median 0.5-0.8 s (a 5 s push can't be foreseen); synthetic
with delays + casts + a level-up: 294/300.

## 2026-10-23 23:15 · Regen rhythm always from the skill (user rule)

User: the bar showed MP every 5 s; "the skill should always be based on what the skill does, the
screenshot should only help with the timing". The 5 s came from the local tracker still running the
22:15 lattice model (5 s also fits a 10 s beat). A fresh recording (vitals3) was a steady +9 every
10 s. Rule now: when the recovery skill states its rhythm ("every 10 seconds", live.py -> Meter
prior), that rhythm is always shown; the screen only anchors the timing (restart at each tick
seen). The measured gap is kept as `measured_gap` for information only. Recordings 1-3 at 1/s and
2/s: 10.0 s shown every time, next tick within 0.2-0.3 s (0.5-0.8 s with pushed-back ticks).
The tick *size* stays measured (+9 = 1% of 630 from the skill + the game's own regen).

## 2026-10-23 23:40 · Stretched game display breaks setup (first other player)

A friend's setup stopped at "Minimap not found. Open it, keep it top-left" with the full minimap
already top-left. Their screenshot (1920x1080, lossy webp, not kept): the WORLD button's orange area
was about x 248-294, y 11-22 (about 47x12 px, against 34x10 normally), and no pixel was exactly
(221, 153, 17). So the game was drawn smaller and stretched about 1.4x (likely 1366x768 -> 1920x1080).
calibrate.find_world needs exact color and size, so it fails, and the message wrongly blames the position.
**Fix used:** the game's "ideal ratio" display option (no stretching). Supporting the game's default
setting is on the roadmap (A). Every exact-pixel reader would need it, not only the minimap.

## 2026-10-23 23:50 · Party box (party HP UI under the minimap)

Live PNG frames + a JPEG burst, 1920x1080, minimap full at 0,0, party of 2 (SnoopySparda leader,
DSius). Box at x 218-410, y 218-252. Bars: white top border row y=222 at x 342..404 (63 px), white
bottom border y=230, side borders x 341 and 405, black outline rows 221 and 231; second bar 17 px
lower (239). Interior rows 223-229, red strongest in rows 225-228 (e.g. cf0505, edge column 342
9f2323), empty = (45,3a,40)-ish. Fill = last red column + 1 over 63: 90.5 % vs exact 380/424 = 89.6 %.
Names: black text (lum < 32) on your map; DSius on another map was grey, darkest ~50-55, background
under the box 100+ (lum < 100 & sat < 35 catches it, ragged in JPEG). Leader star: ~37 gold pixels
(R>200, G>110, R-B>100) in the icon box, member sun ~14. Tesseract (mask x4, psm 7, alnum
whitelist) read both names exactly on lossless frames, "or" for the grey name in JPEG.

## 2026-10-24 01:15 · Status-bar HP/MP digits = EXP digits

Compared the learned tables: every HUD HP/MP digit (glyphs_hud.json) is exactly an EXP digit
(glyphs.json) once empty top/bottom rows are trimmed (the HUD crop starts 2 rows higher). Only '/'
is extra: a 3-wide diagonal, `.../.../.../..#/..#/.#./.#./#../#..`. A fresh install learns the EXP
digits within minutes, so HP/MP now read from the first frame; the Stat window then learns from
HP/MP ("408/408" teaches '/', 0, 4, 8, ...). Fixtures: one Stat window -> all of 0-9 / ( ) %.

## 2026-10-24 20:55 · No server tick for regen: each character has its own 10 s clock

Question (user): does regen land on a fixed server beat (e.g. every XX:XX:10), so HP/MP could be
read only then? Recorded 5 min at 8/s (dev/vitals_log.py, Lv 18, fighting around Perion /
Kerning, 0.13 s windows): 22 MP ticks of +10. Runs of ticks are 9.82-10.19 s apart (never sooner),
but the phase against the wall clock (time mod 10) wandered 4.2 -> 0.3 -> 0.1 -> 7.2 -> 6.2 ->
8.7 -> 1.2 -> 5.0 -> 9.8 s: 8 gaps of 12.5-23.8 s push it each time. So no global clock. Casting
does not reset it (a tick came 0.13 s after a cast, 10.07 s after the one before); the pushes also
happen with no cast or hit seen, so likely movement / jumping (not visible in HP/MP). HP: no rise at
all in the 5 min (only hits), though standing in town it ticked +50 / 10 s (22:15).
Matches the v83 reference design: the *client* times the heal and asks the server (HealOvertime
packet, server only checks >= 1.5 s between). Use: after a seen tick, the next one is >= ~9.8 s
away, so a reader could look rarely for ~9.5 s and then often until it lands. Casts/hits still
change HP/MP at any time.

## 2026-10-24 21:00 · Standing still the regen clock is exact: burst + clock + reconcile works

User stood still 2 min (10/s log, MP 306 -> 426 of 709, HP full so no HP ticks): 12 MP ticks of
+10, gaps 9.87-10.18 s (mean 10.00), time mod 10 stayed 4.19-4.42 s the whole time: no drift.
Replay (scratchpad recon.py): one opening burst (10/s until the first tick), then predict
anchor + 10.0 s x k, with a reconcile check (5/s for +-0.6 s around the prediction) every N ticks:
never re-checked: median 0.09 s / worst 0.14 s off after 11 ticks; checks every 6 ticks: worst
0.10 s, no misses. Steady cost ~6 looks per check (~3/min at one check a minute) vs 60/min at 1/s;
the opening burst (~100 looks) is most of a 2-min run. While moving (20:55 recording) the same
replay misses 4-8 checks in 5 min (ticks pushed 2-14 s), so a missed check must fall back to
watching until the tick shows, then restart the clock there.

## 2026-10-24 21:12 · Correction: map changes push the regen tick, not movement or casts

User's idea: the pushes in the 20:55 recording came from changing maps. Checked against the
tracker's map reads (sessions/20261024_203532/exp_log.csv): all 8 late ticks there had a map
change inside the gap (20:48:52, 20:49:27, 20:50:01, 20:50:28, 20:50:51, 20:51:32, 20:51:47,
20:52:15). Then 2 min grinding on one map (The Forest of Wisdom, 10/s): 49 MP drops from casts,
MP down to 16/709, a potion (+228), hits, and still 12 MP ticks of +10 every 9.88-10.09 s with
time mod 10 at 0.19-0.43 s the whole time. Casting, moving, getting hit and potions don't move the
clock; the potion isn't a tick and the next tick kept its time. Replay (recon.py): a check once a
minute stays within 0.24 s, no misses. One check landed while a cast's drop hid the +10 in the
same 0.1 s look; a miss should simply re-check, not restart. HP never regenerated by itself while
fighting (only potions, +100); standing in town it does (+50 / 10 s, 22:15).
So: one clock per map visit. Burst on map entry (or tracker start), then predict + rare checks.

## 2026-10-24 21:40 · Regen clock with sticky checks + HP/MP usage (mpuse.py)

User: assume HP regen works like MP (until a warrior's Improved HP Recovery can be tested); give
the regen checks "stickiness" like the map check, reset only on a map / channel change; track MP
use, with every potion from MeowDB and the wild cards (cleric, MP Eater).
- vitals.Meter is now a clock: find a tick with a burst (strip-only looks at burst_fps, at most
  one period + pad; HP while fighting has no regen, so it gives up and lets a normal look catch
  one, then a short burst places it), predict anchor + k x 10 s, check +-pad_s around a
  predicted tick: every tick at first, +1 tick per `step` on-time checks, up to max_ticks
  (helper_settings regen_check; Settings > General "HP/MP regen check"). An 'away' fact (screen
  dark = portal or channel change) resets it; 2 missed checks in a row = find again. A tick a
  normal look happens to see only confirms the clock (it used to push the next check away
  forever: 2 checks in 2 min instead of ~8).
- WGC delivers frames at the slider rate; a burst calls capture.boost() (live_fps), which makes
  wgc.py start a new capture session at 10/s: its first frame takes ~0.5 s, so the boost starts
  0.8 s before a check window. Tracker loop: burst_looks() between quick looks
  (ScreenSource.quick_vitals = HP/MP strip only).
- potions_classic.json (dev/fetch_potions.py, MeowDB item API /msclassic/api/items?type=Use):
  52 Use items with hpRecover / mpRecover / hpRecoverPct / mpRecoverPct (plain hp/mp keys are
  scroll stat boosts). Red Potion +100, Blue +200, Elixir 35%, Power Elixir 100%, Mana Elixir 50%.
- **Item bonus is real**: the +228 MP at 21:10:35 = Blue Potion 200 x 1.14 (Improved MP Recovery
  Lv 5: "increases MP recovery from items by 14%"). Red Potion stayed +100 (no HP bonus for mages).
  Whether the bonus applies to % items (Elixirs) isn't measured: both are accepted.
- Magic Claw costs 17 now (Lv 17) while characters.json still says Lv 9 (Skill window last read
  2026-10-23): casts may cost any level from the known one up; 3 casts at one cost = that level
  (levels_seen), used for casts left.
- mpuse.Usage scores every explanation (fewest events; clock agrees; known levels; one cast per
  0.6 s of window; shop items before event items: "Orange Mushroom Invasion" also heals 100).
  Replays: grinding 2 min (10/s): 43 Magic Claw, 4 Red, 1 Blue, 12 ticks, 0 unexplained; same at
  the live schedule (2/s + bursts): identical counts, next tick median 0.09 s, 90% 0.11 s, 5/5
  checks on time, 175 looks/min (vs 240 plain 2/s; at 1/s: 122 vs 120; the opening 10 s burst
  is most of it). The -7 window (cast -17 + tick +10) is split right.
- Live test (dev/regen_live.py, 2 min, own capture): clock matched the 10/s truth; HP +10 tick
  seen once off-combat (HP does tick). Output: live.json "usage", history/usage/<date>.csv.

## 2026-10-24 22:00 · Sitting / chairs: bigger ticks, own beat per meter

Live log 21:42-21:48 (user sitting on a setup chair, "+30 HP / 10 s while sitting"): MP ticks +13
(+10 standing/moving), HP +50 every 10 s on its own phase (~4.5 s off the MP ticks), then +22 to
full. mpuse had called them "unexplained" (only known tick sizes were tried) and "Red Snail
Invasion" (an event item that also heals 50; and any rise to full matched the smallest item big
enough). Now: any tick-sized rise, and a rise to full no bigger than a tick, may be the tick;
event / quest items cost +0.5 and never match a rise to full. Eggs (+60 HP) were matched right.
Recording 21:51 (10/s, sitting then fighting): HP +50, +22 to full = regen; 5 hits; 0 unexplained.
Chair HP isn't separated from base HP regen (one tick per beat, +50 here; +50 standing in town on
2026-10-23 too, so the chair's share isn't measured yet).

## 2026-10-24 22:01 · Chair test: sitting starts the HP beat

User lowered HP, then sat ~2 s after "go" (recording 10/s from 21:58:56.7, HP 144/472, MP full).
No HP change until the first +50 at 21:59:11.04 (~14 s after the recording started, so ~10-12 s
after sitting), then +50 every 9.88-10.08 s (phase 1.0 +-0.1 the whole time), +28 to full at
22:00:11. So the chair's ticks start on their own ~10 s after you sit (user's guess confirmed as
far as the timing of the "go" allows), and standing gave nothing in those first seconds. HP +50
while sitting = the whole tick (chair +30 + something; not split). mpuse: an off-clock tick-sized
rise now costs 1.2 (was 3.0), so a new sitting beat beats a same-sized event item, while an
exact shop potion still wins. All 5 recordings replay unchanged; this one = 7 regen ticks.

## 2026-10-24 22:55 · Level-up animation timing (levelup_shot.py)

Burst at 4 frames/s during Lv 18 -> 19 (dev/fixtures/burst/20261024_223406, local; picks in
dev/fixtures/levelup/). Times from the EXP bar reset (Lv 19, 0.00%, HP/MP refilled; between
frames 36.63 and 36.88): light pillars +0.6 s, "LEVEL UP" fades in +1.4 s, fully drawn +1.6 to
+2.1 s, faded by +2.6 s. User: it's a fixed animation, so take the picture at a fixed time, no
reading of the text; damage numbers / mobs in the shot are part of the moment.
Built: the quick look (~1/s) and readings feed the EXP % to levelup_shot.Timer; a drop >= 40
points = level-up, reset placed halfway between the two reads, shot at reset + 1.75 s (target 1.9
minus the wait for the next frame), capture boosted to 5/s until then; the tracker loop wakes for
it (source.wake_at). The diary level-up entry uses that frame. Replay at 8 quick-look phases:
6/8 fully drawn, 2/8 half faded in/out (the 1 s uncertainty of when the reset happened).
Found: the diary close-up (portrait.py) fails on this map at 1366x768: the bright grey sky fills
the >150 mask, every spot scores ~0.5, and the template (144x17) is from a bigger window (tag is
~80 px here). Not fixed. The JPEG burst frames also don't read the EXP brackets with glyphs (test
harness used the % digits).

## 2026-10-24 23:40 · Close-up without setup: status-bar name = name-tag template (portrait.py)

The tag under the character and the status-bar name are the same pixel font at the same size
(79x11 at 1366x768), so the status bar gives the template every frame; the setup step that boxed
the tag by hand is gone (its template was cut at another window size: 144x17, and the old
bright-only score was fooled by bright maps, ~0.5 everywhere on Caution Falling Down).
Graphics settings recorded by the player (dev/fixtures/settings_runs/, local): Resolution 800x600,
1024x768, 1280x720, 1366x768, 1920x1080, each also "(Full Screen)"; UI Size Ideal Ratio, Default
Ratio, Default Ratio (Filter Applied), an Ideal variant, User Setting. The world is drawn at
window height / 768 (1920x1080: 1.406); the HUD scales with it under Ideal Ratio (centered) and
stays native under Default Ratio. Status-bar name = window bottom-center + (-315, -16) x HUD scale.
Score: 0.5 x (letter px white, min RGB >= 200) + 0.5 x (gap px dark, max RGB <= 110). Yours
0.85-1.0 (1366, 1920 Ideal), ~0.77 at 1920 Default with our resized template, ~0.88 once the real
tag is learned; wrong spots <= 0.69. SURE 0.75, PARTIAL 0.66 near the last sure spot.
Hooks tested on 3 recordings (~850 looks): tag 690, partial 9 (2 pointed at an options window
over you), look 4 (correct), none where hidden (shop, NPC chat, key-binding window, map load).
"Last spot" was dropped: wrong after map changes / behind windows. Cost: local find ~20 ms, full
search ~100 ms (1366) / ~190 ms (1920) with FFT sizes padded to 2-3-5 numbers; tracking on
readings every 10 s (backing off to 120 s after misses) = 0.3-0.4% of a core.

## 2026-10-24 23:59 · Close-up camera tests (0.6.94, 1920x1080 Default Ratio): grey NCC

Player's 12 camera-button shots at 1920x1080 + Default Ratio (HUD native, world 1.406x): only 3
close-ups. The resized binary template scored 0.54-0.74 on visible, clean tags: scaled-up letters
come out dimmer (median min-RGB 177 on a clean tag, under the 200 "white" cut) and the see-through
backing is lighter over bright signs. Switched to normalized cross-correlation of the grey
(min RGB) pattern, template = bar name + 1 px border, resized for Default Ratio: yours 0.79-0.98
(0.60 under grass), best wrong spot 0.57; 1366 recording yours median 0.98 / hidden max 0.53;
1920 Ideal 0.96 / 0.44. SURE 0.70, PARTIAL 0.58, exact tag learned from matches >= 0.80.
Camera shots now 10/12 (misses: grass over the tag, one unknown). Trade-off: a tag mostly covered
by a speech bubble / shop sign (3 of ~330 recorded frames) no longer matches; the old binary
score tolerated that. Cost unchanged: tracking 0.5-0.6% of a core on these busy recordings.
Also seen: a camera click as the game left the screen got no picture (poll stops when not
playing, so the +0.5 s fresh-frame shot never came due).

## 2026-10-25 00:05 · Camera tests confirmed by the player

Of the 6 shots I wasn't sure of (dev/fixtures/review/unsure.png): #1 no match = correct (behind
the inventory window); #3 market crowd (tag also behind a rope), #4 tags touching, #5 see-through
tag artifacts = all correctly found; #2 missed: map decorations (grass) drawn over the tag
(roadmap G). #6: a camera click as the game left the screen got its picture 2 min later, at
another map -> delayed pictures (and the level-up picture) more than 5 s overdue are now dropped
(screen.py STALE_SHOT_S).

## 2026-10-25 00:40 · Training costs (training.py, training_ui.py): EXP comes split on crowded maps

User wants the cost of training on a map (cast cost, potions used, MP per kill, potions to level;
mesos income later). EXP is now read on every quick look (screen.py `_quick_exp`, glyphs only,
~1 ms) and map changes are sent as 'map' facts. Recording (dev/vitals_log.py with EXP, 3 min, 4/s,
Caution Falling Down, Lv 19, no party): 162 EXP rises, mostly 7, 4, 2, 5, 8, 3, 18, many with no
cast near them; the map's mobs give 15 / 23 / 35 (Orange Mushroom, Octopus, Blue Mushroom). Only
145 of 1,219 EXP came as whole kills (+35, +15). So on a busy map EXP is shared by damage with
other players (inferred, not confirmed by the player), and kills can't be counted from rises.
Training therefore reports "mobs' worth" = EXP / the map's average mob EXP (weighted by spawn
count) plus whole kills seen, and per 1,000 EXP costs (honest on crowded maps). Replay: ~53 mobs'
worth, Magic Claw 20 MP = 19.3 mesos (Blue Potion 220 mesos / 228 MP with the +14% bonus),
35 MP + 27 mesos per mob, 1,526 MP / 1k EXP; to level: 86 Blue (96 without regen) + 51 Red,
~21.5k mesos. Shop prices: potions_classic.json "shop" (MeowDB /msclassic/npc-shops, = 10x the
item "price"; Elixirs / event items aren't sold). MP -20 per Magic Claw now (Lv 20).

## 2026-10-25 01:55 · Map entry restarts the regen timer; per-character regen numbers

Portal test (user, 10/s, MP and HP below max, no casts/potions, 3 round trips): the first MP tick
came 9.88 / 9.98 / 9.78 s after the screen came back from the loading fade (dark 0.61-0.76 s), HP
+10 (natural, standing, max 488) ~0.2 s after it; two portals 2-4 s apart: the timer counts from
the last one. So Meter.reset_clock(t_dark) + the first look with numbers places the clock at once
(rough, burst at +10 s makes it exact). Replay at 1-2 looks/s: first tick predicted within
0.07-0.23 s of the real one after each entry. Burst looks report the fade too (strip brightness
< 20; normal strip ~83).
User rule (2026-10-25): stats and skill levels live per character and drive the regen math;
polling only reconciles. regen.py: MP tick = floor(max MP x % in Improved MP Recovery's text) +
an extra: +3 moving, +6 standing/sitting (606: 9/12, 709: 10/13, 733: 10/13, all measured). HP:
sizes learned per character (3 sightings; +50 chair, +10 standing natural). Stored in
characters.json "regen" (mp_extra, hp_sizes, clock {mp/hp anchor, saved t}); a restart within
90 s picks the clock up again. Skill levels learned from cast costs are written into the record
(Profile.learn_skill_level, "how": "from what its casts cost"). Party: the small EXP pieces in the
00:40 recording were party shares (user), not other players.

## 2026-10-25 02:40 · EXP estimate workshop: replays of real stays (sessions/*/exp_log.csv)

Same map, different visits: Caution Falling Down 4,200-19,200 EXP/hr "since entry" (crowd,
channel, breaks), so past visits are a hint, not a prior. The 46-min stay (20261024_203731) said
9,290/hr since entry but had a 22-min no-EXP break (min 7-29): active-only rate 17,171/hr.
Active-only (a 2+ min stretch without EXP doesn't count) estimates vs each stay's final:
1 min unreliable (-97..-2%), 3 min -71..+5%, 5 min -19..+20%, 10-15 min within ~15%.
These logs are readings (5-30 s apart); the quick look's 1/s EXP facts would settle faster.
Draft model (not built): clock = active time on the map from the first gain, paused by 2+ min
without EXP; show from ~1 active min with a band; quest / unexplained lumps and town gains out;
visit average = the settled number, last-10-min as a trend; past visits shown apart.

## 2026-10-25 02:25 · Built: EXP/hr model (exprate.py) and diary viewer changes

EXP/hr (exprate.ExpRate, one per training visit): the clock starts at the first EXP gain on the
map and a gap of 120 s+ without EXP is a break (not counted); first number after 3 gains (and
over at least 30 s), "~" until 5 active min; party share = unexplained rises while partied, shown
apart; lumps (EXP in a town map from maps_classic.json "town", or a rise no mob mix explains that
is > 4x the map's biggest mob) go to exp_quests, out of every rate. to_level: minutes (active
rate) + minutes_with_breaks. The tracker's exp_per_hour_recent / ETA use the visit rate when
there is one (Tracker.rate_source = live.Live.exp_rate), else the old 10-min window; status.json
+ exp_per_hour_party, exp_rate_early; helper shows "~1,621 (+420)" under "EXP / hr (party)".
Replays of 4 real stays: within ~±15% by 3-5 active min; 46-min stay 19,536/hr (old 9,290).
train1.csv (1/s looks, other session): 145 gains / 3 min = 24,783/hr, no false lumps.
Diary: delete for good (history/diary_deleted.json filters entries(); pictures removed; timeline
untouched; confirm text names the entry, picture count, "can't be undone"), lightbox Close-up |
Full tabs (F), Day N nav (‹ › picker Today, #day-N), Vertical | Horizontal Story. Tested on a
scratch copy in the browser pane (port 8799).

## 2026-10-25 03:40 · Monster album (album.py)

- MeowDB monster pages `/msclassic/monsters/<id>` (ids = map_mobs.json ids) embed
  `"monsterId":N,"level":L,"isBoss":true|false` and an og:image subtitle `HP 7,420 · EXP 148`; the
  drop table is "OSMLIB Reference Drop Rates" (rows: item-db link, icon key, name, rate). 32 of 131
  have no table. Sprites at `/msclassic/monsters/sprites/mob_<id>.png` (all 116 album mobs fetched
  fine). isBoss marks exactly Mano, King Slime, Stumpy, Mushmom, Zombie Mushmom, Rotten Mushmom,
  Jr. Balrog.
- Ids 800000+ are party-quest / job-test copies ("(alt)", a Lv 30 Zombie Mushroom on "Ant Tunnel For
  Bowman"); 90-93 are Lv 200 0-EXP "Super" dummies. Left out of the album (King Slime 800003 stays).
- First-kill matching: one rise = exactly one album mob's EXP on that map, and no 2-4 kill mix gives
  the same total, each mob used at most its spawn count there. 682 of 914 spawn entries identifiable;
  every album mob is catchable somewhere except Transforming (Dark) Jr. Yeti (0 EXP) and Werewolf
  (350 = 2 Hectors). Without the spawn-count cap Stumpy (276 = 4 Jr. Boogies) was never catchable.
- Photo: the camera keeps the character near the middle (lower than center in the fixture frame):
  a 720x720 middle square from the look before the rise showed the character fighting the mob, with
  another mob in frame (dev/svc_sim.py frames run, scratch copy).
- 2026-10-25 04:00, live (0.6.100-0.6.102): nothing was caught while training's visit.party was set
  (sticks for the whole map visit); now only a party member on your map *now* blocks it. Then 5 mobs
  were caught live, but not Octopus on Kerning City Construction Site: 23 = Blue Snail x2 + Orange
  Mushroom, a 3-kill mix. Album now allows mixes of at most 2 kills per rise and skips rises over
  > 2.5 s between looks (training.exp_gap). 730 of 914 spawn entries identifiable.

## 2026-10-30: Boss kills in a party (KPQ King Slime, 2 recordings): ASSUMPTION, to correct next KPQ
Recordings (local, gitignored): `dev/fixtures/burst/20261030_222236` (3 active) and `20261030_230602` (4 active): frames at
2 fps + `vitals.csv` + README. The bar EXP jump = base + party bonus, and the bottom-right message corner gives both exactly
(`You received EXP (+N)` white, then `Bonus EXP for PARTY (+M)` yellow; fades; mixes with `You have gained mesos (+N)` lines).
King Slime (800003, Lv 32, boss) is listed at **2,400 EXP** (`mobs_classic.json`, MeowDB).

| active | base | % of 2,400 | even share | base / even | party bonus | bonus / base |
|---|---|---|---|---|---|---|
| 3 | 755 | 31.5% | 800 | 94.4% | 227 | 30.1% |
| 4 | 579 | 24.1% | 600 | 96.5% | 232 | 40.1% |

Small summoned slimes: +3/+1 and +6/+1 (not in `map_mobs.json`; the boss table needs their summons).

**Working rules (assumed, not confirmed):**
1. Boss kill: on a boss map, a base gain above the most any regular mob on that map can give (full EXP + bonus; KPQ last stage:
   Curse Eye 94) = the boss died, and the party killed it. Count it as a party kill either way.
2. Not a leech: base >= 90% of (listed boss EXP / active party members). Lower bound only (more than an even share is never a leech).
3. Party bonus = base x 10% x active members (30% with 3, 40% with 4).

**Open:** both runs had the player fighting. If the split is by level (not damage), a leech also gets ~95% and rule 2 can't tell.
Next KPQ: record a run where the player stays in the boss map and does **not** attack the boss, then correct rules 2-3 here.
A lower-level member gets under an even share even when fighting (level-weighted split?), so 90% may need a level adjustment.
