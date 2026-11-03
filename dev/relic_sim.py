"""Offline test of the relic hunt: the REAL relay v2 (in this process, 127.0.0.1), the REAL party link thread and the
REAL relic component (tracker side), three members (the tracker's player + two simulated ones).

  python dev/relic_sim.py OUT_DIR

OUT_DIR is made fresh (only wiped when this sim made it). The internet gate is opened for this process only.
Checks: Start from the rooms panel's command file (a plain member is refused, a promoted mod starts an Easy hunt);
eligible kills from synthetic exp_rise events, read as whole kills the duel's way (out-of-band level = 0, unknown
map = 0, a rise that is no kill = 0, in band = counted, a party quest map counts whatever the mobs' level; the
challenge component's rise_kills service on its own hub);
together bonus tiers from presence; pieces dropping, no duplicates, the deck finishing; the shared table through the
HTTP API (a conflict, snapping, the board completing, gallery, lobby text); relic.json and the toast trigger; a relay
restart on the same DATA_DIR keeps the finished board. Fictional names only.
"""
import json
import os
import random
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
OUT = Path(args[0] if args else Path(tempfile.gettempdir()) / "relic_sim_out").resolve()
MARK = ".relic_sim"
if OUT.exists() and any(OUT.iterdir()):
    if not (OUT / MARK).exists():
        sys.exit(f"{OUT} is not empty and was not made by this sim; pick an empty or new folder")
    shutil.rmtree(OUT)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / MARK).write_text("output of dev/relic_sim.py; wiped on the next run\n", encoding="utf-8")
os.environ["MAPLE_COMPANION_DATA"] = str(OUT)
from base import lean  # noqa: F401,E402,I001
from base import internet  # noqa: E402
from relayd.config import Config  # noqa: E402
from relayd.server import make_server  # noqa: E402
from components.hangout import join as J  # noqa: E402
from components.party import partylink  # noqa: E402
from components.party.component import PartyComponent  # noqa: E402
from components.relic.component import RelicComponent  # noqa: E402
from components.relic import toast as toast_mod  # noqa: E402
from hub import helper_settings as HS  # noqa: E402
from hub import history as H  # noqa: E402
from launcher import compose  # noqa: E402

fails = []
MAP = "Henesys Hunting Ground I"      # Lv 2-14 mobs, 12.26 EXP on average
ME = "Tester1"
AVG = 429 / 35


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


class AlwaysFind(random.Random):
    def random(self):
        return 0.0


rdata = OUT / "relay_data"
rdata.mkdir()
CFG = dict(bind="127.0.0.1", data_dir=str(rdata), lobby=True, allow_anyone_create=True, rate_per_s=10000,
           rate_burst=10000, sync_gap_s=0, anon_creates_per_day=1000)
srv = make_server(Config(port=0, **CFG))
base = f"http://127.0.0.1:{srv.server_address[1]}"
threading.Thread(target=srv.serve_forever, daemon=True).start()
app = srv.app
app.modules["relic"].rng = AlwaysFind()


def call(method, path, body=None, token=None, b=None):
    req = urllib.request.Request((b or base) + path, data=None if body is None else json.dumps(body).encode(),
                                 method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


s, d = call("POST", "/v2/rooms", {"name": "Hunt test", "owner": "Hostone", "kind": "hangout", "access": "open"})
RID, OWNER = d["id"], d["token"]
room_row = {"id": RID, "name": "Hunt test", "kind": "hangout", "access": "open"}
internet.allowed = lambda name, path=None: True               # this process only; the relay is 127.0.0.1
o = J.Client(lambda: base).join(room_row, ME)
check(o.kind == J.JOINED, "the tracker's player joined the room")
tok2 = call("POST", f"/v2/rooms/{RID}/join", {"name": "Tester2"})[1]["token"]
tok3 = call("POST", f"/v2/rooms/{RID}/join", {"name": "Tester3"})[1]["token"]

# settings, status files ----------------------------------------------------------------------------------
HS.save(HS.load())
s0 = HS.load()
s0["relay"]["server"] = base
s0["relay"]["room"] = o.room
HS.save(s0)
H.write_json(OUT / "status.json", {"state": "playing", "level": 8, "map": MAP, "pct": 12.0}, indent=None)
H.write_json(OUT / "live.json", {"t": time.time(), "character": ME}, indent=None)
H.write_json(OUT / "history" / "characters.json", {"current": "c1", "characters": {"c1": {"name": ME, "channel": {"ch": 3}}}},
             indent=None)

partylink.SEND_PLAYING_S = partylink.SEND_IDLE_S = 0.3
partylink.FAST_S = partylink.FAST_S_V2 = 0.2
hub = compose.make_hub(OUT, components=[PartyComponent, RelicComponent])
rc = hub.components[1]
found_events = []
hub.on("relic_found", lambda **kw: found_events.append(kw))


class FakeTracker:
    level, job, map, cur, need, active_total = 8, "Magician", MAP, None, None, 0.0


hub.state.bind(FakeTracker())
hub.emit("service_start", maps=[])
RJ = OUT / "relic.json"


def rj():
    return H.read_json(RJ, {})


def presence(tok, map, channel):
    return call("POST", f"/v2/rooms/{RID}/sync", {"presence": {"level": 30, "map": map, "channel": channel,
                                                               "state": "playing"}}, tok)


def state(tok=OWNER):
    return call("GET", f"/v2/rooms/{RID}/relic/state", token=tok)


def rise(kills, map=MAP, exp=15):
    """`kills` whole kills, one rise each (Orange Mushroom = 15 EXP on MAP)."""
    for _ in range(kills):
        hub.emit("exp_rise", t=time.time(), map=map, d=exp, party=False, gap=1.0)


def raw_rise(d, map=MAP, party=False):
    hub.emit("exp_rise", t=time.time(), map=map, d=d, party=party, gap=1.0)


try:
    # 1: start from the panel's command file ---------------------------------------------------------------------
    check(wait(lambda: rj().get("room") is True), "relic.json: the room answers with a relic part", rj())
    J.write_relic_command({"action": "start", "difficulty": "easy"}, OUT)
    check(wait(lambda: "owner or mod" in (rj().get("error") or "")), "a plain member's Start is refused (owner or mod only)", rj())
    check(not rj().get("active"), "...and no hunt is running")
    call("POST", f"/v2/rooms/{RID}/members/{ME}/promote", {}, OWNER)
    J.write_relic_command({"action": "start", "difficulty": "easy"}, OUT)
    check(wait(lambda: rj().get("active") is True and rj().get("pieces_total") == 12), "a promoted mod starts an Easy hunt", rj())
    check(rj().get("error") is None, "...no error left")
    lob = [r for r in call("GET", "/v2/lobby")[1]["rooms"] if r["id"] == RID][0]
    check(lob["activity"] == "Relic hunt · 0/12", "lobby text while running", lob["activity"])

    # 2: eligible kills ---------------------------------------------------------------------------------------
    FakeTracker.level = 40                                   # Lv 40 farming Lv 2-14 mobs: nothing counts
    rise(50)
    rise(30, "No Such Map")                                  # an unknown map counts 0
    FakeTracker.level = 8
    raw_rise(7)                                              # 7 EXP: no mob or pair of mobs here (a quest, a share)
    raw_rise(15, party=True)                                 # partied, no challenge component: never guessed
    time.sleep(1.0)
    check(rc.hunt.acc == 0 and state()[1]["found_count"] == 0, "out-of-band level, unknown map, no-kill rise, party share: 0 kills", (rc.hunt.acc, state()[1]["found_count"]))                                    # in band for every mob of the map
    presence(tok2, MAP, 3)                                   # Tester2 stands with me (same map and channel)
    presence(tok3, "Ellinia", 3)                             # Tester3 is elsewhere
    toaster = toast_mod.Toaster(type("Sh", (), {"root": None, "settings": lambda s, n: {"toast": True, "enabled": True},
                                                  "at": None, "px": lambda s, n: n})())
    shown = []
    toaster.show = lambda text, now, found=None, total=None: shown.append((text, found, total))
    toaster.tick()                                           # first look only syncs the sequence number
    rise(5)
    check(wait(lambda: state()[1]["found_count"] == 5), "5 mobs' worth in band: 5 pieces (every roll succeeds in this sim)", state()[1]["found_count"])
    check(wait(lambda: rj().get("found_count") == 5 and len(rj().get("found_ids") or []) == 5), "relic.json: found count and my ids", rj())
    check(rj()["together"] == {"n": 2, "bonus_pct": 5}, "together bonus from presence (same map + channel)", rj().get("together"))
    check(rj()["next_chance_pct"] and rj()["toast"] and rj()["toast"]["text"].startswith("+ Relic piece"), "next chance and toast text in relic.json", rj())
    check(found_events and sum(len(e["ids"]) for e in found_events) == 5, "relic_found events carry the ids", found_events[:1])
    toaster.last_check = 0
    toaster.tick()
    check(shown and shown[-1][0].startswith("+ Relic piece") and shown[-1][1] == 5 and shown[-1][2],
          "the toast fires on the new sequence number, with found / total", shown)
    pic = toast_mod.Toaster.image(type("T", (), {"shell": None})(), shown[-1][1], shown[-1][2])
    check(pic.mode == "RGBA" and pic.width > 150, "the notice draws (piece + two lines)", pic.size)
    FakeTracker.level = 40                                   # a PQ map: its mobs are far below Lv 40, still counted
    rise(1, "1st Accompaniment <Bonus>", exp=26)
    check(wait(lambda: state()[1]["found_count"] == 6), "a party quest map counts my kill whatever the mobs' level", state()[1]["found_count"])
    FakeTracker.level = 8
    rise(6)
    check(wait(lambda: state()[1]["found_count"] == 12), "the deck finishes after exactly 12 drops", state()[1]["found_count"])
    st = state()[1]
    check(sorted(p["id"] for p in st["pieces"] if p["state"] == "found") == list(range(12)), "...no duplicates")
    rise(5)
    time.sleep(0.8)
    check(state()[1]["found_count"] == 12 and rj().get("pending") == 0, "...and nothing more drops", rj().get("pending"))

    # 2b: the rise_kills service (challenge component, its own hub) ------------------------------------------
    from components.challenge.component import ChallengeComponent  # noqa: E402
    from gamedata import relic as relic_data  # noqa: E402
    ch_root = OUT / "challenge_hub"
    ch_root.mkdir(exist_ok=True)
    chub = compose.make_hub(ch_root, components=[ChallengeComponent])
    read = chub.ask("rise_kills")
    check(read is not None and read(MAP, 15, False) == 1 and read(MAP, 30, False) == 2 and read(MAP, 7, False) == 0,
          "rise_kills: one kill, two kills in one rise (30 = 15 + 15), no kill", (read(MAP, 15, False), read(MAP, 30, False), read(MAP, 7, False)))
    check(read(MAP, 15, True) == 0, "rise_kills: partied with my level unknown counts nothing", read(MAP, 15, True))
    check("1st Accompaniment <Last Stage>" in relic_data.pq_maps() and relic_data.weight("1st Accompaniment <Bonus>", 60) == 1.0
          and relic_data.weight(MAP, 60) == 0.0, "PQ maps weigh 1, an out-of-band map 0")

    # 3: the shared table ---------------------------------------------------------------------------------------
    b, cols, rows = st["board"], st["cols"], st["rows"]

    def centre(pid):
        return b["x"] + (pid % cols + 0.5) * b["w"] / cols, b["y"] + (pid // cols + 0.5) * b["h"] / rows

    def move(tok, pid, x, y):
        return call("POST", f"/v2/rooms/{RID}/relic/move", {"piece": pid, "x": x, "y": y}, tok)

    s, d = move(tok2, 0, 0.03, 0.04)
    check(s == 200 and d["placed"] is False, "Tester2 drags piece 1 onto the table")
    s, d = move(tok3, 0, 0.9, 0.9)
    check(s == 409 and d["conflict"] and (d["x"], d["y"]) == (0.03, 0.04), "Tester3's move in the same second loses (409, snap back)", (s, d))
    for pid in range(12):
        who = [tok2, tok3, OWNER][pid % 3]
        cx, cy = centre(pid)
        s, d = move(who, pid, cx + 0.004, cy - 0.004)
        if pid == 0 and s == 409:
            time.sleep(1.1)
            s, d = move(who, pid, cx + 0.004, cy - 0.004)
        if not (s == 200 and d["placed"]):
            check(False, f"piece {pid + 1} snaps and locks", (s, d))
    check(True, "all 12 pieces snap and lock (movers: Tester2, Tester3, the owner)")
    st = state()[1]
    check(st["finished"] and st["placed_count"] == 12, "the board is complete")
    check(st["most_found"] == ME and st["gallery"][0]["most_found"] == ME, "gallery: who found the most", st.get("most_found"))
    check(st["last_placed_by"] == "Hostone" or st["last_placed_by"] in ("Tester2", "Tester3"), "gallery: who placed the last piece", st.get("last_placed_by"))
    lob = [r for r in call("GET", "/v2/lobby")[1]["rooms"] if r["id"] == RID][0]
    check(lob["activity"] == "Relic hunt complete", "lobby text when complete", lob["activity"])
    check(wait(lambda: rj().get("finished") is True and rj().get("active") is False), "relic.json: finished, hunt no longer active", rj())

    # 4: the page is served and the token stays out of the path ---------------------------------------------------
    with urllib.request.urlopen(f"{base}/rooms/{RID}/relic") as r:
        check(r.status == 200 and b"relic.js" in r.read(), "GET /rooms/<id>/relic serves the table page")
    check(call("GET", f"/v2/rooms/{RID}/relic/state")[0] == 401, "...and its state needs a member token")
finally:
    hub.emit("service_stop")
    time.sleep(0.5)
    srv.shutdown()
    srv.server_close()
    app.close()

# 5: a relay restart on the same DATA_DIR ------------------------------------------------------------------------------
srv2 = make_server(Config(port=0, **CFG))
base2 = f"http://127.0.0.1:{srv2.server_address[1]}"
threading.Thread(target=srv2.serve_forever, daemon=True).start()
try:
    s, d = call("GET", f"/v2/rooms/{RID}/relic/state", token=OWNER, b=base2)
    check(s == 200 and d["finished"] and d["placed_count"] == 12 and len(d["gallery"]) == 1,
          "after a relay restart the finished board and the gallery are still there", (s, str(d)[:120]))
finally:
    srv2.shutdown()
    srv2.server_close()
    srv2.app.close()

print("\nFAILED: " + ", ".join(fails) if fails else "\nrelic sim OK")
sys.exit(1 if fails else 0)
