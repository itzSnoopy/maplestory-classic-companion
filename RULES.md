# Rules for every session (whole project or one part)

## Hard rules
- **Screenshots and frames are Nexon game imagery.** Keep them local. Never upload, commit to a
  public repo, or publish them. The same goes for any Nexon client data. This repo is public.
- `config.json`, `helper_settings.json` and everything in the player's data folder are local: never
  commit, share or print them (`config.json` may still hold an old OBS password).
- **Passive only.** Data comes from frames of the game window. Reading game memory or intercepting game
  traffic violates Nexon's terms on the official server (anti-cheat ban risk): don't build it unless the
  player explicitly decides to accept that risk, and flag it when asked. Never send input or automate gameplay.
- **One exception (player's decision, 2026-10-24, ban risk understood):** `dev/local/game_click.py`
  (gitignored, dev only, never in the app) may click through the options menus only (ESC > Game
  Options > Graphics: Resolution, UI Size) to test window/UI sizes. Run it **only after the player
  OKs that specific run in chat**, only when a task needs it, short (its limits: 15 inputs, 90 s).
  Never extend it to gameplay, never run it on your own, never loosen its limits without asking.
- Game facts come from Classic sources only (MeowDB/NiaDB, classic.maplestorywiki.net, OSMS Data
  Explorer, Henesys.GG, Metaroad). **Never** modern GMS or private-server wikis/data.
- Every online feature goes through `base/internet.py` (`internet.allowed("<feature>")` right before
  connecting). Simulations and checks set `MAPLE_COMPANION_OFFLINE=1`.

## How we work
- Layers and import rules are enforced (`python dev/check_layers.py --strict`, in `dev/release_check.py`).
  Components never import each other; they talk through `base/contract.py` EVENTS / SERVICES.
- Test offline first (the part's map lists the commands); save simulation output to a scratch folder,
  never the real `history/`, `journal.md` or `sessions/`.
- New code reaches the player's installed app only through **/testbuild** (local test versions M.m.90+);
  **/release** publishes to GitHub. The player plays a session on a test build before a change is
  committed to main. Don't run the source tracker while the installed app tracks.
- Bigger code changes go to code subagents on **Sonnet**; the session that briefed them reviews the
  diff and runs the checks.
- New per-frame work is wrapped in `perf.timed("name")`. OCR / parsing changes: read `NOTES.md` first,
  add to it after.
- Dev/test windows open on the second monitor (`--at=-2400,40`).
- **At the end of a session:** update the part's memory (`.claude/handoff/components/<name>.md`),
  append a line to `.claude/handoff/08-session-log.md`, and update the status block in
  `.claude/handoff/README.md` if the overall status changed.
