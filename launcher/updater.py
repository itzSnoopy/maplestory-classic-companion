"""updater.py - keeps the packaged app up to date from GitHub releases, in the background.

Only the packaged build updates (paths.FROZEN); running from source never does.

Layout (per user, no admin):
    %LOCALAPPDATA%\\Programs\\MapleClassicCompanion\\
        versions\\0.2.0\\   versions\\0.2.1\\ ...   one unpacked build each (MapleClassicCompanion.exe)
        latest              junction -> the newest installed version
Player data stays in %LOCALAPPDATA%\\MapleClassicCompanion (paths.DATA), shared by all versions.

1. Start (`adopt`, from companion.py): a copy started from anywhere else (the unzipped download)
   installs itself into versions\\<its version> if missing, becomes `latest` if it's newer, then
   hands off to latest\\MapleClassicCompanion.exe and exits. So any old copy or shortcut starts
   the newest version.
2. Background (`Updater`, a thread in the helper): reads the repo's releases feed
   (github.com/<repo>/releases.atom: includes pre-releases, no API rate limit, answers 304 when
   nothing changed). 30 s after start, then each quiet check doubles the wait up to 5 min
   (Settings > General > Updates; helper_settings.json "updates").
   Only while Settings > Internet allows it (internet.py: master switch + "Automatic updates").
3. New version: download the zip and its .sha256 from the release, check the hash, unpack into
   versions\\<ver>.tmp, rename to versions\\<ver>, point `latest` at it (a junction can be re-pointed
   while the old version still runs). A release without a .sha256 is never installed.
4. The helper restarts into it when the tracker isn't in a play session (or right away from the
   tray). Older versions are deleted, keeping the newest two (a version still running is skipped).

Local test builds (dev/testbuild.py, /testbuild skill): versions\\<M.m.9x> with a TESTBUILD.txt
naming the release it's built on (version.TEST_BASE inside the build). Versions are ordered by
`rank`: release first, a test build just above its base. So 0.6.90 (on 0.6.4) beats 0.6.4 and
its own older test builds, and any newer release (0.6.5) replaces it. A test build installed
while the helper runs is picked up like a downloaded update (Updater.check_local).

Network: only github.com (feed, release assets) and GitHub's asset download host, plain HTTPS GETs.
Nothing about the player is sent. Tests: MAPLE_COMPANION_PROGRAMS (install folder) and
MAPLE_COMPANION_UPDATE_BASE (a local fake of https://github.com/<repo>) override the defaults.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "itzSnoopy/maplestory-classic-companion"
NAME = "MapleClassicCompanion"
EXE = NAME + ".exe"
KEEP = 2                                    # installed versions kept (newest first)
LOCAL_S = 15                                # look for a newer local version folder this often
DEFAULTS = {"min_s": 30, "max_s": 300}
UA = "MapleClassicCompanion-updater"


def log(msg):
    try:
        from base.paths import DATA
        with open(DATA / "updater.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


# ------------------------------------------------------------------ versions and places
def parse_version(s):
    """'v0.2.0' / '0.2.0' -> (0, 2, 0); anything else -> None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(s).strip())
    return tuple(int(x) for x in m.groups()) if m else None


def fmt(v):
    return ".".join(map(str, v))


def current():
    from base.version import VERSION
    return parse_version(VERSION)


TEST_MARK = "TESTBUILD.txt"


def test_base(v=None, folder=None):
    """The release a local test build is based on, None for a release. No arguments = this
    process (version.TEST_BASE); else the installed folder versions\\<v> (its TESTBUILD.txt)."""
    if v is None and folder is None:
        try:
            from base.version import TEST_BASE
        except ImportError:
            return None
        return parse_version(TEST_BASE) if TEST_BASE else None
    p = Path(folder) if folder else versions_dir() / fmt(v)
    try:
        return parse_version((p / TEST_MARK).read_text(encoding="ascii").strip())
    except OSError:
        return None


def rank(v, folder=None, me=False):
    """Sort key: (release, 0, v) for a release, (its base, 1, v) for a test build; None -> None.
    me=True: v is this process's own version."""
    if v is None:
        return None
    b = test_base() if me else test_base(v, folder)
    return (b, 1, v) if b else (v, 0, v)


def newest(*ranks):
    rs = [r for r in ranks if r]
    return max(rs) if rs else None


def root():
    env = os.environ.get("MAPLE_COMPANION_PROGRAMS")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / NAME


def base_url():
    return os.environ.get("MAPLE_COMPANION_UPDATE_BASE", f"https://github.com/{REPO}").rstrip("/")


def versions_dir():
    return root() / "versions"


def latest_link():
    return root() / "latest"


def installed():
    """{version tuple: folder} of complete installs (folder name is the version, exe inside)."""
    out = {}
    d = versions_dir()
    if d.is_dir():
        for p in d.iterdir():
            v = parse_version(p.name)
            if v and (p / EXE).exists():
                out[v] = p
    return out


def latest_version():
    """Version `latest` points at, or None."""
    link = latest_link()
    try:
        target = os.readlink(link)
    except OSError:
        return None
    if target.startswith("\\\\?\\"):
        target = target[4:]
    return parse_version(Path(target).name) if (Path(target) / EXE).exists() else None


def latest_exe():
    return latest_link() / EXE


def real_latest_exe():
    """latest/<exe> resolved to versions/<ver>/<exe> (what to start), or None."""
    p = Path(os.path.realpath(latest_exe()))
    return p if p.exists() else None


def set_latest(folder):
    """Point `latest` at `folder` (junction: no admin needed). Running copies keep running."""
    import _winapi
    link = latest_link()
    new = link.with_name("latest.new")
    if os.path.lexists(new):
        os.rmdir(new)
    _winapi.CreateJunction(str(Path(folder).resolve()), str(new))
    if os.path.lexists(link):
        os.rmdir(link)                      # removes the junction only, never its target
    os.rename(new, link)
    log(f"latest -> {Path(folder).name}")


def cleanup():
    """Delete older versions, keeping the newest KEEP and the one `latest` points at."""
    keep = set(sorted(installed(), key=rank, reverse=True)[:KEEP])
    keep |= {v for v in (latest_version(), current()) if v}
    for v, p in installed().items():
        if v in keep:
            continue
        old = p.with_name(p.name + ".old")
        try:
            os.rename(p, old)               # fails while a program runs from it: keep it whole
        except OSError:
            continue
        shutil.rmtree(old, ignore_errors=True)
        log(f"removed old version {fmt(v)}")
    for p in list(versions_dir().glob("*.tmp")) + list(versions_dir().glob("*.old")):
        shutil.rmtree(p, ignore_errors=True)


# ------------------------------------------------------------------ 1. start: install + hand off
def adopt(args, frozen=None, exe=None, launch=None):
    """Called first thing in the packaged app. Returns True when this process should exit
    because the newest installed version was started instead. Never raises."""
    try:
        from base.paths import FROZEN
        frozen = FROZEN if frozen is None else frozen
        if not frozen:
            return False
        exe = Path(exe or sys.executable)
        here = exe.parent
        mine = current()
        vdir = versions_dir()
        inside = os.path.realpath(here).lower().startswith(os.path.realpath(vdir).lower() + os.sep)
        if not inside and mine:
            dest = vdir / fmt(mine)
            if not (dest / EXE).exists():               # install this copy
                tmp = vdir / (fmt(mine) + ".tmp")
                shutil.rmtree(tmp, ignore_errors=True)
                vdir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(here, tmp)
                if test_base():
                    (tmp / TEST_MARK).write_text(fmt(test_base()), encoding="ascii")
                os.replace(tmp, dest)
                log(f"installed {fmt(mine)} from {here}")
            lv = latest_version()
            if lv is None or rank(lv) < rank(mine, me=True):
                set_latest(dest)
        # always run from the real folder (versions\<ver>), never through the `latest` junction:
        # PyInstaller re-opens its own exe for late imports, so a process started via `latest`
        # would read the *new* version's files once `latest` is re-pointed, mid-run.
        target = real_latest_exe()
        if target is None:
            return False
        if "--service" not in args:
            ensure_shortcuts()
        if str(target).lower() == os.path.abspath(exe).lower():
            return False                                # we are latest, by its real path
        if "--no-handoff" in args:
            return False
        (launch or _launch)([str(target), *args])
        log(f"handed off to latest ({latest_version() and fmt(latest_version())}) from {exe}")
        return True
    except Exception as e:
        log(f"adopt failed, running this copy: {type(e).__name__}: {e}")
        return False


SHORTCUT = "Maple Classic Companion.lnk"


def ensure_shortcuts():
    """Start menu + desktop shortcuts to latest/MapleClassicCompanion.exe (always the newest
    version), made once per PC: one you delete isn't made again (history/shortcuts.json).
    Runs PowerShell in the background, never delays the start."""
    try:
        if os.environ.get("MAPLE_COMPANION_PROGRAMS"):
            return              # a test install (release_check): never shortcuts to a temp copy
        from base.paths import DATA
        flag = DATA / "history" / "shortcuts.json"
        if flag.exists():
            return
        target = latest_exe()
        if not target.exists():
            return
        q = lambda v: str(v).replace("'", "''")            # PowerShell single-quote escape
        ps = (
            "$s = New-Object -ComObject WScript.Shell; "
            "foreach ($d in @([Environment]::GetFolderPath('Programs'), "
            "[Environment]::GetFolderPath('Desktop'))) { "
            f"$l = $s.CreateShortcut((Join-Path $d '{SHORTCUT}')); "
            f"$l.TargetPath = '{q(target)}'; $l.WorkingDirectory = '{q(target.parent)}'; "
            f"$l.IconLocation = '{q(target)},0'; "
            "$l.Description = 'MapleStory Classic EXP tracker and helper'; $l.Save() }")
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         creationflags=0x08000000, close_fds=True)      # CREATE_NO_WINDOW
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(json.dumps({"made": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "target": str(target)}), encoding="utf-8")
        log(f"shortcuts made (Start menu + desktop) -> {target}")
    except Exception as e:
        log(f"shortcuts not made: {type(e).__name__}: {e}")


def _launch(cmd):
    flags = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=str(Path(cmd[0]).parent), creationflags=flags, close_fds=True)


# ------------------------------------------------------------------ 2.-3. check and install
def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def feed_versions(etag=None):
    """(versions in the releases feed, new etag); ([], etag) unchanged (304)."""
    try:
        r = _get(base_url() + "/releases.atom", {"If-None-Match": etag} if etag else None)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        raise
    body = r.read().decode("utf-8", "replace")
    tags = re.findall(r"/releases/tag/([^\"<>/?#]+)", body)
    vs = sorted({v for v in map(parse_version, tags) if v}, reverse=True)
    return vs, r.headers.get("ETag") or etag


def install(ver, progress=None):
    """Download, verify and unpack release `ver` into versions\\<ver>. Returns the folder."""
    vdir = versions_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    dest = vdir / fmt(ver)
    if (dest / EXE).exists():
        return dest
    zipname = f"{NAME}-{fmt(ver)}-win64.zip"
    url = f"{base_url()}/releases/download/v{fmt(ver)}/{zipname}"
    want = _get(url + ".sha256").read().decode("ascii", "replace").split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        raise ValueError("bad .sha256 file")
    part = vdir / (zipname + ".part")
    h = hashlib.sha256()
    with _get(url, timeout=60) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            if progress:
                progress(f.tell())
    if h.hexdigest() != want:
        part.unlink(missing_ok=True)
        raise ValueError(f"checksum mismatch for {zipname}")
    tmp = vdir / (fmt(ver) + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        _unpack(part, tmp)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        part.unlink(missing_ok=True)
        raise
    part.unlink(missing_ok=True)
    if not (tmp / EXE).exists():
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError(f"{zipname} has no {EXE}")
    os.replace(tmp, dest)
    log(f"installed {fmt(ver)} (sha256 ok)")
    return dest


def _unpack(part, tmp):
    with zipfile.ZipFile(part) as z:
        top = tmp.resolve()
        for info in z.infolist():
            rel = Path(info.filename)
            if rel.parts and rel.parts[0] == NAME:       # the zip's top folder
                rel = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path()
            if not rel.parts:
                continue
            out = (tmp / rel).resolve()
            if top not in out.parents:                   # no paths escaping the folder
                raise ValueError(f"unsafe path in zip: {info.filename}")
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def settings(path=None):
    if path is None:
        from base.paths import DATA
        path = DATA / "helper_settings.json"
    try:
        u = json.loads(Path(path).read_text(encoding="utf-8")).get("updates", {})
    except (OSError, ValueError, AttributeError):
        u = {}
    s = {**DEFAULTS, **{k: u[k] for k in DEFAULTS if k in u}}
    try:
        s["min_s"] = max(30.0, float(s["min_s"]))     # never hammer GitHub
        s["max_s"] = max(s["min_s"], float(s["max_s"]))
    except (TypeError, ValueError):
        s.update(min_s=30.0, max_s=300.0)
    return s


def allowed():
    """Settings > Internet: master switch and "Automatic updates" both on (internet.py)."""
    from base import internet
    return internet.allowed("updates")


class Updater(threading.Thread):
    """Background checks. `ready` holds the version waiting for a restart, else None.
    on_ready(version) is called from this thread once a new version is installed."""

    def __init__(self, on_ready=None, settings_fn=settings):
        super().__init__(daemon=True, name="updater")
        self.on_ready, self.settings = on_ready, settings_fn
        self.ready = None
        self.etag = None
        self.last_check = self.last_error = None
        self.stop_ev = threading.Event()

    def run(self):
        """Two clocks: the local check (a newer version folder installed, e.g. by /testbuild:
        a readlink, no network) every LOCAL_S; the GitHub feed on its own backing-off wait."""
        wait = None
        while not self.stop_ev.is_set():
            s = self.settings()
            wait = s["min_s"] if wait is None else min(s["max_s"], max(s["min_s"], wait * 2))
            due = time.time() + wait
            while (left := due - time.time()) > 0:
                if self.stop_ev.wait(min(LOCAL_S, left)):
                    return
                self.check_local()
            if not allowed():
                continue
            try:
                if self.check_once():
                    wait = None
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log(f"update check failed: {self.last_error}")

    def check_local(self):
        """A newer build installed by something else (dev/testbuild.py, another copy's update):
        offer the restart like a downloaded update. No network."""
        try:
            lv = latest_version()
            if self.ready or not lv:
                return False
            if rank(lv) > rank(current(), me=True) and                     os.path.realpath(sys.executable).lower() != str(real_latest_exe()).lower():
                log(f"newer build installed locally: {fmt(lv)}")
                self.ready = lv
                if self.on_ready:
                    self.on_ready(lv)
                return True
        except Exception as e:
            log(f"local check failed: {type(e).__name__}: {e}")
        return False

    def check_once(self):
        """One check; installs a newer version if there is one. True if something new happened."""
        if not allowed():                      # re-checked right before connecting
            return False
        self.last_check = time.time()
        vs, self.etag = feed_versions(self.etag)
        if not vs:
            return False
        have = newest(rank(current(), me=True), rank(latest_version())) or ((0, 0, 0), 0, (0, 0, 0))
        new = [v for v in vs if rank(v) > have]
        if not new:
            return False
        for v in new:                           # newest first; skip ones that can't be installed
            try:
                folder = install(v)
            except Exception as e:
                log(f"can't install {fmt(v)}: {type(e).__name__}: {e}")
                continue
            set_latest(folder)
            cleanup()
            self.ready = v
            if self.on_ready:
                self.on_ready(v)
            return True
        return False

    def stop(self):
        self.stop_ev.set()
