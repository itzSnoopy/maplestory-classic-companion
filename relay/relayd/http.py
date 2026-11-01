"""v2 HTTP routing as a mixin for the handler (see legacy.py). JSON in, JSON out."""
import json
import sys
import traceback
from urllib.parse import unquote

from .errors import ApiError
from .rooms import ROOM_ID_RE

MAX_BODY = 8192
FEEDBACK_MAX_BODY = 65536


class V2Mixin:
    """Needs `self._send(code, obj)` and `self.server.app` (a rooms.Relay)."""

    def _json_body(self, limit=MAX_BODY, required=False):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad Content-Length")
        if n > limit:
            self.close_connection = True
            raise ApiError(413, "body too large")
        if n <= 0:
            if required:
                raise ApiError(413, "body too large or empty")
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            raise ApiError(400, "bad json")

    def handle_v2(self, method, path):
        app = self.server.app
        try:
            if not app.rate_ok(self.cinfo.ip):
                raise ApiError(429, "slow down")
            self._send(200, self._route(app, method, path))
        except ApiError as e:
            self._send(e.code, {"error": e.msg, **e.extra})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._send(500, {"error": "internal error"})

    def _route(self, app, method, path):
        seg = [unquote(s) for s in path.strip("/").split("/")]       # seg[0] == "v2"
        n = len(seg)
        auth = self.headers.get("Authorization")
        addr = self.cinfo.ip
        if n == 2 and seg[1] == "lobby" and method == "GET":
            return app.lobby()
        if n == 3 and seg[1] == "feedback":
            return self._feedback(app, method, seg[2], addr)
        if n >= 2 and seg[1] == "rooms":
            if n == 2 and method == "POST":
                return app.create(addr, self._json_body(), self.headers.get("X-Host-Key"))
            if n >= 3:
                rid = seg[2]
                if not ROOM_ID_RE.match(rid):
                    raise ApiError(404, "not found")
                if n == 3:
                    if method == "GET":
                        return app.view(rid, auth, addr)
                    if method == "PATCH":
                        return app.patch(rid, auth, self._json_body(), addr)
                    if method == "DELETE":
                        return app.delete(rid, auth, addr)
                elif n == 4 and method == "GET" and seg[3] == "me":
                    return app.me(rid, auth, addr)
                elif n == 5 and method == "GET" and seg[3] != "members":
                    return app.module_read(rid, auth, seg[3], seg[4], addr)
                elif n == 4 and method == "POST":
                    if seg[3] == "join":
                        return app.join(addr, rid, self._json_body())
                    if seg[3] == "sync":
                        return app.sync(rid, auth, self._json_body(), addr)
                    if seg[3] == "leave":
                        return app.leave(rid, auth, addr)
                elif n == 5 and method == "POST":
                    if seg[3] == "members":
                        raise ApiError(404, "not found")
                    return app.module_action(rid, auth, seg[3], seg[4], self._json_body(), addr)
                elif n == 6 and method == "POST" and seg[3] == "members":
                    return app.member_action(rid, auth, seg[4], seg[5], addr)
        raise ApiError(404, "not found")

    def _feedback(self, app, method, code, addr):
        fb = app.feedback
        if not fb.enabled or method not in ("GET", "POST", "DELETE"):
            raise ApiError(404, "not found")
        fb.check_code(code)
        key = self.headers.get("X-Feedback-Key") or ""
        if method == "POST":
            fb.gap_ok(addr)
            out = fb.post(code, self._json_body(FEEDBACK_MAX_BODY, required=True))
            fb.mark_sent(addr)
            return out
        fb.check_key(key)
        if method == "GET":
            return fb.get(code)
        return fb.delete(code, self._json_body(FEEDBACK_MAX_BODY))
