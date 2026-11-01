"""Tests for the relayd challenge module (ported from the v1 test_ch_* cases).

    python relay/tests/test_v2_challenge.py
Time is faked with clock.offset (Windows' clock is too coarse to rely on real waits).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, clock  # noqa: E402
from relayd.modules import challenge as C  # noqa: E402


class ChBase(Base):
    def setUp(self):
        super().setUp()
        self.rid, self.alice = self.mk(kind="hangout")     # hangout: no member cap
        self.tok = {"Alice": self.alice}

    def member(self, name, **f):
        """Join (once) and post presence on a map."""
        if name not in self.tok:
            self.tok[name] = self.joined(self.rid, name)
        s, d = self.sync(self.rid, self.tok[name], {"presence": {"map": "Henesys", **f}})
        self.assertEqual(s, 200, d)
        return d

    def act(self, who, action, **body):
        return self.call("POST", f"/v2/rooms/{self.rid}/challenge/{action}", body, self.tok[who])

    def mkc(self, name="Alice", mode="1v1", minutes=5, **kw):
        return self.act(name, "create", mode=mode, minutes=minutes, **kw)

    def get(self, name, cid):
        return self.act(name, "get", id=cid)

    def started(self, minutes=5):
        """Alice challenges Bob, Bob accepts; returns the id and the start time."""
        self.member("Alice")
        self.member("Bob")
        cid = self.mkc(target="Bob", minutes=minutes)[1]["id"]
        s, r = self.act("Bob", "accept", id=cid)
        self.assertEqual(s, 200, r)
        return cid, r["start_t"]

    def score(self, cid, name):
        players = self.get("Alice", cid)[1]["players"]
        return {p["name"]: p["score"] for p in players}[name]


class TestChallenge(ChBase):
    def test_create_accept_start_clock(self):
        self.member("Alice")
        self.member("Bob")
        s, r = self.mkc(target="Bob", minutes=5)
        self.assertEqual((s, r["state"], r["invited"], r["start_t"]), (200, "open", ["Bob"], None))
        self.assertEqual((r["type"], r["creator"], r["phase"]), ("kills", "Alice", "open"))
        s, r = self.act("Bob", "accept", id=r["id"])
        self.assertEqual((s, r["state"], r["phase"]), (200, "running", "starting"))
        self.assertAlmostEqual(r["start_t"] - r["server_t"], C.CH_COUNTDOWN_S, delta=0.01)
        self.assertAlmostEqual(r["end_t"] - r["start_t"], 300, delta=0.01)
        self.assertEqual(sorted(p["name"] for p in r["players"]), ["Alice", "Bob"])

    def test_validation(self):
        self.member("Alice")
        self.assertEqual(self.mkc(mode="duel")[0], 400)
        self.assertEqual(self.mkc(minutes=7)[0], 400)
        self.assertEqual(self.mkc(minutes=True)[0], 400)
        self.assertEqual(self.mkc(target="Alice")[0], 400)
        self.assertEqual(self.mkc(target="bad name")[0], 400)
        self.assertEqual(self.mkc(target="Nobody")[0], 404)
        self.assertEqual(self.mkc(mode="all")[0], 409)                 # nobody to invite
        self.assertEqual(self.mkc(mode="map")[0], 409)                 # nobody on the map
        self.assertEqual(self.act("Alice", "accept")[0], 400)          # no id
        self.assertEqual(self.get("Alice", "deadbeef")[0], 404)
        self.assertEqual(self.get("Alice", "nothex!!")[0], 404)
        self.assertEqual(self.act("Alice", "frobnicate", id="x")[0], 404)
        self.assertEqual(self.call("POST", f"/v2/rooms/{self.rid}/challenge/create", {"mode": "all"})[0], 401)

    def test_modes_and_one_at_a_time(self):
        self.member("Alice")
        self.member("Bob")
        self.member("Cy", map="Ellinia")
        s, r = self.mkc(mode="map")
        self.assertEqual(r["invited"], ["Bob"])                        # same map only
        s, r2 = self.mkc(name="Cy", mode="all")
        self.assertEqual(r2["invited"], ["Alice", "Bob"])
        self.assertEqual(self.mkc(target="Bob")[0], 409)               # Alice already waits on her own
        self.act("Bob", "accept", id=r["id"])
        self.assertEqual(self.act("Bob", "accept", id=r2["id"])[0], 409)     # Bob is busy
        self.assertEqual(self.act("Cy", "accept", id=r["id"])[0], 409)       # not invited
        self.assertEqual(self.mkc(name="Bob", target="Cy")[0], 409)

    def test_map_needs_own_presence_and_live_rows_only(self):
        self.tok["Bob"] = self.joined(self.rid, "Bob")
        self.member("Bob")
        self.assertEqual(self.mkc(name="Bob", mode="map")[0], 409)     # Bob has no peers yet
        self.member("Alice")
        clock.offset += 60
        self.member("Bob")                                             # Alice's row is 60 s old: live
        self.assertEqual(self.mkc(name="Bob", mode="map")[1]["invited"], ["Alice"])

    def test_stale_presence_not_invited(self):
        self.member("Alice")
        self.member("Bob")
        clock.offset += 100                                            # Bob's row expired (90 s)
        self.member("Alice")
        self.assertEqual(self.mkc(mode="all")[0], 409)
        self.assertEqual(self.mkc(target="Bob")[0], 200)               # 1v1 only needs membership

    def test_decline_cancel_expire(self):
        self.member("Alice")
        self.member("Bob")
        cid = self.mkc(target="Bob")[1]["id"]
        s, r = self.act("Bob", "decline", id=cid)
        self.assertEqual((s, r["state"]), (200, "declined"))
        cid = self.mkc(target="Bob")[1]["id"]
        self.assertEqual(self.act("Bob", "cancel", id=cid)[0], 409)
        self.assertEqual(self.act("Alice", "cancel", id=cid)[1]["state"], "cancelled")
        cid = self.mkc(target="Bob")[1]["id"]
        clock.offset += C.CH_PENDING_S + 1
        self.assertEqual(self.act("Bob", "accept", id=cid)[0], 409)
        self.assertEqual(self.get("Bob", cid)[1]["state"], "expired")

    def test_group_join_before_start_only(self):
        for n in ("Alice", "Bob", "Cy", "Di"):
            self.member(n)
        cid = self.mkc(mode="all")[1]["id"]
        self.act("Bob", "accept", id=cid)
        self.assertEqual(self.act("Cy", "accept", id=cid)[0], 200)     # still counting down
        clock.offset += C.CH_COUNTDOWN_S + 1
        self.assertEqual(self.act("Di", "accept", id=cid)[0], 409)     # started
        self.assertEqual(self.act("Di", "decline", id=cid)[0], 200)

    def test_kills_count_in_window_and_dedupe(self):
        cid, start = self.started()
        clock.offset += 10                                             # live, 5 s in
        t = clock.now()
        ev = [{"s": 1, "n": 1, "t": t}, {"s": 2, "n": 2, "t": t}]
        s, r = self.act("Alice", "kills", id=cid, kills=ev)
        self.assertEqual([(p["name"], p["score"], p["ack"]) for p in r["players"]],
                         [("Alice", 3, 2), ("Bob", 0, 0)])
        s, r = self.act("Alice", "kills", id=cid, kills=ev + [{"s": 3, "n": 1, "t": t}])   # resend
        self.assertEqual((r["players"][0]["score"], r["players"][0]["ack"]), (4, 3))
        self.act("Alice", "kills", id=cid, kills=[{"s": 4, "n": 9, "t": t}, {"s": 5, "n": 0, "t": t},
                                                  {"s": 6, "n": True}, "x"])
        self.assertEqual(self.score(cid, "Alice"), 4)                  # bad sizes ignored (but acked)
        self.assertEqual(self.get("Alice", cid)[1]["players"][0]["ack"], 6)

    def test_kills_before_start_after_end_and_stamps(self):
        cid, start = self.started()
        self.act("Alice", "kills", id=cid, kills=[{"s": 1, "n": 1}])   # countdown: not live yet
        self.assertEqual(self.score(cid, "Alice"), 0)
        clock.offset += 10
        now = clock.now()
        self.act("Alice", "kills", id=cid, kills=[{"s": 2, "n": 1, "t": start - 1000}])   # before
        self.act("Alice", "kills", id=cid, kills=[{"s": 3, "n": 1, "t": now + 1000}])     # after end
        self.assertEqual(self.score(cid, "Alice"), 0)
        clock.offset += 300                                            # the end passed long ago
        self.act("Alice", "kills", id=cid, kills=[{"s": 4, "n": 1, "t": now}])
        self.assertEqual(self.score(cid, "Alice"), 0)

    def test_late_event_inside_grace_counts(self):
        cid, start = self.started(minutes=2)
        end = start + 120
        clock.offset += 5 + 120 + 2                                    # 2 s past the end
        self.act("Alice", "kills", id=cid, kills=[{"s": 1, "n": 1, "t": end - 1}])
        self.assertEqual(self.score(cid, "Alice"), 1)

    def test_rate_cap(self):
        cid, start = self.started()
        clock.offset += 15                                             # 10 s live -> cap 10*1.5+4 = 19
        t = clock.now()
        for i in range(1, 15):
            self.act("Alice", "kills", id=cid, kills=[{"s": i, "n": 4, "t": t}])
        self.assertEqual(self.score(cid, "Alice"), 16)                 # 4 x 4; the fifth would pass 19

    def test_result_win_and_tie(self):
        cid, _ = self.started(minutes=2)
        clock.offset += 10
        t = clock.now()
        self.act("Alice", "kills", id=cid, kills=[{"s": 1, "n": 2, "t": t}])
        self.act("Bob", "kills", id=cid, kills=[{"s": 1, "n": 1, "t": t}])
        clock.offset += 130                                            # over, past the grace
        r = self.get("Bob", cid)[1]
        self.assertEqual((r["state"], r["result"]["winner"], r["result"]["tie"]), ("done", "Alice", False))
        self.assertEqual(r["result"]["ranking"], [{"name": "Alice", "score": 2}, {"name": "Bob", "score": 1}])
        cid, _ = self.started(minutes=2)                               # free players can start again
        clock.offset += 10
        self.act("Alice", "kills", id=cid, kills=[{"s": 1, "n": 1, "t": clock.now()}])
        self.act("Bob", "kills", id=cid, kills=[{"s": 1, "n": 1, "t": clock.now()}])
        clock.offset += 130
        r = self.get("Alice", cid)[1]
        self.assertEqual((r["result"]["tie"], r["result"]["winner"]), (True, None))
        clock.offset += C.CH_KEEP_S + 10                               # forgotten after a while
        self.assertEqual(self.get("Alice", cid)[0], 404)

    def test_sync_part(self):
        cid, _ = self.started()
        d = self.member("Alice")
        self.assertEqual([c["id"] for c in d["challenge"]["challenges"]], [cid])
        clock.offset += 10
        t = clock.now()
        s, d = self.sync(self.rid, self.tok["Alice"], {"challenge": {"id": cid, "kills": [
            {"s": 1, "n": 1, "t": t}, {"s": 2, "n": 1, "t": t}]}})
        mine = {p["name"]: p for p in d["challenge"]["challenges"][0]["players"]}
        self.assertEqual((mine["Alice"]["score"], mine["Alice"]["ack"]), (2, 2))
        s, d = self.sync(self.rid, self.tok["Bob"], {"challenge": {"id": cid, "kills": [{"s": 1, "n": 1, "t": t}]}})
        self.assertEqual({p["name"]: p["score"] for p in d["challenge"]["challenges"][0]["players"]},
                         {"Alice": 2, "Bob": 1})
        d = self.member("Cy")
        self.assertEqual(d["challenge"]["challenges"], [])             # not involved
        s, d = self.sync(self.rid, self.tok["Alice"], {"challenge": {"id": "nope", "kills": 5}})
        self.assertEqual((s, len(d["challenge"]["challenges"])), (200, 1))   # junk ignored
        s, d = self.sync(self.rid, self.tok["Alice"], {"challenge": "x"})
        self.assertEqual(s, 200)
        clock.offset += 300                                            # finished: result shows ~2 min
        d = self.sync(self.rid, self.tok["Alice"])[1]
        self.assertEqual(d["challenge"]["challenges"][0]["state"], "done")
        clock.offset += C.CH_SHOW_RESULT_S
        d = self.sync(self.rid, self.tok["Alice"])[1]
        self.assertEqual(d["challenge"]["challenges"], [])

    def test_invited_see_open_challenge_on_sync(self):
        self.member("Alice")
        self.member("Bob")
        cid = self.mkc(target="Bob")[1]["id"]
        d = self.member("Bob")
        c = d["challenge"]["challenges"][0]
        self.assertEqual((c["id"], c["state"], c["invited"]), (cid, "open", ["Bob"]))

    def test_limit_per_room(self):
        n = C.CH_MAX + 2
        for i in range(n):
            self.member(f"P{i}a")
            self.member(f"P{i}b")
        made = [self.mkc(name=f"P{i}a", target=f"P{i}b")[0] for i in range(n)]
        self.assertEqual(made.count(200), C.CH_MAX)
        self.assertEqual(made[-1], 429)

    def test_rooms_are_separate(self):
        self.member("Alice")
        self.member("Bob")
        cid = self.mkc(target="Bob")[1]["id"]
        rid2, alice2 = self.mk(name="Other", owner="Alice")
        s, _ = self.call("POST", f"/v2/rooms/{rid2}/challenge/get", {"id": cid}, alice2)
        self.assertEqual(s, 404)
        # and Alice can be in a challenge in each room (one per member per room)
        self.sync(rid2, alice2, {"presence": {"map": "Henesys"}})
        bob2 = self.joined(rid2, "Bob")
        self.sync(rid2, bob2, {"presence": {"map": "Henesys"}})
        s, _ = self.call("POST", f"/v2/rooms/{rid2}/challenge/create",
                         {"mode": "1v1", "minutes": 2, "target": "Bob"}, alice2)
        self.assertEqual(s, 200)


class TestChallengeIdentity(ChBase):
    def test_name_comes_from_token(self):
        self.member("Alice")
        self.member("Bob")
        self.member("Cy")
        s, r = self.act("Alice", "create", mode="1v1", minutes=5, target="Bob", name="Cy")
        self.assertEqual((s, r["creator"]), (200, "Alice"))
        cid = r["id"]
        # Cy tries to accept as Bob: the name is ignored, Cy is not invited
        self.assertEqual(self.act("Cy", "accept", id=cid, name="Bob")[0], 409)
        self.assertEqual(self.act("Cy", "decline", id=cid, name="Bob")[0], 409)
        # Bob tries to cancel as Alice
        self.assertEqual(self.act("Bob", "cancel", id=cid, name="Alice")[0], 409)
        self.act("Bob", "accept", id=cid)
        clock.offset += 10
        t = clock.now()
        s, r = self.act("Alice", "kills", id=cid, name="Bob", kills=[{"s": 1, "n": 2, "t": t}])
        self.assertEqual({p["name"]: p["score"] for p in r["players"]}, {"Alice": 2, "Bob": 0})

    def test_target_must_be_member(self):
        self.member("Alice")
        self.assertEqual(self.mkc(target="Zed")[0], 404)
        rid, alice = self.mk(access="approval", name="Gate")
        self.joined(rid, "Pia")                                        # pending, not active
        s, _ = self.call("POST", f"/v2/rooms/{rid}/challenge/create",
                         {"mode": "1v1", "minutes": 2, "target": "Pia"}, alice)
        self.assertEqual(s, 404)

    def test_other_rooms_token_is_refused(self):
        self.member("Alice")
        self.member("Bob")
        cid = self.mkc(target="Bob")[1]["id"]
        rid, alice = self.mk(name="Other")
        s, _ = self.call("POST", f"/v2/rooms/{self.rid}/challenge/get", {"id": cid}, alice)
        self.assertEqual(s, 401)                                       # token of another room


class TestChallengeModule(ChBase):
    def test_disabled_module(self):
        self.member("Alice")
        self.member("Bob")
        self.assertEqual(self.mkc(target="Bob")[0], 200)
        s, _ = self.call("PATCH", f"/v2/rooms/{self.rid}", {"modules": ["presence"]}, self.alice)
        self.assertEqual(s, 200)
        self.assertEqual(self.mkc(target="Bob")[0], 404)
        self.assertEqual(self.get("Alice", "deadbeef")[0], 404)
        d = self.member("Alice")
        self.assertNotIn("challenge", d)
        self.assertIn("presence", d)

    def test_existing_room_without_module_keeps_it_off(self):
        rid, alice = self.mk(name="Old")
        self.call("PATCH", f"/v2/rooms/{rid}", {"modules": ["presence"]}, alice)
        s, d = self.sync(rid, alice, {"presence": {"level": 3}})
        self.assertEqual(set(d), {"server_t", "presence"})

    def test_new_rooms_get_both_modules(self):
        s, d = self.call("GET", f"/v2/rooms/{self.rid}", token=self.alice)
        self.assertEqual(d["modules"], ["presence", "challenge", "relic"])

    def test_kicked_creator_cancels(self):
        for n in ("Alice", "Bob", "Cy"):
            self.member(n)
        cid = self.mkc(name="Bob", mode="all")[1]["id"]
        self.assertEqual(self.call("POST", f"/v2/rooms/{self.rid}/members/Bob/kick", {}, self.alice)[0], 200)
        r = self.get("Cy", cid)[1]
        self.assertEqual(r["state"], "cancelled")
        d = self.member("Cy")
        self.assertEqual(d["challenge"]["challenges"], [])
        # the kicked name may come back and is free to start again
        self.tok.pop("Bob")
        self.member("Bob")
        self.assertEqual(self.mkc(name="Bob", target="Cy")[0], 200)

    def test_kicked_invitee_dropped(self):
        for n in ("Alice", "Bob", "Cy"):
            self.member(n)
        cid = self.mkc(mode="all")[1]["id"]
        self.call("POST", f"/v2/rooms/{self.rid}/members/Cy/kick", {}, self.alice)
        r = self.get("Alice", cid)[1]
        self.assertEqual((r["state"], r["invited"]), ("open", ["Bob"]))
        self.call("POST", f"/v2/rooms/{self.rid}/members/Bob/kick", {}, self.alice)
        self.assertEqual(self.get("Alice", cid)[1]["state"], "cancelled")      # nobody left to answer

    def test_leave_cleans_up(self):
        self.member("Alice")
        self.member("Bob")
        cid = self.mkc(name="Bob", target="Alice")[1]["id"]
        self.call("POST", f"/v2/rooms/{self.rid}/leave", {}, self.tok["Bob"])
        self.assertEqual(self.get("Alice", cid)[1]["state"], "cancelled")

    def test_room_delete_clears_state(self):
        self.member("Alice")
        self.member("Bob")
        self.mkc(target="Bob")
        mod = self.app.modules["challenge"]
        self.assertIn(self.rid, mod.active_rooms())
        self.tok.pop("Bob")
        self.call("POST", f"/v2/rooms/{self.rid}/members/Bob/kick", {}, self.alice)
        self.call("DELETE", f"/v2/rooms/{self.rid}", token=self.alice)
        self.assertNotIn(self.rid, mod.active_rooms())

    def test_cleanup_ticks_finished(self):
        self.member("Alice")
        self.member("Bob")
        self.mkc(target="Bob")
        mod = self.app.modules["challenge"]
        clock.offset += C.CH_PENDING_S + 5
        self.app.cleanup()                                             # expires it
        self.assertEqual(mod.active_rooms(), [self.rid])
        clock.offset += C.CH_KEEP_S + 5
        self.app.cleanup()                                             # forgets it
        self.assertEqual(mod.active_rooms(), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
