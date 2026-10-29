# discord/: Discord Rich Presence from the helper window

Shows "Playing MapleStory" with the player's level, map, etc. on their Discord profile, text only. Runs in the
**helper** process only (the tracker has nothing to attach); off by default.

**Memory (local):** `.claude/handoff/components/discord.md`: read it before changing anything here.
**May import:** `base`, `hub` (`helper_settings`, `helper_ui`, `ui.theme`). **Never:** another component, `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `DiscordComponent`: Settings page, save hook, per-refresh `update(snap)`, `close()` on exit; creates `Presence` lazily |
| `discord_rpc.py` | `IPC` (named pipe `\.\pipe\discord-ipc-0..9`, handshake, `SET_ACTIVITY`) and `Presence` (background worker: sends changes only, `MIN_SEND_GAP_S` 15 s, reconnect every `RETRY_S` 30 s, `status`) |
| `text.py` | Placeholders (`PLACEHOLDERS`), `SHOW_FIELDS`, `PCT_STYLES`, `values`, `render` (drops empty fields cleanly), `build_activity(snap, settings)` -> activity dict or `None` (= clear), `uses_game_app` |
| `settings_page.py` | `DiscordPage`: Settings > Discord fields + live preview card ("Sent to Discord" text, connection status) |

## Interfaces
- **Listens to (events):** none (helper side; no bus).
- **Emits / services / Source hooks:** none.
- **Settings:** `discord` slice of helper_settings.json: `enabled` (False), `client_id` (default
  `HS.MAPLESTORY_APP_ID`), `app_name` (preview only), `details`, `state`, `large_text`, `large_image`,
  `show{name,job,level,pct,map,channel}`, `pct_style` (`whole|one|two`), `show_elapsed`,
  `when_not_playing` (`clear|idle`). Also reads `character.*` (name, channel, job). DEFAULTS live in `hub/helper_settings.py`.
- **Writes (player data folder):** nothing (the Settings window saves helper_settings.json).
- **Reads (shipped data):** none. (`discord_icon.png` in the repo root is a manual upload for the player's own
  Discord app, not read by code.)
- **Helper side (`ui(shell)`):** `shell.add_settings_page("Discord", ..., after="Character")`,
  `shell.on_settings_save` (strips `client_id`), `shell.on_refresh(update)`, `shell.on_exit(close)`; the helper
  snapshot (`snap.state/level/pct/map/eta_s/rate/session_start`) feeds `build_activity`. Not sent in demo mode.
- **Network:** local IPC to the Discord desktop app only; not an internet feature (not in `base/internet.py`).

## Test it
Offline:
- `python dev/release_check.py` (compile, layer check, packaging HIDDEN lists the three modules).
- Text builder: `python -c "from components.discord import text as T; print(T.render('{name} · Lv {level} {job}', {'name':'A','level':'','job':'Mage'}))"`.
- Settings page and preview card: `python -m hub.helper_ui --demo --settings Discord --at=-2400,40` (fake data, second monitor; presence is never sent in demo mode).
Live (player, with Discord open): turn it on in Settings > Discord, Save; the profile shows both lines and the
timer within ~15 s; stop the game or close the helper and the status clears; "Not playing" preview shows nothing
or "Idle" as chosen.

## Out of scope / don't read
Tracker, engine, screen reading, other pages of the settings window (`hub/settings_ui.py` only for the hook API),
the diary/album. The helper snapshot's shape is in `hub/helper_ui.py` (`Snapshot`).

## Rules that bite here
- Text only: no screenshots or game files are ever sent. Nothing is sent until the player turns it on.
- Art on the player's own Discord app is the friend's original art (`discord_icon.png`); never Nexon art.
  Under MapleStory's own app ID no `assets` are sent (hover text dropped).
- Keep `component.py` light at import (tkinter/PIL only inside `page()`/`settings_page.py`).
