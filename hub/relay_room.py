"""relay_room.py - the joined relay v2 room, as both processes see it (helper_settings.json "relay": {"room"}).

The HELPER owns the write (hangout panel: join / leave / approval / lost); the TRACKER only reads it (the party
link syncs the room). Pure functions, no tkinter, no network. The room dict:

  {"id", "name" (room name), "kind" (party|hangout), "token" (SECRET: never print, log or commit),
   "status" (active|pending), "joined_t", "me" (the member name used in the room),
   "role" (member|mod|owner: from the join reply / GET /me; the Start button shows for mod and owner)}

The tracker reports a room it can no longer use (token refused = kicked / room deleted) in party.json
(`room_lost`, `room_lost_id`, `room_lost_name`); the helper clears the room when it sees that.
"""
import re

NAME_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")       # the relay's member-name rule
STATUSES = ("active", "pending")
ROLES = ("member", "mod", "owner")


def clean(room):
    """A raw room value -> a well-formed dict, or None (anything without an id and a token is no room)."""
    if not isinstance(room, dict):
        return None
    rid, token = room.get("id"), room.get("token")
    if not (isinstance(rid, str) and rid and isinstance(token, str) and token):
        return None
    status = room.get("status") if room.get("status") in STATUSES else "active"
    out = {"id": rid, "name": str(room.get("name") or rid)[:48], "kind": str(room.get("kind") or "hangout"),
           "token": token, "status": status, "me": str(room.get("me") or ""),
           "role": room.get("role") if room.get("role") in ROLES else "member",
           "joined_t": room.get("joined_t") if isinstance(room.get("joined_t"), (int, float)) else 0}
    return out


def get(settings):
    """The joined room from a helper-settings dict, or None."""
    r = (settings or {}).get("relay")
    return clean(r.get("room")) if isinstance(r, dict) else None


def can_host(room):
    """True when this member may start room activities (owner or mod)."""
    return bool(room) and room.get("role") in ("owner", "mod")


def valid_name(name):
    return bool(isinstance(name, str) and NAME_RE.match(name))
