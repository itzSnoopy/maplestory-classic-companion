"""relay_addr.py - the one relay address (helper_settings.json "relay": {"server"}) every online feature
that talks to the player's relay uses: the party link, duels (they ride the party link) and the rooms panel.
Skill feedback uses it too when its own server field is empty (that field is for another relay owner).

The player types only a domain or an IP; normalize() makes the URL. Pure functions, no tkinter, no network:
the party link thread (tracker process) and the helper both import it.

  192.0.2.10 -> http://192.0.2.10:8787      relay.example.com:9000 -> http://relay.example.com:9000
  http://host -> http://host:8787           https://host -> https://host (443)
"""
import re

DEFAULT_PORT = 8787     # a bare host / http:// gets this port. Switches to 443 / https at 1.0: change this one line
                        # (and the scheme in normalize()).

_URL = re.compile(r"^(https?)://(.*)$", re.I)


def normalize(text):
    """What the player typed -> 'scheme://host[:port]' (no path, no trailing slash), or '' when empty."""
    s = str(text or "").strip()
    if not s:
        return ""
    scheme = "http"
    m = _URL.match(s)
    if m:
        scheme, s = m.group(1).lower(), m.group(2)
    s = s.split("/", 1)[0].strip()                  # drop any path and trailing slashes
    if "@" in s:                                    # no user:pass@ here
        s = s.rsplit("@", 1)[1]
    host, port = s, ""
    if s.startswith("["):                           # [::1] or [::1]:9000
        end = s.find("]")
        if end < 0:
            return ""
        host, tail = s[:end + 1], s[end + 1:]
        port = tail[1:] if tail.startswith(":") else ""
    elif s.count(":") > 1:                          # bare IPv6 (::1): bracket it, no port possible
        host = "[" + s + "]"
    elif ":" in s:
        host, _, port = s.rpartition(":")
    if not host or host in ("[]",):
        return ""
    port = port.strip()
    if port and not port.isdigit():
        return ""
    if not port and scheme == "http":
        port = str(DEFAULT_PORT)
    return f"{scheme}://{host}" + (f":{port}" if port else "")


def server(settings):
    """The normalized relay address from a helper-settings dict ('' = none set)."""
    r = (settings or {}).get("relay")
    return normalize(r.get("server") if isinstance(r, dict) else "")


def migrate(data):
    """Once, on a settings dict as read from helper_settings.json (before the defaults are merged): the old
    per-feature server fields become the one relay address. relay.server stays if it is set; otherwise
    party.server, else hangout.server. The old keys are removed; nothing else is touched. Changes `data`
    in place and returns it. Safe to call on every read (nothing to do once migrated)."""
    if not isinstance(data, dict):
        return data
    found = None
    for slice_name in ("party", "hangout"):
        sl = data.get(slice_name)
        if isinstance(sl, dict) and "server" in sl:
            old = str(sl.pop("server") or "").strip()
            found = found or old or None
    if found:
        r = data.get("relay")
        if not isinstance(r, dict):
            r = data["relay"] = {}
        if not str(r.get("server") or "").strip():
            r["server"] = found
    return data
