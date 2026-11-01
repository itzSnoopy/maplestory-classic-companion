# components/challenge: friendly kill-race duels over the party relay, the versus board

Two friends (or a whole group) on the same party-relay code race for the most individual kills in 2 / 5 / 10 / 15
minutes. The tracker counts your kills, sends each to the relay (which keeps the score and decides win / TIE); the
helper draws a black "cage match" bulb board over the game (names, ClassicRank sprites, 000 VS 000, timer bolted
underneath). Both processes. Idea 1 of the challenges plan (the relic hunt reuses the relay hooks and `bulbs.py`).

**Memory (local):** `.claude/handoff/components/challenge.md` (plan: `.claude/handoff/11-challenges-plan.md`): read both before changing anything here.
**May import:** `hub`, `gamedata`, `base`. **Never:** another component (party is reached by events, the `relay_link` service and files), `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `ChallengeComponent`: hooks the party link, command worker thread (reads `challenge_cmd.json`), writes `challenge.json`, record + `challenge_end`, journal line; `ui(shell)` = the board's PanelHost + Settings |
| `duel.py` | `Duel`: plain state (no threads/files): follows the relay's views, `on_rise` -> kill events (`gamedata/kills.py` whole matches, max 2 per rise), unacked events, `post_fields`, `on_reply` -> finished results, `snapshot` = challenge.json |
| `board.py` | The board: `view_state(snap, now, seen, imgs)`, `draw_state(state, flames, bg, s)`, `Board` window (drag / lock / hide), `demo_state`, `--demo` / `--shot` |
| `bulbs.py` | 5x7 bulb font + `draw_text` / `draw_digits` (generic, the relic hunt reuses it) |
| `sprites.py` | ClassicRank pictures: `get(name)` (cached, background fetch, once a day, `internet.allowed("avatars")`), `silhouette()` |
| `settings_page.py` | Settings > General lines: enable, don't disturb, background colour, flame picker with previews |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to:** `service_start` (worker thread, loads done ids), `service_stop`, `exp_rise` (`t, map, d`: counts only while a duel is live), `relay_post` (adds `extra["ch"]` = `{id, kills: [{s, n, t}]}` and `_fast` while a duel / invitation is open; `extra["caps"] = ""` when disabled or don't-disturb), `relay_reply` (`reply, server_off`), `challenge_end` (journal).
- **Emits:** `challenge_end` (`result`: id, mode, minutes, me, won True/False/None, score [mine, best other], opponent, names, ranking, t). The diary adds a `challenge` entry.
- **Asks:** service `relay_link` (party) for `request("challenge", "" | "/<id>", body)`.
- **Settings:** `challenge` {enabled (True), dnd, bg ("#000000"), flames (blaze | tongues | embers | arcane | poison | painted), locked, x, y}; DEFAULTS in `hub/helper_settings.py`. Internet feature `avatars` (sprites).
- **Writes (data folder):** `challenge.json` `{t, enabled, dnd, me, server_off, busy, invite {id, from, mode, minutes, expires}, active {relay view + mine_local} | None, last, error?}` (on every relay answer; the board and the party panel read it; the board ignores it when older than 15 s); `history/challenges.json` `{vs: {me: {opponent: {won, lost, tied}}}, log: [last 50 results]}`; `assets/avatars/<Name>.png` + `index.json`. Reads: `challenge_cmd.json` (written by the party panel, deleted after use).
- **Reads (shipped):** `gamedata/map_mobs.json` through `gamedata/kills.py`.
- **Helper side:** `PanelHost(shell, "challenge", "challenge board", make_board, dy=40)` (no tray entry: the board shows itself while a duel is on; right-click = lock / hide), Settings > General extra order 35.

## Rules of the duel (decisions 2026-10-31)
Kills only; **individual kills** (a rise equal to 1 or 2 whole mobs of the map, album/training matching, never "mobs' worth"); a party share that happens to match counts, one that doesn't is lost for both sides (revise later). Each kill goes to the relay as an event stamped with the relay clock; events are resent until acknowledged. Start = relay clock + 5 s. No sudden death: a tie is a TIE screen. Opponent on the left (sprite mirrored), you on the right.

## Test it
Offline: `python dev/challenge_sim.py OUT [--log exp_log.csv --map "Map"]` (real relay + real link thread on 127.0.0.1, a fake rival; covers create/accept/countdown/kills/ack/link down/win/tie/record/diary/dnd and the board's state machine), `python relay/test_relay.py`, `python dev/release_check.py`.
Board: `python -m components.challenge.board --demo --at=-2400,40 [--phase=start|live|winner|tie] [--flames=arcane]` (second monitor), `python -m components.challenge.board --shot OUT_DIR` (PNG of every phase and flame style).
Live (player + a friend on a build with challenges): the button appears on the party panel, invitation line + Accept, countdown, kills move the score within ~1-2 s, winner / TIE screen, record in `history/challenges.json`, sprites show after the first ClassicRank fetch.

## Out of scope / don't read
`components/party/` internals, `relay/` (own map), `hub/exprate.py`.

## Rules that bite here
- Online only through the party link (`internet.allowed("party")`) and sprites (`internet.allowed("avatars")`); sims set `MAPLE_COMPANION_OFFLINE=1`. No relay address, party code or character names in tracked files.
- Never send game imagery through the relay: only names and kill counts travel; sprites are fetched per client.
- Per-rise work is wrapped in `perf.timed("challenge")`. Link-thread hooks (`relay_post` / `relay_reply`) share `Duel` under its lock.
