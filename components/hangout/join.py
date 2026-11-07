"""join.py - joining, checking and leaving a relay v2 room (no tkinter / PIL; the panel runs these on worker
threads, the sim calls them directly).

  POST /v2/rooms/<id>/join {name, secret?} -> {token, status: active|pending, role}
  GET  /v2/rooms/<id>/me   (Bearer token)  -> {name, role, status, room}      401 = no longer in the room
  POST /v2/rooms/<id>/leave (Bearer token)

The joined room lives in helper_settings.json "relay": {"room"} (hub/relay_room.py). This module never writes
settings: it returns the room dict and the panel stores it. The token is never printed or logged.
Online use only behind internet.allowed("hangout"), right before connecting (Client.call).
"""
import http.client
import json
import time
import urllib.error
import urllib.request

from base import internet
from hub import history as H
from hub import relay_addr, relay_room

FEATURE = "hangout"
TIMEOUT_S = 6.0

# Outcome.kind
JOINED, PENDING, WRONG_SECRET, NAME_TAKEN, NAME_BAD, LOCKED, GONE, DENIED, REFUSED, OFFLINE, ERROR = (
    "joined", "pending", "wrong_secret", "name_taken", "name_bad", "locked", "gone", "denied", "refused",
    "offline", "error")
# check_me() results
ACTIVE, STILL_PENDING, LOST, UNSURE = "active", "pending", "lost", "unsure"


class Outcome:
    def __init__(self, kind, message="", room=None):
        self.kind, self.message, self.room = kind, message, room

    def __repr__(self):                 # never shows the room (it holds the token)
        return f"Outcome({self.kind!r}, {self.message!r})"


def own_name(root=None):
    """The player's character name from files the tracker writes (same sources as the party link), or ''."""
    root = root or H.ROOT
    lv = H.read_json(root / "live.json", {})
    chars = H.read_json(root / "history" / "characters.json", {})
    c = (chars.get("characters") or {}).get(chars.get("current") or "", {})
    name = c.get("name") or lv.get("character") or ""
    return name if relay_room.valid_name(name) else ""


class Client:
    """server_fn() -> the relay address (re-read each call). Every method blocks: call it off the Tk thread."""

    def __init__(self, server_fn):
        self.server_fn = server_fn
        self.last_role = None

    def call(self, method, path, body=None, token=None):
        """-> (status, json). Status 0 = could not connect (or the internet gate is closed)."""
        server = relay_addr.normalize(self.server_fn())
        if not server:
            return 0, {"error": "Set a server in Settings > Internet > Relay"}
        if not internet.allowed(FEATURE):
            return 0, {"error": "Internet is off for Hangout rooms (Settings > Internet)"}
        headers = {"Content-Type": "application/json", "User-Agent": "MapleClassicCompanion"}
        if token:
            headers["Authorization"] = "Bearer " + token
        rq = urllib.request.Request(server + path, method=method, headers=headers,
                                    data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(rq, timeout=TIMEOUT_S) as r:
                return r.status, json.loads(r.read(1024 * 1024) or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read(65536) or b"{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
            return 0, {"error": "Can't reach the relay"}

    # ---- join
    def join(self, room, name, secret=None):
        """room: a lobby row ({id, name, kind, access}). -> Outcome (JOINED / PENDING carry .room)."""
        if not relay_room.valid_name(name):
            return Outcome(NAME_BAD, "Use 1-16 letters or digits")
        body = {"name": name}
        if secret:
            body["secret"] = secret
        st, d = self.call("POST", f"/v2/rooms/{room['id']}/join", body)
        msg = str(d.get("error") or "")
        if st == 200 and isinstance(d.get("token"), str):
            status = "pending" if d.get("status") == "pending" else "active"
            rec = relay_room.clean({"id": room["id"], "name": room.get("name"), "kind": room.get("kind"),
                                    "token": d["token"], "status": status, "me": name,
                                    "role": d.get("role"), "joined_t": round(time.time(), 1)})
            return Outcome(PENDING if status == "pending" else JOINED, "", rec)
        if st == 0:
            return Outcome(OFFLINE, msg or "Can't reach the relay")
        if st == 429:
            return Outcome(LOCKED, msg or "Too many tries, wait a few minutes")
        if st == 403 and "wrong secret" in msg:
            return Outcome(WRONG_SECRET, "Wrong PIN or password" if not secret else "Wrong PIN or password, try again")
        if st == 403:
            return Outcome(REFUSED, msg or "The relay refused")
        if st == 409 and "name" in msg:
            return Outcome(NAME_TAKEN, "That name is already in this room")
        if st == 409:
            return Outcome(REFUSED, msg or "Can't join")
        if st == 404:
            return Outcome(GONE, "That room is gone")
        if st == 400 and "name" in msg:
            return Outcome(NAME_BAD, "Use 1-16 letters or digits")
        return Outcome(ERROR, msg or f"The relay said {st}")

    # ---- keep checking
    def check_me(self, room):
        """GET /me with the stored token -> (ACTIVE | STILL_PENDING | LOST | UNSURE, message).
        A 401 ends it (kicked, denied, room deleted); a network error or a 429 lockout is UNSURE, never LOST.
        `self.last_role` = the role /me reported (a promoted member becomes a mod there)."""
        st, d = self.call("GET", f"/v2/rooms/{room['id']}/me", token=room["token"])
        if st == 200:
            self.last_role = d.get("role") if d.get("role") in relay_room.ROLES else None
            return (ACTIVE if d.get("status") == "active" else STILL_PENDING), ""
        if st == 401 or st == 404:
            return LOST, ""
        return UNSURE, str(d.get("error") or "")

    def leave(self, room):
        """-> (done, message). done False = the relay says no (the owner can't leave a room others are in)."""
        st, d = self.call("POST", f"/v2/rooms/{room['id']}/leave", {}, token=room["token"])
        if st == 409:
            return False, str(d.get("error") or "The owner can't leave while others are in the room")
        if st == 0:
            return True, "Left (the relay was not reachable)"
        return True, ""


# ---- room activities (the Start button): the tracker's challenge component does the relay call
RACE_MINUTES = (2, 5, 10, 15)       # the relay's MINUTES (it answers 400 for anything else)
CMD_FILE = "challenge_cmd.json"     # the same hand-off file the party panel's Challenge button uses


RELIC_LEVELS = (("easy", "Easy · 12 pieces"), ("medium", "Medium · 24 pieces"), ("challenging", "Challenging · 48 pieces"),
                ("epic", "Epic · 96 pieces"), ("hardcore", "Hardcore · 192 pieces"))
RELIC_CMD_FILE = "relic_cmd.json"   # read by the tracker's relic component (components/relic/CLAUDE.md)


RELIC_STALE_S = 60.0       # relic.json is rewritten on every relay reply; older = the tracker isn't linked


def read_relic(root=None, now=None):
    """The tracker's relic.json -> {"found", "total", "placed"} while a hunt runs in my room, else None.
    For the rooms panel's side tag ("1/32" + piece + "Relic Hunt"). The file is the contract; no import of
    that component."""
    root = root or H.ROOT
    now = now or time.time()
    st = H.read_json(root / "relic.json", {})
    if not isinstance(st, dict) or now - (st.get("t") or 0) > RELIC_STALE_S:
        return None
    if not st.get("enabled", True) or not st.get("room") or not st.get("active") or st.get("finished"):
        return None
    total = st.get("pieces_total")
    if not isinstance(total, int) or total <= 0:
        return None
    found = st.get("found_count") if isinstance(st.get("found_count"), int) else 0
    placed = st.get("placed_count") if isinstance(st.get("placed_count"), int) else 0
    return {"found": found, "total": total, "placed": placed}


def write_relic_command(cmd, root=None):
    """Hand a relic hunt command to the tracker's relic component: {"action": "start", "difficulty": "easy"} or
    {"action": "stop"}. The file is the contract; no import of that component."""
    root = root or H.ROOT
    H.write_json(root / RELIC_CMD_FILE, {"t": round(time.time(), 2), **cmd}, indent=None)


def write_command(cmd, root=None):
    """Hand a command to the tracker's challenge component (it reads and deletes the file within ~0.5 s):
    {"action": "start", "kind": "race", "minutes": 5} or {"action": "stop"}. No import of that component:
    the file is the contract (see components/challenge/CLAUDE.md)."""
    root = root or H.ROOT
    H.write_json(root / CMD_FILE, {"t": round(time.time(), 2), **cmd}, indent=None)


def read_status(root=None, now=None):
    """The tracker's challenge.json -> {"error": text or None, "activity": short text or None}. `activity` is the
    race I am in ("Kill race - 3 min left"); an error is shown only while it is recent (the tracker clears it)."""
    root = root or H.ROOT
    now = now or time.time()
    ch = H.read_json(root / "challenge.json", {})
    if not isinstance(ch, dict) or now - (ch.get("t") or 0) > 15:
        return {"error": None, "activity": None}
    act = ch.get("active") if isinstance(ch.get("active"), dict) else None
    text = None
    if act and act.get("state") == "running" and act.get("end_t"):
        left = act["end_t"] - (now + (ch.get("server_off") or 0))
        if left > 0:
            text = f"Kill race \u00b7 {max(1, int(-(-left // 60)))} min left"
    err = ch.get("error")
    return {"error": err if isinstance(err, str) else None, "activity": text}
