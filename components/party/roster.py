"""roster.py - the sticky party roster: who is in the party, kept across looks.

The party box is read about once a second (engine/party.py). On a map change it fades, vanishes for
about 5 s (measured 2026-11-07: 4.5-5.1 s black frames) or shows grey text, so a roster published straight
from the last look jumps (rows 4 -> 0 -> 4), forgets the names and re-reads them from faded text.
The roster keeps each member between looks and only lets go of evidence slowly:

  * Row count: a different count (0 = box gone) is accepted only after it has persisted for
    `grace(stable_s)` seconds: 6 s for a party seen under a minute (just above one map-change blip),
    growing to 20 s for one that held 10+ minutes. Until then the roster is *held* (rows keep their
    last numbers, `held: True`). Only an accepted new count calls `reset()` (the engine forgets its
    row names); members whose name survives keep their first_seen / looks.
  * Names: an established name is replaced only when the new reading repeats for `name_need(age)`
    looks (2 for a fresh name, +1 per 15 s the old one held, at most 8). A reading that is not one of
    the known names (raw OCR) never replaces a name that snapped; it can replace an unsnapped
    one only after twice as many looks and only while the row is read black (on your map).
    A partner's exact name from the relay fills the one row nobody could name.
  * on_map: a change needs 2 looks in a row, so one dim frame does not flip "on my map".
  * Numbers: a member who is on the relay right now (`mates`, from party.json, fresh) has
    `src: "relay"` and the HP % comes from their exact HP; everyone else is `src: "screen"` (box HP %).

No engine or other component imports: the naming hook is passed in. Published rows:
{name, me, on_map, hp, leader, src, held, since, looks}.
"""
import time


GRACE_MIN_S = 6.0          # party seen < RAMP_FROM_S: just above one map-change blip (5.1 s measured)
GRACE_MAX_S = 20.0         # party stable >= RAMP_TO_S
RAMP_FROM_S, RAMP_TO_S = 60.0, 600.0
FLICKER_FACTOR = 3.0       # a count that keeps changing is taken after this many graces anyway
ON_MAP_LOOKS = 2
NAME_NEED_MIN, NAME_NEED_MAX, NAME_NEED_PER_S = 2, 8, 15.0
ESTABLISHED_LOOKS = 5


def grace(stable_s):
    """Seconds a different row count has to persist before it is believed, for a party that has
    been stable `stable_s` seconds."""
    f = (stable_s - RAMP_FROM_S) / (RAMP_TO_S - RAMP_FROM_S)
    f = min(1.0, max(0.0, f))
    return GRACE_MIN_S + (GRACE_MAX_S - GRACE_MIN_S) * f


def name_need(held_s):
    """Looks a different name must repeat to replace one that has held `held_s` seconds."""
    return int(min(NAME_NEED_MAX, NAME_NEED_MIN + max(0.0, held_s) / NAME_NEED_PER_S))


class Roster:
    def __init__(self, name_of=None, reset=None):
        self.name_of = name_of          # (i, mask, known, t) -> text | None  (hub.source.party_name)
        self.reset = reset              # () -> None  (hub.source.party_reset)
        self.m = []                     # members, top row first
        self.pend = None                # [count, first_t, looks] of a different row count
        self.stable_since = None        # when the current count was accepted
        self.last_real = None           # last look whose count matched the roster
        self.held = False
        self.resets = 0

    # -- one look
    def update(self, t, rows, known=(), mates=None, me=None):
        """rows: engine rows [{mask, on_map, hp, leader}]; known: names to snap to; mates: {name:
        {hp [cur, max]|None, ...}} of relay members fresh now; me: your name. Returns published rows."""
        known = set(n for n in known if n)
        mates = mates or {}
        n = len(rows)
        if not self.m:
            if n:
                self._accept(t, rows, known)
        elif n == len(self.m):
            self.pend, self.held, self.last_real = None, False, t
            for i, (m, r) in enumerate(zip(self.m, rows)):
                self._look(m, r, self._read(i, r, known, t), known, t)
        else:
            if self.pend is None or self.pend[0] != n:
                self.pend = [n, t, 0]
            self.pend[2] += 1
            g = grace(self.last_real - self.stable_since)
            if t - self.pend[1] >= g or t - self.last_real >= FLICKER_FACTOR * g:
                self._accept(t, rows, known)
            else:
                self.held = True
        self._fill_blank(known, mates, t)
        return self.publish(known, mates, me)

    def _read(self, i, r, known, t):
        if not self.name_of or r.get("mask") is None:
            return None
        return self.name_of(i, r.get("mask"), sorted(known), t)

    def _accept(self, t, rows, known):
        """A new row count is believed: rebuild the roster (the engine forgets its row names)."""
        old = {m["name"]: m for m in self.m if m["name"]}
        if self.reset:
            self.reset()
        self.resets += 1
        self.m = []
        for i, r in enumerate(rows):
            nm = self._read(i, r, known, t)
            prev = old.get(nm)
            m = {"name": nm, "snapped": bool(nm and nm in known), "name_t": t,
                 "first_seen": prev["first_seen"] if prev else t, "looks": prev["looks"] if prev else 0,
                 "last_seen": t, "on_map": r["on_map"], "hp": r["hp"], "leader": r["leader"],
                 "cand": None, "om_cand": None}
            if prev:
                m["name_t"] = prev["name_t"]
            self.m.append(m)
            m["looks"] += 1
        self.pend, self.held = None, False
        self.stable_since = self.last_real = t

    def _look(self, m, r, raw, known, t):
        m["last_seen"] = t
        m["looks"] += 1
        m["leader"] = r["leader"]
        if r["on_map"] != m["on_map"]:                   # needs ON_MAP_LOOKS looks in a row
            c = m["om_cand"]
            m["om_cand"] = c = (r["on_map"], (c[1] + 1) if c and c[0] == r["on_map"] else 1)
            if c[1] >= ON_MAP_LOOKS:
                m["on_map"], m["om_cand"] = r["on_map"], None
        else:
            m["om_cand"] = None
        if m["on_map"]:
            m["hp"] = r["hp"] if r["on_map"] else m["hp"]
        else:
            m["hp"] = None
        if not raw:
            return
        snapped = raw in known
        if m["name"] is None:
            m.update(name=raw, snapped=snapped, name_t=t, cand=None)
        elif raw == m["name"]:
            m["cand"] = None
            m["snapped"] = m["snapped"] or snapped
        else:
            c = m["cand"]
            m["cand"] = c = [raw, (c[1] + 1) if c and c[0] == raw else 1]
            need = name_need(t - m["name_t"])
            if m["snapped"] and not snapped:
                return                                  # raw OCR never replaces a snapped name
            if not snapped and m["looks"] >= ESTABLISHED_LOOKS:
                if not r["on_map"]:
                    return                              # only text read black can unseat an unsnapped name
                need *= 2
            if c[1] >= need:
                m.update(name=raw, snapped=snapped, name_t=t, cand=None)

    def _fill_blank(self, known, mates, t):
        """One member nobody could name + one relay partner on no row: that is them (exact name)."""
        if not self.m:
            return
        on_rows = {m["name"] for m in self.m}
        missing = [n for n in mates if n and n not in on_rows]
        blank = [m for m in self.m if not m["name"] or (m["name"] not in known and not m["snapped"])]
        if len(missing) == 1 and len(blank) == 1:
            blank[0].update(name=missing[0], snapped=True, name_t=t, cand=None)

    # -- the rows other parts read
    def publish(self, known=(), mates=None, me=None):
        mates = mates or {}
        out = []
        for m in self.m:
            mate = mates.get(m["name"]) if m["name"] else None
            hp = m["hp"]
            if mate and mate.get("hp") and mate["hp"][1]:
                hp = round(100.0 * mate["hp"][0] / mate["hp"][1], 1)
            out.append({"name": m["name"], "me": bool(me and m["name"] == me), "on_map": m["on_map"],
                        "hp": hp, "leader": m["leader"], "src": "relay" if mate else "screen",
                        "held": self.held, "since": round(m["first_seen"], 1), "looks": m["looks"]})
        return out

    def stable_for(self, now=None):
        """Seconds the current roster has held (0 with none)."""
        if not self.m or self.stable_since is None:
            return 0.0
        return max(0.0, (now or time.time()) - self.stable_since)
