"""partylink.py - share your numbers with your party through the relay you host (relay/).

A background thread in the tracker. Every few seconds it posts your own state (name, level, EXP,
EXP/hr, HP, MP, map, channel) to <server>/v1/party/<code> and gets back the others' under the same
code, which it writes to party.json for the party panel (party_ui.py). Never blocks the tracker:
a slow or dead server only shows up as an error in party.json.

Only runs when Settings > Internet allows it (internet.allowed("party")) and both a server
address and a party code are filled in (helper_settings.json "party": server, code).

Your own state comes from files the tracker already writes: status.json (EXP, level, map),
live.json (exact HP/MP from the status bar) and history/characters.json (name from the Stat
window, channel).

party.json:
  t, ok (last exchange worked), error (why not), server, members [{name, level, job, map, channel,
  state, exp_cur, exp_need, exp_pct, exp_hr, eta, hp [cur, max], mp [cur, max], age_s}]
  (yourself left out)
"""
import json
import threading
import time
import urllib.error
import urllib.request

import history as H
import internet
from paths import DATA

SEND_PLAYING_S = 3.0        # while you play
SEND_IDLE_S = 10.0          # not playing: still tell the others (also catches "playing" soon after a start)
TIMEOUT_S = 5.0
MAX_BACKOFF_S = 60.0


def settings():
    try:
        s = json.loads((DATA / "helper_settings.json").read_text(encoding="utf-8")).get("party")
    except (OSError, ValueError):
        s = None
    s = s if isinstance(s, dict) else {}
    return {"server": str(s.get("server") or "").strip(), "code": str(s.get("code") or "").strip()}


def endpoint(server, code):
    server = server.rstrip("/")
    if "://" not in server:
        server = "http://" + server
    return f"{server}/v1/party/{code}"


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
    from version import VERSION
    out["v"] = VERSION
    return out


def exchange(url, state):
    body = json.dumps(state).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "MapleClassicCompanion"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read(2 * 1024 * 1024))     # big hangouts: ~200 bytes a player


class PartyLink(threading.Thread):
    def __init__(self, log=print, root=None):
        super().__init__(daemon=True, name="partylink")
        self.log = log
        self.root = root or H.ROOT
        self.stop_ev = threading.Event()
        self.last_error = None

    def _write(self, **kw):
        H.write_json(self.root / "party.json", {"t": round(time.time(), 2), **kw}, indent=None)

    def run(self):
        backoff, was_on = 0.0, False
        while not self.stop_ev.is_set():
            wait = SEND_IDLE_S
            try:
                s = settings()
                on = bool(s["server"] and s["code"]) and internet.allowed("party")
                if on != was_on:
                    self.log("party link " + (f"on ({s['server']})" if on else "off"))
                    was_on = on
                    if not on:
                        self._write(ok=False, error="off", members=[])
                if on:
                    me = own_state(self.root)
                    if me is None:
                        wait = 5.0
                    else:
                        wait = SEND_PLAYING_S if me["state"] == "playing" else SEND_IDLE_S
                        try:
                            res = exchange(endpoint(s["server"], s["code"]), me)
                            others = [m for m in res.get("members") or []
                                      if m.get("name") != me["name"]]
                            self._write(ok=True, error=None, server=s["server"], me=me["name"],
                                        members=others)
                            if self.last_error:
                                self.log("party link back")
                            self.last_error, backoff = None, 0.0
                        except Exception as e:
                            err = _why(e)
                            if err != self.last_error:
                                self.log(f"party link failed: {err}")
                            self.last_error = err
                            self._write(ok=False, error=err, server=s["server"], members=[])
                            backoff = min(MAX_BACKOFF_S, max(5.0, backoff * 2))
                            wait = max(wait, backoff)
            except Exception as e:
                self.log(f"party link error: {type(e).__name__}: {e}")
            self.stop_ev.wait(wait)

    def stop(self):
        self.stop_ev.set()


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
