# components/party: party box names, party link to the relay, party panel

Shows the in-game party (HP bars, who is on your map, leader) and, for friends who run this app too,
their level / EXP / map / HP / MP. Two processes: the **tracker** reads the party box, names the rows
and runs the link thread; the **helper** draws the panel over the game.

**Memory (local):** `.claude/handoff/components/party.md`: read it before changing anything here.
**May import:** `hub`, `gamedata`, `base`. **Never:** another component, `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `PartyComponent`: names the box rows, publishes live.json `party`, offers `party_on_map`, starts/stops the link; `ui(shell)` for the panel and Settings fields |
| `partylink.py` | `PartyLink` thread, **two modes** chosen each loop from helper_settings.json: **v2** when `relay.room` is set (a room joined in the rooms panel; wins over a party code): POST `<server>/v2/rooms/<id>/sync` with `Authorization: Bearer <token>` and body `{"presence": {own state}, "challenge": {...}, ...}` when the room is `active`; **v1** (only a party code): POST `<server>/v1/party/<code>` as before. Every 3 s while playing (10 s otherwise), writes the others to `party.json`; backoff 5..60 s on errors |
| `party_ui.py` | `PartyPanel` (helper): one card per party member + compact Friends list; `--demo` |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to (events):** `service_start` (starts the link thread), `service_stop` (stops it),
  `fact` (`kind == "party"`: `t`, `value` = box rows `[{mask, on_map, hp, leader}]`, `prof`).
- **Emits (on the link thread, not the tracker's):** `relay_post` (`extra` dict, `me`, `mode` "v1"|"v2"): subscribers add fields to the next post (`extra["ch"]` for a challenge = the v2 `challenge` part, `extra["caps"]` to override (a presence field in v2), `extra["_parts"] = {module: part}` for other v2 modules, `extra["_fast"] = True` = post again in 1 s / 1.1 s in v2); in v2 `me["name"]` is the name used in the room; `relay_reply` (`reply`, `server_off`): the relay's answer in one shape for both modes: `{mode, members, server_t, challenges, ...}` (v2 adds `parts` = the raw sync reply). Keep handlers short and thread-safe. Generic on purpose: the relic hunt hooks in the same way.
- **Provides / asks (services):** provides `party_on_map` (True while >1 box row is `on_map` and the box is <= 10 s old; training asks it for the `party` key of `exp_rise`, so the album skips party-share EXP). Provides `relay_link` -> `{server, code, ok, server_off, mode "v1"|"v2"|"off", room (v2 id), request(kind, suffix, body, method) -> (status, json) [v1; raises in a room], call(module, action, body) -> (status, json) [v2: POST /v2/rooms/<id>/<module>/<action> with the token; raises without an active room]}` or None before the link exists: other components make their own relay calls (challenge create / accept) without importing party. Asks none.
- **Source hooks used:** `hub.source.party_name(i, mask, known, t)` and `party_reset()` (optional, default None / nothing; implemented by `engine/sources/screen.py`). The rows themselves come from the engine via the `party` fact (reader: `engine/party.py`, not described here).
- **Rooms (v2):** the room is `relay.room` in helper_settings.json (`hub/relay_room.py`); **the helper writes it, the tracker only reads it**, so the helper's join/leave and the Settings window never fight the tracker over the file. `pending` -> the link idles quietly (party.json `pending: true`, no sync). `sync` 401 (kicked, banned, room deleted) -> the link stops syncing that exact token for good (kept in memory as a hash; no retry, so the relay's 10-wrong-tokens lockout is never reached), logs once, and writes party.json `{ok: false, room_lost: true, room_lost_id, room_lost_name, members: []}` every loop; the helper's rooms panel sees it, clears `relay.room` and tells the player. A new join (new token) syncs again. 403 on sync = pending; 429 "one sync per second" = retry in 2 s, any other 429 (token lockout) backs off like an error. v2 members come from the presence part (`{name, level, job, map, ..., age_s, role}`), yourself left out by the room name: the same shape party_ui already uses.
- **Settings:** `party` {code} (Settings > Internet > Party link; "Old party link; rooms replace this soon"; unused while a room is joined); the server is the shared `relay.server` (hub-owned, `hub/relay_addr.py`, Settings > Internet > Relay; `partylink.settings()` reads the raw file and migrates the old `party.server` in memory), `partypanel` {enabled, friends, locked, x, y}. DEFAULTS live in `hub/helper_settings.py`; `settings_defaults` here = both slices. Internet gate: `internet.allowed("party")` in `partylink.py` (feature toggle "party"); the link is off unless the relay server **and** the code are filled in.
- **Writes (data folder):** live.json section `party` = `{t, rows: [{name, me, on_map, hp, leader}]}` (rows `[]` = no box) -> read by `party_ui.members()`; `party.json` `{t, ok, error, server, mode "v1"|"v2", room?, pending?, room_lost?, room_lost_id?, room_lost_name?, me, members: [{name, level, job, map, channel, state, exp_cur, exp_need, exp_pct, exp_hr, eta, hp, mp, age_s, v}]}` (yourself left out; `error: "off"` when disabled) -> read by `party_ui` and the Settings status line.
- **Reads (data folder):** status.json (state, level, job, map, exp_*, pct, `exp_per_hour_recent`, `eta_to_level`), live.json (`character`, `hp`, `mp`, `t`), `history/characters.json` (name, channel), `base.version.VERSION`. Shipped data: none.
- **Challenge strips (panel, helper):** when a member's `caps` has `ch` and challenge.json (the tracker's) says `enabled`: each such member / friend row has a small crossed-swords **Duel** button (card header / friend row, right edge): click -> compact picker anchored to the row (2 / 5 / 10 / 15 min, "Challenge <Name>") -> `create` `{mode: "1v1", minutes, target}`; greyed (click shows the reason) while you are in a duel / waiting, or the member is (`busy` in party.json, not sent yet: the relay's refusal text from challenge.json `error` shows for a few seconds). The top line is the group entry, labelled "Race the party" (in-game party on screen) or "Race everyone here" (picker: my map / everyone online). "Waiting for <Name>..." (+ Cancel when challenge.json has `waiting.id`) while your invite is open. An invitation to you shows as one line with Accept / x (second place: the popup in `components/challenge/invite_ui.py`). The panel never talks to the relay: it writes `challenge_cmd.json` `{t, action: create|accept|decline|cancel, mode, minutes, target | id}` and the challenge component (tracker) sends it. `caps` rides on every post (`partylink.CAPS`). Demo: `python -m components.party.party_ui --demo [--waiting]`.
- **Helper side (`ui(shell)`):** `PanelHost(shell, "partypanel", "party panel", make_panel, dy=520)`; menu "Party panel" + "Lock party panel (click-through)"; Settings > General extra (order 30: panel checkbox); Settings > Internet extra `internet.party` (code field, link status from party.json); `on_settings_save` keeps x/y/locked and strips the code.

## Test it
Offline (all dev scripts set `MAPLE_COMPANION_OFFLINE=1`, so the link stays off):
- `python -m components.party.party_ui --demo --at=X,Y` (fake data; use the left monitor).
- `python -m engine.party FRAME [FRAME...]` on `dev/fixtures/party/*.png` (local Nexon frames): box rows.
- `python dev/rooms_sim.py OUT`: the real v2 relay in-process, the real link thread: two members see each other, a kick gives one 401 and `room_lost` with no retry, pending idles, duel over a room.
- Link against a local relay: `PORT=8787 python relay/party_relay.py`, then settings server `http://127.0.0.1:8787` + a code, run the tracker; party.json should show `ok: true`.
- `python dev/svc_sim.py OUT_DIR frames`, `python dev/album_sim.py OUT_DIR` (run the whole tracker incl. this component; the saved frames have no party rows). `python dev/release_check.py` (13 checks, incl. layers).
Live (the player): party box read under the minimap (2 rows, names), panel over the game (HP bars, friends list, move/lock/hide kept), party link to the relay (party.json `ok`). This check is still open since the restructure.

## Out of scope / don't read
`engine/party.py` (box pixels), the OCR in `engine/sources/screen.py`, `relay/` internals (own map: `relay/CLAUDE.md`), diary/album.

## Rules that bite here
- Public repo: no relay address, party code, character names in code, docs or fixtures.
- Online use only behind `internet.allowed("party")`; the link thread must never block the tracker. Never write helper_settings.json from the tracker; never print or log the room token (`room_key` keeps only a hash).
- Frames are Nexon imagery: local only. Passive screen reading only.
