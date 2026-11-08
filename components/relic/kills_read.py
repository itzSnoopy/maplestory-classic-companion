"""Which EXP rises still need the bar fallback: the relic hunt's kill reading (pure, no hub, thread-safe).

Same idea as components/challenge/duel.py: the game's message-corner EXP lines (`exp_gain`) are the source of kills
(a white line = my kill, handled by the component). An EXP bar rise is HELD for SETTLE_S; if any remembered gain
time falls in [rise - COVER_BEFORE_S, rise + COVER_AFTER_S] the lines covered it and it is dropped, otherwise it is
handed back to the caller to read the old way (whole-mob match; partied = a rise flag or a party-bonus line seen
within PARTY_LOOK_S before it).

The tracker thread calls gain() / rise(); the link thread calls settle(). One lock.
"""
import threading
import time

SETTLE_S = 2.5
COVER_BEFORE_S = 1.0
COVER_AFTER_S = 2.5
PARTY_LOOK_S = 10.0
GAINS_KEEP = 40


class KillsRead:
    def __init__(self):
        self.lock = threading.Lock()
        self.gains = []             # (t, bool(party_bonus)), newest last
        self.pending = []           # held rises {t, map, d, party, level}

    def gain(self, t, party_bonus=0):
        with self.lock:
            self.gains.append((t, bool(party_bonus)))
            del self.gains[:-GAINS_KEEP]

    def rise(self, t, map, d, party, level):
        if not d or d <= 0:
            return
        with self.lock:
            self.pending.append({"t": t, "map": map, "d": d, "party": bool(party), "level": level})

    def settle(self, now=None):
        """Decide held rises older than SETTLE_S. Returns [(rise, partied)] the lines did NOT cover: the caller
        counts those with the fallback (and passes party=True to the service when `partied`)."""
        now = time.time() if now is None else now
        out = []
        with self.lock:
            if not self.pending:
                return out
            keep = []
            for r in self.pending:
                if now - r["t"] <= SETTLE_S:
                    keep.append(r)
                    continue
                t = r["t"]
                if any(t - COVER_BEFORE_S <= gt <= t + COVER_AFTER_S for gt, _ in self.gains):
                    continue
                partied = r["party"] or any(b and t - PARTY_LOOK_S <= gt <= t + COVER_AFTER_S
                                            for gt, b in self.gains)
                out.append((r, partied))
            self.pending = keep
        return out
