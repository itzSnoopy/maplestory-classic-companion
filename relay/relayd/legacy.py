"""The v1 bridge: the v2 handler is the v1 handler plus the /v2 routes.

/v1/... goes to party_relay untouched (its own memory state) while LEGACY_V1 is on.
"""
import os
import sys

_RELAY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RELAY_DIR not in sys.path:
    sys.path.insert(0, _RELAY_DIR)
import party_relay  # noqa: E402

from . import VERSION  # noqa: E402
from .admin import AdminMixin  # noqa: E402
from .http import V2Mixin  # noqa: E402
from .public import PublicMixin  # noqa: E402


class Handler(PublicMixin, AdminMixin, V2Mixin, party_relay.Handler):
    server_version = "relay/2"

    def _health(self):
        app = self.server.app
        c = app.access.resolve(self.client_address[0], self.headers)
        # "you": the caller's own address as the relay sees it (helps set EXTRA_PROXIES behind a proxy)
        return {"ok": True, "version": VERSION, "source": party_relay.SOURCE_URL,
                "lobby": app.flag("lobby"), "feedback": bool(party_relay.FEEDBACK_KEY or app.cfg.feedback_key),
                "legacy_v1": app.cfg.legacy_v1,
                "create_open": app.flag("allow_anyone_create"), "you": c.ip, "via_proxy": c.proxied}

    def _dispatch(self, method):
        path = self.path.split("?", 1)[0]
        if method == "GET" and path == "/health":
            return self._send(200, self._health())
        if path.startswith("/v2/") or path.startswith("/admin"):
            self.cinfo = self.server.app.access.resolve(self.client_address[0], self.headers)
            if path == "/admin" or path.startswith("/admin/") or path.startswith("/v2/admin/"):
                return self.handle_admin(method, self.path)
        if path == "/rooms" or path.startswith("/rooms/"):
            self.cinfo = self.server.app.access.resolve(self.client_address[0], self.headers)
            return self.handle_public(method, path)
        if path.startswith("/v1/"):
            if self.server.app.cfg.legacy_v1 and method in ("GET", "POST", "DELETE"):
                return getattr(party_relay.Handler, "do_" + method)(self)
            return self._send(404, {"error": "not found"})
        if path.startswith("/v2/"):
            return self.handle_v2(method, path)
        return self._send(404, {"error": "not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")
