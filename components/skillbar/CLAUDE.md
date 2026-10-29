# components/skillbar: HP/MP regen clock, what the HP/MP went on, casts left, the skill bar

A movable hotbar-style strip over the game: MP/HP regen wheels (tick size, seconds to the next tick),
and every skill with an MP cost with the casts your MP allows. The tracker side measures; the helper
draws. It owns the live.json sections `hp mp regen skills usage` and the `usage` service.

**Memory (local):** `.claude/handoff/components/skillbar.md`: read it before changing anything here.
**May import:** `hub`, `gamedata`, `base`. **Never:** another component, `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `SkillbarComponent`: builds the `Feed` on `service_start`, provides `usage`, publishes the 5 sections; `ui(shell)` for the bar |
| `feed.py` | `Feed`: one `Meter` per HP/MP, `mpuse.Usage`, the per-character `RegenModel`, `burst_looks` (regen clock bursts), the five `section_*` functions, `cast_extras`, `history/usage/<date>.csv` log |
| `meter.py` | `Meter` (regen clock: tick size, period, phase, sticky checks), `regen_settings()`, `REGEN_DEFAULTS`. Pure model, no pixels |
| `mpuse.py` | `Usage`: explains every HP/MP change (regen tick, casts by cost, potions x item bonus, Magic Guard, MP Eater, Heal, Recovery, level-up, death; unexplained = kind `other`). `python -m components.skillbar.mpuse LOG.csv [id=level ...]` replays a recording |
| `regen.py` | `RegenModel`: tick sizes from max MP + Improved MP Recovery text, learned extras per character (`characters.json "regen"`) |
| `skillbar_ui.py` | `SkillBar` (helper): draws at ~10 fps, reads live.json once a second; hidden when live.json is > 6 s old; `--demo` |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to (events):** `service_start` (build feed, provide, publish, `hub.on_idle(feed.burst_looks)`); `facts_begin` (`prof`, `now`: another character starts everything over); `fact` (`t, kind, value, prof`; kinds used: `vitals` {hp, mp: [cur, max]}, `away` (resets the regen clocks), `party` (row count -> usage party flag)).
- **Emits:** nothing.
- **Provides / asks (services):** provides `usage` (the current `mpuse.Usage`; replaced when another character plays). Asks `mp_price` and `price_of` (training) for the skills list.
- **Source hooks used:** `hub.source.boost(fps, seconds)`, `hub.source.quick_vitals(t)`, `hub.take_facts()` (in `burst_looks`). The HP/MP numbers come from the engine via the `vitals` fact (reader `engine/vitals.py`).
- **Settings:** `skillbar` {enabled, locked, x, y, hidden [skill ids], show: casts|mp|mesos}; `regen_check` {min_ticks, max_ticks, step, burst_fps, pad_s} (read by `meter.regen_settings()`; its Settings > General row lives in `hub/settings_ui.py`). DEFAULTS in `hub/helper_settings.py`.
- **Writes (data folder):** live.json sections, published in this order (the regen section sets a meter's `prior` that hp's state shows): `mp`, `skills`, `regen`, `hp`, `usage`. `hp`/`mp` = `{cur, max, tick, period, next, sure_s, ticks_seen, clock, check_every, checks, restarts}`; `regen` = `{mp|hp: {skill, level, says, icon}}`; `skills` = `[{id, name, level, max, mp, casts, mesos, mesos_with, items, meso_varies, icon}]`; `usage` = `{since, hp, mp, recent, levels_seen}`. Readers: `skillbar_ui` (hp, mp, regen, skills), training (via the `usage` service); `usage` is also in live.json for study. Also `history/usage/<date>.csv` (time, meter, kind, name, amount, skill, level) and the character's `regen` record in `history/characters.json` (through `prof.save_regen`, clock saved every 10 s, picked up again within 90 s).
- **Reads (shipped data):** `paths.asset("gamedata/skills_classic.json")` (names, `mp` per level, `levels` texts), `gamedata/potions_classic.json` (in mpuse). Data folder: `templates/skill_icons/<id>.png` (cut from the player's own Skill window; local only, never shipped).
- **Helper side (`ui(shell)`):** `PanelHost(shell, "skillbar", "skill bar", make_bar, dy=400)`; menu "Skill bar" + "Lock skill bar (click-through)"; Settings > General extra (order 10); right-click a slot: lock/hide, corner (casts / MP / mesos), remove / add back a skill; `on_settings_save` keeps x, y, locked, hidden, show.

## Test it
Offline (dev scripts set `MAPLE_COMPANION_OFFLINE=1`):
- `python dev/regen_sim.py LOG.csv [fps=2] [skill_id=level ...]` and `python -m components.skillbar.mpuse LOG.csv [id=level ...]`: both need a **vitals recording** from `python dev/vitals_log.py [seconds=120] [fps=4] [out.csv]` (live, next to the tracker; ask the player "ready?"). No recording is kept in the repo.
- `python -m components.skillbar.skillbar_ui --demo --at=X,Y`; `python dev/regen_live.py OUT_DIR [seconds] [fps]` (live, own capture, scratch output only).
- `python dev/svc_sim.py OUT_DIR frames`, `python dev/album_sim.py OUT_DIR`, `python dev/release_check.py` (the "fresh install reads HP/MP" check covers the engine reader).
Live (the player): bar shows over the game, MP "+N every 10 s" with the next-tick countdown, casts left drop with a cast, lock / drag / remove-a-skill keep after Save.

## Out of scope / don't read
`engine/vitals.py` internals (digit templates `glyphs_hud.json`), `hub/character.py` (Profile), training and party code.

## Rules that bite here
- Passive screen reading only; skill icons are Nexon art: local, never shipped or committed.
- Per-frame work goes in `perf.timed("name")` (burst looks do).
- Changed regen? Validate per level locally with `dev/vitals_log.py` against `skills_classic.json` before trusting the bar.
