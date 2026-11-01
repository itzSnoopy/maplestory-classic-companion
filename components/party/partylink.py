"""partylink.py - share your numbers with your party through the relay you host (relay/).

A background thread in the tracker. Every few seconds it posts your own state (name, level, EXP,
EXP/hr, HP, MP, map, channel) to <server>/v1/party/<code> and gets back the others' under the same
code, which it writes to party.json for the party panel (party_ui.py). Never blocks the tracker:
a slow or dead server only shows up as an error in party.json.

Two modes, chosen every loop from helper_settings.json:
  v2  a joined room ("relay": {"room"}, written by the helper's rooms panel, hub/relay_room.py) with status
      active: POST <server>/v2/rooms/<id>/sync with the member token, body {"presence": {own state}, "challenge":
      {...}, ...parts}, reply {server_t, presence: {members}, challenge: {challenges}}. Mapped to the same party.json
      and relay_reply shapes as v1. A pending room idles quietly; a 401 (kicked, room deleted) stops syncing
      that room for good (no retry) and party.json says room_lost. The tracker never writes helper settings: the
      helper clears the room when it sees room_lost.
  v1  no room, a party code ("party": code): POST <server>/v1/party/<code> as before.
Only runs when Settings > Internet allows it (internet.allowed("party")) and a server address (helper_settings.json
"relay": server, shared with the rooms panel) plus a room or a party code are filled in. The file is read raw here, so the old "party": server is migrated on the fly (hub/relay_addr.py)
even before the helper has saved a migrated file.

Your own state comes from files the tracker already writes: status.json (EXP, level, map),
live.json (exact HP/MP from the status bar) and history/characters.json (name from the Stat
window, channel).

Components can ride on the same post (one thread, one connection) through the hooks the component
gives it: `before(me, mode)` returns extra fields for the post (a challenge adds "ch"; "_fast" = post again in
1 s; "_parts" = {module: part} extra v2 sync parts), `after(reply, server_off)` gets the relay's whole answer
(v2 replies are mapped to {mode, server_t, members, challenges, parts}). `request()` (v1) and `call()` (v2) are for
the occasional other call (create / accept a challenge) on the same server.

party.json:
  t, ok (last exchange worked), error (why not), server, mode ("v1" | "v2"), members [{name, level, job, map,
  channel, state, exp_cur, exp_need, exp_pct, exp_hr, eta, hp [cur, max], mp [cur, max], age_s}]
  (yourself left out). v2 adds room (id), pending (True while waiting for approval), room_lost / room_lost_id /
  room_lost_name (the token was refused: the helper clears the room).
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request

from hub import history as H
from hub import relay_addr, relay_room
from base import internet
from base.paths import DATA

SEND_PLAYING_S = 3.0        # while you play
SEND_IDLE_S = 10.0          # not playing: still tell the others (also catches "playing" soon after a start)
TIMEOUT_S = 5.0
MAX_BACKOFF_S = 60.0
FAST_S = 1.0               # a hook asked for a quick next post (a challenge under way)
FAST_S_V2 = 1.1             # the relay allows one sync per second per member
CAPS = "ch"                 # what this version can do besides the party panel: ch = challenges


def settings():
    try:
        raw = json.loads((DATA / "helper_settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = relay_addr.migrate(raw if isinstance(raw, dict) else {})   # old party.server -> relay.server, in memory
    s = raw.get("party")
    s = s if isinstance(s, dict) else {}
    return {"server": relay_addr.server(raw), "code": str(s.get("code") or "").strip(),
            "room": relay_room.get(raw)}


def mode_of(s):
    """"v2" when a room is joined (it wins over a party code), "v1" with only a code, else "off"."""
    return "v2" if s.get("room") else "v1" if s.get("code") else "off"


def room_key(room):
    """Identity of a room + token without keeping the token around (for "this one was refused")."""
    return (room["id"], hashlib.sha256(room["token"].encode()).hexdigest()[:16])


def sync_url(server, room):
    return f"{relay_addr.normalize(server)}/v2/rooms/{room['id']}/sync"


def endpoint(server, code, kind="party", suffix=""):
    server = relay_addr.normalize(server)
    return f"{server}/v1/{kind}/{code}{suffix}"


def own_state(root=None):
    """What we tell the party, from the files the tracker writes. None until the name is known."""
    root = root or H.ROOT
    st = H.read_json(root / "status.json", {})
    lv = H.read_json(root / "live.json", {})
    chars = H.read_json(root / "history" / "characters.json", {})
    c = (chars.get("characters") or {}).get(chars.get("current") or "", {})
    name = c.get("name") or lv.get("character")
    if not name:
        return None
    out = {"name": name, "state": st.get("state") or "stopped"}
    for k, src in (("level", "level"), ("job", "job"), ("map", "map"), ("exp_cur", "exp_current"),
                   ("exp_need", "exp_needed"), ("exp_pct", "pct"), ("exp_hr", "exp_per_hour_recent"),
                   ("eta", "eta_to_level")):
        if st.get(src) is not None:
            out[k] = st[src]
    fresh = time.time() - (lv.get("t") or 0) < 10
    for k in ("hp", "mp"):
        m = lv.get(k) or {}
        if fresh and m.get("cur") is not None and m.get("max"):
            out[k] = [int(m["cur"]), int(m["max"])]
    ch = (c.get("channel") or {}).get("ch")
    if ch:
        out["channel"] = ch
    from base.version import VERSION
    out["v"] = VERSION
    out["caps"] = CAPS
    return out


def exchange(url, state, token=None):
    """One POST of JSON, the answer as JSON. token: the v2 member token (Bearer; never logged)."""
    body = json.dumps(state).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "MapleClassicCompanion"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read(2 * 1024 * 1024))     # big hangouts: ~200 bytes a player


def v2_body(me, extra):
    """The sync body for a room from own_state + what components added: presence gets the state fields (and
    plain extras like caps), "ch" becomes the challenge part, "_parts" adds whole parts of other modules."""
    pres = {k: v for k, v in me.items() if k != "name"}
    body = {}
    for k, v in extra.items():
        if k == "ch":
            body["challenge"] = v
        elif k == "_parts":
            if isinstance(v, dict):
                body.update({m: p for m, p in v.items() if m != "presence"})
        elif not k.startswith("_"):
            pres[k] = v
    body["presence"] = pres
    return body


def v2_reply(res):
    """A sync answer -> the shape relay_reply subscribers and party.json know (the v1 one, plus mode / parts)."""
    pres = res.get("presence") if isinstance(res.get("presence"), dict) else {}
    ch = res.get("challenge") if isinstance(res.get("challenge"), dict) else {}
    return {"mode": "v2", "server_t": res.get("server_t"), "members": pres.get("members") or [],
            "challenges": ch.get("challenges") or [], "parts": res}


class PartyLink(threading.Thread):
    def __init__(self, log=print, root=None, before=None, after=None):
        super().__init__(daemon=True, name="partylink")
        self.log = log
        self.root = root or H.ROOT
        self.before, self.after = before, after
        self.stop_ev = threading.Event()
        self.last_error = None
        self.ok = False
        self.server_off = 0.0       # relay clock minus ours (seconds), from the last exchange
        self.lost = None            # room_key of a room whose token the relay refused: not tried again

    def info(self):
        """What other components may know of the link (the relay_link service)."""
        s = settings()
        return {"server": s["server"], "code": s["code"], "ok": self.ok, "server_off": self.server_off,
                "mode": mode_of(s), "room": (s["room"] or {}).get("id"), "request": self.request,
                "call": self.call}

    def request(self, kind, suffix="", body=None, method="POST"):
        """One call to <server>/v1/<kind>/<code><suffix> -> (http status, json). Raises when the link is off
        or the server can't be reached. Not the link thread: call it from your own worker."""
        s = settings()
        if s["room"] or not (s["server"] and s["code"]) or not internet.allowed("party"):
            raise RuntimeError("party link off or in a room (use call())")
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(endpoint(s["server"], s["code"], kind, suffix), data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "MapleClassicCompanion"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.status, json.loads(r.read(1024 * 1024))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except ValueError:
                return e.code, {}

    def call(self, module, action, body=None):
        """v2: one call to <server>/v2/rooms/<id>/<module>/<action> with the member token -> (status, json).
        Raises when no active room is joined, the link is off, or the server can't be reached. Not the link
        thread: call it from your own worker. A 401 here is not acted on (the sync loop owns that)."""
        s = settings()
        room = s["room"]
        if not (room and room["status"] == "active" and s["server"]) or not internet.allowed("party"):
            raise RuntimeError("not in a room")
        url = f"{relay_addr.normalize(s['server'])}/v2/rooms/{room['id']}/{module}/{action}"
        req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "MapleClassicCompanion",
                                              "Authorization": "Bearer " + room["token"]})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.status, json.loads(r.read(1024 * 1024))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except ValueError:
                return e.code, {}

    def _write(self, **kw):
        H.write_json(self.root / "party.json", {"t": round(time.time(), 2), **kw}, indent=None)

    def run(self):
        backoff, was_on = 0.0, None
        while not self.stop_ev.is_set():
            wait = SEND_IDLE_S
            try:
                s = settings()
                mode = mode_of(s)
                on = bool(s["server"]) and mode != "off" and internet.allowed("party")
                key = (on, mode)
                if key != was_on:
                    self.log(f"party link ({mode}) " + (f"on ({s['server']})" if on else "off"))
                    was_on = key
                    backoff = 0.0
                    if not on:
                        self._write(ok=False, error="off", members=[])
                if on:
                    wait, backoff = (self._loop_v2 if mode == "v2" else self._loop_v1)(s, backoff)
            except Exception as e:
                self.log(f"party link error: {type(e).__name__}: {e}")
            self.stop_ev.wait(wait)

    def _fail(self, e, s, backoff, wait, **kw):
        err = _why(e)
        self.ok = False
        if err != self.last_error:
            self.log(f"party link failed: {err}")
        self.last_error = err
        self._write(ok=False, error=err, server=s["server"], members=[], **kw)
        backoff = min(MAX_BACKOFF_S, max(5.0, backoff * 2))
        return max(wait, backoff), backoff

    def _loop_v1(self, s, backoff):
        wait = SEND_IDLE_S
        me = own_state(self.root)
        if me is None:
            return 5.0, backoff
        wait = SEND_PLAYING_S if me["state"] == "playing" else SEND_IDLE_S
        try:
            extra = self._extra(me, "v1")
            t0 = time.time()
            res = exchange(endpoint(s["server"], s["code"]), {**me, **{
                k: v for k, v in extra.items() if not k.startswith("_")}})
            if extra.get("_fast"):
                wait = FAST_S
            if isinstance(res.get("server_t"), (int, float)):
                self.server_off = res["server_t"] - (t0 + time.time()) / 2
            self.ok = True
            self._after({**res, "mode": "v1"})
            others = [m for m in res.get("members") or [] if m.get("name") != me["name"]]
            self._write(ok=True, error=None, server=s["server"], me=me["name"], mode="v1", members=others)
            if self.last_error:
                self.log("party link back")
            self.last_error, backoff = None, 0.0
        except Exception as e:
            wait, backoff = self._fail(e, s, backoff, wait, mode="v1")
        return wait, backoff

    def _loop_v2(self, s, backoff):
        room = s["room"]
        base = {"server": s["server"], "mode": "v2", "room": room["id"]}
        if self.lost == room_key(room):             # the relay refused this token: wait for a new join
            self._write(ok=False, error="no longer in the room", members=[], room_lost=True,
                        room_lost_id=room["id"], room_lost_name=room["name"], **base)
            return SEND_IDLE_S, backoff
        if self.lost is not None:
            self.lost = None
        if room["status"] != "active":              # waiting for the owner: idle quietly
            self.ok = False
            self._write(ok=False, error=None, pending=True, members=[], **base)
            return SEND_IDLE_S, backoff
        me = own_state(self.root)
        if me is None:
            return 5.0, backoff
        me = {**me, "name": room.get("me") or me["name"]}       # the name used in the room
        wait = SEND_PLAYING_S if me["state"] == "playing" else SEND_IDLE_S
        try:
            extra = self._extra(me, "v2")
            t0 = time.time()
            res = exchange(sync_url(s["server"], room), v2_body(me, extra), token=room["token"])
            if extra.get("_fast"):
                wait = FAST_S_V2
            reply = v2_reply(res)
            if isinstance(reply.get("server_t"), (int, float)):
                self.server_off = reply["server_t"] - (t0 + time.time()) / 2
            self.ok = True
            self._after(reply)
            others = [m for m in reply["members"] if m.get("name") != me["name"]]
            self._write(ok=True, error=None, me=me["name"], members=others, **base)
            if self.last_error:
                self.log("party link back")
            self.last_error, backoff = None, 0.0
        except urllib.error.HTTPError as e:
            if e.code == 401:                       # kicked, banned or the room is gone: never try this token again
                self.lost, self.ok = room_key(room), False
                self.log(f"party link: no longer in room {room['id']}")
                self._write(ok=False, error="no longer in the room", members=[], room_lost=True,
                            room_lost_id=room["id"], room_lost_name=room["name"], **base)
                return SEND_IDLE_S, 0.0
            if e.code == 403:                       # waiting for approval (the helper updates the status itself)
                self.ok = False
                self._write(ok=False, error=None, pending=True, members=[], **base)
                return SEND_IDLE_S, backoff
            if e.code == 429 and "one sync per second" in _msg(e):     # a little later; any other 429 is a lockout: back off
                return 2.0, backoff
            wait, backoff = self._fail(e, s, backoff, wait, mode="v2", room=room["id"])
        except Exception as e:
            wait, backoff = self._fail(e, s, backoff, wait, mode="v2", room=room["id"])
        return wait, backoff

    def _extra(self, me, mode="v1"):
        try:
            return (self.before(me, mode) if self.before else None) or {}
        except Exception as e:
            self.log(f"party link hook failed: {type(e).__name__}: {e}")
            return {}

    def _after(self, res):
        try:
            if self.after:
                self.after(res, self.server_off)
        except Exception as e:
            self.log(f"party link hook failed: {type(e).__name__}: {e}")

    def stop(self):
        self.stop_ev.set()


def _msg(e):
    try:
        return str(json.loads(e.read()).get("error") or "")
    except Exception:
        return ""


def _why(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:
            msg = None
        return f"server said {e.code}" + (f": {msg}" if msg else "")
    if isinstance(e, urllib.error.URLError):
        return f"can't reach the server ({e.reason})"
    if isinstance(e, TimeoutError):
        return "the server didn't answer in time"
    return f"{type(e).__name__}: {e}"
