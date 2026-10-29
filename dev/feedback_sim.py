"""Offline end-to-end test of skill feedback: review page -> local API -> relay -> dev tool. All outputs
go to OUT_DIR; nothing goes online (relay and page server both on 127.0.0.1).

  python dev/feedback_sim.py OUT_DIR

1. the review page renders with every class tab
2. a relay with a feedback key runs in this process (relay/party_relay.py)
3. two partial exports (one mixed-class, one anonymous) go through the page server's POST
   /api/feedback/send (Host + X-Diary checks) -> FeedbackComponent.send -> relay
4. without X-Diary -> 403; a second send from the same address within 30 s -> 429; wrong code -> 403
5. dev/feedback.py fetch (+ --clear) writes the items to OUT_DIR, merge + suggest read them
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")   # simulations never go online

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "feedback_sim_out").resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ["MAPLE_COMPANION_DATA"] = str(OUT)
KEY, FCODE = "sim-owner-key", "simfeedback"
os.environ["FEEDBACK_KEY"] = KEY
from base import lean  # noqa: F401,E402,I001
from base import internet  # noqa: E402
from components.diary import diary_server  # noqa: E402
from components.feedback import component as fbc, review_page  # noqa: E402
from hub import pages  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({info})"))
    if not ok:
        fails.append(name)


# 1. page
html = review_page.page(reviewer="SimNick")
(OUT / "skill-review.html").write_text(html, encoding="utf-8")
for cls in ("Beginner", "Warrior", "Magician", "Bowman", "Thief"):
    check(f"page has {cls}", cls in html)
check("page prefilled nickname", "SimNick" in html)

# 2. relay in this process (a fresh port each run)
spec = importlib.util.spec_from_file_location("party_relay_sim", CODE / "relay" / "party_relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
rs = relay.ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
threading.Thread(target=rs.serve_forever, daemon=True).start()
RELAY = f"http://127.0.0.1:{rs.server_port}"

# 3. the component, with the relay as its feedback server; the gate opened for localhost only
real_allowed = internet.allowed
internet.allowed = lambda f, path=None: f == "feedback" or real_allowed(f, path)
settings = {"feedback": {"server": RELAY, "code": FCODE}, "character": {"name": "SimNick"}}


class Shell:                          # just what FeedbackComponent.ui uses
    def all_settings(self): return settings
    def add_page(self, p, r): pages.register_page(p, r)
    def add_api(self, p, h): pages.register_api(p, h)
    def add_command(self, *a): pass
    def add_menu(self, *a, **k): pass
    def add_settings_extra(self, *a, **k): pass
    def on_settings_save(self, *a): pass
    def notify(self, t): print("notify:", t)


comp = fbc.FeedbackComponent()
comp.ui(Shell())
base = diary_server.start_thread(0)


def local(body, xdiary=True):
    h = {"Content-Type": "application/json", "Host": base.split("/")[2]}
    if xdiary:
        h["X-Diary"] = "1"
    rq = urllib.request.Request(base + "api/feedback/send", data=json.dumps(body).encode(), headers=h,
                                method="POST")
    try:
        with urllib.request.urlopen(rq, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def export(reviewer, reviews):
    return {"format": "classic-skill-review/1", "branch": "all", "reviewer": reviewer,
            "exported": "2026-10-29T12:00:00Z", "count": len(reviews), "reviews": reviews}


mixed = export("SimNick", {
    "2001000": {"name": "Magic Guard", "status": "ok", "mode": "until death"},
    "1001000": {"name": "Iron Body", "status": "wrong", "kind": "passive", "note": "sim note"},
    "2301002": {"name": "Invincible", "status": "unsure"}})
anon = export("", {"1001000": {"name": "Iron Body", "status": "ok"}})

def status():
    h = {"Content-Type": "application/json", "Host": base.split("/")[2], "X-Diary": "1"}
    rq = urllib.request.Request(base + "api/feedback/status", data=b"{}", headers=h, method="POST")
    with urllib.request.urlopen(rq, timeout=15) as r:
        return json.loads(r.read())


check("status: ONLINE with the inbox on", status().get("online") is True, status())
relay.FEEDBACK_KEY = ""
s = status()
check("status: OFFLINE when the inbox is off", s.get("online") is False and "inbox" in s.get("reason", ""), s)
relay.FEEDBACK_KEY = KEY
settings["feedback"]["server"] = "http://127.0.0.1:9"
s = status()
check("status: OFFLINE when the server does not answer", s.get("online") is False, s)
settings["feedback"]["server"] = RELAY

st, r = local(mixed, xdiary=False)
check("no X-Diary header -> 403", st == 403, st)
st, r = local(mixed)
check("mixed-class export sent", st == 200 and r.get("ok") and r.get("id"), (st, r))
st, r = local(anon)
check("second send within 30 s -> 429", st == 429, (st, r))
relay.fb_last.clear()                       # pretend 30 s passed
st, r = local(anon)
check("anonymous export sent", st == 200 and r.get("ok"), (st, r))
relay.fb_last.clear()
settings["feedback"]["code"] = "wrongcode1"
relay.FEEDBACK_CODES = {FCODE}
st, r = local(anon)
check("wrong code -> 403 with a readable text", st == 403 and "code" in r.get("error", ""), (st, r))
settings["feedback"]["code"] = FCODE
st, r = local(export("x", {}))
check("empty export refused locally", st == 400, (st, r))

# 5. owner side: dev tool
env = {**os.environ, "FEEDBACK_KEY": KEY}
inbox = OUT / "inbox"
for f in inbox.glob("*.json"):              # a reused OUT_DIR keeps the last run's items
    f.unlink()
p = subprocess.run([sys.executable, str(CODE / "dev" / "feedback.py"), "fetch", "--server", RELAY,
                    "--code", FCODE, "--out", str(inbox), "--clear"], env=env, capture_output=True, text=True)
files = sorted(inbox.glob("*.json"))
check("fetch wrote 2 items", p.returncode == 0 and len(files) == 2, p.stderr[-300:] or p.stdout[-300:])
with relay.lock:
    left = [i for c, i in relay.feedback if c == FCODE]
check("--clear emptied the inbox", not left, len(left))
p = subprocess.run([sys.executable, str(CODE / "dev" / "feedback.py"), "merge", str(inbox),
                    "--out", str(OUT / "merged.md")], env=env, capture_output=True, text=True)
md = (OUT / "merged.md").read_text(encoding="utf-8") if (OUT / "merged.md").exists() else ""
check("merge shows both reviewers on Iron Body", p.returncode == 0 and "Iron Body" in md
      and "SimNick" in md and "anonymous" in md.lower(), p.stderr[-300:])
p = subprocess.run([sys.executable, str(CODE / "dev" / "feedback.py"), "suggest", str(inbox)],
                   env=env, capture_output=True, text=True)
(OUT / "suggest.txt").write_text(p.stdout, encoding="utf-8")
check("suggest runs (print only)", p.returncode == 0 and "Traceback" not in p.stderr, p.stderr[-300:])

rs.shutdown()
print(f"\n{'all ok' if not fails else str(len(fails)) + ' failed'}; outputs in {OUT}")
sys.exit(1 if fails else 0)
