"""exprate.py - EXP per hour on the map you're on: early, then settling into the real average.

Workshopped with the player 2026-10-25 (NOTES.md, replays of real stays):
  - the clock is *active* time on the map: it starts at your first EXP gain there, and a stretch
    of BREAK_S without any EXP (shopping, chatting, AFK on the map) doesn't count. The 46-min stay
    on Caution Falling Down said 9,290/hr counting a 22-min break, 17,171/hr without it.
  - a first number after MIN_GAINS gains, marked early ("~") until EARLY_S of active time; after
    that it just flattens as the average grows. No smoothing, no verdict: the player decides.
  - party share counts toward the rate and is reported apart ("(+3.1k party)").
  - quest turn-ins and other lumps (EXP in town, or more than a few of the map's biggest mob at
    once) count toward your level but not toward the rate (training.py decides what a rise is).
  - with_breaks: the same EXP over all the time since the first gain, for a second, realistic ETA.
"""
BREAK_S = 120          # this long without EXP = a break: the time isn't training
MIN_GAINS = 3          # first number after this many EXP gains
MIN_ACTIVE_S = 30      # ...over at least this much time (3 gains in 4 s would read 20x too high)
EARLY_S = 300          # marked early ("~") until this much active time


class ExpRate:
    def __init__(self):
        self.active = 0.0          # seconds of training (breaks left out)
        self.exp = 0               # EXP counted in the rate (kills + shares)
        self.party_exp = 0         # ...of which shares while in a party
        self.gains = 0
        self.first_t = self.last_t = None
        self.lump_exp = 0          # quest turn-ins / lumps: not in the rate
        self.lumps = 0

    def add(self, t, d, kind, party=False):
        """One EXP rise of d at time t. kind: 'kill' (your mobs), 'share' (EXP no mix of mobs
        explains: party shares, damage splits), 'lump' (quest turn-in and the like)."""
        if d <= 0:
            return
        if kind == "lump":
            self.lump_exp += d
            self.lumps += 1
            return
        if self.last_t is not None and 0 < t - self.last_t <= BREAK_S:
            self.active += t - self.last_t
        if self.first_t is None:
            self.first_t = t
        self.last_t = t
        self.gains += 1
        self.exp += d
        if kind == "share" and party:
            self.party_exp += d

    def active_s(self, now):
        """Training time so far, counting the time since the last gain while it's under a break."""
        a = self.active
        if self.last_t is not None and 0 < now - self.last_t <= BREAK_S:
            a += now - self.last_t
        return a

    def rate(self, now):
        """EXP per hour, or None before MIN_GAINS gains."""
        if self.gains < MIN_GAINS:
            return None
        return self.exp / max(self.active_s(now), MIN_ACTIVE_S) * 3600

    def party_rate(self, now):
        if self.gains < MIN_GAINS or not self.party_exp:
            return None
        return self.party_exp / max(self.active_s(now), MIN_ACTIVE_S) * 3600

    def with_breaks(self, now):
        """The same EXP over all the time since the first gain (breaks included)."""
        if self.gains < MIN_GAINS or self.first_t is None:
            return None
        return self.exp / max(now - self.first_t, MIN_ACTIVE_S) * 3600

    def early(self, now):
        return self.active_s(now) < EARLY_S

    def summary(self, now):
        r = self.rate(now)
        span = (now - self.first_t) if self.first_t is not None else 0
        a = self.active_s(now)
        return {"exp_hr": round(r) if r else None,
                "party_hr": round(self.party_rate(now)) if self.party_rate(now) else None,
                "with_breaks_hr": round(self.with_breaks(now)) if self.with_breaks(now) else None,
                "early": bool(r) and self.early(now),
                "gains": self.gains, "exp": self.exp, "party_exp": self.party_exp,
                "active_min": round(a / 60, 1), "break_min": round(max(0.0, span - a) / 60, 1),
                "lump_exp": self.lump_exp, "lumps": self.lumps}
