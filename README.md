# ⚠️ PERSONAL PROJECT: FOR MY OWN USE FIRST

> [!WARNING]
> This is a personal hobby project I built for myself and a few friends. It's an early test
> build, not a finished product. There's **no support, no roadmap promises and no guarantee it
> works on your PC**. Please don't open issues or contact me expecting help or features.
> Use it at your own risk.

---

# Maple Classic Companion

A leveling tracker for Nexon's official **MapleStory Classic World**. A small window shows your
level, EXP per hour and time to the next level, and the app keeps a journal and a diary of your
milestones (level-ups, deaths, job advancements, new regions) with screenshots and silly captions.

## Components

- **[Tracker](docs/tracker.md)**: reads the status bar and minimap, works out EXP per hour, time to
  level, best maps and sessions; floating window, tray icon, settings, Discord status.
- **[Diary](docs/diary.md)**: your character's scrapbook of level-ups, deaths, job advancements and
  new places, with pictures and silly captions.

## How it works

The app works **only by looking at the game window**, the same way screen recording or streaming
software does. It reads the numbers that are already on your screen: the EXP bar, the level badge,
the job name and the map name on the minimap.

1. **Capture**: Windows hands the app a copy of what the MapleStory window shows (Windows Graphics
   Capture, the method OBS's Window Capture uses). It works while the window is covered, not while
   it's minimized.
2. **Read**: the EXP and level digits are matched pixel by pixel against shapes the app learned from
   your own screen during setup. The map name is read with Tesseract OCR, bundled with the app.
3. **Track**: from those readings it works out EXP per hour, time to level, best maps, sessions,
   deaths and level-ups, and saves them to your user folder.

## What it does NOT do

It doesn't touch the game, directly or indirectly:

- **No game files**: it never opens, reads, modifies or copies anything in the MapleStory install
  folder, and doesn't use the game's data files.
- **No memory**: it never reads or writes the game's memory.
- **No injection**: no DLLs, hooks, overlays drawn inside the game, or attaching to the process.
- **No network**: it never looks at, intercepts or sends game traffic. The app makes no internet
  connections at all. (Optional Discord status talks only to the Discord app on your PC, and is off
  by default.)
- **No input**: it never presses keys, clicks or moves the mouse for you. Nothing is automated.

It only sees pixels, like a person watching your screen.

## Nothing from Nexon ships with it

The download contains no game images. The few small reference pictures the app needs (the minimap's
WORLD button and header, your name tag) and the digit shapes are cut **from your own screen** during
the first-run setup, and they stay on your PC.

## Download and start (Windows)

1. Get the zip from [Releases](https://github.com/itzSnoopy/maplestory-classic-companion/releases)
   and unzip it anywhere, for example your Documents folder. **No installer, no admin rights.**
2. Run `MapleClassicCompanion.exe`. Windows may show "Windows protected your PC" because the build
   isn't signed: click **More info**, then **Run anyway**.
3. The setup window opens. Have MapleStory running **in a window**, logged in, with the **minimap
   open in the top-left corner**, and follow the steps (about a minute).

After setup, the tracker runs in the background with an icon in the tray. Right-click the small
window for Settings, the journal, the diary and "Run setup…".

## Your data

Everything is saved on your PC in `%LOCALAPPDATA%\MapleClassicCompanion`: settings, stats, journal,
diary and its screenshots. Nothing is uploaded anywhere.

**Uninstall**: delete the unzipped folder and that data folder.

## Running from source (developers)

Python 3.12 on Windows, with Tesseract installed:

```
pip install -r requirements.txt
pythonw companion.py
```

From source, data is kept in the project folder instead of AppData. `python packaging/build.py`
builds the release zip. The only code that goes online is developer tooling you run by hand
(`python diary.py icons` fetches game icons from Steam, `dev/fetch_map_mobs.py` fetches MeowDB);
the app itself never does.

## Credits

- EXP table, map names and map mob lists: [MeowDB / NiaDB](https://meowdb.com/msclassic) (fan-run).
- Map title OCR: [Tesseract](https://github.com/tesseract-ocr/tesseract) (Apache-2.0). Other bundled
  libraries are listed in `packaging/THIRD_PARTY.txt`.
- `discord_icon.png` is built from Microsoft's [Fluent Emoji](https://github.com/microsoft/fluentui-emoji)
  "Maple leaf" (MIT License, © Microsoft Corporation), modified.

MapleStory is a trademark of Nexon. This project is not affiliated with or endorsed by Nexon.
