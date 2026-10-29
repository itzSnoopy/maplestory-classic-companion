# diary/: the character's diary (timeline, Story page, local page server)

Records milestones (level-ups, deaths, job advancements, first visits, first kills, notes, random moments),
each with a screenshot and a character close-up, and shows them as `diary.md` and the `diary.html` Story view
(captions, Edit/Generate). **Both processes**: the tracker side writes entries from bus events; the helper side
has the menu entry, camera button, pop-up card, Settings > Diary, and `diary_server.py`, which also hosts
every other component's pages (`hub/pages.py`, e.g. `/album.html`).

**Memory (local):** `.claude/handoff/components/diary.md`: read it before changing anything here.
**May import:** `base`, `hub`, `gamedata`. **Never:** another component (killers/album reach it through events), `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `DiaryComponent` (events -> `diary.add`, requests loop, `ui(shell)`) + `PageHost` (the host `hub/pages.py` uses) |
| `diary.py` | Core: `add`, `entries`, `delete_entry`, `render` (diary.md + html), text builders, captions (`caption_lines`, `set_caption`, `generate_caption`, `sources_off`, `nickname`), places (`mark_place`, `seed_places`, `with_context`), `moment_due`, requests (`request_note`, `request_moment`, `take_requests`), `open_diary`, `annotate_last_death`, `backfill`, CLI |
| `diary_html.py` | Builds `diary.html` (one self-contained page; data embedded as JSON); `page(root, live)`, `build` |
| `diary_server.py` | Localhost server `127.0.0.1:8737` (Host check, `X-Diary` header on POSTs): diary page, registered pages, pictures; `/api/caption`, `/api/generate`, `/api/sources`, `/api/delete`; `start_thread`, `running`, `open_page`, `serve` |
| `diary_popup.py` | `DiaryPopup` (card on new entries, watches timeline.jsonl) + `page(win, f, popup)` = Settings > Diary |
| `captions.json` | Story caption dictionary per kind (shipped; the player's copy in the data folder wins) |

## Interfaces
- **Listens to (events):** `service_start` (`maps`), `loop`, `poll`, `observation`, `levelup` (`level_reached`, `map`,
  `active_seconds_in_prev_level`, `job`), `death` (`level`, `map`, `lost`, `pct_lost`, `box`, `killer`, `job`),
  `job_change` (`old`, `new`, `level`, `map`), `map_enter` (`map`, `level`, `job`), `moment` (`level`, `map`, `job`,
  `active_total`), `first_kill` (`mob`, `map`, `level`, `job`, `shots`, `caught`, `text`), `session_end`.
- **Emits:** nothing.
- **Provides (services):** `seen_places` (the album seeds from it). **Asks:** none.
- **Source hooks used:** `hub.snapshot(diary.SHOTS, stem, delays)` (full frame + close-up; delayed shots on a later
  reading), `hub.wake()`, `hub.log`, `hub.state`, `hub.root`.
- **Settings:** `diary` slice: `moments`, `moment_min`, `moment_max`, `popup`, `popup_seconds`, `open_via`
  (`localhost|file`). DEFAULTS in `hub/helper_settings.py`; the tracker re-reads them each check (`moment_settings`).
- **Writes (data folder):** `history/timeline.jsonl` (the record), `diary.md`, `diary.html`, `history/places.json`,
  `history/captions_custom.json` (own/rolled captions, `_nickname`, `_sources_off`), `history/diary_deleted.json`,
  `diary_requests.jsonl` (helper -> tracker queue: notes, camera), `screenshots/diary/<stem>.jpg|_char.png|_kill.jpg`,
  `game_icons/<appid>.jpg` (local only). Read by: the album CLI (`places.json`), the popup (`timeline.jsonl`), `diary_html`.
- **Reads:** `history/levels.csv`, `history/map_stats.json`, `digest.json`, `helper_settings.json` (name, diary),
  `paths.asset("gamedata/maps_classic.json" | "gamedata/important_maps.json" | "gamedata/game_refs.json")`,
  `paths.asset("components/diary/captions.json")` (data-folder copies of captions and important maps win).
- **Helper side (`ui`):** `pages.set_host(PageHost())`, `shell.set_camera(diary.request_moment)`, command `diary`,
  menu "Open diary" (slot `open`), Settings page "Diary" (after "Discord"), `on_refresh` -> pop-up check.

## Test it
Offline (never the real diary; scratch root via `diary.set_root`):
- `python dev/svc_sim.py OUT frames` (level-up + first visit with close-ups in `OUT/diary.md`);
  `python dev/svc_sim.py OUT csv PATH.csv` (a Lv >= 10 EXP drop -> death entry).
- `python dev/album_sim.py OUT` covers the `first_kill` beat, boss captions and the diary.html build.
- `python dev/release_check.py` (compile, layers, data files shipped, privacy of the zip).
- CLI on a scratch root: `python -m components.diary.diary note|killed-by|caption|refs|nickname|render|html|serve|open|backfill|icons`.
- Viewer: copy history files + `screenshots/diary/` to a scratch dir, `diary.set_root(scratch)`, serve over http (the
  browser pane doesn't run scripts on `file://`).
Live (player): level-up, first visit, camera button, a note, pop-up card, Settings > Diary, "Open diary" opens the
editable page, Edit/Generate work, a death shows the right caption.

## Out of scope / don't read
How pictures are taken (`engine/sources/screen.py`, `engine/portrait.py`, `engine/levelup_shot.py`), death/level
detection (`hub/tracker.py`), the album's logic, killer rules, party/skillbar, `gamedata` contents. Caption
wording is the player's call: `captions.json` is curated (see memory).

## Rules that bite here
- Screenshots, close-ups and `game_icons/` (Steam capsule art) are Nexon/publisher imagery: **local only**, never
  commit, upload or ship (`release_check` refuses them). No player names in tracked files.
- The server listens on 127.0.0.1 only and serves pictures only; never loosen it.
- Captions: no long quotes or song lyrics; don't re-add lines the player removed.
- Handlers take `**_`; wrap entries so a failure never stops the tracker; no component imports.
