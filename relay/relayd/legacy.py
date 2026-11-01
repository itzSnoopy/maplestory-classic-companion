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
from .http import V2Mixin  # noqa: E402


class Handler(V2Mixin, party_relay.Handler):
    server_version = "relay/2"

    def _health(self):
        app = self.server.app
        return {"ok": True, "version": VERSION, "source": party_relay.SOURCE_URL,
                "lobby": app.flag("lobby"), "feedback": bool(party_relay.FEEDBACK_KEY),
                "legacy_v1": app.cfg.legacy_v1}

    def _dispatch(self, method):
        path = self.path.split("?", 1)[0]
        if method == "GET" and path == "/health":
            return self._send(200, self._health())
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
