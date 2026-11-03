# components/relic: the relic hunt on the player's side

A slow, shared jigsaw for a whole relay v2 room. The relay rolls the drops and holds the board (see `relay/CLAUDE.md`,
"Relic hunt"); this component turns the player's EXP rises into **eligible kills**, sends them with the party link's
sync, shows a toast when a piece drops, and opens the group's table page. Tracker side + helper side.

**Memory (local):** `.claude/handoff/components/relic.md`; plan: `.claude/handoff/11-challenges-plan.md` (Idea 2 and below).
**May import:** `hub`, `gamedata`, `base`. **Never:** another component (the link is reached by events and the `relay_link` service), `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `RelicComponent`: tracker side (exp_rise -> `Hunt.add`, relay_post adds `extra["_parts"]["relic"]`, relay_reply feeds `Hunt.on_reply`, emits `relic_found`, writes `relic.json`, worker thread for `relic_cmd.json`); helper side `ui(shell)` (menu "Open relic hunt", toast, Settings > General) |
| `hunt.py` | `Hunt`: plain state, no threads / files: pending eligible kills (sent / acknowledged by the next reply), relay answer, found ids, toast sequence, `snapshot` = relic.json |
| `toast.py` | `Toaster`: reads relic.json about once a second, shows a Maple-style notice (navy box, gold frame, the piece picture, "Relic piece found!" / "17 / 48 found") in a corner for 3 s (no sound); `image(found, total)` draws it; per-pixel alpha via `hub/ui/layered`, colour key fallback |
| `gamedata/relic.py` | `mobs_worth`, `in_band_share`, `eligible_kills` (pure, from `map_mobs.json`) |

## Interfaces
- **Listens to:** `service_start` / `service_stop`, `exp_rise` (`map`, `d`; level from `hub.state.level`), `relay_post` (v2 only), `relay_reply` (`reply["parts"]["relic"]`).
- **Emits:** `relic_found` (`ids, found, total, placed, difficulty, t`; the diary may add a line later).
- **Asks:** service `relay_link` (`mode` must be "v2"; `call("relic", "start"|"stop", body)`).
- **Eligible kills:** rise / the map's spawn-weighted average mob EXP (training's formula, so party shares still count) x the share of the map's mobs within +-10 levels of yours. Unknown map or level = 0. Only counted while the relay said a hunt is running.
- **Settings:** `relic` {enabled (True), toast (True)}; DEFAULTS in `hub/helper_settings.py`.
- **Writes (data folder):** `relic.json` `{t, enabled, room, active, finished, difficulty, pieces_total, found_count, placed_count, next_chance_pct, together {n, bonus_pct}, found_ids, pending, toast {seq, t, text}, error}`. Reads `relic_cmd.json` (`{action: start, difficulty, pieces?, hours?, people?}` / `{action: stop}`; written by the rooms panel's Start menu via `components/hangout/join.write_relic_command`, deleted after use).
- **Helper side:** "Open relic hunt" opens `<server>/rooms/<room>/relic#t=<member token>` in the browser (the fragment is never sent to the server and never logged; behind `internet.allowed("party")`).

## Test it
`python dev/relic_sim.py OUT` (real relay v2 + real party link + this component: start from the command file, out-of-band = 0, drops, deck, table moves with a conflict, completion, relic.json, toast trigger, relay restart), `python relay/tests/test_v2_relic.py`, `python dev/check_layers.py --strict`, `python dev/release_check.py`.
Live (the player): join a room, owner starts a hunt (rooms panel Start menu or the web Manage panel), kill mobs near your level, see the toast and the table page.

## Out of scope / don't read
`relay/` internals (own map), `components/party/` internals, `components/challenge/`.

## Rules that bite here
- Online only through the party link (`internet.allowed("party")`). No relay address, room id or token in tracked files or logs.
- Placeholder art is code-drawn (relay page); no AI art. The toast's piece is `hub/ui/art.piece()`: a placeholder picture the player chose (a game item icon) in `<data>/assets/art/relic_piece.gif`, git-ignored and never packaged; without it a drawn piece. Replace with the artist's piece when it comes.
- Per-rise work is wrapped in `perf.timed("relic")`; `relay_post` / `relay_reply` run on the link thread (`Hunt` has its own lock).
