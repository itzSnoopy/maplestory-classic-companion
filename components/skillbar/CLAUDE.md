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
| `maxwatch.py` | `MaxWatch` (per hp/mp): HUD max vs the stored base; a max buff (`BUFF_SKILLS` = Hyper Body 1311005, levels from `skills_classic.json`) is not a new base; a lasting difference (5 same looks or right after a level-up) is: returned to the feed, stored by `Profile.learn_base_max` |
| `regen.py` | `RegenModel`: tick sizes (math in `gamedata/formulas.py`) from max MP + Improved MP Recovery text, learned extras per character (`characters.json "regen"`) |
| `skillbar_ui.py` | `SkillBar` (helper): draws at ~10 fps, reads live.json once a second; hidden when live.json is > 6 s old; `--demo` |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to (events):** `service_start` (build feed, provide, publish, `hub.on_idle(feed.burst_looks)`); `facts_begin` (`prof`, `now`: another character starts everything over); `fact` (`t, kind, value, prof`; kinds used: `vitals` {hp, mp: [cur, max]}, `buffs` {on: [skill ids]} (icons showing at the top right), `away` (resets the regen clocks), `party` (row count -> usage party flag)).
- **Emits:** nothing.
- **Provides / asks (services):** provides `usage` (the current `mpuse.Usage`; replaced when another character plays). Asks `mp_price` and `price_of` (training) for the skills list.
- **Source hooks used:** `hub.source.watch_buffs(ids)` (owned buff-kind skills with an icon file; re-sent when the list changes, checked at every live.json beat), `hub.source.boost(fps, seconds)`, `hub.source.quick_vitals(t)`, `hub.take_facts()` (in `burst_looks`). The HP/MP numbers come from the engine via the `vitals` fact (reader `engine/vitals.py`).
- **Settings:** `skillbar` {enabled, locked, x, y, hidden [skill ids], show: casts|mp|mesos, guard_show: ehp|absorb|last}; `regen_check` {min_ticks, max_ticks, step, burst_fps, pad_s} (read by `meter.regen_settings()`; its Settings > General row lives in `hub/settings_ui.py`). DEFAULTS in `hub/helper_settings.py`.
- **Writes (data folder):** live.json sections, published in this order (the regen section sets a meter's `prior` that hp's state shows): `mp`, `skills`, `regen`, `hp`, `usage`. `hp`/`mp` = `{cur, max, tick, period, next, sure_s, ticks_seen, clock, check_every, checks, restarts, ... , base_max (stored base), buff ({id, name, pct, ends} or null), predicted (bool: the tick shown is from math, no tick seen yet), predicted_from ("stored" before the first HUD read, "hud" after, null)}`; `regen` = `{mp|hp: {skill, level, says, icon}}`; `skills` = `[{id, name, level, max, mp, casts, mesos, mesos_with, items, meso_varies, icon, guard}]` (`guard` only on Magic Guard 2001000: `{pct, absorb, ehp, last, last_t}`; every entry also has `kind` (skill kinds file), `hp_cost`, `active` (buff icon showing: true/false; null = not watched or not seen yet), `cast_n` (casts seen: an attack's slot pulses for `PULSE_S` when it goes up, blue = MP cost, red = HP, both = two rings); its slot shows one of them as a caption under the slot instead of casts, ehp red, MP numbers blue; `skillbar.guard_show` ehp|absorb|last, right-click); `usage` = `{since, hp, mp, recent, levels_seen}`. Readers: `skillbar_ui` (hp, mp, regen, skills), training (via the `usage` service); `usage` is also in live.json for study. Also `live_casts.json` `{t, n: {skill id: casts}}`, written the moment a cast is booked; the bar checks it every 100 ms so the attack pulse comes ~0.2-0.4 s after the cast (live.json alone: up to ~2.4 s). After a cast, `burst_looks` keeps looking at HP/MP `COMBAT_FPS` (5) times a second for `COMBAT_S` (5 s) (`Feed.in_combat`). Also `history/usage/<date>.csv` (time, meter, kind, name, amount, skill, level) and the character's `regen` record in `history/characters.json` (through `prof.save_regen`, clock saved every 10 s, picked up again within 90 s).
- **Predict first (login):** `Feed.begin` runs `_predict` as soon as `prof.who` is set (Profile keeps `current` in characters.json, so it is the last-played character at once): `Meter.known` from stored `mp_max` + stored skill levels, before any HUD read; `_expect` then reconciles with the HUD max. `Profile.learn_base_max(kind, value, t, source)` stores a lasting HUD max; a Stat window read replaces it.
- **Math:** all of it lives in `gamedata/formulas.py` (see `STATUS` there for verified / measured / guess); `regen.py` and `mpuse.py` call it.
- **Reads (shipped data):** `paths.asset("gamedata/skills_classic.json")` (names, `mp` per level, `levels` texts), `gamedata/potions_classic.json` (in mpuse). Data folder: `templates/skill_icons/<id>.png` (cut from the player's own Skill window; local only, never shipped).
- **Helper side (`ui(shell)`):** `PanelHost(shell, "skillbar", "skill bar", make_bar, dy=400)`; menu "Skill bar" + "Lock skill bar (click-through)"; Settings > General extra (order 10); right-click a slot: lock/hide, corner (casts / MP / mesos), remove / add back a skill; `on_settings_save` keeps x, y, locked, hidden, show, guard_show; right-click Magic Guard: its number (ehp / absorb / last).

- **Ambiguous casts (buff icon):** a cast mpuse booked as an attack whose MP cost an owned, watched buff shares (Energy Bolt / Magic Guard, 8 MP) is booked provisionally (mpuse keeps its tie rule, so the replay CLI is unchanged) but parked in `Feed.pending`: not in the usage CSV, not in `live_casts.json` / `cast_n` (`Feed.shown_counts`). Decided by the `buffs` facts: that buff's icon flips (either way) from `FLIP_BEFORE_S` (0.3 s) before to `HOLD_S` (1.0 s) after the MP drop -> `Usage.rebook` moves the cast to the buff (counts, totals, event); `HOLD_S + SETTLE_S` (1.4 s) without a flip -> the attack stands. Only when the buff's state is known (a `buffs` fact arrived), else no waiting. Off with no MP drop: state only.

## Test it
Offline (dev scripts set `MAPLE_COMPANION_OFFLINE=1`):
- `python dev/buffs_check.py`: the decision logic with synthetic facts (+ the reader on the local burst frames; skips without them).
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
