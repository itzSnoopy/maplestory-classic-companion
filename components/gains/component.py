"""gains component: a ledger of everything the game's message corner reports (EXP, party bonus, mesos, items), checked
against the EXP bar so a double or a missed read shows up.

Every notification line is appended to history/gains.csv (one row per line: EXP with its party bonus, mesos, items,
and any other line the corner showed, as OCR text, so new formats can be found). Notifications and bar rises are
grouped into bursts (no event for QUIET_S closes one); each closed burst is checked in history/gain_check.csv:
the EXP the notifications add up to (base + bonus, white and yellow lines alike: a teammate's share raises the bar
too) against how far the bar rose. Verdicts: ok | bar_more (a line missed or unread, quest EXP, overflow) |
notif_more (a double read, or the bar was not read) | levelup (the bar wrapped, no comparison) | overflow.

Subscribes: exp_gain, meso_gain, item_gain, msg_other (engine/msgcorner.py via the bus), fact (kind "exp": the bar), poll.
Emits / provides: nothing. Writes (data folder): history/gains.csv, history/gain_check.csv (each rotates to .old at 5 MB).
"""
from base import perf
from base.contract import Component

QUIET_S = 4.0                   # no notification or bar rise for this long closes a burst
LOG_MAX = 5_000_000
GAINS_HEAD = "t,map,kind,own,base,bonus,mesos,item,count,tab,text,overflow\n"
CHECK_HEAD = "t0,t1,map,notif_exp,bar_exp,diff,lines,own_lines,verdict\n"


def _q(s):
    return '"' + str(s).replace('"', "'") + '"'


def verdict(notif, bar, overflow, levelup):
    """The check of one burst: see the module docstring."""
    if levelup:
        return "levelup"
    if notif == bar:
        return "ok"
    if overflow:
        return "overflow"
    return "bar_more" if bar > notif else "notif_more"


class Burst:
    """Notifications and bar rises of one stretch of fighting, until it goes quiet."""

    def __init__(self, t, map):
        self.t0 = self.t1 = t
        self.map = map
        self.notif = self.bar = self.lines = self.own = 0
        self.overflow = self.levelup = False

    def touch(self, t):
        self.t0, self.t1 = min(self.t0, t), max(self.t1, t)


class GainsComponent(Component):
    name = "gains"

    def attach(self, hub):
        self.hub = hub
        self.burst = None
        self.cur = None             # last EXP bar value, to turn looks into rises
        self.last_t = 0.0
        hub.on("exp_gain", self.on_exp)
        hub.on("meso_gain", self.on_meso)
        hub.on("item_gain", self.on_item)
        hub.on("msg_other", self.on_other)
        hub.on("fact", self.on_fact)
        hub.on("poll", self.on_poll)
        hub.on("service_stop", self.flush)

    # -- notifications
    def _burst(self, t):
        if self.burst is None:
            self.burst = Burst(t, self.hub.state.map)
        self.burst.touch(t)
        self.last_t = max(self.last_t, t)
        return self.burst

    def on_exp(self, t, base=0, party_bonus=0, own=False, overflow=False, **_):
        with perf.timed("gains"):
            b = self._burst(t)
            b.notif += base + party_bonus
            b.lines += 1
            b.own += 1 if own else 0
            b.overflow = b.overflow or overflow
            self._row(t, "exp", own=int(bool(own)), base=base, bonus=party_bonus, overflow=overflow)

    def on_meso(self, t, mesos=0, overflow=False, **_):
        with perf.timed("gains"):
            self._row(t, "meso", mesos=mesos, overflow=overflow)

    def on_item(self, t, name="", count=1, tab="", overflow=False, **_):
        with perf.timed("gains"):
            self._row(t, "item", item=name, count=count, tab=tab, overflow=overflow)

    def on_other(self, t, text="", overflow=False, **_):
        with perf.timed("gains"):
            self._row(t, "other", text=text, overflow=overflow)

    # -- the EXP bar
    def on_fact(self, t, kind, value, **_):
        if kind != "exp":
            return
        with perf.timed("gains"):
            cur = value.get("cur")
            if cur is None:
                return
            prev, self.cur = self.cur, cur
            if prev is None or cur == prev:
                return
            b = self._burst(t)
            if cur > prev:
                b.bar += cur - prev
            else:
                b.levelup = True                    # the bar wrapped: no honest comparison

    def on_poll(self, now, **_):
        if self.burst is not None and now - self.last_t >= QUIET_S:
            with perf.timed("gains"):
                self.flush()

    def flush(self, **_):
        b, self.burst = self.burst, None
        if b is None or not (b.lines or b.bar):
            return
        v = verdict(b.notif, b.bar, b.overflow, b.levelup)
        self._append("gain_check.csv", CHECK_HEAD,
                     f"{b.t0:.2f},{b.t1:.2f},{_q(b.map or '')},{b.notif},{b.bar},{b.bar - b.notif},{b.lines},{b.own},{v}\n")
        if v not in ("ok", "levelup"):
            self.hub.log(f"gains: burst {b.notif} from notices vs {b.bar} on the bar -> {v} ({b.lines} lines)")

    # -- files
    def _row(self, t, kind, own="", base="", bonus="", mesos="", item="", count="", tab="", text="", overflow=False):
        m = str(self.hub.state.map or "")
        self._append("gains.csv", GAINS_HEAD,
                     f"{t:.2f},{_q(m)},{kind},{own},{base},{bonus},{mesos},{_q(item)},{count},{_q(tab)},{_q(text)},"
                     f"{int(bool(overflow))}\n")

    def _append(self, name, head, line):
        try:
            path = self.hub.root / "history" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX:
                path.replace(path.with_suffix(".csv.old"))
            new = not path.exists()
            with open(path, "a", encoding="utf-8", newline="") as f:
                if new:
                    f.write(head)
                f.write(line)
        except OSError:
            pass
