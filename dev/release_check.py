"""Release checks: everything that must pass before a build is published.

    python dev/release_check.py            code checks (compile, data files, privacy, simulations)
    python dev/release_check.py --build    code checks, then build, audit the zip, smoke-test the exe

Never touches the real history/, journal.md, sessions/ or %LOCALAPPDATA%: simulations and the exe
run against throwaway folders in %TEMP%\\maple_release_check. Prints PASS/FAIL per check and exits 1
on any failure. Used by the /release skill (.claude/skills/release/SKILL.md).
"""
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.gettempdir()) / "maple_release_check"
FIXTURE = ROOT / "dev" / "fixtures" / "levelup_lv5"
ORIG_FIXTURE = ROOT / "sessions" / "20261023_021038" / "levelups" / "Lv5_021225"
EXPECT_REGULAR = "Lv 4 84.78% -> Lv 5 20.74%, +42 EXP, 1 level-up(s)"   # svc_sim baseline
# player/local files that must never be committed or packaged
PRIVATE = re.compile(r"(^|/)(config\.json|helper_settings\.json|glyphs(_level|_ui|_hud)?\.json|crash\.log)$|"
                     r"^(digest\.json|status\.json|journal\.md|diary\.(md|html)|album\.html)$|(^|/)(history|sessions|"
                     r"screenshots|templates|assets|crops|glyphs|game_icons|dev/fixtures|build|dist)/|"
                     r"\.(png|jpe?g|gif|bmp|log|zip|exe)$", re.I)
# never inside the release zip (game imagery, player data, third-party art)
ZIP_FORBIDDEN = re.compile(r"(^|/)(config\.json|helper_settings\.json|party\.json|live\.json|glyphs(_level|_ui|_hud)?\.json|"
                           r"minimap_(world|header)\.png|nametag\.png|crash\.log|"
                           r"captions_custom\.json|placeholder_source\.jpg)$|(^|/)(history|sessions|"
                           r"screenshots|templates|assets|crops|glyphs|game_icons|fixtures)/|\.jpe?g$", re.I)
ALLOWED_IMAGES ={"discord_icon.png", "art/icon.png"}     # original art from the player's artist friend
CODE_DIRS = {"base", "launcher", "engine", "gamedata", "hub", "components"}
# root JSON files that are the player's own (never shipped)
LOCAL_JSON = {"config.json", "helper_settings.json", "glyphs.json", "glyphs_level.json", "glyphs_ui.json", "glyphs_hud.json", "live.json", "party.json",
              "digest.json", "status.json"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def run(cmd, env=None, timeout=600):
    e = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=ROOT, env=e, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# ------------------------------------------------------------------ code checks
def check_compile():
    bad = []
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in ("build", "dist", "__pycache__", ".git") for part in p.relative_to(ROOT).parts):
            continue
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f"{p.relative_to(ROOT)}: {e.msg.strip().splitlines()[-1]}")
    check("all Python files compile", not bad, "; ".join(bad))


def check_data_files():
    sys.path.insert(0, str(ROOT / "packaging"))
    import build
    shipped = set(build.DATA_FILES)
    missing = [f for f in shipped if not (ROOT / f).exists()]
    tracked = run(["git", "ls-files", "*.json"]).stdout.split()
    # every tracked .json that is code-side data: root files and the layer folders (not docs/, dev/, art/, ...)
    layer_json = {f for f in tracked if "/" not in f or f.split("/")[0] in CODE_DIRS}
    unshipped = sorted(layer_json - shipped - LOCAL_JSON)
    check("build DATA_FILES exist", not missing, ", ".join(missing))
    check("every tracked code-side .json is shipped or known-local", not unshipped,
          "add to packaging/build.py DATA_FILES or LOCAL_JSON here: " + ", ".join(unshipped))


def check_privacy():
    files = set(run(["git", "ls-files"]).stdout.split())
    files |= set(run(["git", "diff", "--cached", "--name-only"]).stdout.split())
    files |= set(run(["git", "ls-files", "--others", "--exclude-standard"]).stdout.split())
    # docs/img/ = app-only screenshots (anonymized, no game imagery); review new ones by eye
    bad = sorted(f for f in files if PRIVATE.search(f) and f not in ALLOWED_IMAGES
                 and not re.fullmatch(r"docs/img/[\w-]+\.png", f))
    check("nothing private/game-derived tracked or about to be", not bad, ", ".join(bad[:10]))
    try:   # any password left in a local config.json must never land in a tracked file
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        secrets = [v for k, v in _walk(cfg) if "password" in k.lower() and isinstance(v, str) and v]
    except Exception:
        secrets = []
    if secrets:
        hits = [f for f in files if (ROOT / f).is_file() and f != "config.json"
                and any(s in (ROOT / f).read_text(encoding="utf-8", errors="ignore") for s in secrets)]
        check("no local password appears in a tracked file", not hits, ", ".join(hits))


def _walk(d, key=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _walk(v, k)
    else:
        yield key, d


def ensure_fixture():
    if not FIXTURE.exists() and ORIG_FIXTURE.exists():
        FIXTURE.mkdir(parents=True)
        for p in ORIG_FIXTURE.glob("*.png"):
            shutil.copy2(p, FIXTURE / p.name)
    return check("test frames available (dev/fixtures)", FIXTURE.exists() and any(FIXTURE.glob("*.png")),
                 "need sessions/20261023_021038/levelups/Lv5_021225 or dev/fixtures/levelup_lv5")


def check_sim_regular():
    out = TMP / "sim_regular"
    shutil.rmtree(out, ignore_errors=True)
    r = run([sys.executable, "dev/svc_sim.py", str(out), "frames"], {"SVC_SIM_FIXTURE": str(FIXTURE)})
    ok = EXPECT_REGULAR in r.stdout and "Traceback" not in r.stdout + r.stderr
    check("simulation with your data matches the baseline", ok,
          "" if ok else (r.stderr or r.stdout)[-400:])


def check_sim_fresh():
    """Fresh install: calibrate from the first frame like the setup window, then simulate."""
    from engine import glyphs
    from base import paths
    data = TMP / "fresh_data"
    out = TMP / "sim_fresh"
    for d in (data, out):
        shutil.rmtree(d, ignore_errors=True)
    first = sorted(FIXTURE.glob("before_*.png"))[0]
    code = f"""
import sys; sys.path.insert(0, r'{ROOT}')
from PIL import Image
from engine import calibrate
from base import paths
cfg = paths.load_config(); im = Image.open(r'{first}').convert('RGB')
r = calibrate.cut_templates(im); assert r['world'] and r['header'], r
c = calibrate.hud_crops(im, cfg)
ok, msg = calibrate.check_typed(c, cfg, '78[84.78%]', '4'); assert ok, msg
calibrate.learn_typed(c, cfg, '78[84.78%]', '4')
"""
    r = run([sys.executable, "-c", code], {"MAPLE_COMPANION_DATA": str(data)})
    if not check("setup calibration on a fresh folder", r.returncode == 0, r.stderr[-300:]):
        return
    r = run([sys.executable, "dev/svc_sim.py", str(out), "frames"],
            {"MAPLE_COMPANION_DATA": str(data), "SVC_SIM_FIXTURE": str(FIXTURE)})
    real = json.loads((paths.DATA / "glyphs.json").read_text(encoding="utf-8")) \
        if (paths.DATA / "glyphs.json").exists() else {}
    new = json.loads((data / "glyphs.json").read_text(encoding="utf-8"))
    wrong = [(v, real.get(k)) for k, v in new.items() if k in real and real[k] != v]
    learned = sorted(set(new.values()))
    check("fresh install learns no wrong digits", not wrong and "Traceback" not in r.stdout + r.stderr,
          f"learned {''.join(learned)}" + (f"; wrong {wrong}" if wrong else ""))


# what the window fixtures show (read by hand 2026-10-23, NOTES 20:40): file -> expected reads
WINDOW_EXPECT = {
    "lv12_stat.png": {"vitals": "349/376 487/507", "stat": "12 349/376 487/507 4 4 62 5 5"},
    "allcommitted_ch31.png": {"vitals": "408/408 606/606", "stat": "14 408/408 606/606 4 4 77 5 0",
                              "skills": "5 1 0 0 1 6"},
}


def check_fresh_windows():
    """Fresh install that knows only its EXP digits (2026-10-24 bug: the skill bar never showed
    on a new install): HP/MP must read from the first frame, and one Stat window must teach every
    window digit, so stats and skill levels read too."""
    wins = ROOT / "dev" / "fixtures" / "windows"
    if not check("window test frames available (dev/fixtures/windows)",
                 all((wins / f).exists() for f in WINDOW_EXPECT), str(wins)):
        return
    data = TMP / "fresh_windows"
    shutil.rmtree(data, ignore_errors=True)
    shutil.copytree(TMP / "fresh_data", data)
    for f in ("glyphs_hud.json", "glyphs_ui.json"):
        (data / f).unlink(missing_ok=True)
    # a fresh install learns the EXP digits within minutes of play; give it the full set
    from base import paths
    exp = json.loads((paths.DATA / "glyphs.json").read_text(encoding="utf-8"))
    (data / "glyphs.json").write_text(json.dumps(exp), encoding="utf-8")
    code = f"""
import sys, json; sys.path.insert(0, r'{ROOT}')
from base import paths
from engine import ocr
cfg = paths.load_config(); ocr.setup(cfg.get('tesseract'))
from engine.sources.screen import ScreenSource, ImageFrames
from PIL import Image
out = {{}}
for f in {list(WINDOW_EXPECT)!r}:
    src = ScreenSource(cfg, ImageFrames([Image.open(r'{wins}/' + f).convert('RGB')]))
    src.read({{}})
    got = {{}}
    for t, k, v in src.take_facts():
        if k == 'vitals':
            got['vitals'] = ' '.join('%d/%d' % tuple(v[x]) for x in ('hp', 'mp'))
        elif k == 'stat':
            got['stat'] = ' '.join(str(v['raw'].get(x)) for x in
                                   ('level', 'hp', 'mp', 'str', 'dex', 'int', 'luk', 'ap'))
        elif k == 'skills':
            got['skills'] = ' '.join(str(s.get('level')) for s in v.get('skills') or [])
    out[f] = got
print('RESULT ' + json.dumps(out))
"""
    r = run([sys.executable, "-c", code], {"MAPLE_COMPANION_DATA": str(data)})
    m = re.search(r"^RESULT (.+)$", r.stdout, re.M)
    got = json.loads(m.group(1)) if m else {}
    bad = [f"{f} {k}: {got.get(f, {}).get(k)!r} != {v!r}" for f, exp_ in WINDOW_EXPECT.items()
           for k, v in exp_.items() if got.get(f, {}).get(k) != v]
    check("fresh install reads HP/MP and the game windows", m and not bad,
          "; ".join(bad) if m else (r.stderr or r.stdout)[-300:])


def check_killers():
    r = run([sys.executable, "-m", "components.killers.killers", "audit"])
    check("killers audit runs", r.returncode == 0 and "Traceback" not in r.stderr, r.stderr[-200:])


# ------------------------------------------------------------------ build checks
def do_build():
    r = run([sys.executable, "packaging/build.py"], timeout=1200)
    m = re.search(r"^zip\s+(.+\.zip)", r.stdout, re.M)
    check("build succeeds", r.returncode == 0 and m, (r.stderr or r.stdout)[-400:] if not m else "")
    return Path(m.group(1).split(" (")[0]) if m else None


def check_zip(zpath):
    names = zipfile.ZipFile(zpath).namelist()
    rels = [n.split("/", 1)[1] for n in names if "/" in n]
    bad = [r for r in rels if ZIP_FORBIDDEN.search(r)]
    top = sorted({r.split("/")[0] for r in rels if r})
    check("zip holds no player/game files", not bad, ", ".join(bad[:8]))
    check("zip top level is exe + START HERE + _internal",
          set(top) == {"MapleClassicCompanion.exe", "START HERE.txt", "_internal"}, ", ".join(top))
    mb = zpath.stat().st_size / 1e6
    check("zip size sane (< 80 MB)", mb < 80, f"{mb:.1f} MB")
    import hashlib
    sha = zpath.with_name(zpath.name + ".sha256")
    want = sha.read_text(encoding="ascii").split()[0] if sha.exists() else ""
    check("zip has a matching .sha256 (the updater needs it)",
          want == hashlib.sha256(zpath.read_bytes()).hexdigest(), str(sha.name))


def _pids():
    out = run(["tasklist", "/FI", "IMAGENAME eq MapleClassicCompanion.exe", "/FO", "CSV", "/NH"]).stdout
    return {int(l.split('","')[1]) for l in out.strip().splitlines() if l.startswith('"')}


def check_exe():
    exe = ROOT / "dist" / "MapleClassicCompanion" / "MapleClassicCompanion.exe"
    # tracker: starts, picks window capture, no crash
    data = TMP / "exe_tracker"
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    # the exe installs itself on start (updater.adopt): keep that in TMP, never the real
    # %LOCALAPPDATA%\Programs, and keep the run on this copy (--no-handoff)
    env = {**os.environ, "MAPLE_COMPANION_DATA": str(data),
           "MAPLE_COMPANION_PROGRAMS": str(TMP / "programs")}
    before = _pids()
    p = subprocess.Popen([str(exe), "--service", "--no-handoff"], env=env)
    time.sleep(15)
    (data / "stop").touch()
    try:
        p.wait(20)
    except subprocess.TimeoutExpired:
        p.kill()
    log = (data / "tracker_live.log").read_text(encoding="utf-8") if (data / "tracker_live.log").exists() else ""
    check("exe tracker starts (window capture, Tesseract found)",
          "tracker service started" in log and "source: wgc" in log and "Tesseract not available" not in log
          and not (data / "crash.log").exists(), log[-300:] or "no log")
    # helper: setup window opens on an empty folder (placed on the left monitor)
    data = TMP / "exe_helper"
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    env["MAPLE_COMPANION_DATA"] = str(data)
    p = subprocess.Popen([str(exe), "--at=-2400,40", "--no-handoff"], env=env)
    time.sleep(8)
    titles = []
    try:
        import win32gui
        import win32process
        win32gui.EnumWindows(lambda h, a: a.append(win32gui.GetWindowText(h))
                             if win32gui.IsWindowVisible(h)
                             and win32process.GetWindowThreadProcessId(h)[1] == p.pid else None, titles)
    finally:
        for pid in _pids() - before:         # only the copies this check started
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    check("exe helper opens the setup window on first run",
          "Maple Classic Companion setup" in titles and "Maple helper" in titles
          and not (data / "crash.log").exists(), ", ".join(titles))


def main():
    TMP.mkdir(parents=True, exist_ok=True)
    check_compile()
    check_data_files()
    check_privacy()
    if ensure_fixture():
        check_sim_regular()
        check_sim_fresh()
        check_fresh_windows()
    check_killers()
    if "--build" in sys.argv and all(ok for _, ok in results):
        z = do_build()
        if z:
            check_zip(z)
            check_exe()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
