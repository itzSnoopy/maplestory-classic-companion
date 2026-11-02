"""duel.py - the tracker side of a kill-race duel, as plain state (no threads, no files, no bus).

The relay (relay/party_relay.py) keeps the score and decides the winner. This object follows the relay's
challenge views for you, turns the game's EXP lines (own = white = my kill; the EXP bar only as a fallback, gamedata/kills.py) into kill events, keeps
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
PARTY_NOTE_S = 60           # challenge.json says "party: true" this long after the last party hint
SETTLE_S = 2.5              # an EXP bar rise waits this long for the corner's EXP line before the fallback
COVER_BEFORE_S = 1.0        # an EXP line covers a rise when its time is in [rise - 1.0, rise + 2.5]
COVER_AFTER_S = 2.5
PARTY_LOOK_S = 10.0         # a party bonus line seen within this before a rise: the rise is partied
GAINS_KEEP = 40
LINES_NOTE = "kills from the game's EXP lines"
NOT_SEEN_NOTE = "party: EXP lines not seen, kills not counted"
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
        self.party_seen = None  # (note, local time) of the last party hint for challenge.json (see snapshot)
        self.gains = []         # [(t, party bonus seen)] of the latest EXP lines (they cover EXP bar rises)
        self.pending = []       # EXP bar rises held until the corner has had its say: {t, map, d, partied}

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
    # The game's EXP lines (exp_gain, engine/msgcorner.py) are the source of kills: a WHITE line (own=True) is
    # my kill, a yellow one a teammate's share. The EXP bar (exp_rise) is only a fallback for a rise the corner
    # did not cover (corner hidden, line missed): solo -> whole-mob match, partied -> nothing (never guess).
    def _live_ts(self, t):
        """The relay-clock time of t while a challenge I play is running and t is inside its window, else None."""
        v = self.active()
        if v is None:
            return None
        ts = self.to_server(t)
        return ts if v["start_t"] <= ts <= v["end_t"] else None

    def _kill(self, ts, n=1):
        self.seq += 1
        self.events.append({"s": self.seq, "n": n, "t": round(ts, 3)})
        self.mine += n

    def on_gain(self, t, base=0, party_bonus=0, own=False, overflow=False, now=None):
        """One EXP line pair from the message corner at time t. Always remembered (it covers EXP rises); while a
        challenge is live an own (white) line is one kill stamped at t, a teammate's (yellow) counts 0.
        Returns the kills counted."""
        now = now if now is not None else time.time()
        with self.lock:
            self.gains.append((t, bool(party_bonus)))
            del self.gains[:-GAINS_KEEP]
            n = 0
            if self._live_ts(t) is not None:
                if party_bonus:
                    self.party_seen = (LINES_NOTE, time.time())
                if own:
                    self._kill(self._live_ts(t))
                    n = 1
            self._settle(now)
            return n

    def on_rise(self, t, map_name, d, partied=False, now=None):
        """An EXP bar rise d on a look at time t: held until the corner has had its say (SETTLE_S), then, if no
        EXP line covered it, counted by the fallback. `partied` = the player is in a party with someone on the
        map (then the fallback counts nothing). Returns nothing yet (the kill, if any, is sent when it settles)."""
        now = now if now is not None else time.time()
        with self.lock:
            if self._live_ts(t) is not None and d and d > 0:
                self.pending.append({"t": t, "map": map_name, "d": d, "partied": bool(partied)})
            self._settle(now)

    def settle(self, now=None):
        """Decide the held rises that are old enough (called from every hook, also the relay post)."""
        with self.lock:
            self._settle(time.time() if now is None else now)

    def _settle(self, now):
        if not self.pending:
            return
        keep = []
        for r in self.pending:
            if now - r["t"] <= SETTLE_S:
                keep.append(r)
                continue
            if any(r["t"] - COVER_BEFORE_S <= gt <= r["t"] + COVER_AFTER_S for gt, _ in self.gains):
                continue                                    # the EXP lines cover it (they counted my kills)
            ts = self._live_ts(r["t"])
            if ts is None:
                continue
            partied = r["partied"] or any(bonus and r["t"] - PARTY_LOOK_S <= gt <= r["t"] + COVER_AFTER_S for gt, bonus in self.gains)
            if partied:
                self.party_seen = (NOT_SEEN_NOTE, time.time())
                continue                                    # a party share by the bar alone: never guess
            n = kills.whole_kills(r["map"], r["d"], MAX_KILLS)
            if n:
                self._kill(ts, n)
        self.pending = keep

    def _switch(self, cid):
        if cid != self.cid:
            self.cid, self.seq, self.events, self.mine = cid, 0, [], 0
            self.pending = []

    # ---- the party link hooks
    def post_fields(self, enabled, dnd):
        """Extra fields for the next party post (see the relay_post event)."""
        extra = {}
        if not enabled or dnd:
            extra["caps"] = ""                  # friends' Challenge buttons stay off
            return extra
        with self.lock:
            self._settle(time.time())           # held EXP bar rises that nothing covered count now
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
