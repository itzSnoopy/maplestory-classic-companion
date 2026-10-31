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

Each part has a **map** (`<folder>/CLAUDE.md`, tracked) and a **memory** (local): see root CLAUDE.md "Working on one part".

| Folder | What | live.json sections | Map / memory |
|---|---|---|---|
| `killers/` | Who killed you: fills the `death_guess` payload from the map's mob list (`gamedata/map_mobs.json`) + curated boss maps (`killers.json`); `python -m components.killers.killers audit` | - | `killers/CLAUDE.md` / `.claude/handoff/components/killers.md` |
| `diary/` | Timeline of milestones (`history/timeline.jsonl` -> `diary.md` + `diary.html`, screenshots in `screenshots/diary/`) with close-ups, camera button, notes, captions; `diary_server.py` hosts every component's pages | - | `diary/CLAUDE.md` / `.claude/handoff/components/diary.md` |
| `skillbar/` | HP/MP regen wheels, casts left, HP/MP usage (`feed.py`, `meter.py`, `mpuse.py`, `regen.py`, `skillbar_ui.py`) | hp, mp, regen, skills, usage | `skillbar/CLAUDE.md` / `.claude/handoff/components/skillbar.md` |
| `album/` | Monster album: every mob as ? / grey (seen on its map, or a boss <= 15 levels above you) / caught; a first kill (an EXP rise only one mob explains) takes a photo + emits `first_kill` (the diary writes the beat); page `/album.html`; mob facts `dev/fetch_mobs.py` | - | `album/CLAUDE.md` / `.claude/handoff/components/album.md` |
| `party/` | Party box names, party link to the relay, party panel | party | `party/CLAUDE.md` / `.claude/handoff/components/party.md` |
| `training/` | EXP/hr per visit, cost per kill / per 1,000 EXP, potions; emits `exp_rise` | training | `training/CLAUDE.md` / `.claude/handoff/components/training.md` |
| `kpq/` | Party quest runs (Kerning PQ; more = data in `gamedata/pq_classic.json`): EXP per stage, Bonus EXP + kills, time, EXP/hr; the live PQ view replacing the helper's stat grid (`shell.add_main_view`); `history/kpq_runs.json` (last 3 runs + best); `python dev/kpq_sim.py CSV OUT` | kpq | `kpq/CLAUDE.md` / `.claude/handoff/components/kpq.md` |
| `feedback/` | Skill review page `/skill-review.html` (class picker, answers kept in the browser) + Send to the owner's relay feedback inbox; helper only | - | `feedback/CLAUDE.md` / `.claude/handoff/components/feedback.md` |
| `discord/` | Discord Rich Presence (helper only) | - | `discord/CLAUDE.md` / `.claude/handoff/components/discord.md` |
