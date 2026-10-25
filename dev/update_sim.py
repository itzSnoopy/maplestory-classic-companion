"""Offline tests of updater.py against a fake GitHub on localhost. Writes only to OUT_DIR.

  python dev/update_sim.py OUT_DIR

Covers: a downloaded copy installs itself and hands off to `latest`; running from `latest` doesn't
hand off; the feed check (200, then 304 via ETag); download + checksum + unpack + junction swap;
a bad checksum, a release without .sha256 and a zip with ../ paths are refused; old versions are
cleaned up but one with a file in use is left whole. Fake builds are tiny folders with a dummy exe.
"""
import hashlib
import http.server
import io
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
OUT = Path(sys.argv[1]).resolve()
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
os.environ["MAPLE_COMPANION_PROGRAMS"] = str(OUT / "Programs")
os.environ["MAPLE_COMPANION_DATA"] = str(OUT / "data")

import updater as U  # noqa: E402

RELEASES = {}          # url path -> bytes
HITS = []              # every request the fake GitHub got
FAILS = []


def check(name, ok):
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)


def fake_zip(ver, extra=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(f"{U.NAME}/{U.EXE}", f"fake exe {ver}")
        z.writestr(f"{U.NAME}/_internal/base_library.zip", "x" * 1000)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return b.getvalue()


def publish(ver, data=None, sha=True, good_sha=True):
    data = data if data is not None else fake_zip(ver)
    name = f"{U.NAME}-{ver}-win64.zip"
    RELEASES[f"/releases/download/v{ver}/{name}"] = data
    if sha:
        h = hashlib.sha256(data if good_sha else b"other").hexdigest()
        RELEASES[f"/releases/download/v{ver}/{name}.sha256"] = f"{h}  {name}\n".encode()
    tags = sorted({p.split("/")[3] for p in RELEASES if p.startswith("/releases/download/")}, reverse=True)
    RELEASES["/releases.atom"] = ("<feed>" + "".join(
        f'<entry><link rel="alternate" type="text/html" href="https://github.com/x/y/releases/tag/{t}"/></entry>'
        for t in tags) + "</feed>").encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        HITS.append(self.path)
        body = RELEASES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        etag = '"%s"' % hashlib.md5(body).hexdigest()
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["MAPLE_COMPANION_UPDATE_BASE"] = f"http://127.0.0.1:{srv.server_port}"

# --- 1. a downloaded copy (current version) installs itself and hands off
cur = U.fmt(U.current())
loose = OUT / "Downloads" / U.NAME
(loose / "_internal").mkdir(parents=True)
(loose / U.EXE).write_text("fake exe current")
launched = []
r = U.adopt(["--service"], frozen=True, exe=loose / U.EXE, launch=launched.append)
check("loose copy installs itself into versions/", (U.versions_dir() / cur / U.EXE).exists())
check("latest points at it", U.latest_version() == U.current())
check("hands off to latest's real folder with the same arguments",
      r and launched and launched[0][0] == str(U.real_latest_exe())
      and "versions" in launched[0][0] and launched[0][1:] == ["--service"])
launched.clear()
check("started through the latest junction: hands off to the real folder",
      U.adopt([], frozen=True, exe=U.latest_exe(), launch=launched.append) is True
      and launched[0][0] == str(U.real_latest_exe()))
launched.clear()
check("running from the real latest folder: no hand-off",
      U.adopt([], frozen=True, exe=U.real_latest_exe(), launch=launched.append) is False and not launched)
check("from source: nothing happens", U.adopt([], frozen=False, launch=launched.append) is False)

# --- 2. feed + install + swap
nv = list(U.current())
nv[1] += 1
new = U.fmt(nv)
publish(cur)
publish(new)
up = U.Updater(on_ready=lambda v: launched.append(("ready", v)))
check("check finds and installs the new version", up.check_once() is True)
check("latest now points at the new version", U.latest_version() == tuple(nv))
check("helper told (on_ready)", ("ready", tuple(nv)) in launched)
check("unpacked without the zip's top folder", (U.versions_dir() / new / "_internal").is_dir())
check("no leftovers (.part / .tmp)", not list(U.versions_dir().glob("*.part")) + list(U.versions_dir().glob("*.tmp")))
check("second check: feed unchanged (304), nothing to do", up.check_once() is False and up.etag)

# --- 2b. Settings > Internet: nothing is fetched while it's off
import json  # noqa: E402
hs = Path(os.environ["MAPLE_COMPANION_DATA"]) / "helper_settings.json"
probe = list(nv)
probe[1] += 9
publish(U.fmt(probe))
for label, cfg in (("master switch off", {"allowed": False, "features": {"updates": True}}),
                   ("Automatic updates off", {"allowed": True, "features": {"updates": False}})):
    hs.write_text(json.dumps({"internet": cfg}))
    os.utime(hs, None)
    before = len(HITS)
    check(f"{label}: no request, nothing installed",
          U.Updater().check_once() is False and len(HITS) == before
          and not (U.versions_dir() / U.fmt(probe)).exists())
hs.unlink()
for k in [k for k in RELEASES if U.fmt(probe) in k]:
    del RELEASES[k]
publish(new)

# --- 3. refused releases
bad = list(nv)
bad[2] += 1
publish(U.fmt(bad), good_sha=False)
up2 = U.Updater()
check("bad checksum: not installed", up2.check_once() is False and not (U.versions_dir() / U.fmt(bad)).exists())
del RELEASES[f"/releases/download/v{U.fmt(bad)}/{U.NAME}-{U.fmt(bad)}-win64.zip.sha256"]
publish(U.fmt(bad), sha=False)
check("no .sha256: not installed", U.Updater().check_once() is False and U.latest_version() == tuple(nv))
evil = list(nv)
evil[2] += 2
publish(U.fmt(evil), data=fake_zip(U.fmt(evil), {"../../escape.txt": "x"}))
check("zip with ../ paths: refused", U.Updater().check_once() is False
      and not (OUT / "escape.txt").exists() and not (U.root() / "escape.txt").exists())
for k in [k for k in RELEASES if U.fmt(evil) in k or U.fmt(bad) in k]:
    del RELEASES[k]

# --- 4. cleanup keeps the newest two, never one in use
for i in (3, 4):
    v = list(nv)
    v[1] += i
    publish(U.fmt(v))
held = open(U.versions_dir() / new / "_internal" / "base_library.zip")      # "running" new
up3 = U.Updater()
up3.check_once()
held.close()
have = sorted(U.installed())
check(f"cleanup: newest kept, in-use {new} left whole ({[U.fmt(v) for v in have]})",
      (U.versions_dir() / new / U.EXE).exists() and len(have) <= 4)
U.cleanup()
check("cleanup once nothing runs from it: newest two (+ current)",
      len(U.installed()) <= 3 and U.latest_version() == max(U.installed()))

# --- 5. local test builds (dev/testbuild.py): ranked just above their base release
top = max(U.installed())
tb = (top[0], top[1], 90)                       # e.g. 0.11.90 built on the newest release
tdir = U.versions_dir() / U.fmt(tb)
tdir.mkdir()
(tdir / U.EXE).write_text("fake test exe")
(tdir / U.TEST_MARK).write_text(U.fmt(top), encoding="ascii")
U.set_latest(tdir)
check("test build ranks above its base", U.rank(tb) > U.rank(top))
seen = []
up4 = U.Updater(on_ready=seen.append)
check("installed test build is offered for restart (local, no network)",
      up4.check_local() and seen == [tb])
before = len(HITS)
check("feed with only the base release: test build stays", U.Updater().check_once() is False
      and U.latest_version() == tb)
nxt = (top[0], top[1], top[2] + 1)             # the next real release, lower number than 90
publish(U.fmt(nxt))
up5 = U.Updater()
check(f"next release {U.fmt(nxt)} replaces test build {U.fmt(tb)}",
      up5.check_once() and U.latest_version() == nxt and len(HITS) > before)
U.cleanup()
check("cleanup orders by rank (release kept over the older test build)",
      nxt in U.installed() and U.latest_version() == nxt)

# --- 6. the running updater notices a new local folder on its own short clock
import time  # noqa: E402
U.LOCAL_S = 0.2
got = []
bg = U.Updater(on_ready=got.append, settings_fn=lambda: {"min_s": 600, "max_s": 600})
bg.start()
tb2 = (nxt[0], nxt[1], 91)
t2 = U.versions_dir() / U.fmt(tb2)
t2.mkdir()
(t2 / U.EXE).write_text("fake test exe 2")
(t2 / U.TEST_MARK).write_text(U.fmt(nxt), encoding="ascii")
U.set_latest(t2)
t_end = time.time() + 5
while not got and time.time() < t_end:
    time.sleep(0.05)
bg.stop()
check(f"running updater offers a new local build within seconds (feed waits 600 s): {got}",
      got == [tb2])

srv.shutdown()
print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
