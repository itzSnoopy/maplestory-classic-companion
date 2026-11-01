"""presence: today's party data (EXP, HP, MP, map ...). Memory only; rows expire after TTL_S."""
import re
import threading

from . import Module, register

TTL_S = 90
MAX_REPLY = 2000


def _pair(v):
    return (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and 0 <= x < 10 ** 6
                                                         for x in v))


# What a member may send. Copied from party_relay.py; must stay in step with own_state() in
# components/party/partylink.py.
FIELDS = {
    "level": lambda v: isinstance(v, int) and 0 < v < 300,
    "job": lambda v: isinstance(v, str) and len(v) <= 32,
    "map": lambda v: isinstance(v, str) and len(v) <= 64,
    "state": lambda v: v in ("playing", "idle", "waiting_for_source", "stopped"),
    "exp_cur": lambda v: isinstance(v, int) and v >= 0,
    "exp_need": lambda v: isinstance(v, int) and v >= 0,
    "exp_pct": lambda v: isinstance(v, (int, float)) and 0 <= v <= 100,
    "exp_hr": lambda v: isinstance(v, (int, float)) and v >= 0,
    "eta": lambda v: isinstance(v, str) and len(v) <= 16,
    "hp": lambda v: _pair(v),
    "mp": lambda v: _pair(v),
    "channel": lambda v: isinstance(v, (int, str)) and len(str(v)) <= 4,
    "v": lambda v: isinstance(v, str) and len(v) <= 16,
    "caps": lambda v: isinstance(v, str) and len(v) <= 32 and re.match(r"^[a-z0-9,]*$", v),
}


@register
class Presence(Module):
    name = "presence"

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}          # room -> {name: (t, fields, role)}

    def _live(self, room_id, now):
        p = self.rows.get(room_id) or {}
        for n in [n for n, r in p.items() if now - r[0] > TTL_S]:
            del p[n]
        if not p:
            self.rows.pop(room_id, None)
        return p

    def on_sync(self, room, member, part, now):
        with self.lock:
            if isinstance(part, dict):
                fields = {k: v for k, v in part.items() if k in FIELDS and FIELDS[k](v)}
                self.rows.setdefault(room["id"], {})[member["name"]] = (now, fields, member["role"])
            p = self._live(room["id"], now)
            out = sorted(p.items(), key=lambda kv: -kv[1][0])[:MAX_REPLY]       # freshest first
            return {"members": [{**f, "name": n, "age_s": round(now - t, 1), "role": role}
                                for n, (t, f, role) in out]}

    def live_rows(self, room_id, now):
        """Read seam for other modules: {name: fields} of the members whose row is within TTL."""
        with self.lock:
            return {n: dict(r[1]) for n, r in self._live(room_id, now).items()}

    def active_rooms(self):
        with self.lock:
            return list(self.rows)

    def tick(self, room, now):
        with self.lock:
            self._live(room["id"], now)

    def admin_view(self, room):
        with self.lock:
            return {"rows": len(self.rows.get(room["id"]) or {})}

    def online(self, room_id, now):
        with self.lock:
            return len(self._live(room_id, now))

    def on_member_removed(self, room_id, name):
        with self.lock:
            (self.rows.get(room_id) or {}).pop(name, None)

    def on_room_deleted(self, room_id):
        with self.lock:
            self.rows.pop(room_id, None)
