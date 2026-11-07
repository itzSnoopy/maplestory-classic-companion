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
| `duel.py` | `Duel`: plain state (no threads/files): follows the relay's views, `on_gain` (the game's EXP lines: own = white = 1 kill) and `on_rise` (EXP bar, held 2.5 s, fallback only: `gamedata/kills.py` whole matches, max 2 per rise, solo only) -> kill events, `settle`, unacked events, `post_fields`, `on_reply` -> finished results, `snapshot` = challenge.json |
| `partyexp.py` | **Superseded for counting (2026-11-01).** Only `party_levels` is used ("is a member on my map?"). Was: Party EXP for the duel: `party_levels(root, my_level, map)` (levels of the other members on my map: party.json members seen <= 10 s ago + live.json party-box rows, unknown level = mine), `Levels` (list with `.guessed` = stand-in count), `PartyBonus` (learned bonus rule `kills.BonusFit(b, mode)` per eligible party size, samples, background fit; `history/party_bonus.json`) |
| `board.py` | The board: `view_state(snap, now, seen, imgs)`, `draw_state(state, flames, bg, s, k)`, `Board` window (drag / lock / hide, size = `challenge.scale`, keeps its top centre when the size changes), `demo_state`, `--demo [--scale=50|75|100]` / `--shot` (every scale) |
| `invite_ui.py` | The invitation popup over the game ("<Name> challenges you!", mode + minutes, big Accept / Decline, countdown bar to `invite.expires`): `InviteState` (pure logic: shows on invite, hides on answer / expiry / dnd / stale json), `InvitePopup`, `InviteHost` (a PanelHost on the `challenge` slice); writes the same accept / decline commands as the party panel; shown without taking focus (WS_EX_NOACTIVATE); `--demo`, `--selftest` |
| `bulbs.py` | 5x7 bulb font + `draw_text` / `draw_digits` (generic, the relic hunt reuses it) |
| `sprites.py` | ClassicRank pictures: `get(name)` (cached, background fetch, once a day, `internet.allowed("avatars")`), `silhouette()` |
| `settings_page.py` | Settings > General lines: enable, don't disturb, background colour, board size picker (50 / 75 / 100 %) with a live preview, flame picker with previews |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to:** `service_start` (worker thread, loads done ids), `service_stop`, `exp_gain` (`t, base, party_bonus, own, overflow`: from engine/msgcorner via the bus; own gains are the duel's kills; every gain is logged), `exp_rise` (`t, map, d, party`: held as a fallback while a duel is live), `relay_post` (adds `extra["ch"]` = `{id, kills: [{s, n, t}]}` and `_fast` while a duel / invitation is open; `extra["caps"] = ""` when disabled or don't-disturb), `relay_reply` (`reply, server_off`), `challenge_end` (journal).
- **Emits:** `challenge_end` (`result`: id, mode, minutes, me, won True/False/None, score [mine, best other], opponent, names, ranking, t). The diary adds a `challenge` entry.
- **Asks:** service `relay_link` (party). `mode` "v2" (a joined room) -> `call("challenge", create|accept|decline|cancel, body)` = `POST /v2/rooms/<id>/challenge/<action>` (body `{mode, minutes, target?}` / `{id}`; the acting name comes from the member token); `mode` "v1" (party code only) -> `request("challenge", "" | "/<id>", body)` as before. Picked per command from `link["mode"]`. Kills ride the sync either way: v1 `ch` on the post, v2 the same `extra["ch"]` sent as the sync's `challenge` part (the reply's `challenge.challenges` is mapped to `reply["challenges"]`, same view shape, so `Duel` is unchanged). Opponents are the room's members (party.json from the presence part); `d.me` is the name used in the room (`relay_post` `me["name"]`).
- **Settings:** `challenge` {enabled (True), dnd, bg ("#000000"), flames (blaze | tongues | embers | arcane | poison | painted), scale (50 | 75 | 100, default 50: board size in % of the 920 x 250 design), locked, x, y, invite_x, invite_y (popup position, None = top centre)}; DEFAULTS in `hub/helper_settings.py`. Internet feature `avatars` (sprites).
- **Writes (data folder):** `challenge.json` `{t, enabled, dnd, me, server_off, busy, invite {id, from, mode, minutes, expires}, active {relay view + mine_local} | None, last, error?}` (on every relay answer; the board and the party panel read it; the board ignores it when older than 15 s); `history/exp_gains.csv` (`t,map,base,party_bonus,own,overflow`, every EXP line duel or not, rotates to `.old` at 5 MB, so a party session can be checked later); `history/challenges.json` `{vs: {me: {opponent: {won, lost, tied}}}, log: [last 50 results]}`; `assets/avatars/<Name>.png` + `index.json`. Reads: `challenge_cmd.json` (written by the party panel, deleted after use).
- **Reads (shipped):** `gamedata/map_mobs.json` through `gamedata/kills.py`.
- **Helper side:** `InviteHost(shell)` (invite_ui.py) shows the popup while `enabled` and no don't-disturb; `challenge.json` `waiting {id, mode, target}` (my open invite; target only for 1v1) drives the party panel's "Waiting for <Name>... (Cancel)".  `PanelHost(shell, "challenge", "challenge board", make_board, dy=40)` (no tray entry: the board shows itself while a duel is on; right-click = lock / hide), Settings > General extra order 35.

## Room activities (host races, 2026-11-01)
The owner/mod of a joined v2 room starts a room-wide kill race (relay `challenge/start`, see `relay/CLAUDE.md`) from the hangout panel's Start button, or from the web page. Command path: the panel writes `challenge_cmd.json` `{action: "start", kind: "race", minutes}` / `{action: "stop"}`; `ChallengeComponent._command` (v2 only) calls `link["call"]("challenge", "start"|"stop", ...)` and merges the answer. Invited players get the usual invitation (`challenge.json` `invite`, now with `host: true`; the party panel labels it "(room race)") and accept/decline through the same commands. Duel changes for host races: the creator is not necessarily a player (`waiting()`/`active()` already key on `accepted`), `joined_open()` makes an accepted-but-not-started race count as `busy` and keeps the fast post rate; the board and record already handle 3+ players (best other on the left, record per opponent).

## Rules of the duel (rewritten 2026-11-01: kills from the game's EXP lines)
Kills only, **individual kills**. The game's bottom-right `You received EXP (+N)` line (engine/msgcorner.py, event `exp_gain`) is exact and its first-look colour says whose kill it was: **white = my own kill (`own=True`), yellow = a teammate's share (`own=False`)**; a `Bonus EXP for PARTY` line is part of the same gain. Solo and party alike:
- every `exp_gain` with `own=True` while a duel is live (stamped with the relay clock, inside the duel window) = **1 kill** at the gain's time (overflow-flagged ones count too); `own=False` counts 0.
- the **EXP bar is a fallback only**: an `exp_rise` is held. When it is older than 2.5 s (`SETTLE_S`; settled on every rise, every gain and in `post_fields`, i.e. within ~3 s) and **any** gain arrived with t in [rise-1.0, rise+2.5] the corner covers it and the rise counts nothing; otherwise solo -> `kills.whole_kills` (max 2 per rise) as before, **partied -> 0** (never guess). Partied = the rise's `party` flag, fresh party members on my map (`partyexp.party_levels`), or a gain with `party_bonus > 0` within 10 s before the rise.
- `challenge.json`: `party: true` with `party_note` "kills from the game's EXP lines" (a party bonus line seen during the duel) or "party: EXP lines not seen, kills not counted" (a partied fallback rise counted 0).
- Superseded and no longer fed: `gamedata/kills.party_kills`, `partyexp.PartyBonus` (`history/party_bonus.json`), the formula fits. They stay (and `dev/party_kills_sim.py` in release_check) as reference. Mass-kill maps are limited by the corner (> 6 lines in one tick are partly never shown: overflow).
Each kill goes to the relay as an event stamped with the relay clock; events are resent until acknowledged. Start = relay clock + 5 s. No sudden death: a tie is a TIE screen. Opponent on the left (sprite mirrored), you on the right.

## Practice duel (dev only, 2026-11-02)
A solo duel with no relay, to watch the counter: `python dev/practice.py [MINUTES]` writes `challenge_cmd.json` `{action: "practice", minutes}` (no UI, no setting). `Duel.start_practice` makes a one-player view (`id: "practice"`, 5 s countdown) that survives relay answers; own (white) lines count, yellow ones go to `shared` (`challenge.json` `active.mine_local` / `shared_local`); no events are sent; `practice_done` journals "Practice duel: N own kills, M shared". `python dev/practice_sim.py`.

## Test it
Over a room: `python dev/rooms_sim.py OUT` (real relay v2: create / accept / kills via the sync part and the `kills` action / result / record between the tracker and a simulated client; section 6: a mod's Start with 3 simulated members, 3-player result, and an owner-on-web (not live) race).
Gain-based kills: `python dev/gain_kills_sim.py` (synthetic duel + replay of the burst fixtures when present). Offline (v1): `python dev/challenge_sim.py OUT [--log exp_log.csv --map "Map"]` (real relay + real link thread on 127.0.0.1, a fake rival; covers create/accept/countdown/kills/ack/link down/win/tie/record/diary/dnd and the board's state machine), `python relay/test_relay.py`, `python dev/release_check.py`.
Invite popup: `python -m components.challenge.invite_ui --selftest` (state logic, offline), `--demo --at=-2400,40` (a fake invitation every 25 s). Board scales: `--demo --scale=50`.
Board: `python -m components.challenge.board --demo --at=-2400,40 [--phase=start|live|winner|tie] [--flames=arcane]` (second monitor), `python -m components.challenge.board --shot OUT_DIR` (PNG of every phase and flame style).
Live (player + a friend on a build with challenges): the button appears on the party panel, invitation line + Accept, countdown, kills move the score within ~1-2 s, winner / TIE screen, record in `history/challenges.json`, sprites show after the first ClassicRank fetch.

## Out of scope / don't read
`components/party/` internals, `relay/` (own map), `hub/exprate.py`.

## Rules that bite here
- Online only through the party link (`internet.allowed("party")`) and sprites (`internet.allowed("avatars")`); sims set `MAPLE_COMPANION_OFFLINE=1`. No relay address, party code or character names in tracked files.
- Never send game imagery through the relay: only names and kill counts travel; sprites are fetched per client.
- Per-rise / per-gain work is wrapped in `perf.timed("challenge")`. Link-thread hooks (`relay_post` / `relay_reply`) share `Duel` under its lock.
