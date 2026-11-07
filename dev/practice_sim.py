"""practice_sim.py - the solo practice duel as plain state (components/challenge/duel.py): start, countdown, own kills
count, yellow shares are only counted aside, no events for the relay, a relay answer keeps it, it ends."""
import os
import sys
from pathlib import Path

os.environ["MAPLE_COMPANION_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components.challenge.duel import Duel  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(("ok   " if cond else "FAIL ") + name)


d = Duel()
T = 1000.0
check("starts", d.start_practice(5, now=T))
check("second start refused", not d.start_practice(5, now=T))
check("active practice view", d.active() is not None and d.active()["id"] == "practice")
check("countdown: a line before the start counts nothing", d.on_gain(T + 2, 6, 1, True, now=T + 2) == 0)
check("own line counts 1", d.on_gain(T + 6, 6, 1, True, now=T + 6) == 1)
check("teammate line counts 0", d.on_gain(T + 7, 3, 1, False, now=T + 7) == 0)
check("mine 1, shared 1", (d.mine, d.shared) == (1, 1))
check("no relay events", d.events == [] and "ch" not in d.post_fields(True, False))
d.on_reply({"challenges": []}, 0.0, now=T + 8)
check("a relay answer keeps the practice duel", d.active() is not None)
snap = d.snapshot(True, False, now=T + 8)
check("snapshot: mine_local 1, shared_local 1, busy",
      snap["active"]["mine_local"] == 1 and snap["active"]["shared_local"] == 1 and snap["busy"])
check("a line after the end counts nothing", d.on_gain(T + 5 + 301, 6, 0, True, now=T + 400) == 0)
done = d.practice_done(now=T + 400)
check("ends with a summary", done == {"minutes": 5, "kills": 1, "shared": 1})
check("board gone after the end", d.active() is None and d.snapshot(True, False, now=T + 401)["active"] is None)
print("\nall passed" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
