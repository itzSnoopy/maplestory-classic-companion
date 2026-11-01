"""Offline test of the duel (components/challenge + the relay + the party link) end to end. Outputs go to OUT_DIR.

  python dev/challenge_sim.py OUT_DIR [--log exp_log.csv --map "Map Name"]

Starts the real relay (relay/party_relay.py) on 127.0.0.1, builds a hub with the party and challenge components and
the REAL party link thread (the internet gate is switched on for this process only: it talks to 127.0.0.1), and
plays a fake second player ("Rival") over HTTP. Checks:
 1. the Challenge button shows only for a member that says "ch"; the invitation reaches the panel (challenge.json)
 2. create from a command file -> Rival accepts -> countdown -> live (the relay clock; the time shifted, not waited for)
 3. my kills come from exp_rise (whole mobs), a party-share rise and a quest rise count nothing; resent until acked
 4. a finished duel: win, tie, the record per opponent, challenge_end, journal + diary line
 5. a duel with the link down loses nothing (events wait for the ack); don't-disturb hides invitations and turns friends' buttons off
With --log: a real session's exp_log.csv (copy it to a scratch folder first) is replayed as exp_rise events on --map
and the counted kills are compared with training's own kill count for the same rises.
"""
import csv
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")
CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "relay"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(args[0] if args else "challenge_sim_out").resolve()
MARK = ".challenge_sim"                 # the sim starts clean, but only wipes a folder it made itself
if OUT.exists() and any(OUT.iterdir()):
    if not (OUT / MARK).exists():
        sys.exit(f"{OUT} is not empty and was not made by this sim; pick an empty or new folder")
    import shutil
    shutil.rmtree(OUT)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / MARK).write_text("output of dev/challenge_sim.py; wiped on the next run\n", encoding="utf-8")
os.environ["MAPLE_COMPANION_DATA"] = str(OUT)
from base import lean  # noqa: F401,E402,I001
from base import internet  # noqa: E402
import party_relay as R  # noqa: E402
from components.challenge import board  # noqa: E402
from components.challenge.component import ChallengeComponent  # noqa: E402
from components.diary import diary  # noqa: E402
from components.diary.component import DiaryComponent  # noqa: E402
from components.party import party_ui, partylink  # noqa: E402
from components.party.component import PartyComponent  # noqa: E402
from gamedata import kills  # noqa: E402
from hub import history as H  # noqa: E402
from launcher import compose  # noqa: E402

fails = []
MAP = "Thicket Around the Beach III"
CODE_STR = "challenge-sim-1"


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


def wait(cond, secs=8.0, what=""):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# the relay -------------------------------------------------------------------------------------
srv = ThreadingHTTPServer(("127.0.0.1", 0), R.Handler)
base = f"http://127.0.0.1:{srv.server_address[1]}"
threading.Thread(target=srv.serve_forever, daemon=True).start()


def call(method, path, body=None):
    R.buckets.clear()
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def rival_post(**ch):
    return call("POST", f"/v1/party/{CODE_STR}", {"name": "Rival", "level": 20, "map": MAP, "state": "playing",
                                                    "v": "9.9.9", "caps": "ch", **({"ch": ch} if ch else {})})[1]


def shift(cid, seconds):
    c = R.challenges[CODE_STR][cid]
    for k in ("created_t", "start_t", "end_t", "ended_t"):
        if c.get(k) is not None:
            c[k] -= seconds


# the tracker side -------------------------------------------------------------------------------
(OUT / "helper_settings.json").write_text(json.dumps({"relay": {"server": base}, "party": {"code": CODE_STR},
                                                      "challenge": {"enabled": True, "dnd": False}}), encoding="utf-8")
H.write_json(OUT / "status.json", {"state": "playing", "level": 22, "map": MAP, "pct": 12.0}, indent=None)
H.write_json(OUT / "live.json", {"t": time.time(), "character": "Snoopy"}, indent=None)
internet.allowed = lambda name: True                  # this process only; the relay is 127.0.0.1
partylink.SEND_PLAYING_S = partylink.SEND_IDLE_S = 0.3
partylink.FAST_S = 0.2
hub = compose.make_hub(OUT, components=[DiaryComponent, PartyComponent, ChallengeComponent])
journal = []
hub.journal_write = journal.append
party, chal = hub.components[1], hub.components[2]
ev = []
hub.on("challenge_end", lambda result, **_: ev.append(result))


class FakeTracker:
    level, job, map, cur, need, active_total = 22, "Magician", MAP, None, None, 0.0


hub.state.bind(FakeTracker())
hub.emit("service_start", maps=[])
CH = OUT / "challenge.json"


def snap():
    return H.read_json(CH, {})


def rise(d, t=None, party=False):
    hub.emit("exp_rise", t=t or time.time(), map=MAP, d=d, party=party, gap=1.0)


try:
    # 1 ------------------------------------------------------------------------------------------
    rival_post()
    check(wait(lambda: snap().get("me") == "Snoopy" and (OUT / "party.json").exists(), what="link"), "link up, my name known")
    wait(lambda: any(m["name"] == "Rival" for m in H.read_json(OUT / "party.json", {}).get("members", [])))
    pj = H.read_json(OUT / "party.json", {})
    check(any(m["name"] == "Rival" and m.get("caps") == "ch" for m in pj.get("members", [])), "party.json carries Rival's caps")
    ui = party_ui.challenge_ui(pj, snap(), time.time())
    check(ui["button"] and ui["able"] == ["Rival"], "the Challenge button shows (Rival can)")
    rival_post()
    old = H.read_json(OUT / "party.json", {})
    check(not party_ui.challenge_ui({**old, "members": [{**m, "caps": ""} for m in old["members"]]}, snap(),
                                    time.time())["button"], "no button when nobody can")

    # 2 ------------------------------------------------------------------------------------------
    H.write_json(OUT / "challenge_cmd.json", {"t": time.time(), "action": "create", "mode": "1v1", "minutes": 2,
                                              "target": "Rival"}, indent=None)
    check(wait(lambda: any(p["name"] == "Rival" for v in R.challenges.get(CODE_STR, {}).values() for p in [{"name": n} for n in v["invited"]])),
          "create command reaches the relay")
    cid = next(iter(R.challenges[CODE_STR]))
    check(wait(lambda: snap().get("busy")), "I am busy while my invitation is open")   # the app writes just after the relay answers
    r = rival_post()
    inv = [c for c in r["challenges"] if "Rival" in c["invited"]]
    check(len(inv) == 1 and inv[0]["creator"] == "Snoopy", "Rival sees the invitation")
    call("POST", f"/v1/challenge/{CODE_STR}/{cid}", {"name": "Rival", "action": "accept"})
    check(wait(lambda: (snap().get("active") or {}).get("state") == "running"), "my board gets the running challenge")
    a = snap()["active"]
    check(a["phase"] == "starting" and 3 < a["start_t"] - time.time() - snap()["server_off"] < 6, "countdown of about 5 s on the relay clock")
    rise(2, t=time.time())                                  # during the countdown: nothing
    shift(cid, 6)                                           # now live
    check(wait(lambda: (snap().get("active") or {}).get("phase") == "live"), "phase goes live")

    vs = board.view_state(snap(), time.time(), {}, lambda n: None)
    check(vs and vs["phase"] == "live" and vs["right"]["name"] == "Snoopy" and vs["left"]["name"] == "Rival"
          and vs["timer"] in ("1:59", "2:00"), f"board state: live, Rival left / me right, timer {vs and vs['timer']}")

    # 3 ------------------------------------------------------------------------------------------
    t0 = time.time()
    rise(148)                                               # Mano
    rise(296)                                               # 2 Manos in one look
    rise(7)                                                 # no mob/pair of this map: a share, a quest
    rise(148, party=True)                                   # party share rises are still whole matches here: counted
    time.sleep(0.05)

    def mine():
        return {p["name"]: p["score"] for p in (snap().get("active") or {}).get("players", [])}.get("Snoopy")
    check(wait(lambda: mine() == 1 + 2 + 1), "relay score = 4 kills (1 + 2, a 7 EXP rise counts nothing, party-share match counted)")
    check(chal.duel.events == [], "all events acknowledged")
    for i in range(3):
        rival_post(id=cid, kills=[{"s": i + 1, "n": 1, "t": time.time()}])
    check(wait(lambda: {p["name"]: p["score"] for p in (snap().get("active") or {}).get("players", [])}.get("Rival") == 3),
          "Rival's score reaches my board (3)")

    # 5a: the link down loses nothing -----------------------------------------------------------------
    real = partylink.exchange

    def down(url, state):
        raise OSError("down")

    partylink.exchange = down
    time.sleep(0.5)
    rise(148)
    rise(148)
    check(chal.duel.mine == 6 and len(chal.duel.events) == 2, "kills made while the link is down are kept (2 events waiting)")
    partylink.exchange = real
    check(wait(lambda: mine() == 6 and chal.duel.events == []), "...and arrive when it is back (score 6)")

    # 4 ------------------------------------------------------------------------------------------
    shift(cid, 200)                                         # the end + grace passed
    check(wait(lambda: len(ev) == 1), "challenge_end emitted")
    res = ev[0]
    check(res["won"] is True and res["score"] == [6, 3] and res["opponent"] == "Rival", f"I won 6-3 ({res['score']})")
    rec = H.read_json(OUT / "history" / "challenges.json", {})
    check(rec["vs"]["Snoopy"]["Rival"] == {"won": 1, "lost": 0, "tied": 0}, "record vs Rival 1-0")
    check(any("Challenge: won" in j for j in journal), "journal line")
    entries = [json.loads(x) for x in (diary.TIMELINE.read_text(encoding="utf-8").splitlines())] if diary.TIMELINE.exists() else []
    check(any(e["kind"] == "challenge" and "Won" in e["text"] for e in entries), "diary line")
    check(snap().get("last", {}).get("won") is True and (snap().get("active") or {}).get("state") == "done",
          "the finished board stays in challenge.json for a moment")
    check(not snap().get("busy"), "free again")
    seen = {}
    vs = board.view_state(snap(), time.time(), seen, lambda n: None)
    check(vs and vs["phase"] == "winner" and vs["winner"] == "right" and vs["right"]["score"] == 6, "board state: winner = me")
    check(board.view_state(snap(), time.time() + 11, seen, lambda n: None) is None, "the board closes ~10 s after the result")
    time.sleep(0.8)
    check(len(ev) == 1, "the result is reported once")

    # a tie ------------------------------------------------------------------------------------------
    R.challenges.clear()
    chal.duel.views = []
    H.write_json(OUT / "challenge_cmd.json", {"t": time.time(), "action": "create", "mode": "all", "minutes": 2}, indent=None)
    check(wait(lambda: bool(R.challenges.get(CODE_STR))), "second challenge created (everyone)")
    cid = next(iter(R.challenges[CODE_STR]))
    rival_post()
    call("POST", f"/v1/challenge/{CODE_STR}/{cid}", {"name": "Rival", "action": "accept"})
    wait(lambda: (snap().get("active") or {}).get("state") == "running")
    shift(cid, 6)
    wait(lambda: (snap().get("active") or {}).get("phase") == "live")
    rise(148)
    rival_post(id=cid, kills=[{"s": 1, "n": 1, "t": time.time()}])
    wait(lambda: mine() == 1)
    shift(cid, 200)
    check(wait(lambda: len(ev) == 2) and ev[1]["won"] is None, "a tie is reported as a tie (won None)")
    check(H.read_json(OUT / "history" / "challenges.json", {})["vs"]["Snoopy"]["Rival"]["tied"] == 1, "record: 1 tie")
    vs = board.view_state(snap(), time.time(), {}, lambda n: None)
    check(vs and vs["phase"] == "tie", "board state: tie")

    # 5b ---------------------------------------------------------------------------------------------
    R.challenges.clear()
    chal.duel.views = []
    s = json.loads((OUT / "helper_settings.json").read_text(encoding="utf-8"))
    s["challenge"]["dnd"] = True
    (OUT / "helper_settings.json").write_text(json.dumps(s), encoding="utf-8")
    import hub.helper_settings as HS  # the loader caches by file time
    call("POST", f"/v1/challenge/{CODE_STR}", {"name": "Rival", "mode": "1v1", "minutes": 2, "target": "Snoopy"})
    time.sleep(1.0)
    check(snap().get("invite") is None and not snap().get("busy"), "don't disturb: no invitation shown")
    check(wait(lambda: R.parties[CODE_STR]["Snoopy"][1].get("caps") == ""), "...and friends' buttons go off (caps empty)")
finally:
    hub.emit("service_stop")
    srv.shutdown()

# replay of a real exp_log -----------------------------------------------------------------------
if "--log" in sys.argv:
    path = Path(sys.argv[sys.argv.index("--log") + 1])
    map_name = sys.argv[sys.argv.index("--map") + 1] if "--map" in sys.argv else MAP
    prev, counted, rises, share = None, 0, 0, 0
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                cur = int(float(row.get("exp_cur") or row.get("exp") or ""))
            except ValueError:
                continue
            if prev is not None and cur > prev:
                rises += 1
                n = kills.whole_kills(map_name, cur - prev, 2)
                counted += n
                share += 0 if n else 1
            prev = cur
    print(f"replay: {rises} EXP rises, {counted} whole kills counted, {share} rises not whole kills (shares / quests)")

print("\n" + ("all passed" if not fails else f"{len(fails)} failed"))
sys.exit(1 if fails else 0)
