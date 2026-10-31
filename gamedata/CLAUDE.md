# gamedata/: what is true in MapleStory Classic

Reference data about the game (EXP table, maps, mobs, skills, potions) and the code that turns raw
screen readings into game facts (`translate.py`). Used by the tracker (hub), the components, and the
engine only through the injected translator.

**Memory (local):** `.claude/handoff/components/gamedata.md`: provenance, gaps, and the starting points for the stats / damage / EXP math.
**May import:** `base` only. The json files have no dependencies. **Never:** `engine`, `hub`, `components`, `launcher`.

**Classic values only.** Sources: MeowDB/NiaDB (meowdb.com/msclassic), classic.maplestorywiki.net,
OSMS Data Explorer, Henesys.GG, Metaroad. Never modern GMS or private-server wikis, never the parent
folder's v83 `reference/` data (values differ). Every number here is fan-run data, not official.

## Files
| File | Role |
|---|---|
| `translate.py` | The engine's translator: `map_name(text) -> (name, map_id)`, `job_name(text)`, `next_job_level(job)` (`NEXT_ADVANCE` {0:10, 1:30, 2:70, 3:120}, `JOBS`/`TIER`), `snap_skill(text) -> [candidates]` + `skills()`, `exp_table()` / `exp_needed(level)`, `skill_kind(id) -> {kind, sub, target, duration_s, until, name}` or None (from `skill_kinds_classic.json`) |
| `maplist.py` | Snap a minimap title to the 453 real map names (exact, look-alike fold, fuzzy >= 0.88 with the same roman suffix, unique prefix) |
| `formulas.py` | HP/MP and skill math, pure (stdlib only): `mp_pct`/`mp_flat`/`mp_part`/`mp_tick` (regen), `item_bonus_pct`/`potion_amount`, `skill_mp_cost(s)`, `buff_pct(record, level, stat)`/`buff_seconds`/`buffed_max` (Hyper Body: Max HP +N% for N s), `recovery_tick(text)` (Recovery: total / 6 every 5 s, measured 2026-10-30), `magic_guard_pct`/`magic_guard_split`/`magic_guard_mp_for_hp` (MP part = floor(hit x N%), measured), `magic_guard_reach(hp, mp, pct) -> (absorb = floor(MP x pct), ehp = HP + absorb)` (the bar, player definition), `magic_guard_limit` (true damage before death). `STATUS` {formula: (verified\|measured\|guess, source)} says how far each can be trusted |
| `parse.py` | `parse_map` (title clean-up), `parse_gain` + chat helpers (EXP-notice parsing, not used live yet). The HUD's EXP/level format is `engine/hudtext.py` |
| `game_refs.py` | Caption source -> Steam app id, fetches the small game icons for diary captions (local only; `python -m components.diary.diary icons`) |

## Data files (all shipped: listed in `packaging/build.py` DATA_FILES; read via `paths.asset("gamedata/<file>")`)
| File | Content | Source / refresh | Fetched |
|---|---|---|---|
| `exp_table_classic.json` | EXP needed per level 1-99 (`{"1": 15, ...}`, `_source`, `_note`) | MeowDB guide `/msclassic/guides/exp-table-level-1-to-100`, typed in by hand (no script) | 2026-10-23 |
| `maps_classic.json` | 453 maps `{id, region, street, name, town}` (`_count`) | NiaMeowDB `/msclassic/maps/all`, exported in the browser (no script) | 2026-10-23 |
| `map_mobs.json` | per map id: `{name, street, region, mobs[{id, name, level, exp, count, respawn}]}` | `python dev/fetch_map_mobs.py [--all]` (1.5 s/page, resumable) | 2026-10-23 |
| `mobs_classic.json` | 131 mobs `{name, level, hp, exp, boss, maps[], drops[]}` (drops = OSMLib reference rates) | `python dev/fetch_mobs.py [--all]` | 2026-10-25 |
| `skills_classic.json` | 163 skills by id `{name, class, job, max, levels[] (per-level text), mp[], cd, time}` | `python dev/fetch_skills.py` (class pages; per-level text, mp, time, cd parsed from it) | 2026-10-23 |
| `skill_kinds_classic.json` | per skill id `{kind: attack\|buff\|passive\|other, sub (proc, booster, charge, debuff, summon, heal, mobility, utility), target (self\|party\|mobs), duration_s[] per level, until ("toggle": on until it ends, e.g. Magic Guard), name}` | `python dev/make_skill_kinds.py` (text rule + hand-checked `OVERRIDES`; edit OVERRIDES, never the json; `--check` compares) | 2026-10-29 |
| `potions_classic.json` | 52 Use items `{name, subtype, hp, mp, hp_pct, mp_pct, price, shop, cooldown}`; `shop` = cheapest NPC price | `python dev/fetch_potions.py [--keys]` (item API + npc-shops) | 2026-10-25 |
| `pq_classic.json` | Party quests for components/kpq: `{key, name, first, stages [{map, label}], bonus, other []}` (names must exist in `maps_classic.json`: release_check) | typed in by hand from `maps_classic.json` | 2026-10-31 |
| `important_maps.json` | maps worth a diary "first visit" (`towns`, `landmarks` with `why`) | curated by hand (player's copy in DATA wins) | 2026-10-23 |
| `game_refs.json` | caption source -> Steam app id | curated by hand (player's copy in DATA wins) | 2026-10-23 |

Not here (player-measured, hub-owned, in the data folder): `history/exp_table.json` (EXP needed measured from the bar),
`history/characters.json` (stats, skills, regen per character), `history/map_stats.json`, `history/maps.json`.

## Interfaces
- **Called by:** engine (translator, injected by `launcher/compose.py`), `hub/tracker.py` and `hub/history.py` (read `exp_table_classic.json` directly as `REF_TABLE`), `hub/character.py`, components (killers, album, training, skillbar `feed.py`/`mpuse.py`/`regen.py`/`maxwatch.py` (the math: `formulas.py`), diary).
- **Emits/listens/settings:** none. Pure data and functions.

## Test it
- `python dev/release_check.py` ("build DATA_FILES exist", "every tracked code-side .json is shipped or known-local", sims).
- Snapping: `python -c "from gamedata import translate as t; print(t.map_name('Hunting Ground Middle of the Forestl'))"`; skills: `t.snap_skill('Improved WP Reco...')`.
- After a refresh: `python dev/release_check.py`, `python dev/album_sim.py OUT`, `python -m components.killers.killers audit`.

## Out of scope / don't read
The big json files (`map_mobs.json` 185 KB, `mobs_classic.json` 377 KB, `skills_classic.json` 328 KB): query them with a few lines of Python, don't open them.

## Rules that bite here
- New shipped json: add to `DATA_FILES` or release_check fails; fetch scripts are slow on purpose (1.5 s/page, be polite to MeowDB).
- Mob/item pictures are Nexon art: downloaded by each player's app (`components/album/mobicons.py`, `components/training/itemicons.py`, internet features `mobs` / `icons`), never shipped.
- Where the bar and the table disagree the bar wins (`hub/tracker.py Tracker.need`); log disagreements in NOTES.md.
