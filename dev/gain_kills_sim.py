"""Offline test of duel kills from the game's EXP lines (components/challenge/duel.py on_gain / on_rise / settle).
No network, no player data in the repo. Usage: python dev/gain_kills_sim.py [FIXTURE_BURST_DIR]

 a. synthetic: a live Duel fed EXP-bar rises and message-corner gains (white = own kill, yellow = a teammate's):
    own lines count 1 each (solo and party), yellow 0, a rise covered by lines is not counted twice (also when the
    line comes 0.7 s after the rise), an uncovered solo rise falls back to a whole-mob match once after the 2.5 s
    settle, an uncovered partied rise counts 0, gains outside the duel window are ignored, overflow gains count.
 b. replay (skipped without the fixtures, local Nexon imagery): engine MsgCorner over the three burst folders; the
    first-look colours must match the measured ones (9 own in 20261030_222236, 0 in 20261030_230602, 2 in
    20261101_012726); then those gains + the vitals.csv EXP rises go into a Duel and the count equals the own count
    (partied and solo reading: no fallback double counts).
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["MAPLE_COMPANION_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from components.challenge.duel import Duel  # noqa: E402

fails = []
MAP = "Thicket Around the Beach III"          # Mano 148 EXP
DEFAULT_FIX = Path(r"C:\Users\snoopy\Documents\Personal\Maplestory\classic-helper\dev\fixtures\burst")


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


def live_duel(start=1000.0, end=1120.0):
    d = Duel()
    d.me = "Me"
    d.views = [{"id": "c1", "state": "running", "creator": "Me", "invited": [], "start_t": start, "end_t": end,
                "players": [{"name": "Me", "accepted": True, "ack": 0}]}]
    d._switch("c1")
    return d


def gain(d, t, own, bonus=0, overflow=False, now=None):
    return d.on_gain(t, 10, bonus, own, overflow, now=t if now is None else now)


# a ------------------------------------------------------------------------------------------------
d = live_duel()
gain(d, 1010.0, True, 4)
gain(d, 1011.0, True, 4)
gain(d, 1012.0, True, 0)
check(d.mine == 3 and len(d.events) == 3, "own (white) lines count 1 each, party bonus or not")
gain(d, 1013.0, False, 2)
gain(d, 1014.0, False, 0)
check(d.mine == 3, "yellow (teammate's) lines count 0")
check(abs(d.events[0]["t"] - 1010.0) < 1e-6, "the kill is stamped at the line's time (relay clock)")

d = live_duel()
d.on_rise(1020.0, MAP, 148, True, now=1020.0)
gain(d, 1020.7, True, 5, now=1020.7)
d.settle(1030.0)
check(d.mine == 1 and d.pending == [], "a rise covered by its line (0.7 s later) is not counted twice")
d = live_duel()
gain(d, 1019.5, True)
d.on_rise(1020.0, MAP, 148, False, now=1020.0)
d.settle(1024.0)
check(d.mine == 1, "a line 0.5 s BEFORE the rise covers it too")
d = live_duel()
d.on_rise(1020.0, MAP, 148, False, now=1020.0)
gain(d, 1020.6, False, now=1020.6)
d.settle(1030.0)
check(d.mine == 0 and d.pending == [], "a rise covered by a yellow line: 0, no fallback")

d = live_duel()
d.on_rise(1030.0, MAP, 148, False, now=1030.0)
d.settle(1032.0)
check(d.mine == 0 and len(d.pending) == 1, "an uncovered rise is held for 2.5 s")
d.settle(1033.0)
d.settle(1040.0)
check(d.mine == 1 and len(d.events) == 1 and d.pending == [], "...then the solo fallback counts the whole-mob match once")
d = live_duel()
d.on_rise(1030.0, MAP, 296, False, now=1030.0)
d.settle(1034.0)
check(d.mine == 2, "solo fallback: 2 Manos in one rise = 2 kills")
d = live_duel()
d.on_rise(1030.0, MAP, 7, False, now=1030.0)
d.settle(1034.0)
check(d.mine == 0, "solo fallback: a rise that is no whole mob counts 0")

d = live_duel()
d.on_rise(1040.0, MAP, 148, True, now=1040.0)
d.settle(1045.0)
check(d.mine == 0 and "not seen" in d.party_seen[0], "a partied rise with no line counts 0 (note: lines not seen)")
d = live_duel()
gain(d, 1035.0, False, 4)                       # a party bonus line 5 s earlier: the player is partied
d.on_rise(1040.0, MAP, 148, False, now=1040.0)
d.settle(1045.0)
check(d.mine == 0, "a rise (no party flag) after a party bonus line within 10 s is partied: counts 0")
d = live_duel()
gain(d, 1020.0, False, 4)
d.on_rise(1040.0, MAP, 148, False, now=1040.0)
d.settle(1045.0)
check(d.mine == 1, "...an older bonus line (> 10 s) does not make it partied: the solo fallback counts")

d = live_duel()
gain(d, 990.0, True)
gain(d, 1200.0, True)
d.on_rise(990.0, MAP, 148, False, now=990.0)
d.settle(1000.0)
check(d.mine == 0 and d.events == [], "gains and rises outside the duel window are ignored")
d = live_duel()
gain(d, 1050.0, True, 4, overflow=True)
check(d.mine == 1, "an overflow-flagged own line still counts")
d = Duel()
d.me = "Me"
gain(d, 1050.0, True)
check(d.mine == 0, "no live duel: nothing counted")

d = live_duel()
gain(d, 1060.0, True, 6)
check(d.snapshot(True, False, now=time.time())["party_note"] == "kills from the game's EXP lines",
      "challenge.json note: kills from the game's EXP lines")

# post_fields settles held rises (the relay post hook), on real time
now = time.time()
d = live_duel(start=0.0, end=1e12)
d.on_rise(now - 3.0, MAP, 148, False, now=now - 3.0)
check(d.mine == 0 and len(d.pending) == 1, "a fresh rise is held")
d.post_fields(True, False)
check(d.mine == 1, "post_fields settles a held rise (the relay post hook)")


# b ------------------------------------------------------------------------------------------------
def replay(fix):
    import csv
    import re
    from PIL import Image
    from engine.msgcorner import MsgCorner
    store = os.path.join(tempfile.gettempdir(), "msgcorner_test_templates.json")
    expect = {
        "20261030_222236": [(46.07, 1), (46.58, 1), (47.58, 1), (48.58, 1), (49.08, 1), (50.08, 1), (50.58, 1),
                            (51.58, 1), (53.09, 1), (32.55, 0), (36.56, 0), (50.58, 0), (51.08, 0), (53.09, 0),
                            (42.57, 0), (53.59, 0), (56.59, 0), (58.09, 0), (60.09, 0)],
        "20261030_230602": None,           # 6 gains, all yellow
        "20261101_012726": [(77.53, 1), (82.20, 1), (67.83, 0), (68.50, 0), (92.57, 0), (93.24, 0), (94.57, 0),
                            (98.59, 0)],
    }
    mc = MsgCorner(store)
    for name, want in expect.items():
        d0 = fix / name
        if not d0.is_dir():
            print(f"skip {name}: folder missing")
            continue
        facts = []
        for p in sorted(d0.glob("*.jpg")):
            if re.match(r"\d{4}\.\d\d_", p.name):
                facts += mc.feed(float(p.name[:7]), Image.open(p).convert("RGB"))
        gains = [(t, v) for t, k, v in facts if k == "exp_gain"]
        got = sorted((round(t, 2), int(v["own"])) for t, v in gains)
        if want is not None:
            check(got == sorted(want), f"{name}: first-look colours match ({sum(w[1] for w in want)} own of {len(want)})")
        else:
            check(len(gains) == 6 and not any(v["own"] for _, v in gains), f"{name}: 6 gains, all yellow (0 own)")
        own = sum(1 for _, v in gains if v["own"])
        rises = []
        if (d0 / "vitals.csv").exists():
            rows = list(csv.DictReader(open(d0 / "vitals.csv", newline="")))
            t0 = float(rows[0]["t"])
            prev, cur = None, None
            for r in rows:
                t, e = float(r["t"]) - t0, int(r["exp"])
                if prev is not None and e > prev[1]:
                    if cur and cur["t1"] == prev[0]:
                        cur["d"] += e - prev[1]
                        cur["t1"] = t
                    else:
                        cur = {"t": t, "t1": t, "d": e - prev[1]}
                        rises.append(cur)
                prev = (t, e)
        for partied in (True, False):
            d = live_duel(start=900.0, end=2000.0)
            items = [(1000 + t, 1, v) for t, v in gains] + [(1000 + r["t"], 0, r) for r in rises]
            for t, kind, x in sorted(items, key=lambda i: (i[0], i[1])):
                if kind:
                    d.on_gain(t, x["base"], x["party_bonus"], x["own"], x["overflow"], now=t)
                else:
                    d.on_rise(t, MAP, x["d"], partied, now=t)
            d.settle(1000 + 300)
            check(d.mine == own and d.pending == [],
                  f"{name}: duel count {d.mine} == own {own} ({'partied' if partied else 'solo'} reading, {len(rises)} bar rises)")


fix = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIX
if fix.is_dir():
    replay(fix)
else:
    print(f"skip replay: fixture folder {fix} missing")

print("\n" + ("all passed" if not fails else f"{len(fails)} failed"))
sys.exit(1 if fails else 0)
