"""Tests for room creation through the admin page and the public /rooms page.

    python relay/tests/test_v2_public.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, clock  # noqa: E402
from test_v2_admin import AdminBase  # noqa: E402

GOOD = dict(name="Night", owner="Alice", kind="party", access="open")
WAN = {"X-Forwarded-For": "203.0.113.7"}


class TestAdminCreate(AdminBase):
    def setUp(self):
        super().setUp()
        self.ok_login()

    def test_create_works_and_owner_token_works(self):
        d = self.do("POST", "rooms", dict(GOOD, access="private", secret="1234", max_members=4))
        self.assertRegex(d["id"], r"^[a-z0-9]{4}-[a-z0-9]{4}$")
        self.assertEqual(d["room"]["access"], "private")
        self.assertEqual(d["room"]["max_members"], 4)
        s, me = self.call("GET", f"/v2/rooms/{d['id']}/me", token=d["token"])
        self.assertEqual((s, me["role"], me["name"]), (200, "owner", "Alice"))
        rooms = self.get("rooms")["rooms"]
        row = [r for r in rooms if r["id"] == d["id"]][0]
        self.assertEqual(row["owner_name"], "admin")
        self.assertTrue(row["has_secret"])
        self.assertEqual(self.joined(d["id"], "Bob", secret="1234") != "", True)

    def test_refused_without_session_or_csrf(self):
        s, d, _ = self.adm("POST", "rooms", GOOD, csrf=False)
        self.assertEqual(s, 403)
        s, d, _ = self.adm("POST", "rooms", GOOD, cookie=False)
        self.assertEqual(s, 401)
        self.assertEqual(self.get("rooms")["rooms"], [])

    def test_validation(self):
        for bad in (dict(GOOD, name=""), dict(GOOD, owner="bad name!"), dict(GOOD, kind="x"),
                    dict(GOOD, access="x"), dict(GOOD, access="private"),
                    dict(GOOD, secret="12"), dict(GOOD, max_members=-1), dict(GOOD, max_members="3")):
            s, d, _ = self.adm("POST", "rooms", bad)
            self.assertEqual(s, 400, (bad, d))
        self.assertEqual(self.get("rooms")["rooms"], [])

    def test_bypasses_host_key_requirement_and_limits(self):
        self.restart(admin_pass="correct horse battery", allow_anyone_create=False,
                     max_rooms_per_account=1, anon_creates_per_day=0)
        self.ok_login()
        self.assertEqual(self.call("POST", "/v2/rooms", GOOD)[0], 403)
        for i in range(3):                       # more than the per-account limit
            self.do("POST", "rooms", dict(GOOD, name=f"R{i}"))
        accts = self.get("accounts")["accounts"]
        self.assertEqual([a for a in accts if "admin" in a["providers"]][0]["rooms_owned"], 3)
        self.assertEqual(len([a for a in accts if "admin" in a["providers"]]), 1)   # one shared account

    def test_respects_max_rooms(self):
        self.restart(admin_pass="correct horse battery", max_rooms=2)
        self.ok_login()
        self.do("POST", "rooms", GOOD)
        self.do("POST", "rooms", GOOD)
        self.do("POST", "rooms", GOOD, want=503)

    def test_disabled_admin_account_refuses(self):
        self.do("POST", "rooms", GOOD)
        aid = [a for a in self.get("accounts")["accounts"] if "admin" in a["providers"]][0]["id"]
        self.do("POST", f"accounts/{aid}/disable", {})
        self.do("POST", "rooms", GOOD, want=403)

    def test_audit_row(self):
        d = self.do("POST", "rooms", dict(GOOD, access="private", secret="987654"))
        items = self.get("audit?limit=20")["items"]
        row = [a for a in items if a["action"] == "room create"][0]
        self.assertIn(d["id"], row["detail"])
        blob = str(items)
        self.assertNotIn("987654", blob)
        self.assertNotIn(d["token"], blob)


class TestPublicPage(AdminBase):
    def test_page_and_assets(self):
        s, hd, body = self.raw("GET", "/rooms")
        self.assertEqual(s, 200)
        self.assertIn("text/html", hd["Content-Type"])
        self.assertIn("default-src 'self'", hd["Content-Security-Policy"])
        self.assertEqual(hd["X-Frame-Options"], "DENY")
        self.assertNotIn(b"<script>", body)
        self.assertEqual(self.raw("GET", "/rooms/")[0], 200)
        s, hd, body = self.raw("GET", "/rooms/rooms.js")
        self.assertEqual((s, hd["Content-Type"]), (200, "text/javascript; charset=utf-8"))
        s, hd, body = self.raw("GET", "/rooms/rooms.css")
        self.assertEqual((s, hd["Content-Type"]), (200, "text/css; charset=utf-8"))
        self.assertIn("nosniff", hd["X-Content-Type-Options"])

    def test_unknown_paths_404(self):
        for p in ("/rooms/x", "/rooms/../relayd/rooms.py", "/rooms/index.html", "/rooms/rooms.js/x"):
            self.assertEqual(self.raw("GET", p)[0], 404, p)
        self.assertEqual(self.raw("POST", "/rooms", {}, {"Content-Type": "application/json"})[0], 404)

    def test_public_from_internet_but_admin_refused(self):
        self.restart(admin_pass="correct horse battery", trusted_proxies="127.0.0.1")
        for p in ("/rooms", "/rooms/rooms.js", "/rooms/rooms.css"):
            self.assertEqual(self.raw("GET", p, headers=WAN)[0], 200, p)
        self.assertEqual(self.raw("GET", "/admin", headers=WAN)[0], 403)

    def test_works_without_admin_enabled(self):
        self.restart()
        self.assertEqual(self.raw("GET", "/rooms")[0], 200)
        self.assertEqual(self.raw("GET", "/admin")[0], 404)

    def test_health_create_open(self):
        self.restart(admin_pass="correct horse battery", allow_anyone_create=False)
        self.assertEqual(self.call("GET", "/health")[1]["create_open"], False)
        self.restart(admin_pass="correct horse battery", allow_anyone_create=True)
        self.assertEqual(self.call("GET", "/health")[1]["create_open"], True)
        self.restart(admin_pass="correct horse battery", allow_anyone_create=None)
        self.assertEqual(self.call("GET", "/health")[1]["create_open"], False)
        self.app.store.set_setting("allow_anyone_create", "1")
        self.assertEqual(self.call("GET", "/health")[1]["create_open"], True)


class TestHostKeyLockout(Base):
    def setUp(self):
        super().setUp()
        self.stop()
        self.start(allow_anyone_create=False)
        self.key = self.app.store.create_host_key("friend", 10, None)

    def create(self, key):
        return self.call("POST", "/v2/rooms", GOOD, headers={"X-Host-Key": key})

    def test_wrong_keys_lock_the_address(self):
        for _ in range(5):
            s, d = self.create("hk_wrong")
            self.assertEqual((s, d["error"]), (403, "bad host key"))
        s, d = self.create(self.key)                 # even the right key waits
        self.assertEqual(s, 429)
        clock.offset += 601
        self.assertEqual(self.create(self.key)[0], 200)

    def test_right_key_resets_the_count(self):
        for _ in range(4):
            self.assertEqual(self.create("hk_wrong")[0], 403)
        self.assertEqual(self.create(self.key)[0], 200)
        for _ in range(4):
            self.assertEqual(self.create("hk_wrong")[0], 403)
        self.assertEqual(self.create(self.key)[0], 200)

    def test_other_address_not_affected(self):
        self.stop()
        self.start(allow_anyone_create=False, trusted_proxies="127.0.0.1")
        key = self.app.store.create_host_key("friend", 10, None)
        for _ in range(5):
            self.call("POST", "/v2/rooms", GOOD, headers={"X-Host-Key": "hk_wrong", **WAN})
        s, _ = self.call("POST", "/v2/rooms", GOOD, headers={"X-Host-Key": key, **WAN})
        self.assertEqual(s, 429)
        s, _ = self.call("POST", "/v2/rooms", GOOD,
                         headers={"X-Host-Key": key, "X-Forwarded-For": "203.0.113.8"})
        self.assertEqual(s, 200)

    def test_no_key_when_closed_is_not_counted(self):
        for _ in range(8):
            self.assertEqual(self.call("POST", "/v2/rooms", GOOD)[0], 403)
        self.assertEqual(self.create(self.key)[0], 200)


if __name__ == "__main__":
    unittest.main()
