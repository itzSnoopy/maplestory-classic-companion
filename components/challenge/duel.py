"""duel.py - the tracker side of a kill-race duel, as plain state (no threads, no files, no bus).

The relay (relay/party_relay.py) keeps the score and decides the winner. This object follows the relay's
challenge views for you, turns EXP rises into kill events (whole-mob matches only, gamedata/kills.py), keeps
the events the relay has not acknowledged yet, and builds what the helper's board and the party panel read
(challenge.json).

Times: the relay's clock is the clock of a duel. `server_off` (relay minus ours, from the party link)
converts our look times to it.
"""
import threading
import time

from gamedata import kills

PENDING_S = 60              # an invitation lives this long (relay CH_PENDING_S)
MAX_KILLS = 2               # kills that may share one rise (album's number: looks are ~1 s apart)
MAX_SEND = 20               # events in one post (relay CH_MAX_EVENTS)
RESULT_KEEP_S = 30          # how long the finished board stays in challenge.json
PARTY_NOTE_S = 60           # challenge.json says "party: true" this long after a rise read as a party share
STALE_S = 15                # challenge.json older than this: the helper stops showing the board


class Duel:
    def __init__(self):
        self.lock = threading.Lock()
        self.me = None
        self.server_off = 0.0
        self.views = []         # the relay's challenges for me, as of the last answer
        self.got_t = 0.0        # when (local) the last answer came
        self.cid = None         # the challenge the events below belong to
        self.seq = 0
        self.events = []        # kill events the relay has not acknowledged: [{s, n, t}]
        self.mine = 0           # my kills in this challenge, counted here (the board shows the bigger of this and the relay's)
        self.done = set()       # ids of finished challenges already reported
        self.last = None        # the last result (dict) and when it was seen
        self.last_t = 0.0
        self.error = None       # (text, local time) of the last failed command
        self.party_seen = None  # (note, local time) of the last rise read as a party share

    # ---- helpers
    def to_server(self, t):
        return t + self.server_off

    def _mine(self, view):
        return next((p for p in view["players"] if p["name"] == self.me), None)

    def active(self):
        """The running challenge I take part in (countdown, live or ending), else None."""
        for v in self.views:
            if v["state"] == "running" and (p := self._mine(v)) and p["accepted"]:
                return v
        return None

    def waiting(self):
        """An open challenge I made, nobody accepted yet."""
        return next((v for v in self.views if v["state"] == "open" and v["creator"] == self.me), None)

    def joined_open(self):
        """An open race (a host race waiting for answers) that I already accepted."""
        return next((v for v in self.views if v["state"] == "open" and (p := self._mine(v)) and p["accepted"]),
                    None)

    def invite(self, now):
        """The invitation to me that can still be accepted: the view, or None."""
        for v in self.views:
            if self.me in v["invited"] and v["state"] in ("open", "running"):
                if v["state"] == "running" and self.to_server(now) >= v["start_t"]:
                    continue
                return v
        return None

    # ---- kills
    def on_rise(self, t, map_name, d, party=None):
        """An EXP rise d on a look at time t: count the kills in it while a challenge is live.

        Solo: whole-mob matches (kills.whole_kills). `party` = {me_level, levels (other members on my map),
        bonus (a kills.BonusFit or None), estimating} while partied: the rise is a party share, read by kills.party_kills; only MY kills
        count (a teammate's kill or an unknown share counts nothing)."""
        with self.lock:
            v = self.active()
            if v is None:
                return 0
            ts = self.to_server(t)
            if not v["start_t"] <= ts <= v["end_t"]:
                return 0
            if party:
                n = kills.party_kills(map_name, d, party["me_level"], party["levels"], party["bonus"], MAX_KILLS)[0]
                bon = party.get("bonus")
                self.party_seen = (("party share, bonus %s" % (bon if bon is not None else "not learned yet")
                                    + (" (estimating)" if party.get("estimating") else "")), time.time())
            else:
                n = kills.whole_kills(map_name, d, MAX_KILLS)
            if n:
                self.seq += 1
                self.events.append({"s": self.seq, "n": n, "t": round(ts, 3)})
                self.mine += n
            return n

    def _switch(self, cid):
        if cid != self.cid:
            self.cid, self.seq, self.events, self.mine = cid, 0, [], 0

    # ---- the party link hooks
    def post_fields(self, enabled, dnd):
        """Extra fields for the next party post (see the relay_post event)."""
        extra = {}
        if not enabled or dnd:
            extra["caps"] = ""                  # friends' Challenge buttons stay off
            return extra
        with self.lock:
            v = self.active()
            if v is not None:
                self._switch(v["id"])
                extra["ch"] = {"id": v["id"], "kills": self.events[:MAX_SEND]}
                extra["_fast"] = True
            elif (self.waiting() is not None or self.joined_open() is not None
                  or self.invite(time.time()) is not None):
                extra["_fast"] = True
        return extra

    def on_reply(self, reply, server_off, enabled=True, dnd=False, now=None):
        """The relay answered. Returns the finished results to report (list of dicts)."""
        now = now or time.time()
        with self.lock:
            self.server_off = server_off
            self.got_t = now
            self.views = [v for v in reply.get("challenges") or [] if isinstance(v, dict)]
            if not enabled or dnd:
                self.views = [v for v in self.views if self.me in [p["name"] for p in v["players"]]]
            v = self.active()
            if v is not None:
                self._switch(v["id"])
                p = self._mine(v)
                ack = p.get("ack", 0)
                self.events = [e for e in self.events if e["s"] > ack]
                # after a restart mid-duel our numbering starts at 0 again, but the relay ignores
                # numbers it has seen: carry on from its ack so new kills still count
                if self.seq < ack:
                    self.seq = ack
            out = []
            for v in self.views:
                if v["state"] != "done" or v["id"] in self.done or not v.get("result"):
                    continue
                if not (p := self._mine(v)) or not p["accepted"]:
                    continue
                self.done.add(v["id"])
                out.append(self._result(v, now))
            if out:
                self.last, self.last_t = out[-1], now
            return out

    def _result(self, v, now):
        ranking = v["result"]["ranking"]
        mine = next((r["score"] for r in ranking if r["name"] == self.me), 0)
        others = [r for r in ranking if r["name"] != self.me]
        top = max(others, key=lambda r: r["score"]) if others else {"name": "?", "score": 0}
        won = None if v["result"]["tie"] else v["result"]["winner"] == self.me
        return {"id": v["id"], "mode": v["mode"], "minutes": v["minutes"], "me": self.me, "won": won,
                "score": [mine, top["score"]], "opponent": top["name"],
                "names": [r["name"] for r in others], "ranking": ranking, "t": round(now, 2)}

    # ---- challenge.json
    def snapshot(self, enabled, dnd, now=None):
        """What the helper reads (board) and the party panel (button, invitation)."""
        now = now or time.time()
        with self.lock:
            snap = {"t": round(now, 2), "enabled": bool(enabled), "dnd": bool(dnd), "me": self.me,
                    "server_off": round(self.server_off, 3), "busy": False, "invite": None,
                    "active": None, "last": None, "party": False}
            if self.party_seen and now - self.party_seen[1] < PARTY_NOTE_S:
                snap["party"], snap["party_note"] = True, self.party_seen[0]
            if not enabled:
                return snap
            act = self.active()
            wait = self.waiting()
            snap["busy"] = bool(act or wait or self.joined_open())
            if wait is not None:                # my open invite: the party panel shows "Waiting for X" + Cancel
                inv_names = sorted(wait.get("invited") or [])
                snap["waiting"] = {"id": wait["id"], "mode": wait["mode"],
                                   "target": inv_names[0] if len(inv_names) == 1 else None}
            inv = None if dnd else self.invite(now)
            if inv is not None:
                end = (inv["start_t"] - self.server_off if inv["state"] == "running"
                       else inv["created_t"] - self.server_off + PENDING_S)
                snap["invite"] = {"id": inv["id"], "from": inv["creator"], "mode": inv["mode"],
                                  "minutes": inv["minutes"], "expires": round(end, 2),
                                  "host": bool(inv.get("host"))}
            show = act
            if show is None:                    # a finished one stays on the board for a moment
                show = next((v for v in self.views if v["state"] == "done" and self._mine(v)
                             and v["id"] in self.done and now - self.last_t < RESULT_KEEP_S), None)
            if show is not None:
                snap["active"] = {**show, "mine_local": self.mine if show["id"] == self.cid else 0}
            if self.last:
                snap["last"] = self.last
            if self.error and now - self.error[1] < 8:
                snap["error"] = self.error[0]
            return snap
