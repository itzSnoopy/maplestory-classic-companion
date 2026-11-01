"""lobby.py - fetch and parse the relay's room list (GET <server>/v2/lobby), and the poller that
keeps it fresh while the panel is shown. Stdlib only (urllib), no tkinter / PIL: the helper imports
this early and the sim runs it headless.

Shape the relay answers (tolerated when fields are missing):
  {"enabled": bool, "rooms": [{"id","name","kind","access","members","online","max_members","activity"}]}
  kind: "party" | "hangout"; access: "open" | "approval" | "private"; activity: short text or null.

Online use only behind internet.allowed("hangout"), checked right before connecting (in fetch()).
Nothing about the player is sent (a plain GET).
"""
import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.request

from base import internet
from hub import relay_addr

FEATURE = "hangout"
PATH = "/v2/lobby"
POLL_S = 30.0               # while the panel is shown
BACKOFF_S = 60.0            # after an error / lobby off
TIMEOUT_S = 5.0
MAX_BYTES = 200_000
MAX_ROOMS = 50
KINDS = ("hangout", "party")
ACCESS = ("open", "approval", "private")

# what the panel's quiet status line says, by state
LINES = {
    "ok": "",
    "off": "Lobby is off on this server",
    "error": "Can't reach the relay",
    "blocked": "Internet is off for Hangout rooms (Settings > Internet)",
    "noserver": "Set a server in Settings > Internet > Relay",
}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _text(v, limit):
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        return None
    s = _CTRL.sub(" ", str(v)).strip()
    return s[:limit] if s else None


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v) if v >= 0 else None


def parse(obj):
    """Relay answer -> (enabled, [room dicts]). Never raises; junk rooms are skipped."""
    if not isinstance(obj, dict):
        return False, []
    enabled = bool(obj.get("enabled", True))
    rooms = []
    for r in (obj.get("rooms") if isinstance(obj.get("rooms"), list) else []):
        if not isinstance(r, dict):
            continue
        rid = _text(r.get("id"), 64)
        if not rid:
            continue
        kind = str(r.get("kind") or "").lower()
        access = str(r.get("access") or "").lower()
        rooms.append({"id": rid,
                      "name": _text(r.get("name"), 48) or rid,
                      "kind": kind if kind in KINDS else "hangout",
                      "access": access if access in ACCESS else "open",
                      "members": _num(r.get("members")),
                      "online": _num(r.get("online")),
                      "max_members": _num(r.get("max_members")),
                      "activity": _text(r.get("activity"), 60)})
        if len(rooms) >= MAX_ROOMS:
            break
    return enabled, rooms


def subline(room):
    """The small second line: hangout '4 online', party '5/6'; then ' · activity' when present."""
    if room.get("kind") == "party":
        m, mx = room.get("members"), room.get("max_members")
        base = (f"{m}/{mx}" if mx is not None else f"{m}") if m is not None else ""
    else:
        n = room.get("online")
        if n is None:
            n = room.get("members")
        base = f"{n} online" if n is not None else ""
    act = room.get("activity")
    return f"{base} · {act}" if base and act else (act or base)


def fetch(server, timeout=TIMEOUT_S):
    """One request. Returns (state, rooms): state is ok / off / error / blocked / noserver."""
    server = relay_addr.normalize(server)
    if not server:
        return "noserver", []
    if not internet.allowed(FEATURE):
        return "blocked", []
    rq = urllib.request.Request(server + PATH, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            data = json.loads(r.read(MAX_BYTES) or b"{}")
    except urllib.error.HTTPError as e:
        return ("off" if e.code == 404 else "error"), []      # 404: an older relay without a lobby
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return "error", []
    if not isinstance(data, dict):
        return "error", []
    enabled, rooms = parse(data)
    return ("ok" if enabled else "off"), (rooms if enabled else [])


class Poller:
    """Keeps rooms / state fresh. tick() is cheap and called from the UI thread; the request runs on a
    short-lived worker thread. server_fn() -> the server string (re-read each time, so Settings apply)."""

    def __init__(self, server_fn, fetch_fn=None, poll_s=POLL_S, backoff_s=BACKOFF_S):
        self.server_fn, self.fetch_fn = server_fn, fetch_fn or fetch
        self.poll_s, self.backoff_s = poll_s, backoff_s
        self.lock = threading.Lock()
        self.rooms, self.state, self.updated = [], "idle", None
        self.due, self.busy = 0.0, False
        self.version = 0                    # bumped on every change: the panel redraws on it

    @property
    def line(self):
        return LINES.get(self.state, "")

    def snapshot(self):
        with self.lock:
            return list(self.rooms), self.state, self.version

    def refresh(self):
        """Poll again at the next tick (the refresh button)."""
        self.due = 0.0

    def _set(self, state, rooms):
        with self.lock:
            if state == "ok":
                changed = rooms != self.rooms or self.state != "ok"
                self.rooms = rooms
            else:                           # no stale list on screen: it would mislead
                changed = self.state != state or bool(self.rooms)
                self.rooms = []
            self.state, self.updated = state, time.time()
            if changed:
                self.version += 1

    def poll_once(self):
        """Fetch now, in this thread; schedules the next poll. Used by the worker and by the sim."""
        state, rooms = self.fetch_fn(self.server_fn())
        self._set(state, rooms)
        self.due = time.time() + (self.poll_s if state == "ok" else self.backoff_s)
        return state

    def _work(self):
        try:
            self.poll_once()
        except Exception as e:              # never let the thread die loudly
            print(f"hangout poll failed: {e}")
            self._set("error", [])
            self.due = time.time() + self.backoff_s
        finally:
            self.busy = False

    def tick(self, now=None):
        """Called while the panel is shown. Starts a fetch when due; no-ops while one runs."""
        now = now or time.time()
        if self.busy:
            return
        server = self.server_fn()
        if not server:
            if self.state != "noserver":
                self._set("noserver", [])
            return
        if not internet.allowed(FEATURE):
            if self.state != "blocked":
                self._set("blocked", [])
            self.due = 0.0                   # switching it on in Settings polls at once
            return
        if now < self.due:
            return
        self.busy = True
        threading.Thread(target=self._work, name="hangout-poll", daemon=True).start()
