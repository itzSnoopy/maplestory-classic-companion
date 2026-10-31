# album/: the monster album (first kills, seen mobs, /album.html)

A collection book of every Classic mob: "?" (unknown), grey (seen on its map, or a boss at most 15 levels above you),
caught (first kill with a photo). Catches come from EXP rises only one mob can explain. **Both processes**:
the tracker side keeps the state and takes the photo; the helper side adds the menu entry and the page.

**Memory (local):** `.claude/handoff/components/album.md`: read it before changing anything here.
**May import:** `base`, `hub`, `gamedata`. **Never:** another component (diary/training are reached by events and services), `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `AlbumComponent`: events, first-kill flow (photo, record, `first_kill`, journal), `ui(shell)` |
| `album.py` | Core: catalog (`mobs_classic.json` + `map_mobs.json`), `identify` (which mob an EXP rise means), `party_boss`; `MAX_KILLS`, `MAX_GAP_S`, `BOSS_GREY_LEVELS`), `Album` state per character, `render`, `open_album`, CLI |
| `album_html.py` | Builds `album.html` (`page(root, album, live)`, `build`): an open book (two pages of 3x3 slots, arrows, chapter ribbons, entry page per mob at `#mob=<id>`); `SHOWN_REGIONS` hides the other regions (data stays in the JSON); themes = one `[data-book="name"]` CSS variable block each (henesys, ellinia, plain) + the script's `THEMES` list; asks `mobicons.want` for missing pictures of shown mobs |
| `mobicons.py` | Downloads monster pictures once into `assets/mob_icons/` (0.5 s apart, `index.json`), behind `internet.allowed("mobs")` |

## Interfaces
- **Listens to (events):** `service_start`, `session_start`, `observation`, `map_enter` (`map`), `facts` (`prof`),
  `exp_rise` (`t`, `map`, `d`, `party`, `gap`).
- **Emits:** `first_kill` (`mob`, `map`, `level`, `job`, `shots`, `caught`, `text`); the diary writes the beat.
  The journal line is written here (`hub.journal`).
- **Provides:** none. **Asks:** `seen_places` (diary; seeds each character once), `visit_map` (training; map for `wants_photo`).
- **Source hooks used:** `hub.album_shot(hub.shots_dir, stem, t)` (middle square from the look before the rise, then
  full screenshot + close-up), `hub.tell_source(album_watch=bool)` (the screen source keeps the middle square only
  while an uncaught mob may be on the map), `hub.state`, `hub.log`, `hub.journal`. Both are declared in
  `base/contract.py`'s `Source` (`album_watch = False`; `album_shot` defaults to a snapshot).
- **Settings:** none of its own; `internet.features.mobs` ("Monster pictures", `base/internet.py`).
- **Writes (data folder):** `history/album.json` (per character: seen, killed, level, seeded), `album.html`,
  `assets/mob_icons/<id>.png` + `index.json`, photos `screenshots/diary/<stem>_kill.jpg` (+ the usual full/close-up).
  Read by: the diary page server (serves `assets/mob_icons`, `assets/item_icons`, pictures), `diary_html` (`_kill.jpg`).
- **Reads (shipped):** `paths.asset("gamedata/mobs_classic.json")`, `paths.asset("gamedata/map_mobs.json")`; the CLI also
  reads `history/places.json` (written by the diary).
- **Helper side (`ui`):** `shell.add_page("/album.html", render_page)`, command `album`, menu "Open monster album"
  (slot `open`); the diary server hosts the page.

## Test it
Offline:
- `python dev/album_sim.py OUT_DIR` (kill matching over every spawn entry, EXP facts -> first kill -> diary beat,
  repeat/ambiguous/party cases, photo on a saved frame if fixtures exist, pages build).
- `python dev/svc_sim.py OUT frames` (a real catch from saved frames); `python dev/release_check.py`.
- CLI on a scratch root: `python -m components.album.album status|html|kill "Mob"|unkill "Mob"|icons|serve`.
Live (player): a new mob's first kill adds a diary beat and a photo with the mob next to the character; the menu
entry opens the page; `python -m base.perf report` step "album"; watch photo framing near map edges.

## Out of scope / don't read
EXP-rise detection and rates (`components/training/`), how frames are cropped (`engine/sources/screen.py`), diary
captions and server internals, `dev/fetch_mobs.py` (only when mob data must be refreshed).

## Rules that bite here
- Photos are Nexon imagery: local only. Pictures come from NiaMeowDB, only with the "mobs" toggle on, never shipped.
- Per-frame work in `perf.timed("album")`. Classic data only (MeowDB), no private-server wikis.
- Skip kills while another party member is on the map (EXP is shared); never auto-catch 0-EXP mobs (CLI only).
  One exception: `party_boss` catches a boss that is only fought in a party (King Slime) when the party rise
  is above any regular mob's max EXP x `PARTY_MAX_FACTOR` (1.6) and at most the boss EXP x 1.6; recorded with
  `how="party_boss"` (`Album.last_how` after `first_kill`). Assumption from NOTES.md "Boss kills in a party".
  **PQ bosses only** (`PQ_BOSS_IDS`, ids 800000-899999): open-world bosses (Mano, Stumpy, Mushmoms, crowds of
  players) have low ceilings that mixed kills can pass; they need their own rule (player: later).
- `HIDDEN_IDS` (tutorial-only mobs, e.g. Tutorial Jr. Sentinel) and `_hidden` keep mobs out of the album.
