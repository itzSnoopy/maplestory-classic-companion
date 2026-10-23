# MapleStory Classic Companion

A passive leveling tracker and companion for **Nexon's official MapleStory Classic World**
(pre-Big Bang). It watches the game window (or an OBS source), reads the EXP bar, level badge and
map name from the screen, and keeps a log of your EXP/hr, time per level, best maps, deaths and
milestones. A small floating window and tray icon show the live numbers, and optional Discord
Rich Presence shows your level and map.

**It only looks at the screen.** It never reads game memory, never touches game traffic, and never
sends input or automates gameplay.

## Requirements

- Windows, Python 3.12
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (used for the map title)
- Optional: OBS Studio with the WebSocket server enabled, if you capture through OBS

```
pip install -r requirements.txt
```

## Setup

1. Copy `config.example.json` to `config.json`. If you use OBS, fill in `obs.password` and
   `obs.source` (the name of your Game Capture source). `config.json` is git-ignored.
2. Start the tracker: `start_tracker.cmd` (stop with `stop_tracker.cmd`).
3. Start the helper window: `start_helper.cmd`. Settings (capture source, character, Discord) are
   in the helper's settings window and saved to `helper_settings.json` (also git-ignored).

The `.cmd` launchers point at `C:\Program Files\Python312\pythonw.exe`; edit them if your Python
lives elsewhere.

### Files you create locally (not in this repo)

Some files are cut from the game's own screen and are Nexon imagery, so they are never committed:

- `templates/minimap_header.png`, `templates/minimap_world.png`, `templates/nametag.png`: small
  reference crops of the minimap header, the minimap WORLD button and the name tag.
- `glyphs.json`, `glyphs_level.json`: the HUD digit glyphs, learned with `python obs_test.py --learn`.

See `NOTES.md` for how each one is made.

## Layout

| File | Role |
|---|---|
| `tracker.py` | Tracker and background service loop |
| `sources/` | Pluggable data sources (game window, OBS, CSV replay) |
| `layout.py` / `layouts.json` | Where HUD boxes sit at any window size |
| `glyphs.py`, `ocr.py`, `parse.py` | Pixel-font reader, Tesseract wrapper, text to numbers |
| `minimap.py`, `maplist.py`, `maps_classic.json` | Map title reading, snapped to real Classic map names |
| `history.py`, `diary*.py` | Journal, per-map stats, digest, diary timeline |
| `helper_ui.py`, `settings_ui.py`, `discord_rpc.py` | Floating window, settings, Discord presence |
| `dev/` | Offline test tools |

Design notes and OCR findings: `NOTES.md`. Development handoff docs: `.claude/handoff/`.

## Credits

- EXP table and map names from MeowDB / NiaDB (meowdb.com/msclassic).
- `discord_icon.png` is built from Microsoft's Fluent Emoji "Maple leaf"
  (https://github.com/microsoft/fluentui-emoji), MIT License, Copyright (c) Microsoft Corporation.
  Modified.

MapleStory is a trademark of Nexon. This project is not affiliated with or endorsed by Nexon.
