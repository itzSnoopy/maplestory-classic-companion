"""challenge: friendly timed kill races inside a room. Memory only (they last minutes).

Ported from party_relay.py (v1) with the same rules. Differences: state is per room id, the acting
name always comes from the member token, and invitees come from the room (1v1 = a room member,
map / all = members with a live presence row, read through presence.live_rows).

Actions (POST /v2/rooms/<id>/challenge/<action>): create, accept, decline, cancel, kills, get.
Sync part: body "challenge": {"id", "kills": [...]} -> reply {"challenges": [views]}.
"""
import re
import secrets
import threading

from .. import clock
from ..errors import ApiError
from . import Module, register

MODES = ("1v1", "map", "all")
MINUTES = (2, 5, 10, 15)
CH_PENDING_S = 60       # an invitation nobody accepted expires
CH_COUNTDOWN_S = 5      # accept -> start
CH_GRACE_S = 4          # kill events that arrive this long after the end still count (if stamped before it)
CH_KEEP_S = 300         # finished challenges are forgotten after this
CH_SHOW_RESULT_S = 120  # a result stays in the replies this long
CH_MAX = 20             # per room
CH_MAX_KPS = 1.5        # kills per second a player can plausibly make (90/min); more is dropped
CH_MAX_N = 4            # kills in one event
CH_MAX_EVENTS = 20      # events in one post
NAME_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
ID_RE = re.compile(r"^[0-9a-f]{8}$")
OPEN_STATES = ("open", "running")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@register
class Challenge(Module):
    name = "challenge"

    def __init__(self):
        self.lock = threading.RLock()
        self.rooms = {}         # room id -> {challenge id: challenge}

    # ---- clock
    def _tick(self, room_id, now):
        """Move this room's challenges along with the clock; forget old ones. Call under the lock."""
        chs = self.rooms.get(room_id)
        if not chs:
            self.rooms.pop(room_id, None)
            return {}
        for cid, c in list(chs.items()):
            if c["state"] == "open" and now - c["created_t"] > CH_PENDING_S:
                c["state"], c["ended_t"] = "expired", now
            elif c["state"] == "running" and now > c["end_t"] + CH_GRACE_S:
                ranking = sorted(({"name": n, "score": p["score"]} for n, p in c["players"].items()
                                  if p["accepted"]), key=lambda r: (-r["score"], r["name"]))
                tie = len(ranking) > 1 and ranking[0]["score"] == ranking[1]["score"]
                c["state"], c["ended_t"] = "done", now
                c["result"] = {"ranking": ranking, "tie": tie,
                               "winner": None if tie else ranking[0]["name"]}
            if c["state"] not in OPEN_STATES and now - c["ended_t"] > CH_KEEP_S:
                del chs[cid]
        if not chs:
            self.rooms.pop(room_id, None)
        return chs

    @staticmethod
    def _busy(chs, name):
        return any(c["state"] in OPEN_STATES and c["players"].get(name, {}).get("accepted")
                   for c in chs.values())

    @staticmethod
    def _view(c, now):
        if c["state"] == "running":
            phase = "starting" if now < c["start_t"] else "live" if now <= c["end_t"] else "ending"
        else:
            phase = c["state"]
        out = {"id": c["id"], "type": "kills", "mode": c["mode"], "minutes": c["minutes"],
               "creator": c["creator"], "state": c["state"], "phase": phase,
               "created_t": c["created_t"], "invited": sorted(c["invited"]),
               "start_t": c.get("start_t"), "end_t": c.get("end_t"),
               "players": [{"name": n, "score": p["score"], "accepted": p["accepted"],
                            "ack": p["seq"]} for n, p in c["players"].items()]}
        if c.get("result"):
            out["result"] = c["result"]
        return out

    def _for(self, room_id, name, now):
        """The challenges a member is in or invited to (open, running, or finished a moment ago)."""
        out = []
        for c in self._tick(room_id, now).values():
            if name not in c["players"] and name not in c["invited"]:
                continue
            if c["state"] in OPEN_STATES or (c["state"] == "done"
                                             and now - c["ended_t"] < CH_SHOW_RESULT_S):
                out.append(self._view(c, now))
        return out

    def _live_rows(self, room_id, now):
        pres = self.relay.modules.get("presence") if self.relay else None
        return pres.live_rows(room_id, now) if pres else {}

    # ---- kills
    @staticmethod
    def _kills(c, name, events, now):
        """Count a player's kill events: [{"s": seq, "n": kills, "t": server-clock time}]. Events are
        acknowledged by seq (the client resends until "ack" catches up); what can't count (before the
        start, after the end, stamped outside it, faster than anyone can kill) is acknowledged and
        ignored."""
        p = c["players"].get(name)
        if p is None or c["state"] != "running" or not isinstance(events, list):
            return
        for e in events[:CH_MAX_EVENTS]:
            if not isinstance(e, dict):
                continue
            s, n, t = e.get("s"), e.get("n"), e.get("t")
            if not isinstance(s, int) or isinstance(s, bool) or s <= p["seq"]:
                continue
            p["seq"] = s
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= CH_MAX_N:
                continue
            if now < c["start_t"] - 1 or now > c["end_t"] + CH_GRACE_S:
                continue
            stamp = t if _num(t) else now
            if stamp < c["start_t"] or stamp > c["end_t"]:
                continue
            if p["score"] + n > (min(now, c["end_t"]) - c["start_t"]) * CH_MAX_KPS + CH_MAX_N:
                continue
            p["score"] += n

    # ---- sync
    def on_sync(self, room, member, part, now):
        rid, name = room["id"], member["name"]
        with self.lock:
            chs = self._tick(rid, now)
            if isinstance(part, dict) and isinstance(part.get("id"), str):
                c = chs.get(part["id"])
                if c is not None:
                    self._kills(c, name, part.get("kills"), now)
            return {"challenges": self._for(rid, name, now)}

    # ---- actions
    def actions(self):
        return {"create": self._create, "accept": self._accept, "decline": self._decline,
                "cancel": self._cancel, "kills": self._post_kills, "get": self._get}

    def _reply(self, c, now):
        return {**self._view(c, now), "server_t": now}

    def _create(self, room, m, d, now):
        rid, name = room["id"], m["name"]
        mode, minutes, target = d.get("mode"), d.get("minutes"), d.get("target")
        if mode not in MODES or minutes not in MINUTES or isinstance(minutes, bool):
            raise ApiError(400, "bad mode or minutes")
        with self.lock:
            chs = self._tick(rid, now)
            if self._busy(chs, name):
                raise ApiError(409, "you are already in a challenge")
            if sum(1 for c in chs.values() if c["state"] in OPEN_STATES) >= CH_MAX:
                raise ApiError(429, "too many challenges")
            if mode == "1v1":
                if not isinstance(target, str) or not NAME_RE.match(target) or target == name:
                    raise ApiError(400, "bad target")
                t = self.relay.store.get_member(rid, target)
                if not t or t["status"] != "active":
                    raise ApiError(404, "target is not in this room")
                invited = {target}
            else:
                live = self._live_rows(rid, now)
                if mode == "map":
                    here = (live.get(name) or {}).get("map")
                    if not here:
                        raise ApiError(409, "post your state first")
                    invited = {n for n, f in live.items() if n != name and f.get("map") == here}
                else:
                    invited = {n for n in live if n != name}
            if not invited:
                raise ApiError(409, "nobody to invite")
            c = {"id": secrets.token_hex(4), "mode": mode, "minutes": minutes, "creator": name,
                 "created_t": now, "state": "open", "invited": invited,
                 "players": {name: {"accepted": True, "score": 0, "seq": 0}}}
            self.rooms.setdefault(rid, {})[c["id"]] = c
            return self._reply(c, now)

    def _find(self, room, d, now):
        """Challenge by body id (under the lock); also ticks the room."""
        cid = d.get("id")
        if not isinstance(cid, str):
            raise ApiError(400, "bad id")
        c = self._tick(room["id"], now).get(cid) if ID_RE.match(cid) else None
        if c is None:
            raise ApiError(404, "no such challenge")
        return c

    def _accept(self, room, m, d, now):
        name = m["name"]
        with self.lock:
            c = self._find(room, d, now)
            chs = self.rooms.get(room["id"], {})
            if name not in c["invited"] or c["state"] not in OPEN_STATES:
                raise ApiError(409, "can't accept")
            if c["state"] == "running" and now >= c["start_t"]:
                raise ApiError(409, "already started")
            if self._busy(chs, name):
                raise ApiError(409, "you are already in a challenge")
            c["invited"].discard(name)
            c["players"][name] = {"accepted": True, "score": 0, "seq": 0}
            if c["state"] == "open":
                c["state"] = "running"
                c["start_t"] = now + CH_COUNTDOWN_S
                c["end_t"] = c["start_t"] + c["minutes"] * 60
            return self._reply(c, now)

    def _decline(self, room, m, d, now):
        name = m["name"]
        with self.lock:
            c = self._find(room, d, now)
            if name not in c["invited"]:
                raise ApiError(409, "not invited")
            c["invited"].discard(name)
            if c["state"] == "open" and (c["mode"] == "1v1" or not c["invited"]):
                c["state"], c["ended_t"] = "declined", now
            return self._reply(c, now)

    def _cancel(self, room, m, d, now):
        with self.lock:
            c = self._find(room, d, now)
            if m["name"] != c["creator"] or c["state"] != "open":
                raise ApiError(409, "can't cancel")
            c["state"], c["ended_t"] = "cancelled", now
            return self._reply(c, now)

    def _post_kills(self, room, m, d, now):
        with self.lock:
            c = self._find(room, d, now)
            self._kills(c, m["name"], d.get("kills"), now)
            return self._reply(c, now)

    def _get(self, room, m, d, now):
        with self.lock:
            return self._reply(self._find(room, d, now), now)

    # ---- housekeeping
    def tick(self, room, now):
        with self.lock:
            self._tick(room["id"], now)

    def active_rooms(self):
        with self.lock:
            return list(self.rooms)

    def admin_view(self, room):
        with self.lock:
            chs = self.rooms.get(room["id"]) or {}
            return {"open": sum(1 for c in chs.values() if c["state"] in OPEN_STATES)}

    def on_member_removed(self, room_id, name):
        now = clock.now()
        with self.lock:
            for c in (self.rooms.get(room_id) or {}).values():
                if c["state"] not in OPEN_STATES:
                    continue
                if c["creator"] == name:
                    c["state"], c["ended_t"] = "cancelled", now
                else:
                    c["invited"].discard(name)
                    if c["state"] == "open" and not c["invited"]:
                        c["state"], c["ended_t"] = "cancelled", now

    def on_room_deleted(self, room_id):
        with self.lock:
            self.rooms.pop(room_id, None)
