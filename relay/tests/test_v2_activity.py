"""Tests for host-started room activities (the kill race started by the owner / a mod).

    python relay/tests/test_v2_activity.py
Time is faked with clock.offset (Windows' clock is too coarse to rely on real waits).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, clock  # noqa: E402
from relayd.modules import challenge as C  # noqa: E402


class ActBase(Base):
    def setUp(self):
        super().setUp()
        self.rid, self.owner = self.mk(kind="hangout", owner="Owner1")
        self.tok = {"Owner1": self.owner}

    def live(self, name, **f):
        """Join (once) and post presence."""
        if name not in self.tok:
            self.tok[name] = self.joined(self.rid, name)
        s, d = self.sync(self.rid, self.tok[name], {"presence": {"map": "Henesys", **f}})
        self.assertEqual(s, 200, d)
        return d

    def act(self, who, action, **body):
        return self.call("POST", f"/v2/rooms/{self.rid}/challenge/{action}", body, self.tok[who])

    def begin(self, who="Owner1", minutes=5, **kw):
        return self.act(who, "start", minutes=minutes, **kw)

    def view(self, who="Owner1"):
        s, d = self.call("GET", f"/v2/rooms/{self.rid}", token=self.tok[who])
        self.assertEqual(s, 200, d)
        return d

    def lobby_activity(self):
        s, d = self.call("GET", "/v2/lobby")
        return [r for r in d["rooms"] if r["id"] == self.rid][0]["activity"]

    def advance(self, secs):
        clock.offset += secs


class TestStart(ActBase):
    def test_owner_not_live_is_organizer(self):
        for n in ("Tester1", "Tester2", "Tester3"):
            self.live(n)
        s, r = self.begin(minutes=2)
        self.assertEqual(s, 200, r)
        self.assertEqual((r["state"], r["creator"], r["host"], r["kind"], r["mode"]),
                         ("open", "Owner1", True, "race", "all"))
        self.assertEqual(r["invited"], ["Tester1", "Tester2", "Tester3"])
        self.assertEqual(r["players"], [])                  # the organizer is not a player
        self.assertAlmostEqual(r["decide_t"] - r["created_t"], 60, delta=0.01)

    def test_owner_live_plays(self):
        self.live("Owner1")
        self.live("Tester1")
        s, r = self.begin()
        self.assertEqual(s, 200, r)
        self.assertEqual([p["name"] for p in r["players"]], ["Owner1"])
        self.assertEqual(r["invited"], ["Tester1"])
        s, r = self.act("Tester1", "accept", id=r["id"])    # 2 accepted, everyone answered -> starts now
        self.assertEqual((s, r["state"], r["phase"]), (200, "running", "starting"))

    def test_member_cannot_start_or_stop(self):
        self.live("Tester1")
        self.live("Tester2")
        s, r = self.begin("Tester1")
        self.assertEqual(s, 403)
        self.assertEqual(self.begin()[0], 200)
        self.assertEqual(self.act("Tester1", "stop")[0], 403)

    def test_mod_can_start_and_stop(self):
        self.live("Tester1")
        self.live("Tester2")
        s, d = self.call("POST", f"/v2/rooms/{self.rid}/members/Tester1/promote", {}, self.owner)
        self.assertEqual(s, 200, d)
        s, r = self.begin("Tester1")                         # mod is live: plays
        self.assertEqual(s, 200, r)
        self.assertEqual(self.act("Tester1", "stop")[0], 200)

    def test_nobody_live_409(self):
        self.assertEqual(self.begin()[0], 409)
        self.joined(self.rid, "Tester1")                     # joined but never synced
        self.assertEqual(self.begin()[0], 409)

    def test_one_player_is_not_enough(self):
        self.live("Tester1")
        self.assertEqual(self.begin()[0], 409)               # organizer + 1 invitee = 1 possible player
        self.live("Owner1")
        self.assertEqual(self.begin()[0], 200)               # owner live + Tester1 = 2

    def test_validation_and_second_start(self):
        self.live("Tester1")
        self.live("Tester2")
        self.assertEqual(self.begin(minutes=7)[0], 400)
        self.assertEqual(self.begin(minutes=True)[0], 400)
        self.assertEqual(self.begin(kind="relic")[0], 400)
        self.assertEqual(self.begin(kind="race")[0], 200)
        self.assertEqual(self.begin()[0], 409)               # one at a time

    def test_member_modes_unchanged(self):
        self.live("Owner1")
        self.live("Tester1")
        s, r = self.act("Owner1", "create", mode="all", minutes=5)
        self.assertEqual((s, r["host"], r["kind"]), (200, False, "race"))
        self.assertNotIn("decide_t", r)
        s, r = self.act("Tester1", "accept", id=r["id"])     # first accept starts a member duel
        self.assertEqual(r["state"], "running")


class TestStartRule(ActBase):
    def open_race(self, names=("Tester1", "Tester2", "Tester3")):
        for n in names:
            self.live(n)
        return self.begin(minutes=2)[1]["id"]

    def test_two_accept_everyone_answered(self):
        cid = self.open_race()
        s, r = self.act("Tester1", "accept", id=cid)
        self.assertEqual(r["state"], "open")                 # 1 accepted, 2 still to answer
        s, r = self.act("Tester2", "accept", id=cid)
        self.assertEqual(r["state"], "open")                 # 2 accepted, one has not answered
        s, r = self.act("Tester3", "decline", id=cid)        # all answered -> starts
        self.assertEqual((s, r["state"]), (200, "running"))
        self.assertAlmostEqual(r["start_t"] - r["server_t"], C.CH_COUNTDOWN_S, delta=0.01)
        self.assertAlmostEqual(r["end_t"] - r["start_t"], 120, delta=0.01)
        self.assertEqual(sorted(p["name"] for p in r["players"]), ["Tester1", "Tester2"])

    def test_timeout_starts_with_two(self):
        cid = self.open_race()
        self.act("Tester1", "accept", id=cid)
        self.act("Tester2", "accept", id=cid)
        self.advance(59)
        self.assertEqual(self.act("Tester1", "get", id=cid)[1]["state"], "open")
        self.advance(2)
        s, r = self.act("Tester1", "get", id=cid)
        self.assertEqual((r["state"], r["phase"]), ("running", "starting"))
        s, r = self.act("Tester3", "accept", id=cid)         # a late yes during the countdown still joins
        self.assertEqual(s, 200, r)
        self.assertEqual(len(r["players"]), 3)

    def test_timeout_with_one_cancels(self):
        cid = self.open_race()
        self.act("Tester1", "accept", id=cid)
        self.advance(61)
        self.assertEqual(self.act("Tester1", "get", id=cid)[1]["state"], "cancelled")
        self.assertEqual(self.begin()[0], 200)               # free to start again

    def test_all_decline_cancels_at_once(self):
        cid = self.open_race(("Tester1", "Tester2"))
        self.act("Tester1", "decline", id=cid)
        s, r = self.act("Tester2", "decline", id=cid)
        self.assertEqual(r["state"], "cancelled")

    def test_one_accept_rest_decline_cancels(self):
        cid = self.open_race(("Tester1", "Tester2"))
        self.act("Tester1", "accept", id=cid)
        s, r = self.act("Tester2", "decline", id=cid)        # 1 accepted, nobody left to answer
        self.assertEqual(r["state"], "cancelled")

    def test_kills_scores_and_result(self):
        cid = self.open_race(("Tester1", "Tester2", "Tester3"))
        for n in ("Tester1", "Tester2", "Tester3"):
            s, r = self.act(n, "accept", id=cid)
        self.assertEqual(r["state"], "running")
        self.advance(C.CH_COUNTDOWN_S + 5)
        now = clock.now()
        self.act("Tester1", "kills", id=cid, kills=[{"s": 1, "n": 2, "t": now}])
        self.act("Tester3", "kills", id=cid, kills=[{"s": 1, "n": 1, "t": now}])
        d = self.view()                                       # the organizer watches
        cur = d["activity"]
        self.assertEqual((cur["state"], cur["host"]), ("running", True))
        self.assertEqual({p["name"]: p["score"] for p in cur["players"]},
                         {"Tester1": 2, "Tester2": 0, "Tester3": 1})
        self.advance(120)
        s, r = self.act("Tester2", "get", id=cid)
        self.assertEqual(r["state"], "done")
        self.assertEqual([x["name"] for x in r["result"]["ranking"]], ["Tester1", "Tester3", "Tester2"])
        self.assertEqual(r["result"]["winner"], "Tester1")

    def test_sync_shows_the_invite(self):
        self.open_race(("Tester1", "Tester2"))
        d = self.sync(self.rid, self.tok["Tester1"], {})[1]
        v = d["challenge"]["challenges"][0]
        self.assertEqual((v["host"], v["creator"], v["invited"]), (True, "Owner1", ["Tester1", "Tester2"]))
        d = self.sync(self.rid, self.owner, {})[1]            # the organizer is not in or invited to it
        self.assertEqual(d["challenge"]["challenges"], [])


class TestStop(ActBase):
    def test_stop_open_and_running(self):
        for n in ("Tester1", "Tester2"):
            self.live(n)
        self.assertEqual(self.act("Owner1", "stop")[0], 404)         # nothing to stop
        cid = self.begin()[1]["id"]
        s, r = self.act("Owner1", "stop", id=cid)
        self.assertEqual((s, r["state"]), (200, "cancelled"))
        cid = self.begin()[1]["id"]
        self.act("Tester1", "accept", id=cid)
        s, r = self.act("Tester2", "accept", id=cid)
        self.assertEqual(r["state"], "running")
        s, r = self.act("Owner1", "stop")
        self.assertEqual((s, r["state"]), (200, "cancelled"))
        self.assertEqual(self.act("Tester1", "get", id=cid)[1]["state"], "cancelled")
        self.assertEqual(self.sync(self.rid, self.tok["Tester1"], {})[1]["challenge"]["challenges"], [])


class TestLobbyAndOwnerView(ActBase):
    def test_lobby_activity_strings(self):
        for n in ("Tester1", "Tester2", "Tester3", "Tester4"):
            self.live(n)
        self.assertIsNone(self.lobby_activity())
        cid = self.begin(minutes=5)[1]["id"]
        self.assertEqual(self.lobby_activity(), "Kill race starting")
        for n in ("Tester1", "Tester2", "Tester3", "Tester4"):
            self.act(n, "accept", id=cid)
        self.assertEqual(self.lobby_activity(), "Kill race · 4 players · 5 min left")
        self.advance(5 + 180)
        self.assertEqual(self.lobby_activity(), "Kill race · 4 players · 2 min left")
        self.act("Owner1", "stop")
        self.assertIsNone(self.lobby_activity())

    def test_member_duel_strings_unchanged(self):
        self.live("Tester1")
        self.live("Tester2")
        cid = self.act("Tester1", "create", mode="1v1", minutes=5, target="Tester2")[1]["id"]
        self.assertEqual(self.lobby_activity(), "Duel waiting")
        self.act("Tester2", "accept", id=cid)
        self.assertEqual(self.lobby_activity(), "Duel running")

    def test_owner_view_fields(self):
        self.live("Tester1", state="playing")
        self.live("Tester2")
        s, d = self.join(self.rid, "Tester9")                # a member who never synced: offline
        self.assertEqual(s, 200)
        d = self.view()
        by = {m["name"]: m for m in d["members"]}
        self.assertEqual(set(by), {"Owner1", "Tester1", "Tester2", "Tester9"})
        self.assertEqual((by["Tester1"]["online"], by["Tester1"]["state"]), (True, "playing"))
        self.assertFalse(by["Tester9"]["online"])
        self.assertEqual(by["Owner1"]["role"], "owner")
        self.assertEqual(d["activities"]["race"]["minutes"], list(C.MINUTES))
        self.assertIsNone(d["activity"])
        self.assertEqual(d["requests"], [])
        self.begin()
        self.assertEqual(self.view()["activity"]["state"], "open")
        text = str(d)
        self.assertNotIn("token", text)                      # nothing secret in the owner view

    def test_owner_view_for_plain_member_is_unchanged(self):
        self.live("Tester1")
        d = self.view("Tester1")
        for k in ("activity", "activities", "requests"):
            self.assertNotIn(k, d)
        self.assertNotIn("online", d["members"][0])

    def test_pending_requests_in_owner_view(self):
        rid, owner = self.mk(access="approval", kind="hangout", owner="Owner2")
        self.joined(rid, "Tester5")
        s, d = self.call("GET", f"/v2/rooms/{rid}", token=owner)
        self.assertEqual(d["requests"], ["Tester5"])


if __name__ == "__main__":
    unittest.main()
