# gamedata/: what is true in MapleStory Classic

**May import:** `base` only. The json files have no dependencies. Used by hub and components
directly, and by the engine only through injection (`translate.py`, handed in by `launcher/compose.py`).

**Classic values only.** Sources: MeowDB/NiaDB (meowdb.com/msclassic), classic.maplestorywiki.net,
OSMS Data Explorer, Henesys.GG, Metaroad. Never modern GMS or private-server wikis, never the
parent folder's v83 `reference/` data. Refresh scripts: `dev/fetch_*.py`.

| File | Role |
|---|---|
| `translate.py` | Raw reading -> game fact: `map_name(text)` (maplist snap, else `parse_map`), `job_name` + `next_job_level`, `snap_skill` + `skills()`, `exp_table()` / `exp_needed(level)` |
| `maplist.py` / `maps_classic.json` | Snap map reads to the 453 real Classic map names |
| `parse.py` | `parse_map` (title clean-up), `parse_gain` and chat helpers (the HUD's EXP/level format is `engine/hudtext.py`) |
| `game_refs.py` / `game_refs.json` | Caption source -> Steam app id; fetches the small game icons for the diary's caption tags (`python -m components.diary.diary icons`) |
| `exp_table_classic.json` | EXP per level (MeowDB; matches the bar) |
| `skills_classic.json`, `potions_classic.json`, `mobs_classic.json`, `map_mobs.json`, `important_maps.json` | Skills (per-level MP/time/cd), potions, mobs (album), mobs per map (killers), maps worth a diary beat |

Every shipped json read through `paths.asset("gamedata/<file>")` must be in `packaging/build.py` DATA_FILES
(release_check fails otherwise).
