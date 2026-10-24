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
