"""Offline test of the hangout rooms panel's data side: a fake /v2/lobby on 127.0.0.1 -> lobby.fetch / Poller.

  python dev/hangout_sim.py [--show] [--at=-2400,40]

Checks: server normalising (hub/relay_addr), parsing (tolerates missing fields), second-line text, ok / lobby off /
404 / bad JSON / unreachable, the internet gate (blocked by default in this offline process, opened for
127.0.0.1 only after that), and the Poller's backoff. --show then opens the panel against the fake server.
"""
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")   # simulations never go online

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from base import lean  # noqa: F401,E402,I001
from base import internet  # noqa: E402
from components.hangout import lobby  # noqa: E402
from hub import relay_addr  # noqa: E402

ROOMS = {"enabled": True, "rooms": [
    {"id": "r-hen1", "name": "Henesys chill", "kind": "hangout", "access": "open",
     "members": 7, "online": 4, "max_members": 20, "activity": "Duel running"},
    {"id": "r-par1", "name": "Tester1's Kerning run", "kind": "party", "access": "private",
     "members": 5, "online": 5, "max_members": 6, "activity": None},
    {"id": "r-par2", "name": "Ellinia grind", "kind": "party", "access": "approval",
     "members": 2, "online": 1, "max_members": 6, "activity": "Looking for a cleric"},
    {"name": "no id, skipped"},
    {"id": "r-min"},                                  # only an id: every other field missing
]}
MODE = {"v": "ok"}
HITS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS.append(self.path)
        m = MODE["v"]
        if self.path != "/v2/lobby" or m == "404":
            self.send_response(404)
            self.end_headers()
            return
        body = {"ok": ROOMS, "off": {"enabled": False, "rooms": []}}.get(m)
        raw = b"{not json" if m == "badjson" else json.dumps(body).encode()
        self.send_response(500 if m == "500" else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)


fails = []


def check(name, ok, info=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({info})"))
    if not ok:
        fails.append(name)


def main():
    show = "--show" in sys.argv
    at = next((tuple(int(v) for v in a.split("=", 1)[1].split(",")) for a in sys.argv
               if a.startswith("--at=")), (-2400, 40))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"

    # server strings
    check("bare host gets http://", relay_addr.normalize("192.0.2.10:8787") == "http://192.0.2.10:8787")
    check("https kept, slash dropped", relay_addr.normalize(" https://relay.example.com/ ") == "https://relay.example.com")
    check("empty stays empty", relay_addr.normalize("  ") == "")

    # the gate: this process is MAPLE_COMPANION_OFFLINE=1, so nothing may connect
    state, rooms = lobby.fetch(url)
    check("gate blocks while offline", state == "blocked" and not rooms and not HITS, (state, HITS))
    check("no server -> noserver", lobby.fetch("")[0] == "noserver")
    p = Poller_for(url)
    p.tick()
    check("poller tick blocked, no thread", p.state == "blocked" and not p.busy and not HITS, p.state)
    check("blocked line", p.line.startswith("Internet is off"), p.line)

    internet.allowed = lambda name, path=None: True      # this process only; the server is 127.0.0.1
    MODE["v"] = "ok"
    state, rooms = lobby.fetch(url)
    check("ok state", state == "ok", state)
    ids = [r["id"] for r in rooms]
    check("rooms parsed, id-less skipped", ids == ["r-hen1", "r-par1", "r-par2", "r-min"], ids)
    h, p1, p2, mn = rooms
    check("hangout open row", (h["kind"], h["access"], h["online"], h["activity"]) == ("hangout", "open", 4, "Duel running"), h)
    check("party private row", (p1["kind"], p1["access"], p1["members"], p1["max_members"]) == ("party", "private", 5, 6), p1)
    check("party approval row", p2["access"] == "approval", p2)
    check("missing fields tolerated", (mn["name"], mn["kind"], mn["access"], mn["activity"]) == ("r-min", "hangout", "open", None), mn)
    check("hangout subline", lobby.subline(h) == "4 online · Duel running", lobby.subline(h))
    check("party subline", lobby.subline(p1) == "5/6", lobby.subline(p1))
    check("party subline + activity", lobby.subline(p2) == "2/6 · Looking for a cleric", lobby.subline(p2))
    check("empty subline", lobby.subline(mn) == "", lobby.subline(mn))
    check("junk shapes", lobby.parse([])[1] == [] and lobby.parse({"rooms": "x"}) == (True, []))

    MODE["v"] = "off"
    check("lobby off", lobby.fetch(url) == ("off", []))
    MODE["v"] = "404"
    check("404 (old relay) = lobby off", lobby.fetch(url)[0] == "off")
    MODE["v"] = "500"
    check("500 = error", lobby.fetch(url)[0] == "error")
    MODE["v"] = "badjson"
    check("bad JSON = error", lobby.fetch(url)[0] == "error")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = f"127.0.0.1:{s.getsockname()[1]}"
    s.close()                                              # nothing listens there now
    check("unreachable = error", lobby.fetch(dead, timeout=2)[0] == "error")

    # poller: lines, backoff, schedule, no stale rooms after an error
    MODE["v"] = "ok"
    p = Poller_for(url)
    check("poller ok", p.poll_once() == "ok" and len(p.snapshot()[0]) == 4 and p.line == "")
    check("next poll in ~30 s", 28 < p.due - time.time() <= 30, p.due - time.time())
    v = p.version
    p.poll_once()
    check("same data: no redraw", p.version == v)
    MODE["v"] = "off"
    p.poll_once()
    check("lobby off line, rooms cleared", p.line == "Lobby is off on this server" and p.snapshot()[0] == [])
    check("backoff 60 s", 58 < p.due - time.time() <= 60, p.due - time.time())
    p.fetch_fn = lambda server: lobby.fetch(dead, timeout=2)
    p.poll_once()
    check("error line", p.line == "Can't reach the relay" and 58 < p.due - time.time() <= 60, p.line)
    MODE["v"] = "ok"
    p.fetch_fn = lobby.fetch
    p.refresh()
    p.tick()
    for _ in range(50):
        if not p.busy:
            break
        time.sleep(0.1)
    check("tick after refresh polls on a thread", p.state == "ok" and len(p.snapshot()[0]) == 4, p.state)
    n = len(HITS)
    p.tick()
    check("not due: no request", len(HITS) == n)
    check("empty server -> noserver line", Poller_for("").tick() is None)
    pe = Poller_for("")
    pe.tick()
    check("noserver line", pe.state == "noserver" and "Set a server" in pe.line)

    print(f"\n{'ALL OK' if not fails else 'FAILED: ' + ', '.join(fails)}")
    if show and not fails:
        MODE["v"] = "ok"
        from components.hangout import hangout_ui
        print(f"panel against {url} at {at}; close it with Ctrl+C in this terminal")
        hangout_ui.run_standalone(url, at)
    return 1 if fails else 0


def Poller_for(url):
    return lobby.Poller(lambda: relay_addr.normalize(url))


if __name__ == "__main__":
    sys.exit(main())
