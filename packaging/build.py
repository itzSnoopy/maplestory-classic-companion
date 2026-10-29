"""Build the Windows release: dist/MapleClassicCompanion/ and a zip of it.

    python packaging/build.py

Needs: pip install -r requirements.txt pyinstaller, and Tesseract (UB Mannheim) installed at
TESSERACT_DIR (only tesseract.exe, the DLLs it loads, English data and its license are copied).

What goes in: the code, the shipped read-only JSON listed in DATA_FILES (gamedata/*.json,
engine/layouts.json, ...), config.example.json. What never goes in: anything from the player's data (config.json,
helper_settings.json, templates/, glyphs, history, screenshots). Those are Nexon imagery or
personal, and each player's copy makes its own on first run (setup window).
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from base.version import VERSION  # noqa: E402

NAME = "MapleClassicCompanion"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
TESSERACT_DIR = Path(os.environ.get("TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
# shipped read-only files (repo-relative path); each lands at the same relative path in the bundle,
# where paths.asset("<same path>") reads it
DATA_FILES = ["config.example.json",
              "gamedata/maps_classic.json", "gamedata/exp_table_classic.json", "gamedata/map_mobs.json",
              "gamedata/important_maps.json", "gamedata/game_refs.json", "gamedata/skills_classic.json",
              "gamedata/potions_classic.json", "gamedata/mobs_classic.json",
              "engine/layouts.json", "launcher/whats_new.json",
              "components/killers/killers.json", "components/diary/captions.json"]
# imported lazily inside functions; listed so the bundle has them for sure
HIDDEN = ["launcher.setup_ui", "hub.settings_ui", "engine.calibrate", "components.discord.discord_rpc",
          "components.diary.diary_html", "engine.jobread", "components.diary.diary_popup",
          "components.killers.killers", "gamedata.game_refs", "base.internet", "engine.capture", "base.perf",
          "engine.panels", "hub.character", "engine.charname", "engine.vitals", "hub.live",
          "components.skillbar.mpuse", "components.skillbar.feed", "components.skillbar.meter",
          "components.skillbar.skillbar_ui", "hub.tools", "launcher.whats_new",
          "engine.party", "components.party.partylink", "components.party.party_ui",
          "components.training.training", "components.training.training_ui", "components.skillbar.regen",
          "components.training.itemicons", "engine.portrait",
          "components.album.album", "components.album.album_html", "components.album.mobicons",
          "components.diary.diary_server",
          "launcher.compose", "hub.bus", "base.contract", "components",       # composition: the registry loads components by name
          "components.killers.component", "components.diary.component", "components.skillbar.component",
          "components.album.component", "components.party.component", "components.training.component",
          "components.discord.component", "components.discord.settings_page", "components.discord.text",
          "hub.pages", "hub.ui.shell", "hub.ui.overlay", "hub.ui.theme",
          "engine.hudtext", "engine.levelup_shot", "gamedata.translate", "gamedata.parse", "gamedata.maplist",
          "engine.sources.wgc", "engine.sources.screen", "engine.sources.csv_replay", "windows_capture",
          "win32gui", "pystray._win32"]
EXCLUDE = ["cv2",        # only used by windows-capture to save files; replaced by rthook_cv2.py
           "pkg_resources", "setuptools"]   # not used; its runtime hook breaks the build
FORBIDDEN = ["config.json", "helper_settings.json", "glyphs.json", "glyphs_level.json", "glyphs_ui.json", "glyphs_hud.json",
             "party.json", "live.json",
             "minimap_world.png", "minimap_header.png", "nametag.png"]


def make_icon():
    from hub import helper_ui  # noqa: F401
    BUILD.mkdir(exist_ok=True)
    ico = BUILD / "icon.ico"
    from PIL import Image
    Image.open(ROOT / "art" / "icon.png").convert("RGBA").resize((256, 256), Image.LANCZOS).save(
        ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def tesseract_dlls():
    """tesseract.exe plus the DLLs from its folder it loads (directly or through other DLLs)."""
    import pefile
    have = {f.lower(): f for f in os.listdir(TESSERACT_DIR) if f.lower().endswith(".dll")}
    need, todo = set(), ["tesseract.exe"]
    while todo:
        pe = pefile.PE(str(TESSERACT_DIR / todo.pop()), fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            n = e.dll.decode().lower()
            if n in have and n not in need:
                need.add(n)
                todo.append(have[n])
    return sorted(have[n] for n in need)


def copy_tesseract(app_dir):
    out = app_dir / "tesseract"
    (out / "tessdata").mkdir(parents=True, exist_ok=True)
    for f in ["tesseract.exe", *tesseract_dlls()]:
        shutil.copy2(TESSERACT_DIR / f, out / f)
    shutil.copy2(TESSERACT_DIR / "tessdata" / "eng.traineddata", out / "tessdata")
    for f in ("LICENSE", "AUTHORS", "README.md"):
        src = TESSERACT_DIR / "doc" / f
        if src.exists():
            shutil.copy2(src, out / f"TESSERACT_{f}")


def main():
    if not (TESSERACT_DIR / "tesseract.exe").exists():
        sys.exit(f"Tesseract not found in {TESSERACT_DIR} (set TESSERACT_DIR)")
    shutil.rmtree(DIST / NAME, ignore_errors=True)
    ico = make_icon()
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", NAME, "--icon", str(ico),
           "--distpath", str(DIST), "--workpath", str(BUILD / "pyi"), "--specpath", str(BUILD),
           "--paths", str(ROOT)]
    for f in DATA_FILES:
        dest = Path(f).parent.as_posix()            # "." for a root file, else the folder the code reads it from
        cmd += ["--add-data", f"{ROOT / f}{os.pathsep}{dest}"]
    cmd += ["--add-data", f"{ROOT / 'packaging' / 'THIRD_PARTY.txt'}{os.pathsep}."]
    cmd += ["--add-data", f"{ROOT / 'art' / 'icon.png'}{os.pathsep}art"]      # tray icon (original art)
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for x in EXCLUDE:
        cmd += ["--exclude-module", x]
    cmd += ["--runtime-hook", str(ROOT / "packaging" / "rthook_cv2.py")]
    cmd.append(str(ROOT / "companion.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)

    top = DIST / NAME
    app_dir = top / "_internal"            # PyInstaller 6: sys._MEIPASS for one-folder builds
    copy_tesseract(app_dir)
    shutil.copy2(ROOT / "packaging" / "START_HERE.txt", top / "START HERE.txt")

    bad = [p for p in top.rglob("*") if p.name in FORBIDDEN or "game_icons" in p.parts
           or "item_icons" in p.parts]
    if bad:
        sys.exit(f"refusing to package player/game files: {bad}")

    zpath = DIST / f"{NAME}-{VERSION}-win64.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(top.rglob("*")):
            if p.is_file():
                z.write(p, Path(NAME) / p.relative_to(top))
    import hashlib
    h = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (zpath.parent / (zpath.name + ".sha256")).write_text(f"{h}  {zpath.name}\n", encoding="ascii")
    print(f"built {top}\nzip   {zpath} ({zpath.stat().st_size / 1e6:.1f} MB)\nsha256 {h}")


if __name__ == "__main__":
    main()
