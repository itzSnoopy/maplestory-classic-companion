"""Friendly tokens, wrong-token lockout, party size rules, lobby activity, public page.

    python relay/tests/test_v2_tokens.py
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, clock  # noqa: E402
from relayd import words  # noqa: E402

FRIENDLY = re.compile(r"^[a-z]+-[a-z]+-\d{4}$")
WAN1 = {"X-Forwarded-For": "203.0.113.7"}
WAN2 = {"X-Forwarded-For": "203.0.113.8"}


class TestFriendly(Base):
    def test_words_lists(self):
        self.assertIn("mushmom", words.MONSTERS)
        self.assertIn("corsair", words.JOBS)
        for _ in range(200):
            t = words.friendly_token()
            self.assertRegex(t, FRIENDLY)
            mon, job, num = t.split("-")
            self.assertIn(mon, words.MONSTERS)
            self.assertIn(job, words.JOBS)
            self.assertEqual(len(num), 4)

    def test_owner_token_format_and_case_insensitive(self):
        rid, tok = self.mk()
        self.assertRegex(tok, FRIENDLY)
        for variant in (tok.upper(), "  " + tok.title() + " "):
            s, d = self.call("GET", f"/v2/rooms/{rid}/me", token=variant)
            self.assertEqual((s, d["role"]), (200, "owner"), variant)

    def test_member_tokens_stay_long(self):
        rid, _ = self.mk(kind="hangout")
        self.assertGreaterEqual(len(self.joined(rid, "Bob")), 40)

    def test_host_key_format_and_case_insensitive(self):
        key = self.app.store.create_host_key("f", 3, None)
        self.assertRegex(key, FRIENDLY)
        self.app.cfg.allow_anyone_create = False
        body = dict(name="R", owner="Alice", kind="hangout", access="open")
        s, d = self.call("POST", "/v2/rooms", body, headers={"X-Host-Key": key.upper()})
        self.assertEqual(s, 200, d)

    def test_host_key_collision_retries(self):
        orig = words.friendly_token
        seq = iter(["slime-cleric-0001", "slime-cleric-0001", "slime-cleric-0002"])
        words.friendly_token = lambda: next(seq)
        try:
            from relayd import store as st
            st.words.friendly_token = words.friendly_token
            a = self.app.store.create_host_key("a", 1, None)
            b = self.app.store.create_host_key("b", 1, None)
        finally:
            words.friendly_token = orig
        self.assertEqual((a, b), ("slime-cleric-0001", "slime-cleric-0002"))

    def test_secure_mode_long_tokens_old_short_still_work(self):
        rid, short = self.mk()
        old_key = self.app.store.create_host_key("old", 2, None)
        self.app.store.set_setting("secure_tokens", "1")
        rid2, long_tok = self.mk()
        self.assertGreaterEqual(len(long_tok), 40)
        self.assertNotRegex(long_tok, FRIENDLY)
        self.assertGreaterEqual(len(self.app.store.create_host_key("new", 1, None)), 0)
        new_key = self.app.store.create_host_key("new2", 1, None, secure=self.app.flag("secure_tokens"))
        self.assertTrue(new_key.startswith("hk_"))
        # long tokens are exact (case matters), old short ones keep working
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid2}/me", token=long_tok)[0], 200)
        self.assertEqual(self.call("GET", f"/v2/rooms/{rid}/me", token=short)[0], 200)
        self.app.cfg.allow_anyone_create = False
        body = dict(name="R", owner="Alice", kind="hangout", access="open")
        self.assertEqual(self.call("POST", "/v2/rooms", body, headers={"X-Host-Key": old_key})[0], 200)
        self.assertEqual(self.call("POST", "/v2/rooms", body, headers={"X-Host-Key": new_key})[0], 200)

    def test_secure_env_wins(self):
        self.app.cfg.secure_tokens = True
        self.assertGreaterEqual(len(self.mk()[1]), 40)
        self.app.cfg.secure_tokens = False
        self.app.store.set_setting("secure_tokens", "1")
        self.assertRegex(self.mk()[1], FRIENDLY)


class TestTokenLockout(Base):
    def setUp(self):
        super().setUp()
        self.stop()
        self.start(trusted_proxies="127.0.0.1")
        self.rid, self.tok = self.mk()

    def me(self, token, hdr):
        return self.call("GET", f"/v2/rooms/{self.rid}/me", token=token, headers=hdr)

    def test_lockout_per_address(self):
        for i in range(10):
            self.assertEqual(self.me("slime-cleric-%04d" % i, WAN1)[0], 401)
            clock.offset += 0.05
        s, d = self.me("slime-cleric-9999", WAN1)
        self.assertEqual(s, 429)
        self.assertEqual(self.me(self.tok, WAN1)[0], 429)        # even the right one, for that address
        self.assertEqual(self.me(self.tok, WAN2)[0], 200)        # another address is fine
        self.assertEqual(self.call("GET", "/v2/lobby", headers=WAN1)[0], 200)
        self.assertEqual(self.call("GET", "/health", headers=WAN1)[0], 200)
        clock.offset += 601
        self.assertEqual(self.me(self.tok, WAN1)[0], 200)

    def test_right_token_does_not_reset_count(self):
        # a valid token between guesses must not dodge the limit
        for _ in range(9):
            self.me("wrong-token-0000", WAN1)
        self.assertEqual(self.me(self.tok, WAN1)[0], 200)
        self.me("wrong-token-0000", WAN1)
        self.assertEqual(self.me(self.tok, WAN1)[0], 429)

    def test_count_expires_after_quiet_spell_and_missing_token_not_counted(self):
        for _ in range(9):
            self.me("wrong-token-0000", WAN1)
        clock.offset += 601
        for _ in range(9):
            self.me("wrong-token-0000", WAN1)
        self.assertEqual(self.me(self.tok, WAN1)[0], 200)
        for _ in range(20):
            self.assertEqual(self.me(None, WAN1)[0], 401)
        self.assertEqual(self.me(self.tok, WAN1)[0], 200)

    def test_join_by_pin_not_affected(self):
        rid, _ = self.mk(access="private", secret="1234", kind="hangout")
        for i in range(10):
            self.me("wrong-token-%04d" % i, WAN1)
        self.assertEqual(self.me(self.tok, WAN1)[0], 429)
        s, d = self.call("POST", f"/v2/rooms/{rid}/join", dict(name="Bob", secret="1234"), headers=WAN1)
        self.assertEqual(s, 200, d)


class TestRoomSize(Base):
    def body(self, **kw):
        return dict(dict(name="R", owner="Alice", access="open"), **kw)

    def test_party_cap_on_create(self):
        for mm in (0, 7, 100):
            s, d = self.call("POST", "/v2/rooms", self.body(kind="party", max_members=mm))
            self.assertEqual(s, 400, mm)
            self.assertIn("up to 6", d["error"])
        s, d = self.call("POST", "/v2/rooms", self.body(kind="party", max_members=1))
        self.assertEqual((s, d["room"]["max_members"]), (200, 1))
        s, d = self.call("POST", "/v2/rooms", self.body(kind="party"))
        self.assertEqual(d["room"]["max_members"], 6)

    def test_hangout_default_and_any_value(self):
        s, d = self.call("POST", "/v2/rooms", self.body(kind="hangout"))
        self.assertEqual(d["room"]["max_members"], 0)
        s, d = self.call("POST", "/v2/rooms", self.body(kind="hangout", max_members=50))
        self.assertEqual((s, d["room"]["max_members"]), (200, 50))

    def test_global_cap_on_top(self):
        self.app.cfg.max_room_members = 4
        s, d = self.call("POST", "/v2/rooms", self.body(kind="party"))
        self.assertEqual(d["room"]["max_members"], 4)

    def test_patch_party_cap(self):
        rid, tok = self.mk(kind="party")
        p = lambda **b: self.call("PATCH", f"/v2/rooms/{rid}", b, tok)       # noqa: E731
        self.assertEqual(p(max_members=7)[0], 400)
        self.assertEqual(p(max_members=0)[0], 400)
        self.assertEqual(p(max_members=3)[1]["max_members"], 3)
        self.assertEqual(p(name="Other")[0], 200)
        hid, htok = self.mk(kind="hangout", owner="Bob")
        h = lambda **b: self.call("PATCH", f"/v2/rooms/{hid}", b, htok)      # noqa: E731
        self.assertEqual(h(max_members=40)[0], 200)
        s, d = h(kind="party")                       # 40 people cannot become a party
        self.assertEqual(s, 400)
        self.assertIn("up to 6", d["error"])
        self.assertEqual(h(kind="party", max_members=5)[1]["kind"], "party")
        self.assertEqual(h(kind="hangout", max_members=0)[0], 200)

    def test_legacy_party_over_cap_can_still_rename(self):
        rid, tok = self.mk(kind="party")
        self.app.store.update_room(rid, max_members=20)
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"name": "X"}, tok)[0], 200)


class TestLobbyActivity(Base):
    def test_lobby_shape_and_duel_activity(self):
        rid, alice = self.mk(kind="hangout", name="Duelers", max_members=9)
        bob = self.joined(rid, "Bob")
        for t in (alice, bob):
            self.sync(rid, t, {"presence": {"map": "Henesys"}})
        r = self.call("GET", "/v2/lobby")[1]["rooms"][0]
        self.assertEqual(set(r), {"id", "name", "kind", "access", "members", "online",
                                  "max_members", "activity"})
        self.assertEqual((r["max_members"], r["activity"]), (9, None))
        ch = lambda who, act, **b: self.call(                                # noqa: E731
            "POST", f"/v2/rooms/{rid}/challenge/{act}", b, who)
        s, c = ch(alice, "create", mode="1v1", minutes=5, target="Bob")
        self.assertEqual(s, 200, c)
        self.assertEqual(self.call("GET", "/v2/lobby")[1]["rooms"][0]["activity"], "Duel waiting")
        s, c = ch(bob, "accept", id=c["id"])
        self.assertEqual(s, 200, c)
        self.assertEqual(self.call("GET", "/v2/lobby")[1]["rooms"][0]["activity"], "Duel running")
        clock.offset += 5 * 60 + 60
        self.assertIsNone(self.call("GET", "/v2/lobby")[1]["rooms"][0]["activity"])


class TestPublicPage(Base):
    def test_page_served_with_csp_and_new_bits(self):
        import urllib.request
        with urllib.request.urlopen(self.base + "/rooms") as r:
            csp = r.headers["Content-Security-Policy"]
            html = r.read().decode()
        self.assertIn("default-src 'self'", csp)
        self.assertIn('id="createToggle"', html)
        self.assertIn('<option value="hangout" selected>', html)
        with urllib.request.urlopen(self.base + "/rooms/rooms.js") as r:
            self.assertEqual(r.status, 200)
            self.assertNotIn("innerHTML", r.read().decode())


if __name__ == "__main__":
    unittest.main()
