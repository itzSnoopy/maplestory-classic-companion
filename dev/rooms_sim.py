"""Offline test of step 5 of the relay v2 plan: joining a room from the rooms panel, the party link and duels over
the room. Outputs go to OUT_DIR (made fresh; only wiped when this sim made it).

  python dev/rooms_sim.py OUT_DIR

Starts the REAL relay v2 (relay/relayd) in this process on 127.0.0.1 (scratch DATA_DIR under OUT_DIR), builds a hub with
the party + challenge components and the REAL party link thread, and drives the REAL rooms panel's join code
(components/hangout/hangout_ui.py HangoutPanel on a withdrawn Tk root; its prompts are answered by the sim, no window
opens). The internet gate is opened for this process only (everything is 127.0.0.1). Fictional names only. Checks:
 1. open / private (wrong PIN, then right; leave-and-join prompt) / approval (waiting, then approved by the owner)
 2. a taken name asks for another one; wrong-secret lockout shows the server's wait message; one room at a time
 3. the party link syncs the room: two members see each other in the party.json / party panel shape; v1 untouched
 4. leave; a kicked member: one 401, room_lost in party.json, no retry storm, the panel clears the room and says so
 5. a duel create / accept / kills / result over the room between the tracker and a simulated second client
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")
CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "relay"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(args[0] if args else Path(tempfile.gettempdir()) / "rooms_sim_out").resolve()
MARK = ".rooms_sim"
if OUT.exists() and any(OUT.iterdir()):
    if not (OUT / MARK).exists():
        sys.exit(f"{OUT} is not empty and was not made by this sim; pick an empty or new folder")
    shutil.rmtree(OUT)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / MARK).write_text("output of dev/rooms_sim.py; wiped on the next run\n", encoding="utf-8")
os.environ["MAPLE_COMPANION_DATA"] = str(OUT)
from base import lean  # noqa: F401,E402,I001
from base import internet  # noqa: E402
from relayd import clock as rclock  # noqa: E402
from relayd.config import Config  # noqa: E402
from relayd.server import make_server  # noqa: E402
from components.challenge import board  # noqa: E402
from components.challenge.component import ChallengeComponent  # noqa: E402
from components.hangout import join as J  # noqa: E402
from components.party import party_ui, partylink  # noqa: E402
from components.party.component import PartyComponent  # noqa: E402
from hub import helper_settings as HS  # noqa: E402
from hub import history as H  # noqa: E402
from hub import relay_room  # noqa: E402
from launcher import compose  # noqa: E402

fails = []
MAP = "Thicket Around the Beach III"
ME = "Tester1"


def check(ok, what, info=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else f"  ({info})"))
    if not ok:
        fails.append(what)


def wait(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# the relay -----------------------------------------------------------------------------------------
rdata = OUT / "relay_data"
rdata.mkdir()
srv = make_server(Config(port=0, bind="127.0.0.1", data_dir=str(rdata), lobby=True, allow_anyone_create=True,
                         rate_per_s=10000, rate_burst=10000, sync_gap_s=0, anon_creates_per_day=1000))
base = f"http://127.0.0.1:{srv.server_address[1]}"
threading.Thread(target=srv.serve_forever, daemon=True).start()


def call(method, path, body=None, token=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def mk(name, access="open", kind="party", secret=None):
    body = {"name": name, "owner": "Hostone", "kind": kind, "access": access}
    if secret:
        body["secret"] = secret
    s, d = call("POST", "/v2/rooms", body)
    assert s == 200, d
    return {"id": d["id"], "name": name, "kind": kind, "access": access}, d["token"]


R_OPEN, T_OPEN = mk("Open test", "open", "party")
R_PRIV, T_PRIV = mk("Private test", "private", "hangout", secret="4821")
R_APPR, T_APPR = mk("Approval test", "approval", "party")
R_LOCK, _ = mk("Locked test", "private", "hangout", secret="7392")
R_NAME, _ = mk("Taken name test", "open", "hangout")
lobby = call("GET", "/v2/lobby")[1]
check(len(lobby["rooms"]) == 5, "five rooms in the lobby", lobby)

# the helper side: settings + the real panel's join code ----------------------------------------------
HS.save(HS.load())
s0 = HS.load()
s0["relay"]["server"] = base
HS.save(s0)
H.write_json(OUT / "status.json", {"state": "playing", "level": 22, "map": MAP, "pct": 12.0}, indent=None)
H.write_json(OUT / "live.json", {"t": time.time(), "character": ME}, indent=None)
internet.allowed = lambda name, path=None: True               # this process only; the relay is 127.0.0.1


def room_get():
    return relay_room.get(HS.load())


def room_put(room):
    d = HS.load()
    d["relay"]["room"] = room
    HS.save(d)


try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
except Exception as e:                                            # no display: the panel part is skipped
    root = None
    print(f"skip  rooms panel checks (no Tk: {e})")

from components.hangout.hangout_ui import HangoutPanel  # noqa: E402

prompts = []
panel = None
if root is not None:
    cfg = {"enabled": False, "locked": False, "x": None, "y": None}   # not shown: no window, no lobby polling
    panel = HangoutPanel(root, lambda: cfg, lambda **kw: cfg.update(kw), server_fn=lambda: base,
                         room_get=room_get, room_put=room_put, party_path=OUT / "party.json")
    panel._ask = lambda title, note, done, **kw: prompts.append({"title": title, "note": note, "done": done, **kw})


def pump(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        panel._room_tick()
        if cond():
            return True
        time.sleep(0.03)
    return False


def settled():
    return not panel.joining


# the tracker side: hub with the real link --------------------------------------------------------------
sync_calls = []
real_exchange = partylink.exchange


def counting(url, state, token=None):
    sync_calls.append((url, time.time()))
    return real_exchange(url, state, token=token)


partylink.exchange = counting
partylink.SEND_PLAYING_S = partylink.SEND_IDLE_S = 0.3
partylink.FAST_S = partylink.FAST_S_V2 = 0.2
hub = compose.make_hub(OUT, components=[PartyComponent, ChallengeComponent])
chal = hub.components[1]
ev = []
hub.on("challenge_end", lambda result, **_: ev.append(result))
posts = []
hub.on("relay_post", lambda extra, me, mode="v1", **_: posts.append(mode))


class FakeTracker:
    level, job, map, cur, need, active_total = 22, "Magician", MAP, None, None, 0.0


hub.state.bind(FakeTracker())
hub.emit("service_start", maps=[])
PJ, CH = OUT / "party.json", OUT / "challenge.json"


def pj():
    return H.read_json(PJ, {})


def snap():
    return H.read_json(CH, {})


def v2_calls(rid):
    return [c for c in sync_calls if f"/v2/rooms/{rid}/sync" in c[0]]


def leave_current():
    cur = room_get()
    if cur:
        panel.on_leave()
        pump(lambda: room_get() is None and settled())


try:
    check(wait(lambda: pj().get("error") == "off"), "no room and no code: the link is off")

    if panel is not None:
        # 1c: approval ------------------------------------------------------------------------------
        panel.on_join(R_APPR)
        check(pump(lambda: bool(room_get())), "approval room: join stored a room")
        r = room_get()
        check(r["status"] == "pending" and r["me"] == ME and r["name"] == "Approval test" and r["kind"] == "party",
              "...status pending, my character name, room name and kind", {k: v for k, v in r.items() if k != "token"})
        check("Waiting for approval" in panel.toast, "...toast: waiting for approval", panel.toast)
        check(wait(lambda: pj().get("pending") is True and pj().get("mode") == "v2"), "link idles quietly while pending", pj())
        time.sleep(0.8)
        check(not v2_calls(R_APPR["id"]), "...and sends no sync")
        check(not pj().get("room_lost"), "...and is not 'lost'")
        s, d = call("POST", f"/v2/rooms/{R_APPR['id']}/members/{ME}/approve", None, T_APPR)
        check(s == 200, "owner approves", d)
        panel.next_check = 0
        check(pump(lambda: room_get()["status"] == "active"), "panel's 10 s poll picks up the approval")
        check(wait(lambda: pj().get("ok") and pj().get("mode") == "v2"), "link syncs the room now", pj())
        check(wait(lambda: len(v2_calls(R_APPR["id"])) >= 2), "...every loop")
        leave_current()
        check(room_get() is None, "leave clears the room")
        check(call("GET", f"/v2/rooms/{R_APPR['id']}/me", None, r["token"])[0] == 401, "...and the relay forgot me")
        check(wait(lambda: pj().get("error") == "off"), "link goes off after leave")

        # 1b: private ------------------------------------------------------------------------------
        prompts.clear()
        panel.on_join(R_OPEN)
        pump(lambda: room_get() is not None and settled())
        panel.on_join(R_PRIV)
        check(prompts and "Leave Open test and join Private test" in prompts[-1]["title"], "joining another room asks to leave this one")
        prompts[-1]["done"](True)
        check(pump(lambda: prompts and "PIN or password" in prompts[-1]["title"]), "...then a private room asks for the PIN", [p["title"] for p in prompts])
        check(room_get() is None, "...the old room was left first")
        check(prompts[-1].get("secret") is True, "...hidden entry")
        prompts[-1]["done"]("0000")
        check(pump(lambda: len(prompts) >= 3 and "Wrong PIN" in (prompts[-1]["note"] or "")), "wrong PIN: the prompt comes back with the server's message", prompts[-1])
        prompts[-1]["done"]("4821")
        check(pump(lambda: room_get() is not None), "right PIN: joined")
        check(room_get()["kind"] == "hangout" and room_get()["status"] == "active", "...active hangout room")
        leave_current()

        # 2: name taken, lockout --------------------------------------------------------------------
        cl = J.Client(lambda: base)
        o = cl.join(R_NAME, ME)
        check(o.kind == J.JOINED, "another client holds my name in the room")
        prompts.clear()
        panel.on_join(R_NAME)
        check(pump(lambda: bool(prompts)) and "Another character name" in prompts[-1]["title"] and "already" in prompts[-1]["note"],
              "name taken: asks for another name", prompts[-1:])
        prompts[-1]["done"]("Tester1b")
        check(pump(lambda: room_get() is not None) and room_get()["me"] == "Tester1b", "...joined under the new name")
        leave_current()
        prompts.clear()
        panel.on_join({"id": "no-no-no", "name": "Ghost", "kind": "hangout", "access": "open"})
        pump(lambda: bool(panel.toast) and settled())
        check("gone" in panel.toast.lower(), "a deleted room says it is gone", panel.toast)
        for pin in ("1111", "2222", "3333", "4444", "5555"):
            cl.join(R_LOCK, "Tester9", secret=pin)
        o = cl.join(R_LOCK, "Tester9", secret="7392")
        check(o.kind == J.LOCKED and "wait" in o.message, "5 wrong PINs: the right one is refused with the wait message", repr(o))
        prompts.clear()
        panel.on_join(R_LOCK)
        check(pump(lambda: bool(prompts)), "locked room still asks the PIN")
        prompts[-1]["done"]("7392")
        check(pump(lambda: "wait" in panel.toast.lower()), "429 shows the wait message in the panel", panel.toast)
        check(room_get() is None, "...and no room is stored")

    # 3: the open room, two members through the link ----------------------------------------------------
    if panel is not None:
        panel.on_join(R_OPEN)
        check(pump(lambda: room_get() is not None and settled()), "open room: joined at once (no prompt)")
    else:
        cl0 = J.Client(lambda: base)
        o = cl0.join(R_OPEN, ME)
        room_put(o.room)
    me_room = room_get()
    check(me_room["status"] == "active", "open room is active")
    cl2 = J.Client(lambda: base)
    o2 = cl2.join(R_OPEN, "Tester2")
    tok2 = o2.room["token"]


    def sync2(**parts):
        return call("POST", f"/v2/rooms/{R_OPEN['id']}/sync", {"presence": {
            "level": 30, "job": "Cleric", "map": MAP, "state": "playing", "caps": "ch", "v": "9.9.9"}, **parts}, tok2)

    s, d = sync2()
    check(s == 200, "second member syncs")
    check(wait(lambda: any(m["name"] == "Tester2" for m in pj().get("members", []))), "party.json lists the second member", pj())
    p = pj()
    t2 = next(m for m in p["members"] if m["name"] == "Tester2")
    check(p["ok"] and p["mode"] == "v2" and p["me"] == ME and all(m["name"] != ME for m in p["members"]),
          "...v2 mode, me left out", {k: p.get(k) for k in ("ok", "mode", "me")})
    check(t2["level"] == 30 and t2["job"] == "Cleric" and t2["map"] == MAP and t2["caps"] == "ch" and "age_s" in t2,
          "...in the v1 member shape (level, job, map, caps, age_s)", t2)
    s, d = sync2()
    mine = next((m for m in d["presence"]["members"] if m["name"] == ME), None)
    check(mine and mine["level"] == 22 and mine["map"] == MAP and mine["state"] == "playing" and mine["caps"] == "ch",
          "the second member sees me (level, map, state, caps)", mine)
    cards = party_ui.members(H.read_json(OUT / "live.json", {}), H.read_json(OUT / "status.json", {}), p, time.time())
    friend = next((c for c in cards if c["name"] == "Tester2"), None)
    check(friend and friend.get("friend") and friend["level"] == 30 and friend["same_map"], "the party panel builds a friends card from it", friend)
    check(posts and set(posts) == {"v2"}, "relay_post carried mode v2 only", set(posts))
    info = hub.ask("relay_link")
    check(info["mode"] == "v2" and info["room"] == R_OPEN["id"] and callable(info["call"]), "relay_link service: mode v2, room, call()")
    try:
        info["request"]("challenge", "", {})
        check(False, "v1 request refuses while in a room")
    except RuntimeError:
        check(True, "v1 request refuses while in a room")

    # 5: a duel over the room ----------------------------------------------------------------------------
    sync2()
    H.write_json(OUT / "challenge_cmd.json", {"t": time.time(), "action": "create", "mode": "1v1", "minutes": 2,
                                              "target": "Tester2"}, indent=None)
    check(wait(lambda: snap().get("busy")), "create over the room: I am busy while my invitation is open", snap())
    s, d = sync2()
    inv = [c for c in d["challenge"]["challenges"] if "Tester2" in c["invited"]]
    check(len(inv) == 1 and inv[0]["creator"] == ME, "the second client sees the invitation in its sync reply")
    cid = inv[0]["id"]
    s, view = call("POST", f"/v2/rooms/{R_OPEN['id']}/challenge/accept", {"id": cid}, tok2)
    check(s == 200 and view["state"] == "running", "second client accepts", view)
    check(wait(lambda: (snap().get("active") or {}).get("state") == "running"), "my board gets the running challenge")
    a = snap()["active"]
    check(a["phase"] == "starting" and 3 < a["start_t"] - time.time() - snap()["server_off"] < 6, "countdown of about 5 s on the relay clock")
    hub.emit("exp_rise", t=time.time(), map=MAP, d=148, party=False, gap=1.0)          # during the countdown: nothing
    rclock.offset += 6                                                                    # now live
    check(wait(lambda: (snap().get("active") or {}).get("phase") == "live"), "phase goes live")
    for d_ in (148, 296, 7):                                                              # 1 + 2 kills, a 7 EXP rise counts nothing
        hub.emit("exp_rise", t=time.time(), map=MAP, d=d_, party=False, gap=1.0)

    def score(name):
        return {p["name"]: p["score"] for p in (snap().get("active") or {}).get("players", [])}.get(name)
    check(wait(lambda: score(ME) == 3), "my kills reach the room's challenge (3)", score(ME))
    check(wait(lambda: chal.duel.events == []), "...and are acknowledged")
    s, view = call("POST", f"/v2/rooms/{R_OPEN['id']}/challenge/kills", {"id": cid, "kills": [
        {"s": 1, "n": 1, "t": time.time() + rclock.offset}, {"s": 2, "n": 1, "t": time.time() + rclock.offset}]}, tok2)
    check(s == 200, "second client posts kills over the action", view)
    check(wait(lambda: score("Tester2") == 2), "its score reaches my board (2)", score("Tester2"))
    rclock.offset += 200                                                                  # the end + grace passed
    check(wait(lambda: len(ev) == 1), "challenge_end emitted")
    res = ev[0] if ev else {}
    check(res.get("won") is True and res.get("score") == [3, 2] and res.get("opponent") == "Tester2" and res.get("me") == ME,
          "I won 3-2 over the room", res)
    rec = H.read_json(OUT / "history" / "challenges.json", {})
    check(rec.get("vs", {}).get(ME, {}).get("Tester2") == {"won": 1, "lost": 0, "tied": 0}, "record vs Tester2 1-0")
    vs = board.view_state(snap(), time.time(), {}, lambda n: None)
    check(vs and vs["phase"] == "winner" and vs["winner"] == "right", "board state: winner = me")
    time.sleep(0.8)
    check(len(ev) == 1, "the result is reported once")

    # 4: kicked -> one 401 -> room_lost, no retry --------------------------------------------------------
    n_before = len(v2_calls(R_OPEN["id"]))
    s, d = call("POST", f"/v2/rooms/{R_OPEN['id']}/members/{ME}/kick", None, T_OPEN)
    check(s == 200, "owner kicks me", d)
    check(wait(lambda: pj().get("room_lost") is True), "party.json says room_lost", pj())
    p = pj()
    check(p.get("room_lost_id") == R_OPEN["id"] and p.get("room_lost_name") == "Open test" and p.get("ok") is False and p.get("members") == [],
          "...with the room id and name, members empty")
    n_lost = len(v2_calls(R_OPEN["id"]))
    time.sleep(1.8)                                                  # ~6 loops at 0.3 s
    check(len(v2_calls(R_OPEN["id"])) == n_lost, "no retry after the 401 (no new sync)", (n_lost, len(v2_calls(R_OPEN["id"]))))
    check(n_lost - n_before <= 2, "...and at most a couple of syncs ran between the kick and the 401", (n_before, n_lost))
    check(pj().get("room_lost") is True, "room_lost stays in party.json for the helper")
    check(room_get() is not None, "the tracker did not touch helper_settings.json (the room is still stored there)")
    if panel is not None:
        panel.lost_t = 0
        check(pump(lambda: room_get() is None), "the helper clears the room when it sees room_lost")
        check("no longer in Open test" in panel.toast, "...and says so", panel.toast)
    else:
        room_put(None)
    check(wait(lambda: pj().get("error") == "off"), "link goes off once the room is cleared")
    # joining again works and the link syncs again (lost is per token)
    if panel is not None:
        panel.on_join(R_OPEN)
        check(pump(lambda: room_get() is not None and settled()), "joined the same room again")
        check(wait(lambda: pj().get("ok") is True and pj().get("mode") == "v2"), "link syncs the new token", pj())
        leave_current()
finally:
    hub.emit("service_stop")
    partylink.exchange = real_exchange
    if root is not None:
        try:
            panel.destroy()
            root.destroy()
        except Exception:
            pass
    srv.shutdown()
    srv.server_close()
    srv.app.close()
    rclock.offset = 0.0

print("\n" + ("all passed" if not fails else f"{len(fails)} failed: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
