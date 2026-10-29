# components/: one folder per feature (plugins on the hub)

**May import:** `hub`, `gamedata`, `base`. **Never another component** and never `engine` or
`launcher`. Components talk through bus events and services (`base/contract.py` EVENTS / SERVICES),
and reach the screen only through `hub.source` / `hub.snapshot` (the Source hooks).

Each component has `component.py` with a `Component` subclass:
- `attach(hub)` (tracker process, once): `hub.on(event, fn)`, `hub.provide(name, fn)`, `hub.publish(section, fn)`.
  Start threads / touch files on `service_start`, not in attach. Handlers take `**_` for keys added later.
- `ui(shell)` (helper process): menu items, Settings pages/extras, panels over the game (`hub.ui.overlay.PanelHost`), pages.
- `settings_defaults`: its slice of helper_settings.json (same keys as before; DEFAULTS stay in `hub/helper_settings.py`).
- Keep `component.py` light at import time (no PIL/numpy at the top): the helper imports all of them.

`__init__.py` is the registry: `COMPONENTS` ("module:Class" strings, loaded lazily by `load_all()`).
**Order matters**: handlers and `ui()` run in that order (killers before diary; skillbar before party
before training). A new component = a folder + a registry line + its module names in
`packaging/build.py` HIDDEN.

| Folder | What | live.json sections | Handoff (local only, git-ignored) |
|---|---|---|---|
| `killers/` | Who killed you: fills the `death_guess` payload from the map's mob list (`gamedata/map_mobs.json`) + curated boss maps (`killers.json`); `python -m components.killers.killers audit` | - | - |
| `diary/` | Timeline of milestones (`history/timeline.jsonl` -> `diary.md` + `diary.html`, screenshots in `screenshots/diary/`) with close-ups, camera button, notes, captions; `diary_server.py` hosts every component's pages | - | `.claude/handoff/components/diary.md` |
| `skillbar/` | HP/MP regen wheels, casts left, HP/MP usage (`feed.py`, `meter.py`, `mpuse.py`, `regen.py`, `skillbar_ui.py`) | hp, mp, regen, skills, usage | `.claude/handoff/components/skillbar.md` |
| `album/` | Monster album: every mob as ? / grey (seen, boss, or <= 3 levels above you) / caught; a first kill (an EXP rise only one mob explains) takes a photo + emits `first_kill` (the diary writes the beat); page `/album.html`; mob facts `dev/fetch_mobs.py` | - | `.claude/handoff/components/album.md` |
| `party/` | Party box names, party link to the relay, party panel | party | `.claude/handoff/components/party.md` |
| `training/` | EXP/hr per visit, cost per kill / per 1,000 EXP, potions; emits `exp_rise` | training | - |
| `discord/` | Discord Rich Presence (helper only) | - | - |
