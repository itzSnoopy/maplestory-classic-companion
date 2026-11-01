"""Admin page and API: sessions, login lockouts, CSRF checks, audit, and the /v2/admin routes.

Reachable only through netaccess.Access.admin_ok (LAN over http, or a trusted TLS proxy).
Sessions and lockouts live in memory (a restart logs everyone out). Nothing here stores addresses.
"""
import http.cookies
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import traceback
from collections import namedtuple
from urllib.parse import parse_qs, unquote

from . import VERSION, auth, clock
from .errors import ApiError

COOKIE = "relay_admin"
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_ui")
PAGES = {                       # explicit allowlist: URL path -> (file in UI_DIR, content type)
    "/admin": ("index.html", "text/html; charset=utf-8"),
    "/admin/": ("index.html", "text/html; charset=utf-8"),
    "/admin/admin.js": ("admin.js", "text/javascript; charset=utf-8"),
    "/admin/admin.css": ("admin.css", "text/css; charset=utf-8"),
}
SEC_HEADERS = (
    ("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; "
     "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
)
REFUSED = "admin is only reachable from the LAN or over https"
ROOM_ID_RE = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
SETTING_KEYS = ("lobby", "allow_anyone_create")

Raw = namedtuple("Raw", "body ctype headers")


def _int_in(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ApiError(400, f"{what} must be a whole number from {lo} to {hi}")
    return v


class Admin:
    """State and rules of the admin side; the HTTP glue is AdminMixin below."""

    def __init__(self, app):
        self.app = app
        cfg = app.cfg
        # hash the plain pass once, so plain and hashed setups verify the same way
        self.pass_hash = cfg.admin_pass_hash or (
            auth.hash_secret(cfg.admin_pass) if cfg.admin_pass else "")
        self.lock = threading.Lock()
        self.max_age_s, self.idle_s = 12 * 3600, 2 * 3600
        self.addr_limit, self.addr_lock_s = 5, 900
        self.global_limit, self.global_window_s, self.global_pause_s = 20, 900, 300
        self.max_sessions = 20
        self._sessions = {}         # sha256(token) -> [created, last used]
        self._addr = {}             # client address -> [wrong count, locked until]
        self._fails = []            # times of recent wrong logins (all addresses)
        self._global_until = 0.0

    # ---- audit
    def audit(self, via, action, detail=""):
        self.app.store.add_audit(clock.now(), action, detail, via)

    # ---- login / sessions
    def second_factor_ok(self, body):
        """Seam for an optional TOTP code later (check body.get("code")). Always true today."""
        return True

    def login(self, client, via, body):
        now = clock.now()
        with self.lock:
            # the global brake only stops internet logins, so strangers can't lock the LAN owner out
            if via != "lan" and self._global_until > now:
                raise ApiError(429, "too many wrong logins, try again in a few minutes")
            a = self._addr.get(client)
            if a and a[1] > now:
                raise ApiError(429, "too many wrong logins from here, try again later")
        pw = body.get("pass") if isinstance(body, dict) else None
        good = isinstance(pw, str) and len(pw) <= 512 and auth.verify_secret(pw, self.pass_hash)
        good = good and self.second_factor_ok(body)
        with self.lock:
            if good:
                self._addr.pop(client, None)
                token = self._new_session(now)
            else:
                self._fail(client, via, now)
        if not good:
            raise ApiError(401, "wrong password")
        self.audit(via, "login ok")
        return token

    def _fail(self, client, via, now):
        if len(self._addr) > 5000:
            for k in [k for k, a in self._addr.items() if a[1] <= now]:
                del self._addr[k]
        a = self._addr.setdefault(client, [0, 0.0])
        a[0] += 1
        self.audit(via, "login failed")
        if a[0] >= self.addr_limit:
            a[0], a[1] = 0, now + self.addr_lock_s
            self.audit(via, "login lockout", f"one address locked for {self.addr_lock_s // 60} min")
        if via == "lan":
            return
        self._fails = [t for t in self._fails if now - t < self.global_window_s] + [now]
        if len(self._fails) > self.global_limit:
            self._fails = []
            self._global_until = now + self.global_pause_s
            self.audit(via, "login lockout",
                       f"too many wrong logins overall, all logins paused {self.global_pause_s // 60} min")

    def _new_session(self, now):
        for h in [h for h, s in self._sessions.items() if self._expired(s, now)]:
            del self._sessions[h]
        while len(self._sessions) >= self.max_sessions:
            del self._sessions[min(self._sessions, key=lambda h: self._sessions[h][1])]
        token = secrets.token_urlsafe(32)
        self._sessions[auth.sha256hex(token)] = [now, now]
        return token

    def _expired(self, s, now):
        return now - s[0] >= self.max_age_s or now - s[1] >= self.idle_s

    def session_ok(self, token):
        if not token:
            return False
        now = clock.now()
        h = auth.sha256hex(token)
        with self.lock:
            s = self._sessions.get(h)
            if s and not self._expired(s, now):
                s[1] = now
                return True
            self._sessions.pop(h, None)
        return False

    def logout(self, token):
        with self.lock:
            self._sessions.pop(auth.sha256hex(token or ""), None)

    # ---- API
    def route(self, method, seg, query, body, via):
        """seg = the path after /v2/admin. Returns a dict, or a Raw for a file download."""
        store = self.app.store
        n = len(seg)
        now = clock.now()
        if n == 1 and seg[0] == "me" and method == "GET":
            return {"ok": True, "via": via}
        if n == 1 and seg[0] == "status" and method == "GET":
            return self._status(now)
        if n == 1 and seg[0] == "settings" and method == "PATCH":
            return self._settings(body, via)
        if seg[0] == "rooms":
            return self._rooms(method, seg, body, via, now)
        if seg[0] == "hostkeys":
            return self._hostkeys(method, seg, body, via, now)
        if seg[0] == "accounts":
            return self._accounts(method, seg, via)
        if seg[0] == "feedback":
            return self._feedback(method, seg, body, via)
        if n == 1 and seg[0] == "audit" and method == "GET":
            try:
                limit = int((query.get("limit") or ["200"])[0])
            except ValueError:
                limit = 200
            return {"items": store.list_audit(max(1, min(500, limit)))}
        if n == 1 and seg[0] == "backup" and method == "GET":
            return self._backup(now, via)
        raise ApiError(404, "not found")

    def _status(self, now):
        app = self.app
        c = app.store.counts(now)
        pres = app.modules.get("presence")
        online = 0
        for r in app.store.admin_rooms():
            if pres and "presence" in r["modules"]:
                online += pres.online(r["id"], now)
        return {"version": VERSION, "uptime_s": int(now - app.started),
                "counts": {**c, "online": online},
                "settings": {k: {"value": app.flag(k), "locked_by_env": getattr(app.cfg, k) is not None}
                             for k in SETTING_KEYS},
                "legacy_v1": app.cfg.legacy_v1, "feedback_enabled": app.feedback.enabled}

    def _settings(self, body, via):
        app = self.app
        if not isinstance(body, dict) or not body:
            raise ApiError(400, "send lobby and/or allow_anyone_create")
        for k, v in body.items():
            if k not in SETTING_KEYS or not isinstance(v, bool):
                raise ApiError(400, "only lobby and allow_anyone_create (true/false) can be set")
            if getattr(app.cfg, k) is not None:
                raise ApiError(409, f"{k} is set by the environment and cannot be changed here")
        for k, v in body.items():
            app.store.set_setting(k, "1" if v else "0")
        self.audit(via, "settings", ", ".join(f"{k}={'on' if v else 'off'}" for k, v in body.items()))
        return self._status(clock.now())["settings"]

    def _room_row(self, rid):
        for r in self.app.store.admin_rooms():
            if r["id"] == rid:
                return r
        raise ApiError(404, "no such room")

    def _online(self, r, now):
        pres = self.app.modules.get("presence")
        return pres.online(r["id"], now) if pres and "presence" in r["modules"] else 0

    def _rooms(self, method, seg, body, via, now):
        app, store = self.app, self.app.store
        n = len(seg)
        if n == 1 and method == "POST":
            out = app.create_admin(body)
            self.audit(via, "room create", f"created room '{out['room']['name']}' ({out['id']}), "
                       f"{out['room']['kind']}, {out['room']['access']}")
            return out
        if n == 1 and method == "GET":
            rows = store.admin_rooms()
            for r in rows:
                r["online"] = self._online(r, now)
                r["failed_joins"] = app.lockouts.failed_joins(r["id"])
            return {"rooms": rows}
        if n < 2 or not ROOM_ID_RE.match(seg[1]):
            raise ApiError(404, "not found")
        rid = seg[1]
        if n == 2 and method == "GET":
            r = self._room_row(rid)
            room = store.get_room(rid)
            members = store.admin_members(rid)
            for m in members:
                m["last_seen_t"] = max(m["last_seen_t"] or 0, app._seen.get((rid, m["name"]), 0))
            r["online"] = self._online(r, now)
            r["failed_joins"] = app.lockouts.failed_joins(rid)
            r["members_list"] = members
            r["requests"] = [m["name"] for m in members if m["status"] == "pending"]
            r["module_views"] = {name: app.modules[name].admin_view(room)
                                 for name in room["modules"] if name in app.modules}
            return r
        room = store.get_room(rid)
        if not room:
            raise ApiError(404, "no such room")
        if n == 2 and method == "PATCH":
            out = app._patch(room, body)
            keys = [k for k in ("name", "kind", "access", "secret", "max_members", "modules")
                    if k in body]
            self.audit(via, "room edit", f"edited room '{room['name']}' ({rid}): {', '.join(keys)}")
            return out
        if n == 2 and method == "DELETE":
            with app.lock:
                app._delete_room(rid)
            self.audit(via, "room delete", f"deleted room '{room['name']}' ({rid})")
            return {"ok": True}
        if n == 5 and method == "POST" and seg[2] == "members":
            app._member_action(rid, "admin", seg[3], seg[4])
            self.audit(via, "member " + seg[4], f"{seg[4]} '{seg[3]}' in room '{room['name']}' ({rid})")
            return {"ok": True}
        raise ApiError(404, "not found")

    def _hostkeys(self, method, seg, body, via, now):
        store = self.app.store
        n = len(seg)
        if n == 1 and method == "GET":
            return {"hostkeys": [{
                "id": h["id"], "label": h["label"], "max_rooms": h["max_rooms"],
                "expires_t": h["expires_t"], "revoked": bool(h["revoked"]),
                "rooms_owned": h["rooms_owned"] if h["account_id"] is not None else 0,
                "created_t": h["created_t"]} for h in store.admin_host_keys()]}
        if n == 1 and method == "POST":
            if not isinstance(body, dict):
                raise ApiError(400, "body must be a JSON object")
            label = body.get("label")
            if (not isinstance(label, str) or not 1 <= len(label) <= 40 or CTRL_RE.search(label)
                    or not label.strip()):
                raise ApiError(400, "label must be 1-40 characters")
            max_rooms = _int_in(body.get("max_rooms"), 1, 100, "max_rooms")
            exp = None
            if body.get("expires_days") is not None:
                exp = now + _int_in(body["expires_days"], 1, 3650, "expires_days") * 86400
            kid, key = store.create_host_key(label, max_rooms, exp, with_id=True, created_t=now)
            self.audit(via, "hostkey create", f"created host key '{label}' (max {max_rooms} rooms)")
            return {"id": kid, "key": key}
        if n == 3 and method == "POST" and seg[2] == "revoke" and seg[1].isdigit():
            hk = store.host_key(int(seg[1]))
            if not hk:
                raise ApiError(404, "no such host key")
            store.revoke_host_key(hk["id"])
            self.audit(via, "hostkey revoke", f"revoked host key '{hk['label']}' (#{hk['id']})")
            return {"ok": True}
        raise ApiError(404, "not found")

    def _accounts(self, method, seg, via):
        store = self.app.store
        n = len(seg)
        if n == 1 and method == "GET":
            return {"accounts": [{
                "id": a["id"], "display_name": a["display_name"],
                "providers": a["providers"].split(",") if a["providers"] else [],
                "rooms_owned": a["rooms_owned"], "created_t": a["created_t"],
                "disabled": bool(a["disabled"])} for a in store.admin_accounts()]}
        if n == 3 and method == "POST" and seg[2] in ("disable", "enable") and seg[1].isdigit():
            acct = store.account(int(seg[1]))
            if not acct:
                raise ApiError(404, "no such account")
            store.set_account_disabled(acct["id"], seg[2] == "disable")
            self.audit(via, "account " + seg[2],
                       f"{seg[2]}d account '{acct['display_name']}' (#{acct['id']})")
            return {"ok": True}
        raise ApiError(404, "not found")

    def _feedback(self, method, seg, body, via):
        app = self.app
        fb = app.feedback
        if not fb.enabled:
            raise ApiError(404, "not found")
        n = len(seg)
        if n == 1 and method == "GET":
            fb.prune(clock.now())
            return {"codes": app.store.feedback_codes()}
        if n == 2 and CODE_RE.match(seg[1]):
            if method == "GET":
                return fb.get(seg[1])
            if method == "DELETE":
                out = fb.delete(seg[1], body)
                self.audit(via, "feedback delete",
                           f"deleted {out['deleted']} feedback item(s) of one code")
                return out
        raise ApiError(404, "not found")

    def _backup(self, now, via):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self.app.store.backup_to(path)
            with open(path, "rb") as f:
                data = f.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        name = time.strftime("relay-%Y%m%d-%H%M.db", time.gmtime(now))
        self.audit(via, "backup", "downloaded a database backup")
        return Raw(data, "application/octet-stream",
                   [("Content-Disposition", f'attachment; filename="{name}"')])


class AdminMixin:
    """HTTP side. Needs self.cinfo (netaccess.Client), self.server.app, V2Mixin._json_body."""

    def _admin_send(self, code, body, ctype="application/json", extra=()):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (*SEC_HEADERS, *extra):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)            # never any CORS headers

    def _csrf_ok(self):
        if self.headers.get("X-Relay-Admin") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin is not None:
            want = ("https" if self.cinfo.via_tls else "http") + "://" + (self.headers.get("Host") or "")
            if origin.lower() != want.lower():
                return False
        return True

    def _session_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        try:
            jar = http.cookies.SimpleCookie(raw)
        except http.cookies.CookieError:
            return ""
        m = jar.get(COOKIE)
        return m.value if m else ""

    def _cookie(self, value, max_age):
        c = f"{COOKIE}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"
        return c + ("; Secure" if self.cinfo.via_tls else "")

    def handle_admin(self, method, rawpath):
        app = self.server.app
        path, _, qs = rawpath.partition("?")
        c = self.cinfo
        try:
            if not app.cfg.admin_enabled:
                raise ApiError(404, "not found")
            if not app.access.admin_ok(c):
                raise ApiError(403, REFUSED)
            if not app.rate_ok(c.ip):
                raise ApiError(429, "slow down")
            if path in PAGES:
                if method != "GET":
                    raise ApiError(404, "not found")
                name, ctype = PAGES[path]
                with open(os.path.join(UI_DIR, name), "rb") as f:
                    return self._admin_send(200, f.read(), ctype)
            if not path.startswith("/v2/admin/"):
                raise ApiError(404, "not found")
            seg = [unquote(s) for s in path[len("/v2/admin/"):].strip("/").split("/")]
            via = "proxy" if c.proxied else "lan"
            adm = app.admin
            body = {}
            if method != "GET":
                if not self._csrf_ok():
                    raise ApiError(403, "missing or wrong X-Relay-Admin / Origin")
                body = self._json_body()
            if seg == ["login"] and method == "POST":
                token = adm.login(c.ip, via, body)
                return self._admin_send(200, {"ok": True},
                                        extra=[("Set-Cookie", self._cookie(token, adm.max_age_s))])
            token = self._session_token()
            if not adm.session_ok(token):
                raise ApiError(401, "not logged in")
            if seg == ["logout"] and method == "POST":
                adm.logout(token)
                adm.audit(via, "logout")
                return self._admin_send(200, {"ok": True},
                                        extra=[("Set-Cookie", self._cookie("", 0))])
            out = adm.route(method, seg, parse_qs(qs), body, via)
            if isinstance(out, Raw):
                return self._admin_send(200, out.body, out.ctype, out.headers)
            self._admin_send(200, out)
        except ApiError as e:
            self._admin_send(e.code, {"error": e.msg, **e.extra})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._admin_send(500, {"error": "internal error"})
