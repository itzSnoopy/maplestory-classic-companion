"""The public /rooms page (create a room, see the lobby). Static files from an explicit allowlist.

Not behind the admin gate: anyone who can reach the relay can open it. It holds no secrets and sets
no cookies; the buttons call the ordinary /v2 API (host key or tokens, no cookies, so no CSRF).
"""
import os
import re
import sys
import traceback

from .admin import SEC_HEADERS
from .errors import ApiError

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_ui")
PAGES = {                       # explicit allowlist: URL path -> (file in UI_DIR, content type)
    "/rooms": ("index.html", "text/html; charset=utf-8"),
    "/rooms/": ("index.html", "text/html; charset=utf-8"),
    "/rooms/rooms.js": ("rooms.js", "text/javascript; charset=utf-8"),
    "/rooms/rooms.css": ("rooms.css", "text/css; charset=utf-8"),
    "/rooms/relic.js": ("relic.js", "text/javascript; charset=utf-8"),
    "/rooms/relic.css": ("relic.css", "text/css; charset=utf-8"),
}
RELIC_PAGE = re.compile(r"^/rooms/[a-z0-9]{4}-[a-z0-9]{4}/relic/?$")      # the shared table of one room


class PublicMixin:
    """Needs self.cinfo, self.server.app and AdminMixin._admin_send (same security headers)."""

    def handle_public(self, method, path):
        app = self.server.app
        try:
            if method != "GET" or not (path in PAGES or RELIC_PAGE.match(path)):
                raise ApiError(404, "not found")
            if not app.rate_ok(self.cinfo.ip):
                raise ApiError(429, "slow down")
            name, ctype = PAGES.get(path) or ("relic.html", "text/html; charset=utf-8")
            with open(os.path.join(UI_DIR, name), "rb") as f:
                self._admin_send(200, f.read(), ctype)
        except ApiError as e:
            self._admin_send(e.code, {"error": e.msg, **e.extra})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._admin_send(500, {"error": "internal error"})
