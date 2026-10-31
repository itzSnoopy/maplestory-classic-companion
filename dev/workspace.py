r"""Set up a folder outside this project as a workspace for ONE part of it.

    python dev/workspace.py --list                 the parts
    python dev/workspace.py engine D:\work\ocr     write D:\work\ocr\CLAUDE.md for the engine
    python dev/workspace.py engine D:\work\ocr --force   overwrite an existing CLAUDE.md

The CLAUDE.md it writes @-imports, by absolute path: the project rules (RULES.md), the part's map
(<folder>/CLAUDE.md) and its memory (.claude/handoff/components/<name>.md, local only). So a session
started there loads only what that part needs, always the current version (nothing is copied).
Add this project folder to that session (Claude desktop: add folder; CLI: --add-dir) so it can
edit files and run commands here. Writes one file; touches nothing in this project.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# part -> (what it is, map files, memory file in .claude/handoff/components/)
PARTS = {
    "engine": ("screen reading: OCR, HUD layout, HP/MP, game windows, capture", ["engine/CLAUDE.md"], "engine.md"),
    "gamedata": ("Classic game data, name snapping, EXP table", ["gamedata/CLAUDE.md"], "gamedata.md"),
    "hub": ("tracker core, event bus, history, helper shell (+ base/)", ["hub/CLAUDE.md", "base/CLAUDE.md"], "hub.md"),
    "launcher": ("entry, wiring, updater, setup, packaging, releases", ["launcher/CLAUDE.md"], "installer.md"),
    "diary": ("diary timeline, close-ups, camera, page server", ["components/diary/CLAUDE.md"], "diary.md"),
    "album": ("monster album, first-kill photos", ["components/album/CLAUDE.md"], "album.md"),
    "killers": ("who killed you (death guess)", ["components/killers/CLAUDE.md"], "killers.md"),
    "discord": ("Discord Rich Presence", ["components/discord/CLAUDE.md"], "discord.md"),
    "party": ("party box, party link, party panel, relay server",
              ["components/party/CLAUDE.md", "relay/CLAUDE.md"], "party.md"),
    "skillbar": ("skill bar: HP/MP regen, casts left, HP/MP usage", ["components/skillbar/CLAUDE.md"], "skillbar.md"),
    "training": ("training cost panel, EXP/hr per visit", ["components/training/CLAUDE.md"], "training.md"),
    "kpq": ("party quest runs, live PQ view", ["components/kpq/CLAUDE.md"], "kpq.md"),
}

TEMPLATE = """# Workspace: {part} ({what})

This folder is a workspace for **one part** of the MapleStory Classic Companion project:
`{root}`. All code, data files and commands live there; this folder holds nothing but this file.
Work only on this part. Read the files below (they are imported) and `base/contract.py`; open other
project files only when the map sends you there.

- Run commands from the project folder (`cd "{root}"`), e.g. its offline checks.
- Edit files in the project folder; don't copy code here.
- At the end: update the memory file below, and append a line to
  `{root}/.claude/handoff/08-session-log.md`.

## Rules
@{rules}

## Map
{maps}

## Memory (local, current status)
@{memory}
"""


def render(part):
    what, maps, memory = PARTS[part]
    p = lambda rel: (ROOT / rel).as_posix()
    missing = [rel for rel in [*maps, f".claude/handoff/components/{memory}"] if not (ROOT / rel).exists()]
    text = TEMPLATE.format(part=part, what=what, root=ROOT.as_posix(), rules=p("RULES.md"),
                           maps="\n".join(f"@{p(m)}" for m in maps),
                           memory=p(f".claude/handoff/components/{memory}"))
    return text, missing


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part", nargs="?", choices=sorted(PARTS))
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true", help="overwrite an existing CLAUDE.md")
    a = ap.parse_args(argv)
    if a.list or not a.part:
        for name, (what, maps, memory) in PARTS.items():
            print(f"  {name:<9} {what}")
        return 0
    if not a.folder:
        ap.error("give the folder to set up")
    out = Path(a.folder).resolve()
    if out == ROOT or ROOT in out.parents:
        ap.error("the workspace goes outside this project (inside it, the part's own CLAUDE.md already loads)")
    text, missing = render(a.part)
    target = out / "CLAUDE.md"
    if target.exists() and not a.force:
        ap.error(f"{target} exists (use --force to overwrite)")
    out.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    for rel in missing:
        print(f"  note: {rel} doesn't exist yet; the import will be empty until it does")
    print(f'next: start a session in {out} and add the project folder "{ROOT}" to it')
    return 0


if __name__ == "__main__":
    sys.exit(main())
