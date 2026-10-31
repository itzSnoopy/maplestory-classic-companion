"""Release checks: everything that must pass before a build is published.

    python dev/release_check.py            code checks (compile, layers, data files, privacy, simulations)
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
os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")   # simulations never go online (party relay, updates)

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


def check_layers():
    """The layer rules (dev/check_layers.py, plan 09): no import against them."""
    r = run([sys.executable, str(ROOT / "dev" / "check_layers.py"), "--strict"], timeout=120)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith(("TOTAL", "hub.", "components.", "engine.", "gamedata.", "base.", "launcher."))
             or "->" in ln]
    check("import layers hold (check_layers --strict)", r.returncode == 0, "; ".join(x.strip() for x in lines[:4]))


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


def check_pq_data():
    pq = json.loads((ROOT / "gamedata" / "pq_classic.json").read_text(encoding="utf-8"))["pqs"]
    names = {m["name"] for m in json.loads((ROOT / "gamedata" / "maps_classic.json").read_text(encoding="utf-8"))["maps"]}
    used = set()
    for p in pq:
        used |= {p["first"], p["bonus"], *p.get("other", []), *(s["map"] for s in p["stages"])}
    missing = sorted(used - names)
    check("pq_classic.json: every map name is in maps_classic.json", not missing, ", ".join(missing))


def check_skill_kinds():
    try:
        sk = json.loads((ROOT / "gamedata" / "skills_classic.json").read_text(encoding="utf-8"))["skills"]
        kd = json.loads((ROOT / "gamedata" / "skill_kinds_classic.json").read_text(encoding="utf-8"))["kinds"]
    except (OSError, ValueError, KeyError) as e:
        return check("skill kinds cover every skill", False, str(e))
    bad = [f"missing {i}" for i in sk if i not in kd] + [f"extra {i}" for i in kd if i not in sk]
    for i, v in kd.items():
        if v.get("kind") not in ("attack", "buff", "passive", "other"):
            bad.append(f"{i} kind {v.get('kind')}")
        d = v.get("duration_s")
        if d is not None and i in sk and len(d) != sk[i]["max"]:
            bad.append(f"{i} duration_s length")
    r = run([sys.executable, "dev/make_skill_kinds.py", "--check"])
    if r.returncode:
        bad.append("file differs from dev/make_skill_kinds.py (edit OVERRIDES, not the json)")
    check("skill kinds: every skill has one, valid, matches the generator", not bad, "; ".join(bad[:5]))


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
from gamedata import translate
ok, msg = calibrate.check_typed(c, cfg, '78[84.78%]', '4', translate.exp_needed); assert ok, msg
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
from engine.sources.screen import ImageFrames
from launcher import compose
from PIL import Image
out = {{}}
for f in {list(WINDOW_EXPECT)!r}:
    src = compose.screen_source(cfg, ImageFrames([Image.open(r'{wins}/' + f).convert('RGB')]))
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


def check_kpq():
    """KPQ run summary: a synthetic log (town -> stages -> bonus with a level-up and one garbled map
    reading -> town) replayed through the composed hub must give exactly these stage + bonus EXP."""
    import csv
    from datetime import datetime, timedelta
    P = "1st Accompaniment <"
    seq = [("Kerning City", 50000, 26)] * 2         + [(P + "1st Stage>", c, 26) for c in (50100, 50200, 50300)]         + [(P + "2nd Stage>", c, 26) for c in (50500, 50700)]         + [(P + "Last Stage>", c, 26) for c in (51000, 51300)]         + [(P + "Bonus>", 52300, 26), (P + "Bonus>", 53300, 26), (P + "Bonus>", 54300, 26),
           (P + "Bonus>", 400, 27), (P + "Bonus>", 1400, 27), ("Boaus Qxv", 2400, 27),
           (P + "Bonus>", 3400, 27)]         + [("Kerning City", 3500, 27)] * 3
    out = TMP / "sim_kpq"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    path = out / "exp_log.csv"
    t0 = datetime(2026, 10, 30, 20, 0, 0)
    cols = "time,event,map,level,exp_current,exp_needed,exp_pct,exp_delta,exp_gained_total,eta_s,interval_s,source,raw".split(",")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i, (m, cur, lv) in enumerate(seq):
            need = 54900 if lv == 26 else 60000
            w.writerow([(t0 + timedelta(seconds=30 * i)).isoformat(timespec="milliseconds"), "", m, lv, cur,
                        need, round(cur / need * 100, 2), 0, 0, "", 30, "csv", ""])
    stage, bonus = 52300 - 50100, (54900 + 3500) - 52300
    r = run([sys.executable, "dev/kpq_sim.py", str(path), str(out / "data"), "--expect", f"{stage}+{bonus}"])
    ok = r.returncode == 0 and "EXPECT OK" in r.stdout
    check("KPQ run summary matches the synthetic log", ok, "" if ok else (r.stderr + r.stdout)[-400:])


def check_kpq_logic():
    """kpq.Runs on readings 5 s apart: the completion reward (+1,000 five seconds after entering the
    bonus) goes to the Last stage, the bonus rate waits for 10 kills, and the view clears only on EXP
    gained on a later non-PQ reading (not on the tail reading that carries the last kills)."""
    from components.kpq import kpq
    P = "1st Accompaniment <"
    bonus = P + "Bonus>"
    R = kpq.Runs(avg={bonus: 36.0})
    feeds = [(0, "Kerning City", 0), (10, P + "1st Stage>", 100), (15, P + "2nd Stage>", 300),
             (20, P + "Last Stage>", 600), (25, bonus, 900), (30, bonus, 1900), (35, bonus, 2080)]
    for t, m, e in feeds:
        R.feed(t, m, e)
    v5 = R.view()
    R.feed(40, bonus, 2260)
    v10 = R.view()
    R.on_left("Kerning City")
    rec = R.feed(45, "Kerning City", 2300)                 # the tail reading: closes the run, keeps the view
    after_tail = R.view()
    R.feed(50, "Kerning City", 2300)
    still = R.view()
    R.feed(55, "Kerning City", 2350)                       # EXP outside the PQ
    ok = (v5["bonus"]["kills"] == 5 and v5["bonus"]["rate"] is None
          and v10["bonus"]["kills"] == 10 and v10["bonus"]["rate"]
          and rec and rec["stage_exp"] == 1800 and rec["bonus_exp"] == 400 and rec["bonus_kills"] == 11
          and rec["stages"][-1]["exp"] == 1300 and rec["stages"][-1]["state"] == "done"
          and after_tail and after_tail["done"] and still and R.view() is None)
    check("KPQ reward / 10-kill gate / view lifecycle", bool(ok), f"{rec} {v5} {v10}")


def check_kpq_best():
    """kpq best records: FASTEST per cleared stage and per completed run (bonus not counted); only the last
    3 runs are kept but the best survives the trim; a file without "best" gets it computed from its runs."""
    from components.kpq import kpq
    P = "1st Accompaniment <"
    R = kpq.Runs(avg={P + "Bonus>": 36.0})
    recs, t, exp = [], 0, 0

    def play(d, completed=True):
        nonlocal t, exp
        t, exp = t + 600, exp + 100
        R.feed(t, "Kerning City", exp)
        steps = [(P + "1st Stage>", d[0]), (P + "2nd Stage>", d[1]), (P + "Last Stage>", d[2])]
        for m, dur in steps:
            R.feed(t, m, exp)
            t, exp = t + dur, exp + 100
        if completed:
            R.feed(t, P + "Bonus>", exp)
            t, exp = t + 30, exp + 400
            R.feed(t, P + "Bonus>", exp)
        R.on_left("Kerning City")
        recs.append(R.feed(t + 5, "Kerning City", exp))
    play((60, 40, 100))
    play((50, 50, 120))
    play((70, 30, 60), completed=False)     # left in the last stage: Stage 2 counts, King Slime and the run don't
    play((55, 45, 90))
    play((80, 80, 200))
    b = R.best["kpq"]
    st = {k: v["minutes"] for k, v in b["stages"].items()}
    ok = (st == {"Stage 1": round(50 / 60, 2), "Stage 2": round(30 / 60, 2), "King Slime": round(90 / 60, 2)}
          and b["run"]["stage_minutes"] == round(190 / 60, 2) and len(R.runs) == 3
          and all(r["reason"] == "left" for r in recs))
    M = kpq.Runs(recs)                       # migration: five runs, no best
    ok = ok and M.best == R.best and len(M.runs) == 3 and M.runs[-1]["start"] == recs[-1]["start"]
    check("KPQ best = fastest per stage / completed run, last 3 kept, best computed on load", bool(ok),
          f"{R.best} {len(R.runs)}")


def check_relay():
    r = run([sys.executable, str(ROOT / "relay" / "test_relay.py")], env={"MAPLE_COMPANION_OFFLINE": "1"}, timeout=120)
    check("relay tests pass (relay/test_relay.py)", r.returncode == 0, (r.stderr or r.stdout)[-300:])
    r = run([sys.executable, str(ROOT / "dev" / "feedback_sim.py"), str(TMP / "feedback_sim")],
            env={"MAPLE_COMPANION_OFFLINE": "1"}, timeout=180)
    check("skill feedback sim (page -> relay -> dev tool)", r.returncode == 0, (r.stdout or r.stderr)[-300:])


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


def check_buffs():
    """Buff icon reader on the local burst frames (skips without them) + the skill bar's deferred cast decision."""
    sys.path.insert(0, str(ROOT / "dev"))
    import buffs_check
    ok, note, skipped = buffs_check.run_frames()
    check("buff icons: Magic Guard found exactly when on in the local burst frames"
          + (" (SKIPPED: no frames/icons here)" if skipped else ""), ok, note)
    ok, note, _ = buffs_check.run_logic()
    check("skill bar: ambiguous 8-MP cast decided by the buff icon flip", ok, note)


def main():
    TMP.mkdir(parents=True, exist_ok=True)
    check_compile()
    check_layers()
    check_data_files()
    check_skill_kinds()
    check_pq_data()
    check_privacy()
    if ensure_fixture():
        check_sim_regular()
        check_sim_fresh()
        check_fresh_windows()
    check_killers()
    check_buffs()
    check_kpq()
    check_kpq_logic()
    check_kpq_best()
    check_relay()
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
