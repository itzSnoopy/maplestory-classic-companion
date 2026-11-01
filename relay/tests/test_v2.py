"""Tests for relayd (relay v2). stdlib unittest, in-process server on 127.0.0.1.

    python relay/tests/test_v2.py
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RELAY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RELAY))
from relayd import clock  # noqa: E402
from relayd.config import Config  # noqa: E402
from relayd.server import make_server  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base  # noqa: E402
from relayd.errors import ApiError  # noqa: E402
import party_relay  # noqa: E402


class TestBasics(Base):
    def test_health(self):
        s, d = self.call("GET", "/health")
        self.assertEqual(s, 200)
        self.assertTrue(d["ok"])
        for k in ("version", "source", "lobby", "feedback", "legacy_v1"):
            self.assertIn(k, d)
        self.assertTrue(d["lobby"])
        self.assertTrue(d["legacy_v1"])

    def test_room_id_format(self):
        rid, _ = self.mk()
        self.assertRegex(rid, r"^[a-hj-km-np-z2-9]{4}-[a-hj-km-np-z2-9]{4}$")

    def test_bad_input(self):
        s, _ = self.call("POST", "/v2/rooms", raw=b"{nope")
        self.assertEqual(s, 400)
        s, _ = self.call("POST", "/v2/rooms", {"name": "x", "owner": "Al ice", "kind": "party",
                                              "access": "open"})
        self.assertEqual(s, 400)
        s, _ = self.call("POST", "/v2/rooms", {"name": "", "owner": "Alice", "kind": "party",
                                              "access": "open"})
        self.assertEqual(s, 400)
        s, _ = self.call("POST", "/v2/rooms", raw=b"x" * 9000)
        self.assertEqual(s, 413)
        s, _ = self.call("GET", "/v2/nothing")
        self.assertEqual(s, 404)


class TestCreate(Base):
    def body(self, **kw):
        d = dict(name="Room", owner="Alice", kind="party", access="open")
        d.update(kw)
        return d

    def test_anon_off_needs_host_key(self):
        self.app.cfg.allow_anyone_create = False
        s, _ = self.call("POST", "/v2/rooms", self.body())
        self.assertEqual(s, 403)
        key = self.app.store.create_host_key("friend", 2, None)
        s, d = self.call("POST", "/v2/rooms", self.body(host_key=key))
        self.assertEqual(s, 200)
        self.assertEqual(d["room"]["modules"], ["presence", "challenge"])
        s, d = self.call("POST", "/v2/rooms", self.body(name="Two"), headers={"X-Host-Key": key})
        self.assertEqual(s, 200)

    def test_db_setting_used_when_env_unset(self):
        self.app.cfg.allow_anyone_create = None
        s, _ = self.call("POST", "/v2/rooms", self.body())
        self.assertEqual(s, 403)
        self.app.store.set_setting("allow_anyone_create", "1")
        s, _ = self.call("POST", "/v2/rooms", self.body())
        self.assertEqual(s, 200)

    def test_bad_host_keys(self):
        self.app.cfg.allow_anyone_create = False
        s, _ = self.call("POST", "/v2/rooms", self.body(host_key="hk_nonsense"))
        self.assertEqual(s, 403)
        revoked = self.app.store.create_host_key("r", 2, None)
        self.app.store.revoke_host_key(self.app.store.host_key_by_hash(
            __import__("relayd.auth", fromlist=["x"]).sha256hex(revoked))["id"])
        s, _ = self.call("POST", "/v2/rooms", self.body(host_key=revoked))
        self.assertEqual(s, 403)
        expired = self.app.store.create_host_key("e", 2, clock.now() - 10)
        s, _ = self.call("POST", "/v2/rooms", self.body(host_key=expired))
        self.assertEqual(s, 403)

    def test_host_key_room_limit_and_account_limit(self):
        key = self.app.store.create_host_key("one", 1, None)
        self.assertEqual(self.call("POST", "/v2/rooms", self.body(host_key=key))[0], 200)
        s, d = self.call("POST", "/v2/rooms", self.body(host_key=key))
        self.assertEqual(s, 409, d)
        self.app.cfg.max_rooms_per_account = 2
        key2 = self.app.store.create_host_key("many", 10, None)
        for _ in range(2):
            self.assertEqual(self.call("POST", "/v2/rooms", self.body(host_key=key2))[0], 200)
        self.assertEqual(self.call("POST", "/v2/rooms", self.body(host_key=key2))[0], 409)

    def test_total_rooms(self):
        self.app.cfg.max_rooms = 2
        self.mk()
        self.mk()
        s, _ = self.call("POST", "/v2/rooms", self.body())
        self.assertEqual(s, 503)

    def test_anon_daily_limit(self):
        self.app.cfg.anon_creates_per_day = 2
        self.mk()
        self.mk()
        s, _ = self.call("POST", "/v2/rooms", self.body())
        self.assertEqual(s, 429)

    def test_secret_rules(self):
        for access in ("private", "unlisted"):
            s, _ = self.call("POST", "/v2/rooms", self.body(access=access))
            self.assertEqual(s, 400)
        s, _ = self.call("POST", "/v2/rooms", self.body(access="private", secret="12"))
        self.assertEqual(s, 400)
        s, _ = self.call("POST", "/v2/rooms", self.body(access="private", secret="1234"))
        self.assertEqual(s, 200)
        s, _ = self.call("POST", "/v2/rooms", self.body(access="private", secret="longenoughpass"))
        self.assertEqual(s, 200)

    def test_defaults_and_caps(self):
        rid, tok = self.mk()
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}", token=tok)[1]["max_members"], 6)
        rid, tok = self.mk(kind="hangout")
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}", token=tok)[1]["max_members"], 0)
        self.app.cfg.max_room_members = 8
        rid, tok = self.mk(kind="hangout")
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}", token=tok)[1]["max_members"], 8)
        rid, tok = self.mk(kind="hangout", max_members=50)
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}", token=tok)[1]["max_members"], 8)

    def test_creator_is_owner(self):
        rid, tok = self.mk()
        s, d = self.call("GET", f"/v2/rooms/{rid}/me", token=tok)
        self.assertEqual((d["name"], d["role"], d["status"]), ("Alice", "owner", "active"))


class TestJoin(Base):
    def test_open(self):
        rid, _ = self.mk()
        s, d = self.join(rid, "Bob")
        self.assertEqual((s, d["status"], d["role"]), (200, "active", "member"))

    def test_unknown_room_and_bad_name(self):
        self.assertEqual(self.join("abcd-efgh", "Bob")[0], 404)
        rid, _ = self.mk()
        self.assertEqual(self.join(rid, "bad name")[0], 400)

    def test_approval_flow(self):
        rid, owner = self.mk(access="approval")
        s, d = self.join(rid, "Bob")
        self.assertEqual((s, d["status"]), (200, "pending"))
        bob = d["token"]
        s, d = self.sync(rid, bob)
        self.assertEqual(s, 403)
        self.assertEqual(d["status"], "pending")
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}/me", token=bob)[1]["status"], "pending")
        s, d = self.call("GET", f"/v2/rooms/{rid}", token=owner)
        self.assertEqual(d["requests"], ["Bob"])
        self.assertEqual(self.call("POST", f"/v2/rooms/{rid}/members/Bob/approve", token=owner)[0], 200)
        self.assertEqual(self.sync(rid, bob)[0], 200)

    def test_deny(self):
        rid, owner = self.mk(access="approval")
        bob = self.joined(rid, "Bob")
        self.assertEqual(self.call("POST", f"/v2/rooms/{rid}/members/Bob/deny", token=owner)[0], 200)
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}/me", token=bob)[0], 401)
        self.assertEqual(self.join(rid, "Bob")[0], 200)     # not banned, can ask again

    def test_private_secret(self):
        rid, _ = self.mk(access="private", secret="1234")
        self.assertEqual(self.join(rid, "Bob")[0], 403)
        self.assertEqual(self.join(rid, "Bob", secret="9999")[0], 403)
        self.assertEqual(self.join(rid, "Bob", secret="1234")[0], 200)

    def test_unlisted_looks_like_missing(self):
        rid, _ = self.mk(access="unlisted", secret="1234")
        s, d1 = self.join(rid, "Bob", secret="9999")
        s2, d2 = self.join("abcd-efgh", "Bob", secret="9999")
        self.assertEqual((s, s2), (404, 404))
        self.assertEqual(d1, d2)
        self.assertEqual(self.join(rid, "Bob", secret="1234")[0], 200)

    def test_lockout_after_five(self):
        rid, owner = self.mk(access="private", secret="1234")
        for _ in range(5):
            self.assertEqual(self.join(rid, "Bob", secret="0000")[0], 403)
        self.assertEqual(self.join(rid, "Bob", secret="0000")[0], 429)
        self.assertEqual(self.join(rid, "Bob", secret="1234")[0], 429)      # even the right one
        self.assertGreaterEqual(self.call("GET", f"/v2/rooms/{rid}", token=owner)[1]["failed_joins"], 5)
        clock.offset += 601
        self.assertEqual(self.join(rid, "Bob", secret="1234")[0], 200)

    def test_room_pause(self):
        rid, owner = self.mk(access="private", secret="1234")
        self.app.lockouts.room_limit = 4
        for i in range(4):          # four different addresses, each below its own limit
            with self.assertRaises(ApiError) as c:
                self.app.join(f"10.0.0.{i}", rid, {"name": "Bob", "secret": "0000"})
            self.assertEqual(c.exception.code, 403)
        s, _ = self.join(rid, "Bob", secret="1234")
        self.assertEqual(s, 429)
        clock.offset += 901
        self.assertEqual(self.join(rid, "Bob", secret="1234")[0], 200)

    def test_name_taken_and_full(self):
        rid, _ = self.mk(max_members=2)
        self.assertEqual(self.join(rid, "Alice")[0], 409)
        self.assertEqual(self.join(rid, "Bob")[0], 200)
        s, d = self.join(rid, "Cara")
        self.assertEqual(s, 409)
        self.assertIn("full", d["error"])


class TestMembers(Base):
    def setUp(self):
        super().setUp()
        self.rid, self.owner = self.mk()
        self.bob = self.joined(self.rid, "Bob")
        self.cara = self.joined(self.rid, "Cara")
        self.dan = self.joined(self.rid, "Dan")

    def act(self, token, name, action):
        return self.call("POST", f"/v2/rooms/{self.rid}/members/{name}/{action}", token=token)

    def test_kick_revokes_token(self):
        self.assertEqual(self.act(self.owner, "Bob", "kick")[0], 200)
        self.assertEqual(self.sync(self.rid, self.bob)[0], 401)
        self.assertEqual(self.join(self.rid, "Bob")[0], 200)

    def test_ban_unban(self):
        self.assertEqual(self.act(self.owner, "Bob", "ban")[0], 200)
        self.assertEqual(self.sync(self.rid, self.bob)[0], 401)
        self.assertEqual(self.join(self.rid, "Bob")[0], 403)
        self.assertEqual(self.act(self.owner, "Bob", "unban")[0], 200)
        self.assertEqual(self.join(self.rid, "Bob")[0], 200)
        self.assertEqual(self.act(self.owner, "Bob", "unban")[0], 404)

    def test_mod_and_owner_rights(self):
        self.assertEqual(self.act(self.bob, "Cara", "kick")[0], 403)        # plain member
        self.assertEqual(self.act(self.owner, "Bob", "promote")[0], 200)
        self.assertEqual(self.act(self.bob, "Cara", "ban")[0], 403)         # mod: no ban
        self.assertEqual(self.act(self.bob, "Cara", "promote")[0], 403)     # mod: no promote
        self.assertEqual(self.act(self.bob, "Alice", "kick")[0], 403)       # nobody acts on owner
        self.assertEqual(self.act(self.owner, "Alice", "kick")[0], 403)
        self.assertEqual(self.act(self.owner, "Cara", "promote")[0], 200)
        self.assertEqual(self.act(self.bob, "Cara", "kick")[0], 403)        # mod vs mod
        self.assertEqual(self.act(self.bob, "Dan", "kick")[0], 200)         # mod kicks member
        self.assertEqual(self.act(self.owner, "Bob", "demote")[0], 200)
        self.assertEqual(self.act(self.bob, "Cara", "kick")[0], 403)
        self.assertEqual(self.act(self.owner, "Zed", "kick")[0], 404)
        self.assertEqual(self.act(self.owner, "Bob", "explode")[0], 404)

    def test_mod_sees_requests(self):
        self.act(self.owner, "Bob", "promote")
        d = self.call("GET", f"/v2/rooms/{self.rid}", token=self.bob)[1]
        self.assertIn("requests", d)
        d = self.call("GET", f"/v2/rooms/{self.rid}", token=self.cara)[1]
        self.assertNotIn("requests", d)
        self.assertEqual({m["name"] for m in d["members"]}, {"Alice", "Bob", "Cara", "Dan"})

    def test_mod_approves(self):
        self.call("PATCH", f"/v2/rooms/{self.rid}", {"access": "approval"}, self.owner)
        eve = self.joined(self.rid, "Eve")
        self.act(self.owner, "Bob", "promote")
        self.assertEqual(self.act(self.bob, "Eve", "approve")[0], 200)
        self.assertEqual(self.sync(self.rid, eve)[0], 200)


class TestPatchLeave(Base):
    def test_patch_owner_only(self):
        rid, owner = self.mk()
        bob = self.joined(rid, "Bob")
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"name": "X"}, bob)[0], 403)
        s, d = self.call("PATCH", f"/v2/rooms/{rid}", {"name": "New", "kind": "hangout",
                                                       "max_members": 3}, owner)
        self.assertEqual((s, d["name"], d["kind"], d["max_members"]), (200, "New", "hangout", 3))
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"modules": ["nope"]}, owner)[0], 400)
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"modules": []}, owner)[1]["modules"], [])
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"kind": "zzz"}, owner)[0], 400)

    def test_secret_changes(self):
        rid, owner = self.mk(access="private", secret="1234")
        # cannot clear the secret of a private room
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"secret": None}, owner)[0], 400)
        # nor go private without one
        rid2, owner2 = self.mk()
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid2}", {"access": "private"}, owner2)[0], 400)
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid2}", {"access": "private",
                                                                  "secret": "5678"}, owner2)[0], 200)
        self.assertEqual(self.join(rid2, "Bob", secret="1234")[0], 403)
        self.assertEqual(self.join(rid2, "Bob", secret="5678")[0], 200)
        # open + clearing is fine
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid2}", {"access": "approval",
                                                                  "secret": None}, owner2)[0], 200)
        self.assertEqual(self.join(rid2, "Cara")[1]["status"], "pending")

    def test_delete(self):
        rid, owner = self.mk()
        bob = self.joined(rid, "Bob")
        self.assertEqual(self.call("DELETE", f"/v2/rooms/{rid}", token=bob)[0], 403)
        self.assertEqual(self.call("DELETE", f"/v2/rooms/{rid}", token=owner)[0], 200)
        self.assertEqual(self.join(rid, "Cara")[0], 404)

    def test_leave(self):
        rid, owner = self.mk()
        bob = self.joined(rid, "Bob")
        self.assertEqual(self.call("POST", f"/v2/rooms/{rid}/leave", token=owner)[0], 409)
        self.assertEqual(self.call("POST", f"/v2/rooms/{rid}/leave", token=bob)[0], 200)
        self.assertEqual(self.sync(rid, bob)[0], 401)
        self.assertEqual(self.call("POST", f"/v2/rooms/{rid}/leave", token=owner)[0], 200)
        self.assertEqual(self.join(rid, "Cara")[0], 404)

    def test_auth_required(self):
        rid, _ = self.mk()
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}")[0], 401)
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}", token="junk")[0], 401)
        self.assertEqual(self.call("GET", "/v2/rooms/abcd-efgh", token="junk")[0], 401)


class TestLobby(Base):
    def test_lobby_lists_and_hides(self):
        self.mk(access="open", name="OpenOne")
        self.mk(access="approval", name="ApprovalOne")
        self.mk(access="private", name="PrivateOne", secret="1234")
        self.mk(access="unlisted", name="HiddenOne", secret="1234")
        s, d = self.call("GET", "/v2/lobby")
        self.assertTrue(d["enabled"])
        names = {r["name"] for r in d["rooms"]}
        self.assertEqual(names, {"OpenOne", "ApprovalOne", "PrivateOne"})
        self.assertEqual(set(d["rooms"][0]), {"id", "name", "kind", "access", "members", "online",
                                                  "max_members", "activity"})

    def test_lobby_off(self):
        self.app.cfg.lobby = False
        self.mk()
        self.assertEqual(self.call("GET", "/v2/lobby")[1], {"enabled": False, "rooms": []})

    def test_online_count(self):
        rid, owner = self.mk()
        self.joined(rid, "Bob")
        self.sync(rid, owner, {"presence": {"level": 10}})
        r = self.call("GET", "/v2/lobby")[1]["rooms"][0]
        self.assertEqual((r["members"], r["online"]), (2, 1))
        clock.offset += 100
        self.assertEqual(self.call("GET", "/v2/lobby")[1]["rooms"][0]["online"], 0)


class TestSync(Base):
    def test_presence_round_trip(self):
        rid, alice = self.mk()
        bob = self.joined(rid, "Bob")
        s, d = self.sync(rid, alice, {"presence": {"state": "playing", "level": 30, "map": "Henesys",
                                                   "hp": [10, 20], "bogus": 1, "name": "Evil",
                                                   "exp_pct": 500, "caps": "ch"}})
        self.assertEqual(s, 200)
        self.assertIn("server_t", d)
        clock.offset += 0.5         # Windows' clock ticks ~15 ms: keep the two syncs apart
        s, d = self.sync(rid, bob, {"presence": {"level": 12}})
        rows = {m["name"]: m for m in d["presence"]["members"]}
        self.assertEqual(set(rows), {"Alice", "Bob"})
        a = rows["Alice"]
        self.assertEqual((a["level"], a["map"], a["hp"], a["role"]), (30, "Henesys", [10, 20], "owner"))
        self.assertEqual(a["caps"], "ch")
        self.assertNotIn("bogus", a)
        self.assertNotIn("exp_pct", a)      # out of range, dropped
        self.assertIn("age_s", a)
        self.assertEqual(d["presence"]["members"][0]["name"], "Bob")        # freshest first

    def test_ttl(self):
        rid, alice = self.mk()
        bob = self.joined(rid, "Bob")
        self.sync(rid, alice, {"presence": {"level": 5}})
        clock.offset += 91
        d = self.sync(rid, bob, {"presence": {"level": 6}})[1]
        self.assertEqual([m["name"] for m in d["presence"]["members"]], ["Bob"])

    def test_unknown_and_disabled_module_keys(self):
        rid, alice = self.mk()
        s, d = self.sync(rid, alice, {"nosuchmodule": {"x": 1}, "presence": {"level": 5}})
        self.assertEqual(s, 200)
        self.assertEqual(set(d), {"server_t", "presence", "challenge"})
        self.call("PATCH", f"/v2/rooms/{rid}", {"modules": []}, alice)
        d = self.sync(rid, alice, {"presence": {"level": 5}})[1]
        self.assertEqual(set(d), {"server_t"})

    def test_rate_limit(self):
        self.app.cfg.sync_gap_s = 1.0
        rid, alice = self.mk()
        bob = self.joined(rid, "Bob")
        self.assertEqual(self.sync(rid, alice)[0], 200)
        self.assertEqual(self.sync(rid, alice)[0], 429)
        self.assertEqual(self.sync(rid, bob)[0], 200)       # per token
        clock.offset += 1.5
        self.assertEqual(self.sync(rid, alice)[0], 200)

    def test_address_bucket(self):
        self.app.cfg.rate_burst = 3
        self.app.cfg.rate_per_s = 0.0001
        codes = [self.call("GET", "/v2/lobby")[0] for _ in range(5)]
        self.assertEqual(codes[:3], [200] * 3)
        self.assertEqual(codes[-1], 429)

    def test_db_write_throttle(self):
        rid, alice = self.mk()
        before = self.app.store.get_member(rid, "Alice")["last_seen_t"]
        clock.offset += 5
        self.sync(rid, alice)
        self.assertEqual(self.app.store.get_member(rid, "Alice")["last_seen_t"] > before, True)
        written = self.app.store.get_member(rid, "Alice")["last_seen_t"]
        clock.offset += 5
        self.sync(rid, alice)
        self.assertEqual(self.app.store.get_member(rid, "Alice")["last_seen_t"], written)
        clock.offset += 700
        self.sync(rid, alice)
        self.assertGreater(self.app.store.get_member(rid, "Alice")["last_seen_t"], written)

    def test_module_action_404(self):
        rid, alice = self.mk()
        self.assertEqual(self.call("POST", f"/v2/rooms/{rid}/presence/nothing", {}, alice)[0], 404)
        self.assertEqual(self.call("POST", f"/v2/rooms/{rid}/relic/go", {}, alice)[0], 404)


class TestPersistenceCleanup(Base):
    def test_survives_restart(self):
        rid, alice = self.mk(access="private", secret="1234")
        bob = self.joined(rid, "Bob", secret="1234")
        self.stop()
        self.start()
        s, d = self.call("GET", f"/v2/rooms/{rid}/me", token=bob)
        self.assertEqual((s, d["name"]), (200, "Bob"))
        self.assertEqual(self.join(rid, "Cara", secret="1234")[0], 200)
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}", token=alice)[0], 200)

    def test_cleanup(self):
        rid, alice = self.mk()
        bob = self.joined(rid, "Bob")
        rid2, alice2 = self.mk(name="Other")
        clock.offset += 31 * 86400
        self.sync(rid, alice)                   # Alice active again, Bob not
        res = self.app.cleanup()
        self.assertGreaterEqual(res["members"], 1)
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}/me", token=bob)[0], 401)
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}/me", token=alice)[0], 200)
        clock.offset += 91 * 86400
        res = self.app.cleanup()
        self.assertEqual(res["rooms"], 2)
        self.assertEqual(self.join(rid, "Cara")[0], 404)
        self.assertEqual(self.join(rid2, "Cara")[0], 404)


class TestLegacy(Base):
    def test_v1_through_v2(self):
        party_relay.buckets.clear()
        s, d = self.call("POST", "/v1/party/testcode1", {"name": "Alice", "level": 10})
        self.assertEqual(s, 200)
        self.assertEqual(d["members"][0]["name"], "Alice")
        party_relay.parties.clear()

    def test_v1_off(self):
        self.app.cfg.legacy_v1 = False
        s, _ = self.call("POST", "/v1/party/testcode1", {"name": "Alice"})
        self.assertEqual(s, 404)
        self.assertFalse(self.call("GET", "/health")[1]["legacy_v1"])


if __name__ == "__main__":
    unittest.main()
