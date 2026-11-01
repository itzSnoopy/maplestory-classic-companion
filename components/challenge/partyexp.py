"""partyexp.py - party EXP shares for the duel: who is in my party on this map, and the learned party bonus.

Plain state + two small file readers (no bus, no relay). `gamedata/kills.py` holds the model (own kill =
ceil(E*(0.2+0.8*L/sumL)), teammate's = ceil(E*0.8*L/sumL), the party bonus a separate amount b*base);
this module collects the player's own rises while partied and fits b and the rounding mode per eligible party
size (me + members on my map), persisted in history/party_bonus.json:

    {"sizes": {"4": {"b": 0.3, "mode": "ceil", "explained": 31, "n": 40, "t": ts}},
     "samples": {"4": [[map, d, my level, [levels], guessed 0/1]]}}

Until a size has a fit (>= kills.fit_bonus min_explained explained rises) `bonus()` gives (None, True): the base
amounts alone already count my kills (exact), and base + any plausible grid bonus when nothing else fits; the
count is shown as "estimating". If no rule explains the data well the size has no fit (`b` None): never invented.
"""
import threading
import time

from gamedata import kills
from hub import history as H

SAMPLES_MAX = 300           # kept per party size (newest)
REFIT_EVERY = 5             # new samples between fits
SAVE_EVERY = 5
MEMBER_MAX_AGE_S = 10.0     # a party.json member / party box row older than this is not "on my map"
PATH = ("history", "party_bonus.json")


class Levels(list):
    """Levels of the other members on my map; `guessed` = how many are stand-ins (my own level) for members
    whose level is unknown. kills.party_kills widens its base amounts by +-1 when guessed > 0."""
    guessed = 0


class PartyBonus:
    def __init__(self, root):
        self.path = root.joinpath(*PATH)
        self.lock = threading.Lock()
        d = H.read_json(self.path, {})
        self.sizes = d.get("sizes") if isinstance(d.get("sizes"), dict) else {}
        self.samples = d.get("samples") if isinstance(d.get("samples"), dict) else {}
        self.since_fit = {}
        self.since_save = 0
        self.fitting = False

    def learned(self):
        """{size: kills.BonusFit} of the fitted sizes (entries from the older B-multiplier model are ignored)."""
        with self.lock:
            return {int(k): kills.BonusFit(v["b"], v["mode"]) for k, v in self.sizes.items()
                    if isinstance(v, dict) and v.get("b") and v.get("mode") in kills.MODES}

    def bonus(self, size):
        """(BonusFit or None, estimating): the fitted rule for this size, else (None, True)."""
        f = self.learned().get(size)
        return (f, False) if f else (None, True)

    def add(self, size, map_name, d, me_level, levels):
        with self.lock:
            lst = self.samples.setdefault(str(size), [])
            lst.append([map_name, int(d), int(me_level), [int(x) for x in levels],
                        1 if getattr(levels, "guessed", 0) else 0])
            del lst[:-SAMPLES_MAX]
            k = str(size)
            self.since_fit[k] = self.since_fit.get(k, 0) + 1
            self.since_save += 1
            due = self.since_fit[k] >= REFIT_EVERY and not self.fitting
            if due:
                self.fitting = True
                self.since_fit[k] = 0
        if due:
            threading.Thread(target=self._fit, args=(str(size),), daemon=True, name="partyfit").start()
        elif self.since_save >= SAVE_EVERY:
            self.save()

    def _fit(self, k):
        try:
            with self.lock:
                rises = [tuple(r) for r in self.samples.get(k, []) if len(r) >= 4]
            res = kills.fit_bonus(rises)
            with self.lock:
                if res:
                    self.sizes[k] = {**res, "t": round(time.time(), 1)}
                else:                                  # no rule explains the data: say so, keep the samples
                    self.sizes[k] = {"b": None, "n": len(rises), "t": round(time.time(), 1)}
        finally:
            self.fitting = False
            self.save()

    def save(self):
        with self.lock:
            data = {"sizes": self.sizes, "samples": self.samples}
            self.since_save = 0
        try:
            H.write_json(self.path, data, indent=None)
        except OSError:
            pass


def party_levels(root, my_level, map_name, now=None, rows=None):
    """Levels of the OTHER party members on `map_name` right now, or None when I am not in a party with anybody there.

    Sources: party.json members (relay presence: level, map, age_s; named) and the party box rows in live.json
    (`party.rows`: name, on_map; it also sees party members who are not in the room, with no level: they get
    my own level as a stand-in: the result's `.guessed` counts them). Rows older than MEMBER_MAX_AGE_S are not used."""
    now = now or time.time()
    mates = []
    pj = H.read_json(root / "party.json", {})
    lag = max(0.0, now - (pj.get("t") or now))          # age_s was true when the file was written
    for m in (pj.get("members") or []):
        if (m.get("map") == map_name and m.get("level") and (m.get("age_s") or 0) + lag <= MEMBER_MAX_AGE_S
                and m.get("state", "playing") == "playing"):
            mates.append((m.get("name"), int(m["level"])))
    names = {n for n, _ in mates}
    extra = 0
    box = rows if rows is not None else (H.read_json(root / "live.json", {}).get("party") or {})
    if box.get("t") and now - box["t"] <= MEMBER_MAX_AGE_S:
        for r in box.get("rows") or []:
            if r.get("on_map") and not r.get("me") and r.get("name") not in names:
                extra += 1
    out = Levels([lv for _, lv in mates] + [my_level] * extra)
    out.guessed = extra
    return out or None
