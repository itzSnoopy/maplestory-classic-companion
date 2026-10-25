"""dev/testbuild.py - build the app and install it on THIS PC only, as a local test version.

  python dev/testbuild.py            build + install the next test version (e.g. 0.6.90, 0.6.91)
  python dev/testbuild.py --no-build install the zip already in dist/ for the next number
  python dev/testbuild.py --dry-run  only print what it would do

Nothing goes to GitHub. The version is <last release M.m>.<90 + n> (0.6.4 -> 0.6.90, 0.6.91 ...),
built with version.TEST_BASE = the last release, and installed like an update:
%LOCALAPPDATA%\\Programs\\MapleClassicCompanion\\versions\\<ver> (+ TESTBUILD.txt naming the base),
then `latest` points at it. updater.rank orders a test build just above its base, so the next
real release (0.6.5) still replaces it on this PC, and a running helper from a test build picks
the new one up by itself (Updater.check_local: restarts when you're not playing, or now from the
tray "Update" item). A helper from a release before this existed (0.6.4) needs a restart by hand.

version.py is rewritten only for the build and restored right after (also on errors).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import updater as U  # noqa: E402

VERSION_PY = ROOT / "version.py"
COUNTER = ROOT / "build" / "testbuilds.json"      # {base: last n} (build/ is git-ignored)
FIRST = 90


def base_release():
    m = re.search(r'^VERSION = "([^"]+)"', VERSION_PY.read_text(encoding="utf-8"), re.M)
    v = U.parse_version(m.group(1)) if m else None
    if not v or v[2] >= FIRST:
        sys.exit(f"version.py should hold the last release, not {m and m.group(1)}")
    return v


def next_version(base):
    used = [v[2] for v in U.installed() if v[:2] == base[:2] and v[2] >= FIRST
            and U.test_base(v) == base]
    try:
        last = json.loads(COUNTER.read_text(encoding="utf-8")).get(U.fmt(base))
    except (OSError, ValueError):
        last = None
    n = max(used + ([last] if last is not None else []) + [FIRST - 1]) + 1
    return (base[0], base[1], n)


def build(ver, base):
    orig = VERSION_PY.read_bytes()
    text = orig.decode("utf-8")
    text = re.sub(r'^VERSION = "[^"]+"', f'VERSION = "{U.fmt(ver)}"', text, flags=re.M)
    text = re.sub(r"^TEST_BASE = .*$", f'TEST_BASE = "{U.fmt(base)}"    # local test build',
                  text, flags=re.M)
    try:
        VERSION_PY.write_text(text, encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "packaging" / "build.py")], check=True, cwd=ROOT)
    finally:
        VERSION_PY.write_bytes(orig)


def install(ver, base):
    zpath = ROOT / "dist" / f"{U.NAME}-{U.fmt(ver)}-win64.zip"
    if not zpath.exists():
        sys.exit(f"no {zpath.name} in dist/ (build first)")
    vdir = U.versions_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    dest, tmp = vdir / U.fmt(ver), vdir / (U.fmt(ver) + ".tmp")
    if dest.exists():
        sys.exit(f"{dest} already exists")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    U._unpack(zpath, tmp)
    if not (tmp / U.EXE).exists():
        shutil.rmtree(tmp, ignore_errors=True)
        sys.exit(f"{zpath.name} has no {U.EXE}")
    (tmp / U.TEST_MARK).write_text(U.fmt(base), encoding="ascii")
    import os
    os.replace(tmp, dest)
    U.log(f"installed local test build {U.fmt(ver)} (on {U.fmt(base)}) from dev/testbuild.py")
    U.set_latest(dest)
    U.cleanup()
    COUNTER.parent.mkdir(parents=True, exist_ok=True)
    try:
        c = json.loads(COUNTER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    c[U.fmt(base)] = ver[2]
    COUNTER.write_text(json.dumps(c, indent=1), encoding="utf-8")
    return dest


def main():
    args = sys.argv[1:]
    base = base_release()
    ver = next_version(base)
    lv = U.latest_version()
    print(f"last release {U.fmt(base)} -> test build {U.fmt(ver)}; installed now: "
          f"{', '.join(U.fmt(v) for v in sorted(U.installed(), key=U.rank)) or 'none'}; "
          f"latest -> {U.fmt(lv) if lv else 'none'}")
    if "--dry-run" in args:
        return
    if "--no-build" not in args:
        build(ver, base)
    dest = install(ver, base)
    print(f"installed {dest}\nlatest -> {U.fmt(U.latest_version())}; kept: "
          f"{', '.join(U.fmt(v) for v in sorted(U.installed(), key=U.rank))}")
    print("A running helper from a test build switches by itself when you're not playing "
          "(or now: tray > Update). From 0.6.4 or older: exit it from the tray and start it again "
          "from the desktop shortcut.")


if __name__ == "__main__":
    main()
