"""The public /rooms page (create a room, see the lobby). Static files from an explicit allowlist.

Not behind the admin gate: anyone who can reach the relay can open it. It holds no secrets and sets
no cookies; the buttons call the ordinary /v2 API (host key or tokens, no cookies, so no CSRF).
"""
import os
import re
import sys
import traceback
from urllib.parse import unquote

from . import pictures
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
GALLERY_PATH = re.compile(r"^/rooms/gallery/([^/]+)$")                    # one gallery picture (the name is checked again)
# the same policy as the admin page, with blob: added to img-src only (the table shows an uploaded picture as a blob)
CSP = ("Content-Security-Policy", dict(SEC_HEADERS)["Content-Security-Policy"].replace(
    "img-src 'self' data:", "img-src 'self' data: blob:"))
RELIC_PAGE = re.compile(r"^/rooms/[a-z0-9]{4}-[a-z0-9]{4}/relic/?$")      # the shared table of one room


class PublicMixin:
    """Needs self.cinfo, self.server.app and AdminMixin._admin_send (same security headers)."""

    def handle_public(self, method, path):
        app = self.server.app
        try:
            gm = GALLERY_PATH.match(path)
            if method != "GET" or not (path in PAGES or RELIC_PAGE.match(path) or gm):
                raise ApiError(404, "not found")
            if not app.rate_ok(self.cinfo.ip):
                raise ApiError(429, "slow down")
            if gm:
                hit = pictures.gallery_file(app.cfg.data_dir, unquote(gm.group(1)))
                if not hit:
                    raise ApiError(404, "not found")
                with open(hit[0], "rb") as f:
                    return self._admin_send(200, f.read(), hit[1].ctype,
                                            [("Cache-Control", "public, max-age=3600")])
            name, ctype = PAGES.get(path) or ("relic.html", "text/html; charset=utf-8")
            with open(os.path.join(UI_DIR, name), "rb") as f:
                self._admin_send(200, f.read(), ctype, [CSP])
        except ApiError as e:
            self._admin_send(e.code, {"error": e.msg, **e.extra})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._admin_send(500, {"error": "internal error"})
