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
| `partylink.py` | `PartyLink` thread: every 3 s while playing (10 s otherwise) POSTs your state to `<server>/v1/party/<code>`, writes the others to `party.json`; backoff 5..60 s on errors |
| `party_ui.py` | `PartyPanel` (helper): one card per party member + compact Friends list; `--demo` |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to (events):** `service_start` (starts the link thread), `service_stop` (stops it),
  `fact` (`kind == "party"`: `t`, `value` = box rows `[{mask, on_map, hp, leader}]`, `prof`).
- **Emits:** nothing.
- **Provides / asks (services):** provides `party_on_map` (True while >1 box row is `on_map` and the box is <= 10 s old; training asks it for the `party` key of `exp_rise`, so the album skips party-share EXP). Asks none.
- **Source hooks used:** `hub.source.party_name(i, mask, known, t)` and `party_reset()` (optional, default None / nothing; implemented by `engine/sources/screen.py`). The rows themselves come from the engine via the `party` fact (reader: `engine/party.py`, not described here).
- **Settings:** `party` {server, code} (Settings > Internet > Party link), `partypanel` {enabled, friends, locked, x, y}. DEFAULTS live in `hub/helper_settings.py`; `settings_defaults` here = both slices. Internet gate: `internet.allowed("party")` in `partylink.py` (feature toggle "party"); the link is off unless server **and** code are filled in.
- **Writes (data folder):** live.json section `party` = `{t, rows: [{name, me, on_map, hp, leader}]}` (rows `[]` = no box) -> read by `party_ui.members()`; `party.json` `{t, ok, error, server, me, members: [{name, level, job, map, channel, state, exp_cur, exp_need, exp_pct, exp_hr, eta, hp, mp, age_s, v}]}` (yourself left out; `error: "off"` when disabled) -> read by `party_ui` and the Settings status line.
- **Reads (data folder):** status.json (state, level, job, map, exp_*, pct, `exp_per_hour_recent`, `eta_to_level`), live.json (`character`, `hp`, `mp`, `t`), `history/characters.json` (name, channel), `base.version.VERSION`. Shipped data: none.
- **Helper side (`ui(shell)`):** `PanelHost(shell, "partypanel", "party panel", make_panel, dy=520)`; menu "Party panel" + "Lock party panel (click-through)"; Settings > General extra (order 30: panel checkbox); Settings > Internet extra `internet.party` (server + code fields, link status from party.json); `on_settings_save` keeps x/y/locked and strips server/code.

## Test it
Offline (all dev scripts set `MAPLE_COMPANION_OFFLINE=1`, so the link stays off):
- `python -m components.party.party_ui --demo --at=X,Y` (fake data; use the left monitor).
- `python -m engine.party FRAME [FRAME...]` on `dev/fixtures/party/*.png` (local Nexon frames): box rows.
- Link against a local relay: `PORT=8787 python relay/party_relay.py`, then settings server `http://127.0.0.1:8787` + a code, run the tracker; party.json should show `ok: true`.
- `python dev/svc_sim.py OUT_DIR frames`, `python dev/album_sim.py OUT_DIR` (run the whole tracker incl. this component; the saved frames have no party rows). `python dev/release_check.py` (13 checks, incl. layers).
Live (the player): party box read under the minimap (2 rows, names), panel over the game (HP bars, friends list, move/lock/hide kept), party link to the relay (party.json `ok`). This check is still open since the restructure.

## Out of scope / don't read
`engine/party.py` (box pixels), the OCR in `engine/sources/screen.py`, `relay/` internals (own map: `relay/CLAUDE.md`), diary/album.

## Rules that bite here
- Public repo: no relay address, party code, character names in code, docs or fixtures.
- Online use only behind `internet.allowed("party")`; the link thread must never block the tracker.
- Frames are Nexon imagery: local only. Passive screen reading only.
