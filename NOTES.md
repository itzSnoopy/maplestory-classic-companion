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
