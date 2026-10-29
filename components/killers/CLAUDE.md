# killers/: who killed you (a guess from the map's mob list)

The game never names the killer. When the tracker detects a death it asks `death_guess`; this
component fills the answer from the map's mob list and a few hand-written rules. It names a killer
only when it is sure. Runs in the **tracker** process only (no helper side).

**Memory (local):** `.claude/handoff/components/killers.md`: read it before changing anything here.
**May import:** `base`, `hub`, `gamedata` (data only). **Never:** another component, `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `KillersComponent`: subscribes `death_guess`, calls `killers.attribute` + `killers.label`, never raises |
| `killers.py` | The rules (`attribute(map, level)` -> `{killer, how, sure, suspects, why}`; `label(a)`) + CLI. Constants `STRONGER_BY`, `WEAK_BELOW`, `BOSS_RESPAWN_S`, `RARE_COUNT`, `RARE_RESPAWN_S` |
| `killers.json` | Curated per-map rules `{map, killer, probably?, ignore?, why}` (boss maps). Shipped; the player's copy in the data folder wins |

Rule order in `attribute`: curated -> `only_mob` (one species) -> `strongest` (one regular species, the
only one within `WEAK_BELOW` levels of the player, or `STRONGER_BY` above the rest) -> none (suspects only).
Bosses/rare wanderers (`_rare`) are never guessed. Same-name maps are merged (the tracker only reads the name).

## Interfaces
- **Listens to (events):** `death_guess` (`map`, `level`, `killer`): updates the `killer` dict in place with
  `killer`, `how`, `sure`, `suspects`, `why`, plus `label` (name when sure, else `None`).
- **Emits:** nothing. Consumers read the dict from the `death` event (`killer`): the diary (`label`, `how`,
  `killer`, `suspects`) and the journal (`label`).
- **Provides / asks (services):** none.
- **Source hooks used:** none.
- **Settings:** none.
- **Writes (player data folder):** nothing.
- **Reads:** `map_mobs.json` and `killers.json`, each first from the data folder (`DATA / name`), else
  `paths.asset("gamedata/map_mobs.json")` / `paths.asset("components/killers/killers.json")`.
- **Helper side (`ui(shell)`):** none.

## Test it
Offline (all read-only):
- `python -m components.killers.killers audit`: maps where a death would get no killer (+ boss maps not curated);
  this is also release_check's `killers audit runs` check.
- `python -m components.killers.killers "<map name>" [level]`: what a death there would say.
- `python dev/release_check.py` (compiles, layer check, `map_mobs.json` / `killers.json` shipped in `packaging/build.py`).
- Whole path (death event -> diary entry with killer fields): `python dev/svc_sim.py OUT csv PATH.csv` with a
  CSV containing a >= Lv 10 EXP drop; the diary entry carries `killed_by` / `killer_guess`.
Live (player): after a real death on a single-species or curated map, the journal line and the diary caption
name the mob; on a mixed map neither names one and the diary entry still has `suspects`.

## Out of scope / don't read
Death detection itself (`hub/tracker.py`, `engine/deathbox.py`), diary captions/pages, the screen engine,
`dev/fetch_map_mobs.py` (only if map data must be refetched). Ask for `.claude/handoff/05-game-ui-facts.md` only
for the death dialog.

## Rules that bite here
- Guesses ("probably") and suspects are stored in the timeline but **never shown by name**; `label()` is the one
  place that decides (player decision 2026-10-23).
- Classic data only (MeowDB/NiaDB spawn lists). Add a handler key `**_` for new payload keys.
- Keep the handler cheap and exception-safe: a failed guess must not break death tracking.
