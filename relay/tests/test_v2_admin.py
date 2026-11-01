"""Tests for the relayd admin page and API (/admin, /v2/admin/*).

    python relay/tests/test_v2_admin.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, RELAY, clock  # noqa: E402
from relayd import auth  # noqa: E402
from relayd.errors import ApiError  # noqa: E402
from relayd.config import Config, ConfigError  # noqa: E402
from relayd.server import make_server  # noqa: E402
from test_v2_feedback import export, KEY as FB_KEY  # noqa: E402

PASS = "correct horse battery"
PROXY = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"}


class AdminBase(Base):
    CFG = dict(admin_pass=PASS)

    def setUp(self):
        super().setUp()
        self.restart(**self.CFG)

    def restart(self, **kw):
        self.stop()
        self.start(**kw)
        self.cookie = None

    def raw(self, method, path, body=None, headers=None):
        """-> (status, response headers, bytes). No cookie/CSRF added."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def adm(self, method, path, body=None, extra=None, cookie=True, csrf=True):
        h = {}
        if method != "GET" and csrf:
            h["X-Relay-Admin"] = "1"
        if cookie and self.cookie:
            h["Cookie"] = "relay_admin=" + self.cookie
        h.update(extra or {})
        s, hd, raw = self.raw(method, "/v2/admin/" + path, body if method != "GET" else None, h)
        try:
            d = json.loads(raw)
        except ValueError:
            d = raw
        return s, d, hd

    def login(self, pw=PASS, extra=None):
        s, d, hd = self.adm("POST", "login", {"pass": pw}, extra=extra)
        if s == 200:
            sc = hd["Set-Cookie"]
            self.cookie = sc.split(";")[0].split("=", 1)[1]
        return s, d, hd

    def ok_login(self):
        s, d, _ = self.login()
        self.assertEqual(s, 200, d)

    def get(self, path):
        s, d, _ = self.adm("GET", path)
        self.assertEqual(s, 200, d)
        return d

    def do(self, method, path, body=None, want=200):
        s, d, _ = self.adm(method, path, body)
        self.assertEqual(s, want, d)
        return d


class TestGateAndLogin(AdminBase):
    def test_disabled_is_404(self):
        self.restart()
        for p in ("/admin", "/admin/", "/admin/admin.js", "/v2/admin/me", "/v2/admin/login"):
            self.assertEqual(self.raw("GET", p)[0], 404, p)
        s, _, _ = self.raw("POST", "/v2/admin/login", {"pass": PASS}, {"X-Relay-Admin": "1"})
        self.assertEqual(s, 404)

    def test_short_pass_refuses_to_start(self):
        with self.assertRaises(ConfigError):
            make_server(self.cfg(admin_pass="short-pass"))
        with self.assertRaises(ConfigError):
            Config.from_env({"ADMIN_PASS": "x" * 11}).validate()
        Config.from_env({"ADMIN_PASS": "x" * 12}).validate()

    def test_bad_hash_or_nets_refuse(self):
        with self.assertRaises(ConfigError):
            make_server(self.cfg(admin_pass_hash="nope"))
        with self.assertRaises(ConfigError):
            make_server(self.cfg(admin_pass=PASS, admin_nets="not-a-net"))

    def test_main_exits_nonzero_on_short_pass(self):
        env = dict(os.environ, ADMIN_PASS="short", DATA_DIR=self.tmp, PORT="0")
        r = subprocess.run([sys.executable, "-m", "relayd"], cwd=str(RELAY), env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ADMIN_PASS", r.stderr)

    def test_hash_pass_works(self):
        self.restart(admin_pass_hash=auth.hash_secret("another long pass"))
        self.assertEqual(self.login(PASS)[0], 401)
        self.assertEqual(self.login("another long pass")[0], 200)

    def test_login_ok_wrong_and_cookie(self):
        self.assertEqual(self.login("wrong pass here")[0], 401)
        s, d, hd = self.login()
        self.assertEqual(s, 200)
        self.assertEqual(d, {"ok": True})
        sc = hd["Set-Cookie"]
        for part in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, sc)
        self.assertNotIn("Secure", sc)
        self.assertEqual(self.get("me"), {"ok": True, "via": "lan"})

    def test_no_cookie_is_401(self):
        self.assertEqual(self.adm("GET", "me")[0], 401)
        self.assertEqual(self.adm("GET", "status")[0], 401)

    def test_logout(self):
        self.ok_login()
        self.do("POST", "logout", {})
        self.assertEqual(self.adm("GET", "me")[0], 401)

    def test_session_idle_and_max_age(self):
        self.ok_login()
        clock.offset += 3600
        self.get("me")                          # use refreshes idle
        clock.offset += 3600 + 1800
        self.get("me")
        clock.offset += 2 * 3600 + 1
        self.assertEqual(self.adm("GET", "me")[0], 401)       # idle
        self.ok_login()
        for _ in range(7):                       # stays active, but 12 h is the cap
            clock.offset += 1.75 * 3600
            s = self.adm("GET", "me")[0]
            if s != 200:
                break
        self.assertEqual(s, 401)

    def test_per_address_lockout(self):
        for _ in range(5):
            self.assertEqual(self.login("wrong pass here")[0], 401)
        self.assertEqual(self.login()[0], 429)             # even the right pass
        clock.offset += 15 * 60 + 1
        self.assertEqual(self.login()[0], 200)

    def test_global_brake(self):
        # internet logins (through the proxy) trip it; LAN logins neither count nor get stopped
        self.restart(admin_pass=PASS, trusted_proxies="127.0.0.1")
        self.app.admin.addr_limit = 1000
        self.app.admin.global_limit = 3
        lan_peer = {"X-Forwarded-For": "192.168.1.5"}              # proxied over plain http: refused anyway
        for i in range(4):
            fwd = {"X-Forwarded-Proto": "https", "X-Forwarded-For": f"203.0.113.{i + 1}"}
            self.assertEqual(self.login("wrong pass here", extra=fwd)[0], 401)
        self.assertEqual(self.login(extra=PROXY)[0], 429)
        self.assertEqual(self.login(extra=lan_peer)[0], 403)
        self.restart(admin_pass=PASS)                               # direct LAN, fresh admin state
        self.app.admin.addr_limit = 1000
        self.app.admin.global_limit = 3
        for _ in range(6):
            self.assertEqual(self.login("wrong pass here")[0], 401)   # LAN fails don't trip it
        self.assertEqual(self.login()[0], 200)

    def test_global_brake_spares_lan(self):
        self.restart(admin_pass=PASS, trusted_proxies="198.51.100.0/24")   # 127.0.0.1 is a direct LAN peer
        adm = self.app.admin
        adm._global_until = clock.now() + 300                       # brake on (as if the internet tripped it)
        self.assertEqual(self.login()[0], 200)                      # LAN gets in during the brake
        with self.assertRaises(ApiError) as e:
            adm.login("203.0.113.9", "proxy", {"pass": PASS})      # the internet does not
        self.assertEqual(e.exception.code, 429)

    def test_csrf_header_and_origin(self):
        self.ok_login()
        self.assertEqual(self.adm("POST", "logout", {}, csrf=False)[0], 403)
        self.assertEqual(self.adm("POST", "login", {"pass": PASS}, csrf=False)[0], 403)
        host = self.base.split("//")[1]
        s, _, _ = self.adm("POST", "settings", {"lobby": True}, extra={"Origin": "http://evil.example.com"})
        self.assertEqual(s, 403)
        s, _, _ = self.adm("POST", "logout", {}, extra={"Origin": "https://" + host})
        self.assertEqual(s, 403)                           # wrong scheme
        s, hd = self.adm("GET", "me", extra={"Origin": "http://evil.example.com"})[0:3:2]
        self.assertEqual(s, 200)                           # GET needs no header
        self.assertNotIn("Access-Control-Allow-Origin", hd)
        s, _, _ = self.adm("POST", "logout", {}, extra={"Origin": "http://" + host})
        self.assertEqual(s, 200)


class TestNetwork(AdminBase):
    def test_non_lan_refused(self):
        self.restart(admin_pass=PASS, admin_nets="10.0.0.0/8")
        for p in ("/admin", "/v2/admin/me"):
            s, _, raw = self.raw("GET", p)
            self.assertEqual(s, 403)
        self.assertEqual(json.loads(raw)["error"], "admin is only reachable from the LAN or over https")
        # a forwarded header from an untrusted peer is ignored
        s, _, _ = self.raw("GET", "/admin", headers={"X-Forwarded-For": "10.1.2.3", "X-Forwarded-Proto": "https"})
        self.assertEqual(s, 403)
        self.assertEqual(self.login()[0], 403)

    def test_xff_from_untrusted_peer_ignored(self):
        s, _, _ = self.raw("GET", "/admin", headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(s, 200)                           # peer is loopback, XFF means nothing

    def test_trusted_proxy_https_allowed_with_secure_cookie(self):
        self.restart(admin_pass=PASS, trusted_proxies="127.0.0.1", admin_nets="10.0.0.0/8")
        host = self.base.split("//")[1]
        hdr = dict(PROXY, Origin="https://" + host)
        s, d, hd = self.login(extra=hdr)
        self.assertEqual(s, 200, d)
        self.assertIn("Secure", hd["Set-Cookie"])
        s, d, _ = self.adm("GET", "me", extra=PROXY)
        self.assertEqual(d["via"], "proxy")
        self.assertEqual(self.raw("GET", "/admin", headers=PROXY)[0], 200)

    def test_trusted_proxy_plain_http_refused(self):
        self.restart(admin_pass=PASS, trusted_proxies="127.0.0.1")
        for hdr in ({"X-Forwarded-For": "192.168.1.5"},
                    {"X-Forwarded-For": "192.168.1.5", "X-Forwarded-Proto": "http"}, {}):
            self.assertEqual(self.raw("GET", "/admin", headers=hdr)[0], 403)
            self.assertEqual(self.raw("GET", "/v2/admin/me", headers=hdr)[0], 403)

    def test_forwarded_address_drives_v2_limits(self):
        self.restart(trusted_proxies="127.0.0.1")
        rid, _ = self.mk(access="private", secret="12345678")
        a = {"X-Forwarded-For": "203.0.113.1"}
        b = {"X-Forwarded-For": "203.0.113.2"}
        for _ in range(5):
            s, _ = self.call("POST", f"/v2/rooms/{rid}/join", {"name": "Bob", "secret": "wrongwrong"}, headers=a)
            self.assertEqual(s, 403)
        s, _ = self.call("POST", f"/v2/rooms/{rid}/join", {"name": "Bob", "secret": "12345678"}, headers=a)
        self.assertEqual(s, 429)
        s, _ = self.call("POST", f"/v2/rooms/{rid}/join", {"name": "Bob", "secret": "12345678"}, headers=b)
        self.assertEqual(s, 200)

    def test_forwarded_rate_bucket_and_anon_limit(self):
        self.restart(trusted_proxies="127.0.0.1", anon_creates_per_day=1)
        a = {"X-Forwarded-For": "203.0.113.1"}
        b = {"X-Forwarded-For": "203.0.113.2"}
        body = dict(name="R", owner="Alice", kind="party", access="open")
        self.assertEqual(self.call("POST", "/v2/rooms", body, headers=a)[0], 200)
        self.assertEqual(self.call("POST", "/v2/rooms", body, headers=a)[0], 429)
        self.assertEqual(self.call("POST", "/v2/rooms", body, headers=b)[0], 200)

    def test_rightmost_untrusted_xff_wins(self):
        self.restart(trusted_proxies="127.0.0.1,198.51.100.0/24")
        acc = self.app.access
        c = acc.resolve("127.0.0.1", _H({"X-Forwarded-For": "1.2.3.4, 203.0.113.7, 198.51.100.9"}))
        self.assertEqual(c.ip, "203.0.113.7")
        c = acc.resolve("::ffff:127.0.0.1", _H({}))
        self.assertTrue(c.proxied)


class _H:
    def __init__(self, d):
        self.d = d

    def get(self, k, default=None):
        return self.d.get(k, default)

    def get_all(self, k):
        return [self.d[k]] if k in self.d else []


class TestPage(AdminBase):
    def test_page_assets_and_headers(self):
        for p, ct in (("/admin", "text/html"), ("/admin/", "text/html"),
                      ("/admin/admin.js", "text/javascript"), ("/admin/admin.css", "text/css")):
            s, hd, raw = self.raw("GET", p)
            self.assertEqual(s, 200, p)
            self.assertTrue(hd["Content-Type"].startswith(ct))
            self.assertIn("default-src 'self'", hd["Content-Security-Policy"])
            self.assertIn("frame-ancestors 'none'", hd["Content-Security-Policy"])
            self.assertEqual(hd["X-Frame-Options"], "DENY")
            self.assertEqual(hd["Referrer-Policy"], "no-referrer")
            self.assertEqual(hd["Cache-Control"], "no-store")
            self.assertEqual(hd["X-Content-Type-Options"], "nosniff")
            self.assertNotIn("Access-Control-Allow-Origin", hd)
            self.assertTrue(raw)
        s, hd, raw = self.raw("GET", "/admin")
        self.assertNotIn(b"<script>", raw)                  # no inline scripts
        self.assertNotIn(b" style=", raw)
        js = self.raw("GET", "/admin/admin.js")[2]
        self.assertNotIn(b"innerHTML", js)
        s, hd, _ = self.raw("GET", "/v2/admin/me")           # API errors carry them too
        self.assertEqual(s, 401)
        self.assertIn("Content-Security-Policy", hd)

    def test_no_traversal_or_extra_files(self):
        for p in ("/admin/../relayd/store.py", "/admin/%2e%2e/relayd/store.py", "/admin/index.html",
                  "/admin/../../party_relay.py", "/admin/admin.py", "/admin/x", "/adminx"):
            self.assertEqual(self.raw("GET", p)[0], 404, p)


class TestApi(AdminBase):
    CFG = dict(admin_pass=PASS, lobby=None, allow_anyone_create=None)

    def setUp(self):
        super().setUp()
        self.ok_login()

    def test_status_and_settings(self):
        st = self.get("status")
        self.assertEqual(st["counts"]["rooms"], 0)
        self.assertFalse(st["settings"]["lobby"]["locked_by_env"])
        self.assertFalse(st["settings"]["lobby"]["value"])
        self.assertIn("legacy_v1", st)
        self.assertFalse(st["feedback_enabled"])
        self.do("PATCH", "settings", {"lobby": True})
        self.assertTrue(self.get("status")["settings"]["lobby"]["value"])
        self.assertEqual(self.call("GET", "/v2/lobby")[1]["enabled"], True)
        self.do("PATCH", "settings", {"lobby": "yes"}, want=400)
        self.do("PATCH", "settings", {}, want=400)
        self.do("PATCH", "settings", {"other": True}, want=400)

    def test_env_locked_setting_409(self):
        self.restart(admin_pass=PASS, lobby=True, allow_anyone_create=None)
        self.ok_login()
        st = self.get("status")
        self.assertTrue(st["settings"]["lobby"]["locked_by_env"])
        self.assertFalse(st["settings"]["allow_anyone_create"]["locked_by_env"])
        self.do("PATCH", "settings", {"lobby": False}, want=409)
        self.do("PATCH", "settings", {"allow_anyone_create": True})

    def test_rooms_list_shows_unlisted_and_detail(self):
        self.do("PATCH", "settings", {"allow_anyone_create": True})
        rid, tok = self.mk(access="unlisted", secret="12345678", name="Hidden")
        self.joined_ok = self.joined(rid, "Bob", secret="12345678")
        self.assertEqual(self.call("GET", "/v2/lobby")[1]["rooms"], [])
        rooms = self.get("rooms")["rooms"]
        self.assertEqual([r["id"] for r in rooms], [rid])
        r = rooms[0]
        for k in ("name", "kind", "access", "has_secret", "members", "pending", "online",
                  "owner_account", "owner_name", "created_t", "last_active_t", "failed_joins", "modules"):
            self.assertIn(k, r)
        self.assertTrue(r["has_secret"])
        self.assertNotIn("secret_hash", r)
        d = self.get("rooms/" + rid)
        self.assertEqual({m["name"] for m in d["members_list"]}, {"Alice", "Bob"})
        self.assertIn("presence", d["module_views"])
        self.assertIn("challenge", d["module_views"])
        self.do("GET", "rooms/abcd-efgh", want=404)

    def test_room_patch_reuses_validation_and_delete(self):
        self.do("PATCH", "settings", {"allow_anyone_create": True})
        rid, _ = self.mk()
        self.do("PATCH", f"rooms/{rid}", {"access": "private"}, want=400)       # needs a secret
        self.do("PATCH", f"rooms/{rid}", {"secret": "x"}, want=400)
        self.do("PATCH", f"rooms/{rid}", {"modules": ["nope"]}, want=400)
        self.do("PATCH", f"rooms/{rid}", {"name": ""}, want=400)
        out = self.do("PATCH", f"rooms/{rid}", {"name": "New", "access": "private", "secret": "123456",
                                                "max_members": 3, "modules": ["presence"]})
        self.assertEqual((out["name"], out["access"], out["modules"]), ("New", "private", ["presence"]))
        self.do("DELETE", f"rooms/{rid}")
        self.do("GET", f"rooms/{rid}", want=404)
        self.assertEqual(self.get("rooms")["rooms"], [])

    def test_member_actions(self):
        self.do("PATCH", "settings", {"allow_anyone_create": True})
        rid, atok = self.mk(access="approval")
        btok = self.joined(rid, "Bob")
        base = f"rooms/{rid}/members/"
        self.assertEqual(self.get("rooms/" + rid)["requests"], ["Bob"])
        self.do("POST", base + "Bob/approve", {})
        self.do("POST", base + "Bob/promote", {})
        self.do("POST", base + "Bob/demote", {})
        self.do("POST", base + "Bob/make_owner", {})
        d = self.get("rooms/" + rid)
        roles = {m["name"]: m["role"] for m in d["members_list"]}
        self.assertEqual(roles, {"Alice": "mod", "Bob": "owner"})
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"name": "X"}, atok)[0], 403)
        self.assertEqual(self.call("PATCH", f"/v2/rooms/{rid}", {"name": "X"}, btok)[0], 200)
        self.do("POST", base + "Bob/make_owner", {}, want=409)
        self.do("POST", base + "Bob/kick", {}, want=403)           # the owner is not kickable
        self.do("POST", base + "Alice/ban", {})
        self.do("POST", base + "Alice/unban", {})
        self.do("POST", base + "Zed/make_owner", {}, want=404)
        self.do("POST", base + "Alice/explode", {}, want=404)

    def test_host_keys_and_accounts(self):
        self.do("POST", "hostkeys", {"label": "", "max_rooms": 1}, want=400)
        self.do("POST", "hostkeys", {"label": "Bob", "max_rooms": 0}, want=400)
        self.do("POST", "hostkeys", {"label": "Bob", "max_rooms": 1, "expires_days": 0}, want=400)
        k = self.do("POST", "hostkeys", {"label": "Bob", "max_rooms": 2, "expires_days": 30})
        self.assertRegex(k["key"], r"^[a-z]+-[a-z]+-\d{4}$")
        listed = self.get("hostkeys")["hostkeys"]
        self.assertEqual(len(listed), 1)
        self.assertNotIn("key", listed[0])
        self.assertNotIn("key_hash", listed[0])
        self.assertTrue(listed[0]["expires_t"] > clock.now())
        body = dict(name="R", owner="Bob", kind="party", access="open")
        s, d = self.call("POST", "/v2/rooms", body, headers={"X-Host-Key": k["key"]})
        self.assertEqual(s, 200, d)
        self.assertEqual(self.get("hostkeys")["hostkeys"][0]["rooms_owned"], 1)
        accts = self.get("accounts")["accounts"]
        self.assertEqual(len(accts), 1)
        self.assertEqual(accts[0]["providers"], ["hostkey"])
        self.assertEqual(accts[0]["rooms_owned"], 1)
        self.do("POST", f"accounts/{accts[0]['id']}/disable", {})
        self.assertTrue(self.get("accounts")["accounts"][0]["disabled"])
        s, d = self.call("POST", "/v2/rooms", body, headers={"X-Host-Key": k["key"]})
        self.assertEqual(s, 403)
        self.do("POST", f"accounts/{accts[0]['id']}/enable", {})
        self.assertEqual(self.call("POST", "/v2/rooms", body, headers={"X-Host-Key": k["key"]})[0], 200)
        self.do("POST", "accounts/999/disable", {}, want=404)
        self.do("POST", f"hostkeys/{k['id']}/revoke", {})
        self.assertTrue(self.get("hostkeys")["hostkeys"][0]["revoked"])
        self.assertEqual(self.call("POST", "/v2/rooms", body, headers={"X-Host-Key": k["key"]})[0], 403)
        self.do("POST", "hostkeys/999/revoke", {}, want=404)

    def test_feedback_disabled_404(self):
        self.do("GET", "feedback", want=404)

    def test_feedback_admin(self):
        self.restart(admin_pass=PASS, feedback_key=FB_KEY, feedback_gap_s=0, lobby=None)
        self.ok_login()
        self.assertEqual(self.get("feedback"), {"codes": []})
        code = "feedback-test-1"
        ids = []
        for _ in range(2):
            s, r = self.call("POST", f"/v2/feedback/{code}", export())
            self.assertEqual(s, 200)
            ids.append(r["id"])
        codes = self.get("feedback")["codes"]
        self.assertEqual((codes[0]["code"], codes[0]["items"]), (code, 2))
        self.assertEqual(len(self.get("feedback/" + code)["items"]), 2)
        self.assertEqual(self.do("DELETE", "feedback/" + code, {"ids": [ids[0]]})["deleted"], 1)
        self.assertEqual(self.do("DELETE", "feedback/" + code, {})["deleted"], 1)
        self.assertEqual(self.get("feedback")["codes"], [])
        self.assertTrue(self.get("status")["feedback_enabled"])

    def test_audit_has_rows_without_addresses(self):
        self.do("PATCH", "settings", {"lobby": True})
        self.do("POST", "hostkeys", {"label": "Alice", "max_rooms": 3})
        self.restart(admin_pass=PASS, trusted_proxies="127.0.0.1")
        host = self.base.split("//")[1]
        hdr = dict(PROXY, Origin="https://" + host)
        self.login("wrong pass here", extra=hdr)
        self.login(extra=hdr)
        s, d, _ = self.adm("GET", "backup", extra=PROXY)
        self.assertEqual(s, 200)
        rows = self.adm("GET", "audit?limit=50", extra=PROXY)[1]["items"]
        self.assertTrue(rows)
        text = json.dumps(rows)
        for bad in ("127.0.0.1", "203.0.113", "::1"):
            self.assertNotIn(bad, text)
        self.assertIn("proxy", {r["via"] for r in rows})
        self.assertIn("login failed", {r["action"] for r in rows})
        self.assertIn("backup", {r["action"] for r in rows})
        # earlier lan rows survive the restart (same DB) and never hold the pass or a key
        self.assertNotIn(PASS, text)
        db = self.app.store._q("SELECT action, detail, via FROM admin_audit")
        self.assertIn("lan", {r["via"] for r in db})
        self.assertIn("created host key 'Alice' (max 3 rooms)", [r["detail"] for r in db])
        self.assertNotIn("hk_", json.dumps(db))

    def test_audit_trim_and_limit(self):
        for i in range(2010):
            self.app.store.add_audit(clock.now(), "x", str(i), "lan")
        self.assertEqual(self.app.store._q("SELECT COUNT(*) AS n FROM admin_audit")[0]["n"], 2000)
        self.assertEqual(len(self.get("audit?limit=9999")["items"]), 500)
        self.assertEqual(len(self.get("audit?limit=3")["items"]), 3)
        self.assertEqual(self.get("audit?limit=3")["items"][0]["detail"], "2009")

    def test_backup(self):
        self.do("PATCH", "settings", {"lobby": True})
        s, hd, raw = self.raw("GET", "/v2/admin/backup", headers={"Cookie": "relay_admin=" + self.cookie})
        self.assertEqual(s, 200)
        self.assertTrue(raw.startswith(b"SQLite format 3\x00"))
        self.assertEqual(hd["Content-Type"], "application/octet-stream")
        self.assertRegex(hd["Content-Disposition"], r'^attachment; filename="relay-\d{8}-\d{4}\.db"$')
        path = Path(self.tmp) / "copy.db"
        path.write_bytes(raw)
        con = sqlite3.connect(str(path))
        self.assertEqual(con.execute("SELECT v FROM settings WHERE k='lobby'").fetchone()[0], "1")
        con.close()

    def test_unknown_admin_path_404(self):
        self.do("GET", "nothing", want=404)
        self.do("GET", "rooms/not-an-id", want=404)


if __name__ == "__main__":
    unittest.main()
