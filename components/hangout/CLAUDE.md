# components/hangout: rooms panel (the relay's room list) over the game

A narrow panel that lists the rooms on the player's relay (hangouts and parties: name, who is online,
what they are doing, how to get in) and **joins** them (Join / Leave per row). **Helper process only**: the panel talks to the relay itself, the
tracker is not involved in the list or the join (like `feedback/`; party's link thread lives in the tracker because it must post
game state every few seconds, a lobby list needs nothing from the tracker).

**Memory (local):** `.claude/handoff/components/hangout.md`: read it before changing anything here.
**May import:** `hub`, `gamedata`, `base`. **Never:** another component (the relay address comes from
`hub/relay_addr.py`, not from `components.party`), `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `HangoutComponent`: helper side only; PanelHost "hangout", menu toggles, Settings > General line, Settings > Internet > Hangout rooms (feature checkbox + note only), `server()` (the shared `relay.server`) |
| `lobby.py` | No tkinter / PIL. `parse` (tolerant), `subline`, `fetch` (the gate + urllib GET), `Poller` (worker thread, 30 s, 60 s backoff, status lines) |
| `join.py` | No tkinter / PIL. `Client(server_fn)`: `join(room, name, secret)` -> `Outcome(kind, message, room)`, `check_me(room)` (active / pending / lost / unsure), `leave(room)`; `own_name()` (live.json / history/characters.json); gated by `internet.allowed("hangout")` |
| `hangout_ui.py` | `HangoutPanel` (PIL strip in a Toplevel like party_ui): header + refresh, "In: <room> · <kind>" strip with Leave, one row per room (joined row highlighted, button Join / Leave), `Prompt` (small themed prompt for the PIN / password, a character name, "Leave X and join Y?"), join/leave on worker threads, 10 s `/me` poll while pending, 60 s while active, reads party.json `room_lost`; drag / lock / right-click menu |
| `__init__.py` | docstring only |

Room activities (2026-11-01): the "In:" strip shows a green **Start** button when you are the room's owner or a mod (`relay.room.role`, saved from the join reply and refreshed by the 60 s `/me` poll; `relay_room.can_host`). It pops a menu (Kill race 2 / 5 / 10 / 15 min, and "Stop the current activity" while one is on) and writes `challenge_cmd.json` `{action: "start", kind: "race", minutes}` / `{action: "stop"}` through `join.write_command`; the tracker's challenge component does the relay call (see its map). The strip's second line shows the activity: the joined room's lobby row `activity`, else the tracker's `challenge.json` (`join.read_status`, also surfaces a failed command's error as a toast). The app only learns owner/mod from the relay; an owner token is not entered in the app (the owner starts from the web page, or promotes their app name to mod).

## Interfaces
- **Listens to / emits / services:** none.
- **Join flow (`HangoutPanel.on_join`):** open -> `POST /v2/rooms/<id>/join {name}` at once; private -> prompt (hidden entry) for the PIN / password, wrong secret (403) comes back with the prompt again, 429 shows the server's wait message (no reprompt); approval -> joined as `pending`, row says "Waiting for approval", `GET /me` every 10 s until `active` (401 = denied/removed: room cleared, "The owner didn't approve you in X"); 409 "name taken" / bad name -> prompt for another name; 404 -> "That room is gone". The name is the character name (`join.own_name()`), asked for when unknown. One room at a time: another room asks "Leave X and join Y?" first. Leave = `POST /leave`, then the room is cleared (409 = the owner can't leave while others are in: shown, room kept).
- **The joined room** lives in helper_settings.json `relay.room` = `{id, name, kind, token, status active|pending, joined_t, me}` (`hub/relay_room.py`; **the helper is its only writer**, via `shell.save_settings("relay", room=...)` so the other slices stay; the Settings window keeps the live room on Save and drops it when the relay address changed). The token is a secret: never printed, logged or committed. The tracker's party link reads it every loop (see `components/party/CLAUDE.md`).
- **Lost room:** a 401 from `/me` (60 s check) or `room_lost` in party.json for this room id (the tracker's link got a 401 on sync) -> `relay.room` cleared, toast "You're no longer in <room>". No retry with a bad token (the relay locks an address out after 10 wrong tokens in 10 minutes).
- **Relay:** `GET <server>/v2/lobby` -> `{"enabled", "rooms": [{id, name, kind party|hangout, access open|approval|private, members, online, max_members, activity}]}`. Missing fields tolerated; unlisted rooms never come. 404 = lobby off.
- **Settings:** `hangout` {enabled (False), locked, x, y}; DEFAULTS in `hub/helper_settings.py`, `settings_defaults` here. The server is the shared `relay.server` (Settings > Internet > Relay, `hub/relay_addr.py`: bare host -> `http://host:8787`); the old `hangout.server` is migrated away on load.
- **Internet gate:** feature `hangout` (`base/internet.py`); `lobby.fetch` calls `internet.allowed("hangout")` right before connecting; `Poller.tick` shows "Internet is off..." without connecting. Polls only while the panel is enabled (shown).
- **Writes / reads (data folder):** writes helper_settings.json `relay.room` (now with `role`) and `challenge_cmd.json` (Start/Stop, the party panel's hand-off file); reads challenge.json (activity line, command errors); reads party.json (`room_lost`, `room_lost_id`), live.json `character`, history/characters.json (join name).
- **Helper side (`ui(shell)`):** `PanelHost(shell, "hangout", ...)`, menu "Rooms panel" + "Lock rooms panel (click-through)", Settings > General extra (order 40), no Internet extra (the server field is the hub's Relay section).

## Test it
- `python dev/relay_addr_sim.py` (address normalising + settings migration).
- `python dev/hangout_sim.py` (fake lobby on 127.0.0.1; sets `MAPLE_COMPANION_OFFLINE=1`, checks the gate blocks first, then opens it for this process only).
- `python dev/hangout_sim.py --show --at=-2400,40` also opens the panel against the fake server (second monitor).
- `python dev/rooms_sim.py OUT` (real relay v2 in-process + the panel's real join code on a withdrawn Tk root, prompts answered by the sim: open / private / approval / taken name / lockout / leave / kick; also covers the party link and duels over a room).
- `python dev/check_layers.py --strict`.
Live (the player): set a server, switch the panel on, see the rooms; Join an open room, a private one (PIN prompt), an approval one; Leave.

## Out of scope / don't read
`relay/` internals (own map: `relay/CLAUDE.md`), party link, challenge.

## Rules that bite here
- Public repo: no relay address, room names or character names in code, docs or fixtures (fictional only).
- Online use only behind `internet.allowed("hangout")`; never block the Tk thread (every request, join included, runs on a worker thread, timeout 5-6 s). Never print or log the room token; `Outcome.__repr__` hides it.
- Keep `component.py` light at import time (PIL loads when the panel is switched on).
