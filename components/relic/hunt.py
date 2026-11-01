"""hunt.py - the relic hunt's plain state (no threads, files or tkinter): the eligible kills waiting to be sent, the
relay's last answer, and what relic.json says. The component wires it to the bus and the party link."""
import threading
import time

TOAST_S = 3.0
MAX_IDS = 400


class Hunt:
    def __init__(self):
        self.lock = threading.Lock()
        self.acc = 0.0              # eligible kills not yet acknowledged by the relay
        self.sent = 0.0             # part of acc in the post that is on its way
        self.active = False         # the relay says a hunt is running
        self.room_ok = False        # a v2 room answered with a relic part
        self.reply = {}             # the last relic part of a sync reply
        self.found_ids = []         # piece ids this player found (since the tracker started)
        self.toast_seq = 0
        self.toast = None           # {seq, t, text}
        self.error = None
        self.last_t = 0.0

    def add(self, kills):
        """Eligible kills from one EXP rise (fractional). Dropped while no hunt is running."""
        if kills <= 0:
            return False
        with self.lock:
            if not self.active:
                return False
            self.acc += kills
            return True

    def post_part(self):
        """The `relic` sync part for the next post, or None (nothing to send / no hunt)."""
        with self.lock:
            if not self.active or self.acc <= 0:
                self.sent = 0.0
                return None
            self.sent = self.acc
            return {"kills": round(self.sent, 4)}

    def on_reply(self, part, now=None):
        """The relay's relic part (or None when the room has no relic module). -> piece ids found just now."""
        now = now or time.time()
        with self.lock:
            self.acc = max(0.0, self.acc - self.sent)       # acknowledged by this answer
            self.sent = 0.0
            self.last_t = now
            if not isinstance(part, dict):
                self.room_ok, self.active, self.reply = False, False, {}
                self.acc = 0.0
                return []
            self.room_ok = True
            self.active = bool(part.get("active"))
            self.reply = part
            if not self.active:
                self.acc = 0.0
            ids = [i for i in part.get("found") or [] if isinstance(i, int)]
            if ids:
                self.found_ids = (self.found_ids + ids)[-MAX_IDS:]
                self.toast_seq += 1
                total = part.get("pieces_total")
                self.toast = {"seq": self.toast_seq, "t": round(now, 2),
                              "text": f"+ Relic piece {part.get('found_count')}/{total}"}
            return ids

    def snapshot(self, enabled, now=None):
        """relic.json."""
        now = now or time.time()
        with self.lock:
            r = self.reply
            return {"t": round(now, 2), "enabled": enabled, "room": self.room_ok, "active": self.active,
                    "finished": bool(r.get("finished")), "difficulty": r.get("difficulty"),
                    "pieces_total": r.get("pieces_total"), "found_count": r.get("found_count"),
                    "placed_count": r.get("placed_count"), "next_chance_pct": r.get("next_chance_pct"),
                    "together": r.get("together"), "found_ids": list(self.found_ids),
                    "pending": round(self.acc, 3), "toast": self.toast, "error": self.error}
