"""Build the Windows release: dist/MapleClassicCompanion/ and a zip of it.

    python packaging/build.py

Needs: pip install -r requirements.txt pyinstaller, and Tesseract (UB Mannheim) installed at
TESSERACT_DIR (only tesseract.exe, the DLLs it loads, English data and its license are copied).

What goes in: the code, maps_classic.json, exp_table_classic.json, layouts.json,
config.example.json. What never goes in: anything from the player's data (config.json,
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
from version import VERSION  # noqa: E402

NAME = "MapleClassicCompanion"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
TESSERACT_DIR = Path(os.environ.get("TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
DATA_FILES = ["maps_classic.json", "exp_table_classic.json", "layouts.json", "config.example.json",
              "map_mobs.json", "killers.json", "captions.json", "important_maps.json",
              "game_refs.json"]
# imported lazily inside functions; listed so the bundle has them for sure
HIDDEN = ["setup_ui", "settings_ui", "calibrate", "discord_rpc", "diary_html", "jobread",
          "diary_popup", "killers", "game_refs",
          "sources.wgc", "sources.screen", "sources.csv_replay", "windows_capture", "win32gui",
          "pystray._win32"]
EXCLUDE = ["cv2",        # only used by windows-capture to save files; replaced by rthook_cv2.py
           "pkg_resources", "setuptools"]   # not used; its runtime hook breaks the build
FORBIDDEN = ["config.json", "helper_settings.json", "glyphs.json", "glyphs_level.json",
             "minimap_world.png", "minimap_header.png", "nametag.png", "icon.png"]


def make_icon():
    import helper_ui
    BUILD.mkdir(exist_ok=True)
    ico = BUILD / "icon.ico"
    helper_ui.make_icon_image(helper_ui.GREEN, 256).save(
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
        cmd += ["--add-data", f"{ROOT / f}{os.pathsep}."]
    cmd += ["--add-data", f"{ROOT / 'packaging' / 'THIRD_PARTY.txt'}{os.pathsep}."]
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

    bad = [p for p in top.rglob("*") if p.name in FORBIDDEN or "game_icons" in p.parts]
    if bad:
        sys.exit(f"refusing to package player/game files: {bad}")

    zpath = DIST / f"{NAME}-{VERSION}-win64.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(top.rglob("*")):
            if p.is_file():
                z.write(p, Path(NAME) / p.relative_to(top))
    print(f"built {top}\nzip   {zpath} ({zpath.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
