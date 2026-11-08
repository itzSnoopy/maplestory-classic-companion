"""Offline check of the party Heal credit (both ends).

  python dev/partyheal_sim.py

Receiver side: feed._party_heal_stats (seq counts every received party Heal, the average only uncapped ones),
the relay_post hook of the skillbar component (extra["healed"] + "_fast" once per new seq) and both
party link body shapes (v1 flat, v2 presence). Cleric side: partyfx.credit_heals (baseline, credit,
no credit without an own cast, two members, window, seq going down, stale list). Never goes online, writes nothing.
"""
import os
import sys
from pathlib import Path
os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from base import lean  # noqa: F401,E402,I001
from components.skillbar import feed as F  # noqa: E402
from components.skillbar import partyfx  # noqa: E402
from components.skillbar.component import SkillbarComponent  # noqa: E402
from components.party import partylink  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({info})"))
    if not ok:
        fails.append(name)


# ---- receiver side: feed stats
class FakeModel:
    data = {}


f = F.Feed()
f.model = FakeModel()
f.model.data = {}


def heal(t, amount, full=False):
    return {"t": t, "meter": "hp", "kind": "heal", "name": "Heal (party?)", "amount": amount, "to_full": full}


f._party_heal_stats([heal(1.0, 130), heal(2.0, 140), heal(3.0, 50, full=True),
                     {"t": 4.0, "meter": "hp", "kind": "heal", "name": "Heal", "amount": 99}])   # own Heal: not counted
st = f.model.data["party_heal"]
check("seq counts every party heal (capped too)", st["seq"] == 3, st)
check("n / sum / min / max / recent only uncapped", (st["n"], st["sum"], st["min"], st["max"], st["recent"]) ==
      (2, 270, 130, 140, [130, 140]), st)
check("last = last amount (any), last_t", (st["last"], st["last_t"]) == (50, 3.0), st)
check("heal_dirty set (same save path)", f.heal_dirty)
check("party_heal_info", f.party_heal_info() == {"avg": 135, "n": 2, "last": 50, "last_t": 3.0, "seq": 3}, f.party_heal_info())
check("post tuple (seq, last, avg)", f.party_heal_post == (3, 50, 135), f.party_heal_post)
f2 = F.Feed()
f2.model = FakeModel()
f2.model.data = {}
f2._party_heal_stats([heal(1.0, 50, full=True)])
check("only capped: avg None / post avg 0", f2.party_heal_info()["avg"] is None and f2.party_heal_post == (1, 50, 0),
      (f2.party_heal_info(), f2.party_heal_post))
f3 = F.Feed()
check("no party heal: info None", f3.party_heal_info() is None and f3.party_heal_post is None)

# ---- receiver side: relay_post hook, both link bodies
comp = SkillbarComponent()
comp.feed, comp._posted_seq = f, 0
extra = {}
comp.on_post(extra=extra, me={}, mode="v2")
check("hook adds healed list", extra.get("healed") == [3, 50, 135], extra)
check("hook asks _fast the first time", extra.get("_fast") is True, extra)
extra = {}
comp.on_post(extra=extra, me={}, mode="v2")
check("no _fast when seq unchanged", extra.get("healed") == [3, 50, 135] and "_fast" not in extra, extra)
f._party_heal_stats([heal(5.0, 120)])
extra = {}
comp.on_post(extra=extra)
check("_fast again after a new heal", extra.get("healed") == [4, 120, 130] and extra.get("_fast") is True, extra)
comp.feed = F.Feed()
extra = {}
comp.on_post(extra=extra)
check("nothing to say: nothing added", extra == {}, extra)
me = {"name": "Abc", "state": "playing"}
body = partylink.v2_body(me, {"healed": [4, 120, 130], "_fast": True})
check("v2 body: healed in presence, no _fast", body["presence"].get("healed") == [4, 120, 130]
      and "_fast" not in body["presence"], body)
v1 = {**me, **{k: v for k, v in {"healed": [4, 120, 130], "_fast": True}.items() if not k.startswith("_")}}
check("v1 body (the _loop_v1 merge): healed flat, no _fast", v1.get("healed") == [4, 120, 130] and "_fast" not in v1, v1)

# ---- Cleric side: credit_heals
T = 1000.0
M = lambda *a: [{"name": n, "healed": h} for n, h in a]   # noqa: E731

s = {}
c = partyfx.credit_heals(s, M(("Bob", [5, 130, 128])), [T - 1], T)
check("first sight is a baseline (no credit)", c == [], c)
c = partyfx.credit_heals(s, M(("Bob", [5, 130, 128])), [T - 1], T + 1)
check("same seq: no credit", c == [], c)
c = partyfx.credit_heals(s, M(("Bob", [6, 133, 129])), [T - 1], T + 2)
check("seq up + own cast within 12 s: credit", len(c) == 1 and c[0]["name"] == "Bob" and c[0]["amount"] == 133
      and c[0]["avg"] == 129 and c[0]["t"] == T + 2 and c[0]["cast_t"] == T - 1, c)
c = partyfx.credit_heals(s, M(("Bob", [6, 133, 129])), [T - 1], T + 3)
check("no second credit for the same seq", len(c) == 1, c)

s = {}
partyfx.credit_heals(s, M(("Bob", [1, 100, 100])), [], T)
c = partyfx.credit_heals(s, M(("Bob", [2, 100, 100])), [], T + 1)
check("no credit without an own cast", c == [], c)
c = partyfx.credit_heals(s, M(("Bob", [3, 100, 100])), [T - 20], T + 2)
check("cast older than 12 s: no credit", c == [], c)
c = partyfx.credit_heals(s, M(("Bob", [4, 110, 100])), [T - 20, T + 3 - partyfx.HEAL_CREDIT_S], T + 3)
check("cast exactly at the window edge counts", len(c) == 1, c)

s = {}
partyfx.credit_heals(s, M(("Bob", [1, 0, 0]), ("Cy", [7, 0, 0])), [], T)
c = partyfx.credit_heals(s, M(("Bob", [2, 130, 128]), ("Cy", [8, 140, 135])), [T + 0.5], T + 1.5)
check("two members both credited for the one cast", {x["name"]: x["amount"] for x in c} == {"Bob": 130, "Cy": 140}
      and len({x["cast_t"] for x in c}) == 1, c)
c = partyfx.credit_heals(s, M(("Bob", [3, 131, 128]), ("Cy", [8, 140, 135])), [T + 0.5, T + 4], T + 5)
check("one member of two: only that one, credited to the newest cast", len(c) == 3 and c[-1]["name"] == "Bob"
      and c[-1]["cast_t"] == T + 4, c)

s = {}
partyfx.credit_heals(s, M(("Bob", [9, 1, 1])), [], T)
c = partyfx.credit_heals(s, M(("Bob", [2, 1, 1])), [T], T + 1)
check("seq going down (restart / new character): re-baseline, no credit", c == [], c)
c = partyfx.credit_heals(s, M(("Bob", [3, 1, 1])), [T], T + 2)
check("... and counts again after", len(c) == 1, c)
c = partyfx.credit_heals(s, [], [T], T + 3)
c = partyfx.credit_heals(s, M(("Bob", [50, 1, 1])), [T + 2.5], T + 4)
check("gone from the list (stale party.json) = baseline again", len(c) == 1, c)   # the new sight is a baseline: only the earlier credit stays
c = partyfx.credit_heals(s, M(("Bob", [51, 1, 1])), [T + 2.5], T + 5)
check("... then a rise credits again", len(c) == 2, c)

s = {}
bad = [{"name": "A"}, {"name": "B", "healed": [1, 2]}, {"name": "C", "healed": "x"}, {"healed": [1, 2, 3]}, "junk",
       {"name": "D", "healed": [True, 2, 3]}]
c = partyfx.credit_heals(s, bad, [T], T)
check("members without a valid healed are ignored", c == [] and s["seen"] == {}, s)

s = {}
partyfx.credit_heals(s, M(("Bob", [1, 1, 1])), [], T)
partyfx.credit_heals(s, M(("Bob", [2, 1, 1])), [T], T + 1)
c = partyfx.credit_heals(s, M(("Bob", [2, 1, 1])), [], T + 1 + partyfx.CREDIT_KEEP_S + 1)
check("old credits are cleared", c == [], c)

print("\n" + ("FAILED: " + ", ".join(fails) if fails else "all ok"))
sys.exit(1 if fails else 0)
