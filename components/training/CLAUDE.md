# components/training: EXP/hr per map visit, cost of training (MP, potions, mesos), the cost panel

Turns the EXP bar and the HP/MP accounting into "what does this map give and cost me": kills (as
mobs' worth of EXP), EXP/hr on the active clock, MP per cast, potions used with shop or own prices,
per kill / per 1,000 EXP / per hour, and potions needed to level. Also emits `exp_rise` for the album.
Tracker process computes; the helper draws the optional panel over the game (off by default).

**Memory (local):** `.claude/handoff/components/training.md`: read it before changing anything here.
**May import:** `hub` (incl. `hub.exprate`), `gamedata`, `base`. **Never:** another component, `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `TrainingComponent`: owns `Training`, wires `on_rise` -> `exp_rise`, provides 4 services, publishes `training`; `ui(shell)` for the panel |
| `training.py` | `Training` / `Visit`: map visits, EXP rise -> kills (`_split` by the map's mobs, `_lump` = quest/town), diff of `mpuse.Usage` totals per visit, prices, `summary()`, all-time save to `history/training.json` |
| `training_ui.py` | `TrainingPanel` (helper): rows from live.json `training`; right-click "Price MP/HP with", lock, hide; `--demo` |
| `itemicons.py` | Potion pictures for the panel, downloaded once from NiaMeowDB (`internet.allowed("icons")`) into `<data>/assets/item_icons/` (+ `index.json`) |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to (events):** `service_start`; `facts_begin` (`prof`, `now`: another character -> close the visit, start over); `fact` (`map` name -> new visit; `exp` {cur, pct} -> kills; `party` rows > 1 -> `visit.party`); `progress` (`level, cur, need`).
- **Emits:** `exp_rise` (`t, map, d, party, gap`): `party` = `hub.ask("party_on_map", False)` *now*, `gap` = seconds since the previous EXP look. Consumer: album.
- **Provides / asks (services):** provides `exp_rate` (EXP/hr summary of the visit or None; tracker ETA), `visit_map`, `mp_price` -> (potion, mesos per MP, MP per potion), `price_of` -> fn(item) -> mesos|None. Asks `usage` (skillbar) and `party_on_map` (party). **Without a `usage` service (no skillbar) it provides and publishes nothing.**
- **Source hooks used:** none (works on facts and events only).
- **Settings:** `trainingpanel` {enabled (default False), locked, x, y} in DEFAULTS (`hub/helper_settings.py`); the panel also stores `mp_item`, `hp_item` (potion used to price MP/HP, None = the one you use most) and `prices` {item: mesos} (own prices) in the same slice; `training.prefs()` re-reads them by file time. Internet feature `icons` ("Item pictures") gates `itemicons`.
- **Writes (data folder):** live.json section `training` (= `Training.summary(now)`: `map, since, minutes, kills, kills_seen, kills_total, party, mob_exp_avg, exp, exp_whole_kills, exp_shared, exp_quests, rate, casts, potions, mp, hp, mesos, per_hour, mp_item, hp_item, cast_cost, per_kill, per_1k_exp, to_level`) -> `training_ui`; `history/training.json` (`{map: {visits, minutes, kills, exp, casts, potions, mp, hp, mesos}}` plus `_open` = visit under way, saved every 60 s); `assets/item_icons/*.png` + `index.json`.
- **Reads (shipped data):** `gamedata/potions_classic.json` (`items`, shop prices), `gamedata/map_mobs.json` (`maps`: mobs + EXP per map), `gamedata/maps_classic.json` (`town` flag). Engine-fed facts come from `engine/sources/screen.py` (`map`, `exp`).
- **Helper side (`ui(shell)`):** `PanelHost(shell, "trainingpanel", "training panel", make_panel, dy=760)`; menu "Training cost panel" + "Lock training panel (click-through)"; Settings > General extra (order 20: checkbox); `on_settings_save` keeps x, y, locked, mp_item, hp_item, prices.

## Test it
Offline (dev scripts set `MAPLE_COMPANION_OFFLINE=1`, which also keeps `itemicons` from downloading):
- `python dev/training_sim.py LOG.csv "Map Name" [fps=2] [need=EXP_NEEDED] [skill_id=level ...]`: replays a `dev/vitals_log.py` recording (with EXP columns) through `mpuse.Usage` + `Training`, prints the summary, writes nothing. **Needs a vitals recording** (`python dev/vitals_log.py [seconds] [fps] out.csv`, live; ask the player "ready?").
- `python -m components.training.training_ui --demo --at=X,Y` (fake data).
- `python dev/album_sim.py OUT_DIR` (EXP facts -> `hub.feed_facts` -> this component -> `exp_rise` -> album), `python dev/svc_sim.py OUT_DIR frames`, `python dev/release_check.py`.
Live: panel off by default; turn it on in Settings > General, stand on a map, kill a few mobs: kills / EXP/hr appear after 3 gains ("~" for the first 5 active minutes), potions and prices after using some, panel keeps position / lock, "Price with" menu works.

## Out of scope / don't read
`hub/exprate.py` internals (the active-time EXP/hr model; read only its docstring), `components/skillbar/mpuse.py` (it explains HP/MP changes), the album.

## Rules that bite here
- Item pictures are Nexon art: downloaded into the player's data folder only, never committed or shipped (`assets/` is git-ignored).
- Online use only through `internet.allowed("icons")`.
- Costs only for now: meso income is not read (player, 2026-10-24).
