# engine/: frames of the game window -> raw readings

**May import:** `base` only. No `gamedata`, no `hub`, no `components`, no `asset("gamedata/...")`.
Classic knowledge (map/job/skill name snapping, EXP table) reaches the engine as a **translator
injected by `launcher/compose.py`** (`gamedata/translate.py` module): `make_source(cfg, translate)`,
`ScreenSource(cfg, frames, translate)`, `panels.use(translate)`, `JobWatcher(translate, ...)`.
CLIs that need the translator live in `dev/engine_tools.py` (`record`, `panels`).

Passive only: frames of the game window. Never game memory, network traffic or input.
Every new per-frame reader wraps its work in `perf.timed("name")`.
**Read NOTES.md before changing OCR or parsing, and add to it after.**

| File | Role |
|---|---|
| `sources/` | `__init__.py` registry (`make_source`), `base.py` (re-exports base/contract), `wgc.py` (Windows Graphics Capture frames, default), `screen.py` (`ScreenSource`: HUD reads, quick looks ~1/s, facts, snapshots), `csv_replay.py` |
| `capture.py` | "Screen checks per second" (1-5): WGC frame rate + quick-look loop |
| `layout.py` / `layouts.json` | Where HUD boxes sit at any window size (anchors + recorded per-size boxes). Record: `python dev/engine_tools.py record` |
| `glyphs.py` | Exact pixel-font reader (EXP bar, level badge, windows); learns new glyphs |
| `hudtext.py` | The HUD's own text format: `parse_exp`, `parse_level` (`12,113[29.90%]`) |
| `ocr.py` | Tesseract wrapper (map title, job, names) |
| `minimap.py` / `mapwatch.py` | Map title from the full or collapsed minimap / when to check it (1 s after a change, slowing to 15 s) |
| `deathbox.py`, `hpbar.py`, `vitals.py` | "You have died." box / HP % from the red fill / exact HP/MP from the status bar text |
| `panels.py` | Character Stat, Skill, Change Channel windows: spotted on a 1/4-scale overview, read at full size |
| `jobread.py`, `charname.py` | Job from the status bar / who is playing (status-bar name fingerprint) |
| `party.py` | Party box rows under the minimap (HP %, on your map, leader) |
| `portrait.py` | Character close-up (name tag found by template) |
| `levelup_shot.py` | When to grab the level-up picture (EXP reset + 1.75 s) |
| `calibrate.py` | First-run calibration helpers (setup_ui calls them, with `exp_needed` passed in) |

Small CLIs on saved frames: `python -m engine.deathbox FRAME...`, `python -m engine.minimap learn-world ...`,
`python -m engine.layout learn-minimap FRAME`, `python -m engine.sources.wgc out.png`.
