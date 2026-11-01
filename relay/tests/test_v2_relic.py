"""Tests for the relic hunt module (relay/relayd/modules/relic.py).

    python relay/tests/test_v2_relic.py
Time is faked with clock.offset; the dice are a seeded random.Random put into the module.
"""
import random
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, clock  # noqa: E402
from relayd.modules import relic as R  # noqa: E402


class AlwaysFind(random.Random):
    def random(self):
        return 0.0


class RelicBase(Base):
    def setUp(self):
        super().setUp()
        self.rid, self.owner = self.mk(kind="hangout", owner="Owner1")
        self.tok = {"Owner1": self.owner}
        self.mod = self.app.modules["relic"]
        self.mod.rng = AlwaysFind()

    def member(self, name):
        if name not in self.tok:
            self.tok[name] = self.joined(self.rid, name)
        return self.tok[name]

    def live(self, name, map="Henesys", channel=1):
        s, d = self.sync(self.rid, self.member(name), {"presence": {"map": map, "channel": channel}})
        self.assertEqual(s, 200, d)
        return d

    def kills(self, name, k, **pres):
        body = {"relic": {"kills": k}}
        if pres:
            body["presence"] = pres
        s, d = self.sync(self.rid, self.member(name), body)
        self.assertEqual(s, 200, d)
        return d["relic"]

    def act(self, who, action, **body):
        return self.call("POST", f"/v2/rooms/{self.rid}/relic/{action}", body, self.member(who))

    def state(self, who="Owner1"):
        s, d = self.call("GET", f"/v2/rooms/{self.rid}/relic/state", token=self.member(who))
        self.assertEqual(s, 200, d)
        return d

    def begin(self, difficulty="easy", **kw):
        s, d = self.act("Owner1", "start", difficulty=difficulty, **kw)
        self.assertEqual(s, 200, d)
        return d

    def lobby_activity(self):
        s, d = self.call("GET", "/v2/lobby")
        return [r for r in d["rooms"] if r["id"] == self.rid][0]["activity"]

    def place_all(self, who="Owner1"):
        for p in self.state(who)["pieces"]:
            if p["state"] == "found":
                s, d = self.act(who, "move", piece=p["id"], x=0.5, y=0.5)      # wrong place first
                clock.offset += 2
                s, d = self.call("GET", f"/v2/rooms/{self.rid}/relic/state", token=self.member(who))
                st = self.state(who)
                b = st["board"]
                cx = b["x"] + (p["id"] % st["cols"] + 0.5) * b["w"] / st["cols"]
                cy = b["y"] + (p["id"] // st["cols"] + 0.5) * b["h"] / st["rows"]
                s, d = self.act(who, "move", piece=p["id"], x=cx, y=cy)
                self.assertEqual(s, 200, d)
                self.assertTrue(d["placed"])


class TestCurve(unittest.TestCase):
    def check(self, n, people, hours, r):
        ps = R.curve(n, people, hours, r)
        b = n / (people * hours * R.KILLS_PER_HOUR)
        self.assertAlmostEqual(ps[0] / b, r, places=3)
        self.assertAlmostEqual(ps[n // 2] / b, 0.85, places=3)
        self.assertAlmostEqual(ps[-1] / b, 1.15, places=3)
        total = sum(1 / p for p in ps)
        self.assertAlmostEqual(total / (people * hours * R.KILLS_PER_HOUR), 1.0, delta=0.01)
        self.assertTrue(all(p > 0 for p in ps))
        return ps

    def test_difficulties(self):
        for key, (label, n, cols, rows, hours, people, r) in R.DIFFICULTIES.items():
            self.assertEqual(cols * rows, n)
            self.check(n, people, hours, r)

    def test_u_shape(self):
        ps = self.check(48, 6, 2.0, 2.5)
        low = min(range(48), key=lambda i: ps[i])
        self.assertTrue(10 < low < 40)
        self.assertGreater(ps[0], ps[low])
        self.assertGreater(ps[-1], ps[low])

    def test_custom_sizes(self):
        for n in (6, 20, 30, 64, 150, 400):
            if R.grid_for(n):
                self.check(n, 3, 3.0, R.r_for(n))

    def test_grid(self):
        self.assertEqual(R.grid_for(12), (4, 3))
        self.assertIsNone(R.grid_for(13))
        self.assertTrue(R.grid_for(R.nearest_grid_pieces(13)))


class TestStart(RelicBase):
    def test_permissions(self):
        self.live("Tester1")
        self.assertEqual(self.act("Tester1", "start", difficulty="easy")[0], 403)
        self.assertEqual(self.act("Tester1", "stop")[0], 403)
        self.begin()
        self.assertEqual(self.act("Owner1", "start", difficulty="easy")[0], 409)       # one hunt per room
        s, d = self.call("POST", f"/v2/rooms/{self.rid}/members/Tester1/promote", {}, self.owner)
        self.assertEqual(s, 200, d)
        self.assertEqual(self.act("Tester1", "stop")[0], 200)
        self.assertEqual(self.act("Tester1", "stop")[0], 404)
        self.assertEqual(self.act("Tester1", "start", difficulty="medium")[0], 200)     # a mod may start

    def test_bad_input(self):
        self.assertEqual(self.act("Owner1", "start", difficulty="nope")[0], 400)
        self.assertEqual(self.act("Owner1", "start", difficulty="custom", pieces=13, hours=1, people=2)[0], 400)
        self.assertEqual(self.act("Owner1", "start", difficulty="custom", pieces=24, hours=0, people=2)[0], 400)
        self.assertEqual(self.act("Owner1", "start", difficulty="custom", pieces=24, hours=1, people=0)[0], 400)
        self.assertEqual(self.act("Owner1", "start", difficulty="custom", pieces=400, hours=0.25, people=1)[0], 400)
        s, d = self.act("Owner1", "start", difficulty="custom", pieces=30, hours=3, people=3)
        self.assertEqual((s, d["pieces_total"], d["cols"] * d["rows"]), (200, 30, 30))

    def test_enabled_on_start_in_older_room(self):
        rid, tok = self.mk(owner="Owner2")
        self.call("PATCH", f"/v2/rooms/{rid}", {"modules": ["presence"]}, tok)
        s, d = self.call("GET", f"/v2/rooms/{rid}", token=tok)
        self.assertIn("relic", d["activities"])                      # offered before it is on
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}/relic/state", token=tok)[0], 404)
        s, d = self.call("POST", f"/v2/rooms/{rid}/relic/start", {"difficulty": "easy"}, tok)
        self.assertEqual(s, 200, d)
        self.assertIn("relic", self.call("GET", f"/v2/rooms/{rid}", token=tok)[1]["modules"])

    def test_owner_view_and_lobby(self):
        v = self.call("GET", f"/v2/rooms/{self.rid}", token=self.owner)[1]
        self.assertEqual([d["key"] for d in v["activities"]["relic"]["difficulties"]],
                         ["easy", "medium", "challenging", "epic", "hardcore"])
        self.assertIsNone(v["relic"])
        self.assertIsNone(self.lobby_activity())
        self.begin()
        self.assertEqual(self.lobby_activity(), "Relic hunt · 0/12")
        v = self.call("GET", f"/v2/rooms/{self.rid}", token=self.owner)[1]
        self.assertEqual((v["relic"]["state"], v["relic"]["pieces"]), ("running", 12))
        self.live("Tester1")
        self.assertNotIn("relic", self.call("GET", f"/v2/rooms/{self.rid}", token=self.member("Tester1"))[1])
        self.kills("Tester1", 3)
        self.assertEqual(self.lobby_activity(), "Relic hunt · 3/12")


class TestRolls(RelicBase):
    def test_deck_no_duplicates_finishes_at_n(self):
        self.begin()
        self.live("Tester1")
        seen = []
        for _ in range(20):
            clock.offset += 60                       # refill the rate cap
            seen += self.kills("Tester1", 12)["found"]
        self.assertEqual(sorted(seen), list(range(12)))
        r = self.kills("Tester1", 5)
        self.assertEqual((r["found_count"], r["found"]), (12, []))
        self.assertIsNone(r["next_chance_pct"])

    def test_cap(self):
        self.begin()
        self.live("Tester1")
        r = self.kills("Tester1", 1000)
        self.assertEqual(r["counted"], R.CAP_PER_MIN)
        self.assertEqual(r["found_count"], R.CAP_PER_MIN if R.CAP_PER_MIN < 12 else 12)
        r = self.kills("Tester1", 30)                          # no time passed: dropped
        self.assertEqual(r["counted"], 0)
        clock.offset += 30                                     # 30 s = 20 kills back
        self.assertEqual(self.kills("Tester1", 100)["counted"], 20)

    def test_fractions_accumulate(self):
        self.begin("challenging")
        self.live("Tester1")
        counted = [self.kills("Tester1", 0.4)["counted"] for _ in range(5)]
        self.assertEqual(counted, [0, 0, 1, 0, 1])          # 0.4 0.8 1.2 0.6 1.0
        self.assertEqual(self.kills("Tester1", float("nan") if False else -3)["counted"], 0)

    def test_no_rolls_without_hunt_or_after_stop(self):
        self.live("Tester1")
        self.assertEqual(self.kills("Tester1", 5)["active"], False)
        self.begin()
        self.assertEqual(self.act("Owner1", "stop")[0], 200)
        r = self.kills("Tester1", 5)
        self.assertEqual((r["active"], r.get("found_count")), (False, None))

    def test_chance_is_the_curve_not_a_constant(self):
        self.begin("easy")
        self.live("Tester1")
        r = self.kills("Tester1", 0)
        first = R.curve(12, 2, 0.5, 1.3)[0] * 100
        self.assertAlmostEqual(r["next_chance_pct"], round(first, 3), places=3)

    def test_bonus_scales_roll_chance(self):
        self.mod.rng = random.Random(1)
        self.begin("hardcore")
        names = ["Tester1", "Tester2", "Tester3", "Tester4"]
        for n in names:
            self.live(n)
        r = self.kills("Tester1", 40)
        self.assertEqual(r["together"], {"n": 4, "bonus_pct": 20})

    def test_together_tiers_use_map_and_channel(self):
        self.begin()
        want = []
        self.live("Tester1", "Henesys", 1)
        want.append(self.kills("Tester1", 0)["together"])
        self.live("Tester2", "Henesys", 1)
        want.append(self.kills("Tester1", 0)["together"])
        self.live("Tester3", "Henesys", 2)                     # other channel: not together
        want.append(self.kills("Tester1", 0)["together"])
        self.live("Tester4", "Ellinia", 1)                     # other map: not together
        want.append(self.kills("Tester1", 0)["together"])
        self.live("Tester5", "Henesys", 1)
        want.append(self.kills("Tester1", 0)["together"])
        self.live("Tester6", "Henesys", 1)
        want.append(self.kills("Tester1", 0)["together"])
        self.assertEqual([(w["n"], w["bonus_pct"]) for w in want],
                         [(1, 0), (2, 5), (2, 5), (2, 5), (3, 12), (4, 20)])
        self.live("Tester7", "Henesys", 1)
        self.assertEqual(self.kills("Tester1", 0)["together"], {"n": 5, "bonus_pct": 20})
        self.assertEqual(self.kills("Tester4", 0)["together"], {"n": 1, "bonus_pct": 0})


class TestTable(RelicBase):
    def found(self, n=3):
        self.begin()
        self.live("Tester1")
        self.kills("Tester1", n)
        return [p for p in self.state()["pieces"] if p["state"] == "found"]

    def test_state_shape_and_reply_ids(self):
        self.begin()
        self.live("Tester1")
        r = self.kills("Tester1", 2)
        self.assertEqual(len(r["found"]), 2)
        self.assertEqual(self.kills("Tester1", 0)["found"], [])          # delivered once
        st = self.state()
        self.assertEqual((st["pieces_total"], st["cols"], st["rows"], st["found_count"], st["placed_count"]),
                         (12, 4, 3, 2, 0))
        self.assertEqual(sum(1 for p in st["pieces"] if p["state"] == "hidden"), 10)
        f = [p for p in st["pieces"] if p["state"] == "found"][0]
        self.assertEqual(f["by"], "Tester1")
        self.assertTrue(0 <= f["x"] <= 1 and 0 <= f["y"] <= 1)
        b = st["board"]
        self.assertFalse(b["x"] < f["x"] < b["x"] + b["w"] and b["y"] < f["y"] < b["y"] + b["h"])   # beside the board
        hidden = [p for p in st["pieces"] if p["state"] == "hidden"][0]
        self.assertEqual(set(hidden), {"id", "state"})

    def test_move_snap_lock(self):
        p = self.found()[0]
        s, d = self.act("Tester1", "move", piece=p["id"], x=0.01, y=0.01)
        self.assertEqual((s, d["placed"]), (200, False))
        st = self.state()
        b = st["board"]
        cx = b["x"] + (p["id"] % 4 + 0.5) * b["w"] / 4
        cy = b["y"] + (p["id"] // 4 + 0.5) * b["h"] / 3
        s, d = self.act("Tester1", "move", piece=p["id"], x=cx + 0.003, y=cy - 0.003)
        self.assertEqual((s, d["placed"]), (200, True))
        self.assertEqual(self.state()["placed_count"], 1)
        s, d = self.act("Tester1", "move", piece=p["id"], x=0.1, y=0.1)         # locked
        self.assertEqual(s, 409)
        self.assertEqual(self.act("Tester1", "move", piece=11 if p["id"] != 11 else 10, x=0.1, y=0.1)[0], 404)  # not found yet

    def test_move_bad_input(self):
        p = self.found()[0]
        for body in ({"piece": p["id"], "x": 2, "y": 0.5}, {"piece": "a", "x": 0.5, "y": 0.5},
                     {"piece": p["id"], "x": True, "y": 0.5}, {"piece": p["id"]}):
            self.assertEqual(self.act("Tester1", "move", **body)[0], 400, body)

    def test_first_move_wins(self):
        p = self.found()[0]
        self.live("Tester2")
        self.assertEqual(self.act("Tester1", "move", piece=p["id"], x=0.05, y=0.05)[0], 200)
        s, d = self.act("Tester2", "move", piece=p["id"], x=0.9, y=0.9)
        self.assertEqual((s, d["conflict"]), (409, True))
        self.assertEqual((d["x"], d["y"]), (0.05, 0.05))                      # the client snaps back to this
        self.assertEqual(self.act("Tester1", "move", piece=p["id"], x=0.06, y=0.05)[0], 200)   # the same member again
        clock.offset += 1.5
        self.assertEqual(self.act("Tester2", "move", piece=p["id"], x=0.9, y=0.9)[0], 200)

    def test_complete_board(self):
        self.begin()
        self.live("Tester1")
        self.live("Tester2")
        for _ in range(3):
            clock.offset += 60
            self.kills("Tester1", 4)
        for _ in range(2):
            clock.offset += 60
            self.kills("Tester2", 4)
        self.assertEqual(self.state()["found_count"], 12)
        self.assertEqual(self.lobby_activity(), "Relic hunt · 12/12")
        self.place_all("Tester2")
        st = self.state()
        self.assertTrue(st["finished"])
        self.assertFalse(st["active"])
        self.assertEqual((st["placed_count"], st["last_placed_by"]), (12, "Tester2"))
        self.assertEqual(st["finders"], {"Tester1": 12})
        self.assertEqual(st["most_found"], "Tester1")
        self.assertEqual(st["gallery"][0]["most_found"], "Tester1")
        self.assertEqual(self.lobby_activity(), "Relic hunt complete")
        self.assertEqual(self.kills("Tester1", 5)["found_count"], 12)
        # a new hunt may follow; the gallery keeps the old one
        self.begin("medium")
        st = self.state()
        self.assertEqual((st["pieces_total"], len(st["gallery"])), (24, 1))
        self.assertEqual(self.lobby_activity(), "Relic hunt · 0/24")


class TestPersistence(RelicBase):
    def test_restart_keeps_the_hunt(self):
        self.begin()
        self.live("Tester1")
        self.kills("Tester1", 5)
        p = [x for x in self.state()["pieces"] if x["state"] == "found"][0]
        self.act("Tester1", "move", piece=p["id"], x=0.02, y=0.03)
        before = self.state()
        self.stop()
        Base.start(self)                                      # a new Relay on the same DATA_DIR (new port)
        self.mod = self.app.modules["relic"]
        after = self.state()
        for k in ("pieces", "found_count", "placed_count", "difficulty", "board"):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(self.lobby_activity(), "Relic hunt · 5/12")
        s, d = self.sync(self.rid, self.member("Tester1"), {"relic": {"kills": 0}})
        self.assertEqual(d["relic"]["found_count"], 5)
        # the deck survived too: 7 more drops complete the set without duplicates
        self.mod.rng = AlwaysFind()
        ids = [x["id"] for x in before["pieces"] if x["state"] != "hidden"]
        clock.offset += 120
        new = self.kills("Tester1", 12)["found"]
        self.assertEqual(sorted(ids + new), list(range(12)))


class TestPage(RelicBase):
    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_page_and_assets(self):
        s, body, h = self.get(f"/rooms/{self.rid}/relic")
        self.assertEqual(s, 200)
        self.assertIn(b"relic.js", body)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertNotIn(b"<script>", body)
        self.assertEqual(self.get("/rooms/relic.js")[0], 200)
        self.assertEqual(self.get("/rooms/relic.css")[0], 200)
        self.assertEqual(self.get("/rooms/relic.py")[0], 404)
        self.assertEqual(self.get("/rooms/not-valid/relic")[0], 404)
        self.assertEqual(self.get(f"/rooms/{self.rid}/relic/../../x")[0], 404)

    def test_state_needs_a_member_token(self):
        self.begin()
        self.assertEqual(self.call("GET", f"/v2/rooms/{self.rid}/relic/state")[0], 401)
        self.assertEqual(self.call("GET", f"/v2/rooms/{self.rid}/relic/state", token="nope")[0], 401)
        self.assertEqual(self.call("GET", f"/v2/rooms/{self.rid}/relic/state", token=self.owner)[0], 200)


if __name__ == "__main__":
    unittest.main()
