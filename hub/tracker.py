"""
tracker.py - MapleStory Classic World EXP tracker (reads the game window). No AI needed.

Runs as a long-lived service. Data comes from a pluggable *source* (sources/: `wgc` reads the
game window; see base/contract.py for the Observation model) and live text goes to *sinks*
(none built in today). The tracker itself only sees Observations, so
sources can be swapped without touching anything below.

A play session opens when the game is present and EXP moves, and closes when the game has been
gone for a while or you've been AFK (no EXP) too long.

Writes:
  journal.md                     readable log of sessions, level-ups, map changes, deaths
  status.json                    live snapshot (state, level, %, ETA, EXP/hr, map)
  digest.json                    compact long-term summary (see history.py)
  sessions/<ts>/exp_log.csv      every frame; summary.json at session end
  sessions/<ts>/levelups/        full frames around each level-up (pruned after N days)
  screenshots/levelups/          one frame per level-up, kept forever
  history/                       levels.csv, sessions.csv, map_stats.json, exp_table.json,
                                 maps.json, state.json (resume across restarts)

Capture rate follows time-to-level: ~`frames_before_level` frames before each level-up,
between `fast_interval` and `interval` seconds.

Passive only: reads frames of the game window, never the game process or memory,
never sends input.

Run:   python -m hub.tracker         (console, Ctrl+C to stop; composition lives in launcher/compose.py)
       pythonw companion.py --service  (background; logs to tracker_live.log)

The tracker knows no component and no engine module: it emits events on the hub (hub/bus.py,
names in base/contract.py) and components react. Whatever else it needs from outside comes in
through `hub` and the Source interface.
Stop:  create a file named `stop` in this folder (stop_tracker.cmd does that)
"""
from base import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
import csv
import difflib
import json
import math
import os
import re
import statistics
import sys
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

from hub import character
from hub.bus import Hub
from base import perf
from hub import history as H
from gamedata import parse
from hub.history import fmt_secs, read_json, write_json
from base.contract import ABSENT, UNAVAILABLE

from base.paths import DATA, asset
REF_TABLE = asset("gamedata/exp_table_classic.json")       # MeowDB reference (Classic World)


def set_root(root):
    """Where all outputs go (default: this folder). Tests point it at a scratch dir."""
    global ROOT, STATUS, STOP_FILE, LOG_FILE, HISTORY, STATE, EXP_TABLE, LEVELS_CSV, MAPS, LOCK
    ROOT = Path(root)
    STATUS = ROOT / "status.json"
    STOP_FILE = ROOT / "stop"
    LOG_FILE = ROOT / "tracker_live.log"
    HISTORY = ROOT / "history"
    STATE = HISTORY / "state.json"
    EXP_TABLE = HISTORY / "exp_table.json"      # measured from the bar
    LEVELS_CSV = HISTORY / "levels.csv"
    MAPS = HISTORY / "maps.json"                # map names seen, with frame counts
    LOCK = HISTORY / "tracker.lock"
    H.set_root(ROOT)            # components follow through hub.root (Component.attach)


set_root(DATA)

RATE_WINDOW_S = 600       # rolling window for EXP/hr and ETA
RATE_MIN_SPAN_S = 45      # need at least this much history before trusting a rate
DEATH_MAX_PCT = 12        # a death costs at most ~10% of the level; bigger drops are level-ups
NO_DEATH_PENALTY_BELOW = 10   # pre-BB beginners (Lv 1-9) lose no EXP on death
BOX_SETTLE_S = 5          # "You have died." box seen: wait this long for the EXP drop, then log
                          # the death anyway (no EXP lost: Lv 1-9, or the bar was already empty)
BOX_SAME_DEATH_S = 60     # a box and an EXP drop this close together are the same death


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}")


def notify(cfg, title, msg):
    print(f"  >>> {title}: {msg}")
    if not cfg.get("notifications", {}).get("enabled"):
        return
    try:
        from winotify import Notification
        Notification(app_id="Maple Tracker", title=title, msg=msg).show()
    except Exception:
        pass


class MapStats:
    def __init__(self):
        self.seconds = 0.0
        self.exp = 0
        self.pct = 0.0
        self.solo_gains = []
        self.party_gains = []

    def summary(self):
        hrs = self.seconds / 3600
        base = self.solo_gains + [b for b, _ in self.party_gains]
        return {
            "time": fmt_secs(self.seconds),
            "seconds": round(self.seconds),
            "exp_gained": self.exp,
            "pct_gained": round(self.pct, 2),
            "exp_per_hour": round(self.exp / hrs) if hrs > 0.01 else None,
            "pct_per_hour": round(self.pct / hrs, 2) if hrs > 0.01 else None,
            "kills_seen": len(base),
            "median_exp_per_kill": statistics.median(base) if base else None,
            "solo_kills": len(self.solo_gains),
            "party_kills": len(self.party_gains),
        }


class Tracker:
    def __init__(self, cfg, level=None, hub=None):
        self.cfg = cfg
        self.hub = hub
        self.base_iv = cfg.get("interval", 30)
        self.fast_iv = cfg.get("fast_interval", 2)
        self.frames_before = cfg.get("frames_before_level", 15)
        self.active_gap = max(3 * self.base_iv, 120)   # longer gaps between frames = not playing
        self.afk_s = cfg.get("afk_after_s", 300)        # no EXP for this long = AFK, not counted

        self.start = time.time()
        self.level = level
        self.cur = self.pct = None
        self.start_pct = self.start_level = None
        self.need_lo, self.need_hi = 0.0, math.inf
        self.exp_total, self.pct_total, self.exp_lost = 0, 0.0, 0
        self.deaths = self.rejected = 0
        self.last_death_t = None            # last death logged (from the EXP drop or the box)
        self.box_pending = None             # when the "You have died." box appeared, not yet logged
        self.box_only_t = None              # last death logged from the box alone (no EXP drop)
        self.hist = deque()                 # (t, exp_total, pct_total)
        self.rate_source = None             # -> exprate summary of this map visit (hub.ask("exp_rate"))
        self.last_t = None
        self.last_progress_t = None
        self.active_total = 0.0
        self.level_started = None           # epoch when the current level began (if seen)
        self.level_active = 0.0             # active seconds spent in the current level
        self.level_ups = []                 # this session: dicts
        self.events = []                    # drained by the service into journal.md
        self.map = self.map_candidate = None
        self.job = None                     # from obs.job (read rarely by the source)
        self.stint = None                   # current stay on one map
        self.map_counts = read_json(MAPS, {})
        self.maps = {}
        self.prev_chat = []
        self.fired = set()
        self.exp_table = {int(k): v for k, v in read_json(EXP_TABLE, {}).items()}
        self.ref_table = {int(k): v for k, v in read_json(REF_TABLE, {}).items()
                          if not k.startswith("_")}
        self.last_levelup_t = None
        self.warned_table = set()
        self.need_exact = None              # from a source that knows it (API, export...)
        self.forced_level = level
        self._resume()

    # ---------------------------------------------------------------- persistence
    def _resume(self):
        st = read_json(STATE, None)
        self._saved = st
        if st and self.level is None:
            self.level = st.get("level")

    def _adopt_saved(self):
        """Once the first good reading confirms we're still on the saved level, continue it."""
        st, self._saved = self._saved, None
        if not st or st.get("level") != self.level:
            return
        if self.cur is not None and st.get("cur") is not None and self.cur < st["cur"]:
            return                          # EXP went down: died or different character
        self.level_started = st.get("level_started")
        self.level_active = st.get("level_active", 0.0)
        lo, hi = st.get("need_lo"), st.get("need_hi")
        if lo is not None and hi is not None:
            self.need_lo, self.need_hi = lo, hi

    def save_maps(self):
        write_json(MAPS, {k: v for k, v in sorted(self.map_counts.items(), key=lambda x: -x[1])
                          if v >= 3})

    def save_state(self):
        write_json(STATE, {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "level": self.level, "cur": self.cur, "pct": self.pct,
            "level_started": self.level_started, "level_active": round(self.level_active),
            "need_lo": self.need_lo if self.need_hi != math.inf else None,
            "need_hi": self.need_hi if self.need_hi != math.inf else None,
        })

    # ---------------------------------------------------------------- EXP needed estimate
    @property
    def need(self):
        """Source-provided exact value > measured table > reference table (if the bar doesn't
        contradict it) > estimate from the bar."""
        if self.need_exact:
            return self.need_exact
        if self.level in self.exp_table:
            return self.exp_table[self.level]
        est = None if self.need_hi == math.inf else round((self.need_lo + self.need_hi) / 2)
        ref = self.ref_table.get(self.level)
        if ref:
            if est is None or self.need_lo - 1 <= ref <= self.need_hi + 1:
                return ref
            if self.level not in self.warned_table:
                self.warned_table.add(self.level)
                log(f"WARNING: reference table says Lv {self.level} needs {ref}, "
                    f"bar suggests {est}; using the bar")
        return est

    def need_source(self):
        if self.level in self.exp_table:
            return "measured"
        n = self.need
        if n is not None and n == self.ref_table.get(self.level):
            return "reference"
        return "estimate" if n else None

    def _need_update(self, cur, pct):
        """Displayed pct p (2 decimals, rounded or truncated) means true % in [p-0.005, p+0.01)."""
        if cur is None or pct is None or pct < 0.5:
            return
        lo, hi = cur * 100 / (pct + 0.01), cur * 100 / (pct - 0.005)
        nlo, nhi = max(self.need_lo, lo), min(self.need_hi, hi)
        if nlo > nhi:                       # inconsistent with earlier frames: start over
            nlo, nhi = lo, hi
        self.need_lo, self.need_hi = nlo, nhi

    def _check_level_from_table(self, t):
        """If the bar pins EXP needed tightly and it matches exactly one level in the reference
        table (but not the level we think we're on), correct the level. Catches level-ups
        that happened while the tracker was off and the badge digit isn't learned yet."""
        if self.need_hi - self.need_lo > 1.5 or not self.ref_table:
            return
        if self.level is not None and \
                self.need_lo - 1 <= self.ref_table.get(self.level, -9) <= self.need_hi + 1:
            return
        hits = [lv for lv, n in self.ref_table.items() if self.need_lo - 1 <= n <= self.need_hi + 1]
        if len(hits) == 1 and hits[0] != self.level:
            log(f"level corrected {self.level} -> {hits[0]} (EXP needed matches the table)")
            self.level = hits[0]
            self.last_levelup_t = t           # lets the badge glyph be learned now
            self.level_started, self.level_active = None, 0.0

    def _death(self, t, lost, pct_lost, box, hp=None):
        self.deaths += 1
        self.last_death_t, self.box_pending = t, None
        killer = {}                         # components fill it (killers: who probably did it)
        if self.hub is not None:
            self.hub.emit("death_guess", map=self.map, level=self.level, killer=killer)
        self.events.append({"type": "death", "lost": lost, "level": self.level, "map": self.map,
                            "killer": killer or None, "pct_lost": pct_lost,
                            "box": box, "hp": hp})

    def death_box(self, t):
        """The "You have died." box just appeared (sources poll for it, deathbox.py). The EXP
        drop usually shows on the next reading and logs the death; settle_box() logs it without
        one after BOX_SETTLE_S."""
        if self.last_death_t is not None and t - self.last_death_t < BOX_SAME_DEATH_S:
            return                          # the EXP drop already logged this death
        self.box_pending = t

    def settle_box(self, t):
        if self.box_pending is not None and t - self.box_pending >= BOX_SETTLE_S:
            self.box_only_t = t
            self._death(t, 0, 0.0, box=True)

    def consistent(self, cur, pct, need=None, tol=0.3):
        need = need or self.need
        if not need or cur is None or pct is None:
            return None
        return abs(cur * 100 / need - pct) <= tol

    def plausible(self, cur, pct):
        """Is this reading believable as 'same level, normal progress'? Used for auto-learning."""
        if cur is None or pct is None:
            return False
        c = self.consistent(cur, pct, tol=0.2)
        if c is not None:
            return c and (self.cur is None or cur >= self.cur)
        if self.cur is None:
            return False
        return self.cur <= cur and self.pct <= pct <= self.pct + 40

    # ---------------------------------------------------------------- map
    def snap_map(self, name):
        """Snap an OCR'd name to a map seen in >= 3 frames before (fixes one-off garbles)."""
        if self.map_counts.get(name, 0) >= 3:
            return name

        def suffix(n):
            m = re.search(r"\s([IVX]+|\d+)$", n)
            return m.group(1) if m else ""
        known = [k for k, n in self.map_counts.items() if n >= 3]
        for m in difflib.get_close_matches(name, known, n=3, cutoff=0.75):
            # never merge "Hunting Ground I" into "Hunting Ground II"
            if suffix(m) != suffix(name):
                return name
            return m
        return name

    def update_map(self, name, t, sure=False):
        """sure: the source already confirmed the name (two checks agree, mapwatch.py), so a new
        map counts at once instead of after two readings."""
        if not name:
            return
        self.map_counts[name] = self.map_counts.get(name, 0) + 1   # raw reads
        name = self.snap_map(name)
        if name == self.map:
            self.map_candidate = None
        elif sure or name == self.map_candidate or self.map is None:
            old = self.close_stint()
            self.map, self.map_candidate = name, None
            self.stint = {"map": name, "start": t, "active": 0.0, "exp": 0}
            self.events.append({"type": "enter", "map": name, "level": self.level})
            if old and old["active"] >= 30:
                self.events.append({"type": "map", "to": name, "from": old})
        else:
            self.map_candidate = name

    def close_stint(self):
        """Fold the current map stay into all-time per-map stats; returns it."""
        s, self.stint = self.stint, None
        if s:
            H.add_map_stint(s["map"], s["active"], s["exp"], self.level)
        return s

    # ---------------------------------------------------------------- main update
    def hints(self):
        """Context a source may use to read better (see base/contract.py)."""
        return {"need": self.need, "level": self.level, "cur": self.cur, "pct": self.pct,
                "plausible": self.plausible}

    def ctx(self):
        return {"level": self.level, "last_levelup_t": self.last_levelup_t,
                "exp_total": self.exp_total, "forced_level": self.forced_level}

    def step(self, obs):
        """Consume one Observation (status OK).
        Returns (event, d_exp, gains); event is None, 'levelup', 'death' or 'rejected'."""
        t, cur, pct, level_read = obs.t, obs.exp_cur, obs.exp_pct, obs.level
        chat_text = obs.raw.get("chat")
        self.update_map(obs.map, t, obs.map_confirmed)
        if obs.job and obs.job != self.job:
            if self.job:
                self.events.append({"type": "job", "from": self.job, "to": obs.job,
                                    "level": self.level, "map": self.map})
            self.job = obs.job
        stats = self.maps.setdefault(self.map or "unknown", MapStats())
        dt = (t - self.last_t) if self.last_t is not None else 0
        self.last_t = t
        if self.last_progress_t is None:
            self.last_progress_t = t

        # first good frame: trust the badge (you may have leveled while the tracker was off);
        # after that, level OCR is only trusted as "same" or "+1"
        if self.cur is None and level_read is not None:
            self.level = level_read
        if level_read is not None and self.level is not None \
                and level_read not in (self.level, self.level + 1):
            level_read = None
        if self.level is None and level_read is not None:
            self.level = level_read

        event, d_exp, d_pct = None, 0, 0.0
        if cur is None or pct is None:
            event = "rejected"
        elif self.cur is None:
            self.start_pct, self.start_level = pct, self.level
        else:
            # The badge can lag the EXP bar by a frame, so a big EXP drop decides on its own.
            # Decided from level + EXP bar only (user, 2026-10-23: HP rules were too much; the HP
            # reading obs.hp_pct is still logged for later use).
            dropped = pct < self.pct - 0.005 or cur < self.cur
            if dropped and (level_read == (self.level or 0) + 1
                            or self.pct - pct > DEATH_MAX_PCT):
                event = "levelup"
            elif dropped:
                beginner = self.level is not None and self.level < NO_DEATH_PENALTY_BELOW
                # a death can empty the bar: 0 counts when the % reads 0 too (2026-10-23, Lv 13
                # with 6 EXP died to 0 and was rejected)
                if not beginner and (cur > 0 or pct == 0) and self.consistent(cur, pct):
                    event = "death"
                else:
                    event = "rejected"           # small unexplained drop: OCR noise
            elif self.consistent(cur, pct, tol=0.5) is False:
                event = "rejected"               # cur and pct disagree: misread digit

        if event == "rejected":
            self.rejected += 1
            return event, 0, []

        if event == "levelup":
            need_prev = self.need
            self.need_exact = None
            d_exp = ((need_prev - self.cur) if need_prev else 0) + cur
            d_pct = (100 - self.pct) + pct
        elif event == "death":
            self.exp_lost += self.cur - cur
            need = self.need
            if self.box_only_t is not None and t - self.box_only_t < BOX_SAME_DEATH_S:
                log(f"EXP drop -{self.cur - cur} belongs to the death already logged from the box")
            else:
                self._death(t, self.cur - cur,
                            round((self.cur - cur) * 100 / need, 2) if need else round(self.pct - pct, 2),
                            box=self.box_pending is not None, hp=obs.hp_pct)
        elif self.cur is not None:
            d_exp, d_pct = cur - self.cur, pct - self.pct

        # active time: frames close together, and EXP moved within the last afk_s seconds
        if d_exp > 0 or event == "levelup":
            self.last_progress_t = t
        if 0 < dt <= self.active_gap and t - self.last_progress_t <= self.afk_s:
            self.active_total += dt
            self.level_active += dt
            stats.seconds += dt
            if self.stint:
                self.stint["active"] += dt
        if event == "levelup":
            self._on_levelup(t, need_prev)

        self.cur, self.pct = cur, pct
        if obs.exp_needed:
            self.need_exact = obs.exp_needed
        if self._saved is not None:
            self._adopt_saved()
        self._need_update(cur, pct)
        self._check_level_from_table(t)
        self.exp_total += d_exp
        self.pct_total += d_pct
        stats.exp += d_exp
        stats.pct += d_pct
        if self.stint:
            self.stint["exp"] += d_exp
        self.hist.append((t, self.exp_total, self.pct_total))
        while len(self.hist) > 2 and t - self.hist[0][0] > RATE_WINDOW_S:
            self.hist.popleft()

        gains = list(obs.gains) + self._chat(chat_text, stats)
        return event, d_exp, gains

    def afk_for(self, t):
        return t - self.last_progress_t if self.last_progress_t else 0

    def _chat(self, chat_text, stats):
        gains = []
        if chat_text is None:
            return gains
        lines = parse.chat_lines(chat_text)
        for line in parse.new_chat_lines(self.prev_chat, lines):
            g = parse.parse_gain(line)
            if g:
                gains.append(g)
                (stats.party_gains.append(g) if g[1] else stats.solo_gains.append(g[0]))
        self.prev_chat = lines
        return gains

    def _on_levelup(self, t, need_prev):
        old = self.level
        if need_prev and old and old not in self.exp_table and self.need_hi - self.need_lo < 1.5:
            self.exp_table[old] = need_prev
            write_json(EXP_TABLE, {str(k): v for k, v in sorted(self.exp_table.items())})
        rec = {
            "time": datetime.fromtimestamp(t).isoformat(timespec="seconds"),
            "level_reached": (old + 1) if old else None,
            "active_seconds_in_prev_level": round(self.level_active) if self.level_started else None,
            "wall_seconds_in_prev_level": round(t - self.level_started) if self.level_started else None,
            "exp_needed_prev_level": need_prev,
            "map": self.map,
        }
        self.level_ups.append(rec)
        H.append_csv(LEVELS_CSV, rec, list(rec))
        self.level = (old + 1) if old else None
        self.level_started, self.level_active = t, 0.0
        self.last_levelup_t = t
        self.need_lo, self.need_hi = 0.0, math.inf
        self.fired.clear()
        self.events.append({"type": "levelup", **rec})
        took = fmt_secs(rec["active_seconds_in_prev_level"]) \
            if rec["active_seconds_in_prev_level"] else "?"
        notify(self.cfg, "Level up!", f"Lv {self.level}  (last level took {took})")

    # ---------------------------------------------------------------- rates / ETA / interval
    def _window(self):
        if len(self.hist) < 2:
            return None
        (t0, e0, p0), (t1, e1, p1) = self.hist[0], self.hist[-1]
        if t1 - t0 < RATE_MIN_SPAN_S:
            return None
        return t1 - t0, e1 - e0, p1 - p0

    def _visit_rate(self):
        """This map visit's EXP/hr from the quick looks (exprate.py: active time, quest lumps
        and breaks left out, party share apart), or None."""
        if self.rate_source is None:
            return None
        try:
            r = self.rate_source()
        except Exception:
            return None
        return r if r and r.get("exp_hr") else None

    def exp_rate(self):
        r = self._visit_rate()
        if r:
            return r["exp_hr"]
        w = self._window()                   # fallback: the last 10 min of readings
        return w[1] / w[0] * 3600 if w and w[1] > 0 else None

    def pct_rate(self):
        w = self._window()
        return w[2] / w[0] * 3600 if w and w[2] > 0 else None

    def eta_seconds(self):
        if self.cur is None:
            return None
        need, er = self.need, self.exp_rate()
        if need and er:                         # EXP-based: stays right across level-ups
            return max(0, need - self.cur) / er * 3600
        pr = self.pct_rate()
        if pr:
            return (100 - self.pct) / pr * 3600
        return None

    def next_interval(self):
        eta = self.eta_seconds()
        if eta is not None:
            return min(self.base_iv, max(self.fast_iv, eta / self.frames_before))
        if self.pct is not None and self.pct >= 95:
            return min(self.base_iv, 5)
        if len(self.hist) < 2 or self.hist[-1][0] - self.hist[0][0] < RATE_MIN_SPAN_S * 2:
            return min(self.base_iv, 5)           # warming up: learn the rate quickly
        return self.base_iv

    # ---------------------------------------------------------------- outputs
    def status(self, interval=None):
        s = self.maps.get(self.map or "unknown")
        ss = s.summary() if s else {}
        need = self.need
        return {
            "state": "playing",
            "updated": datetime.now().isoformat(timespec="seconds"),
            "level": self.level,
            "pct": self.pct,
            "exp_current": self.cur,
            "exp_needed": need,
            "exp_needed_source": self.need_source(),
            "exp_to_level": (need - self.cur) if need and self.cur is not None else None,
            "eta_to_level": fmt_secs(self.eta_seconds()),
            "exp_per_hour_recent": round(self.exp_rate()) if self.exp_rate() else None,
            "exp_per_hour_party": (self._visit_rate() or {}).get("party_hr"),
            "exp_rate_early": bool((self._visit_rate() or {}).get("early")),
            "pct_per_hour_recent": round(self.pct_rate(), 2) if self.pct_rate() else None,
            "time_in_level_active": fmt_secs(self.level_active) if self.level_started else None,
            "map": self.map,
            "job": self.job,
            "map_exp_per_hour": ss.get("exp_per_hour"),
            "level_ups_this_session": len(self.level_ups),
            "exp_gained_session": self.exp_total,
            "exp_lost_session": self.exp_lost,
            "session_active": fmt_secs(self.active_total),
            "capture_interval_s": round(interval, 1) if interval else None,
        }

    def overlay_text(self):
        eta = self.eta_seconds()
        nxt = f"Lv {self.level + 1}" if self.level else "Next lvl"
        parts = [f"{nxt} in ~{fmt_secs(eta)}" if eta is not None else
                 f"{self.pct:.2f}%" if self.pct is not None else "EXP ?"]
        er = self.exp_rate()
        if er:
            parts.append(f"{er:,.0f} EXP/hr")
        if self.map:
            parts.append(self.map)
        return " | ".join(parts)

    def check_milestones(self):
        n = self.cfg.get("notifications", {})
        if self.pct is not None and self.pct >= n.get("at_pct", 90) and "pct" not in self.fired:
            self.fired.add("pct")
            notify(self.cfg, "Almost there", f"{self.pct:.1f}% to next level")
        eta = self.eta_seconds()
        if eta is not None and eta <= n.get("minutes_left", 10) * 60 and "eta" not in self.fired:
            self.fired.add("eta")
            notify(self.cfg, "Level soon", f"~{fmt_secs(eta)} to next level")

    def summary(self, session):
        hrs = self.active_total / 3600
        return {
            "session": str(session),
            "wall_time": fmt_secs(time.time() - self.start),
            "active_time": fmt_secs(self.active_total),
            "level_start": self.start_level,
            "level_end": self.level,
            "start_pct": self.start_pct,
            "end_pct": self.pct,
            "level_ups": self.level_ups,
            "exp_gained_total": self.exp_total,
            "exp_lost_total": self.exp_lost,
            "deaths": self.deaths,
            "rejected_frames": self.rejected,
            "pct_gained_total": round(self.pct_total, 2),
            "exp_per_active_hour": round(self.exp_total / hrs) if hrs > 0.01 else None,
            "maps": {k: v.summary() for k, v in self.maps.items()},
        }


# -------------------------------------------------------------------- one play session
class Session:
    """Files for one play session. Knows nothing about where observations come from."""

    def __init__(self, cfg, args, journal, source, hub):
        self.cfg, self.args, self.journal, self.source, self.hub = cfg, args, journal, source, hub
        self.dir = ROOT / "sessions" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.csv_f = open(self.dir / "exp_log.csv", "w", newline="", encoding="utf-8")
        self.csv = csv.writer(self.csv_f)
        self.csv.writerow(["time", "event", "map", "level", "exp_current", "exp_needed",
                           "exp_pct", "exp_delta", "exp_gained_total", "eta_s", "interval_s",
                           "source", "raw"])
        self.tr = Tracker(cfg, args.level or cfg.get("level"), hub)
        if args.interval:
            self.tr.base_iv = args.interval
        self.started = datetime.now()
        self.announced = False
        self.last_print = 0
        self.closed = False
        source.begin_session(self.dir, ROOT / "screenshots" / "levelups")
        hub.state.bind(self.tr)
        log(f"session started: {self.dir.name} (source {source.name})")
        hub.emit("session_start", dir=self.dir, source=source.name)

    def process(self, obs):
        tr = self.tr
        self.hub.emit("observation", obs=obs)
        event, d_exp, gains = tr.step(obs)
        tr.settle_box(obs.t)
        self.source.after_step(obs, event, tr.ctx())
        tr.check_milestones()

        # a session only counts once EXP moves (logging in and idling in town isn't play)
        if not self.announced and (tr.exp_total > 0 or tr.level_ups):
            self.announced = True
            self.journal.write(f"Session start: {H.lvl_pct(tr.start_level, tr.start_pct)}"
                               + (f" @ {tr.map}" if tr.map else ""))
        self._drain_events()
        # a random moment now and then (the diary decides when)
        self.hub.emit("moment", level=tr.level, map=tr.map, job=tr.job, active_total=tr.active_total)

        interval = tr.next_interval()
        when = datetime.fromtimestamp(obs.t)
        self.csv.writerow([when.isoformat(timespec="milliseconds"), event or "", tr.map, tr.level,
                           obs.exp_cur, tr.need, obs.exp_pct, d_exp, tr.exp_total,
                           round(tr.eta_seconds()) if tr.eta_seconds() is not None else "",
                           round(interval, 1), obs.source,
                           json.dumps(obs.raw, ensure_ascii=False) if obs.raw else ""])
        self.csv_f.flush()
        write_json(STATUS, {**tr.status(interval), "source": obs.source})
        tr.save_state()
        tr.save_maps()

        if event or obs.t - self.last_print >= 60:   # readable log: events + 1 line/min
            self.last_print = obs.t
            tag = f" [{event}]" if event else ""
            log(f"Lv {tr.level}  {obs.raw.get('exp', obs.exp_cur)}  | {tr.overlay_text()}"
                f"  | next {interval:.0f}s{tag}")
        return interval, event

    def _emit(self, e):
        """One Tracker.events entry -> a hub event (see base/contract.py EVENTS)."""
        tr, data = self.tr, {k: v for k, v in e.items() if k != "type"}
        kind = e["type"]
        if kind == "job":
            self.hub.emit("job_change", old=data.pop("from"), new=data.pop("to"), job=tr.job, **data)
        elif kind == "enter":
            self.hub.emit("map_enter", job=tr.job, **data)
        elif kind == "map":
            self.hub.emit("map_change", to=data["to"], from_stint=data["from"])
        else:
            self.hub.emit(kind, job=tr.job, **data)

    def _drain_events(self):
        tr = self.tr
        for e in tr.events:
            self._emit(e)
            if e["type"] == "levelup":
                took = fmt_secs(e["active_seconds_in_prev_level"])                     if e["active_seconds_in_prev_level"] else "?"
                self.journal.write(f"**Level up: Lv {e['level_reached']}** @ {e['map']} "
                                   f"(previous level took {took} of play)")
            elif e["type"] == "job":
                self.journal.write(f"**Job advancement: {e['from']} -> {e['to']}** at Lv {e['level']}"
                                   f" @ {e['map']}")
            elif e["type"] == "death":
                k = e.get("killer") or {}
                who = k.get("label")
                self.journal.write(f"Died at Lv {e['level']} @ {e['map']}: "
                                   + (f"-{e['lost']:,} EXP" if e["lost"] else "no EXP lost")
                                   + (f", killed by {who}" if who else ""))
            elif e["type"] == "map":
                s = e["from"]
                rate = round(s["exp"] / (s["active"] / 3600)) if s["active"] >= 60 else None
                self.journal.write(f"Map: {s['map']} -> {e['to']} (stayed {fmt_secs(s['active'])}"
                                   + (f", {rate:,} EXP/hr" if rate else "") + ")")
        tr.events.clear()

    def close(self, reason):
        if self.closed:
            return
        self.closed = True
        tr = self.tr
        tr.close_stint()
        self._drain_events()
        self.source.end_session()
        self.hub.emit("session_end", reason=reason)
        self.csv_f.close()
        summary = tr.summary(self.dir)
        write_json(self.dir / "summary.json", summary)
        if not self.announced:          # no EXP gained: nothing worth recording
            log(f"session closed ({reason}), no EXP gained; not recorded")
            return
        main_map = max(tr.maps.items(), key=lambda kv: kv[1].seconds)[0] if tr.maps else None
        H.add_session({
            "start": self.started.isoformat(timespec="seconds"),
            "end": datetime.now().isoformat(timespec="seconds"),
            "end_reason": reason, "active_s": round(tr.active_total),
            "level_start": tr.start_level, "pct_start": tr.start_pct,
            "level_end": tr.level, "pct_end": tr.pct,
            "exp_gained": tr.exp_total, "exp_per_active_hour": summary["exp_per_active_hour"],
            "level_ups": len(tr.level_ups), "deaths": tr.deaths, "exp_lost": tr.exp_lost,
            "main_map": main_map, "folder": self.dir.name})
        rate = summary["exp_per_active_hour"]
        self.journal.write(
            f"Session end ({reason}): {fmt_secs(tr.active_total)} played, "
            f"{H.lvl_pct(tr.start_level, tr.start_pct)} -> {H.lvl_pct(tr.level, tr.pct)}, "
            f"+{tr.exp_total:,} EXP" + (f" ({rate:,}/hr)" if rate else "")
            + (f", {len(tr.level_ups)} level-up(s)" if tr.level_ups else "")
            + (f", {tr.deaths} death(s)" if tr.deaths else ""))
        log(f"session closed ({reason})")


# -------------------------------------------------------------------- service loop
def setup_service_logging():
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5_000_000:
        os.replace(LOG_FILE, LOG_FILE.with_suffix(".log.1"))
    f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f


def acquire_lock():
    """One tracker at a time (e.g. the startup copy and a manual run)."""
    import msvcrt
    HISTORY.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a+")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


def write_idle_status(state, reason):
    st = read_json(STATUS, {})
    st.update({"state": state, "idle_reason": reason,
               "updated": datetime.now().isoformat(timespec="seconds")})
    write_json(STATUS, st)


def run(cfg, args, source, sinks=None, hub=None):
    """Service loop. `source`: a Source, or a function that builds one (called once the lock is held,
    so a second tracker never opens the game window). `hub` has the components attached
    (launcher/compose.py builds both); tests inject their own."""
    if getattr(args, "service", False) or sys.stdout is None:
        setup_service_logging()
    lock = acquire_lock()
    if lock is None:
        log("another tracker is already running; exiting")
        return
    if STOP_FILE.exists():          # asked of a tracker that had already gone: not for us
        STOP_FILE.unlink()
        log("removed a stale stop request")
    if callable(source):
        source = source()
    if hub is None:
        hub = Hub(ROOT, log)
    hub.source = source
    sinks = [] if sinks is None else sinks

    svc = cfg.get("service", {})
    absent_close = svc.get("close_after_absent_s", 600)
    afk_close = svc.get("close_after_afk_s", 1800)
    idle_poll = svc.get("idle_poll_s", 10)
    box_cfg = cfg.get("death_box", {})
    box_on = box_cfg.get("enabled", True)

    def box_poll():          # seconds between quick looks: Settings "Screen checks per second"
        return 1.0 / source.checks_per_s() if box_on else 0
    was_dead = False
    digest_every = svc.get("digest_every_s", 300)

    H.cleanup(svc.get("retention_days", 14))
    journal = H.Journal()
    hub.journal_write = journal.write
    session = None
    absent_since = None
    afk_reading = None                # (cur, pct, level) when a session closed for AFK
    last_state = None
    last_digest = 0.0
    log(f"tracker service started (pid {os.getpid()}, source {source.name})")
    perf.start(HISTORY / "perf.csv")          # self-measurement, one row a minute (perf.py)
    # stats / skills / channel from game windows the player opens (panels.py, character.py)
    prof = character.Profile(note=lambda s: (log("character: " + s), journal.write(s)),
                             log=lambda s: log("character: " + s))

    def take_facts():
        try:
            facts = source.take_facts()
            if facts:
                prof.update(facts)
                hub.emit("facts", facts=facts, prof=prof)
        except Exception:
            log("character update failed: " + traceback.format_exc())
    hub.take_facts = take_facts
    # components start their threads / open their files here (the album, the party link, the live feed)
    hub.emit("service_start", maps=list(read_json(MAPS, {})) + list(read_json(HISTORY / "map_stats.json", {})))
    box_since = None                          # when the death box was first seen

    def show(text):
        for s in sinks:
            s.show(text)

    def close_session(reason):
        nonlocal session
        if session:
            session.close(reason)
            session = None
            hub.state.bind(None)
            H.build_digest()

    def went_away(t, state, reason):
        nonlocal absent_since, last_state
        absent_since = absent_since or t
        if last_state != state:
            last_state = state
            log(f"{state}: {reason}")
            write_idle_status(state, reason)
            show("")
        if session and t - absent_since > absent_close:
            close_session(reason)

    try:
        while True:
            if STOP_FILE.exists():
                STOP_FILE.unlink()
                log("stop file found")
                break
            t = time.time()
            perf.tick(last_state, source.checks_per_s())
            sleep_for = idle_poll
            hub.state.playing = last_state == "playing"
            hub.emit("loop")                           # diary: `python diary.py note "..."`, camera button
            try:
                try:
                    with perf.timed("read"):
                        obs = source.read(session.tr.hints() if session else {})
                except StopIteration:               # replay sources end
                    log("source exhausted")
                    break
                take_facts()
                if obs.status == UNAVAILABLE:
                    went_away(t, "waiting_for_source", obs.note or "source unavailable")
                    sleep_for = source.retry_s
                elif obs.status == ABSENT:
                    went_away(t, "idle", obs.note or "game not present")
                else:
                    absent_since, last_state = None, "playing"
                    key = (obs.exp_cur, obs.exp_pct, obs.level)
                    if session is None and afk_reading is not None and key == afk_reading:
                        sleep_for = cfg.get("interval", 30)     # still AFK
                    else:
                        afk_reading = None
                        if session is None:
                            session = Session(cfg, args, journal, source, hub)
                            session.tr.rate_source = lambda: hub.ask("exp_rate")
                        interval, event = session.process(obs)
                        hub.emit("progress", level=session.tr.level, cur=session.tr.cur,
                                 need=session.tr.need)
                        show(session.tr.overlay_text())
                        sleep_for = interval
                        if session.tr.afk_for(obs.t) > afk_close:
                            afk_reading = key
                            close_session(f"AFK {fmt_secs(afk_close)}")
                            write_idle_status("idle", "AFK")
                            last_state = "idle"
                            show("")
                        if event == "levelup" or t - last_digest > digest_every:
                            H.build_digest()
                            last_digest = t
            except Exception:
                log("ERROR in loop:\n" + traceback.format_exc())
                sleep_for = 10
            # wait for the next reading; while playing, look for the "You have died." box about
            # once a second (it can be gone in a second or two if the player clicks Revive)
            end = t + sleep_for
            while (left := end - time.time()) > 0:
                if STOP_FILE.exists():               # stop / update: don't wait up to 30 s for
                    break                            # the next reading (handled at loop top)
                if box_on and session and last_state == "playing":
                    now = time.time()
                    try:
                        with perf.timed("poll"):
                            dead = source.poll(now)
                    except Exception:
                        log("death box poll failed: " + traceback.format_exc())
                        dead = None
                    take_facts()
                    if dead and not was_dead:
                        box_since = now
                    elif dead is False and was_dead and box_since:
                        # on screen at most this long (first seen -> first look without it)
                        perf.sample("death_box_visible_s", now - box_since)
                        box_since = None
                    if dead and not was_dead:
                        log("death box seen")
                        session.tr.death_box(now)
                        end = now                    # read the EXP bar right away
                    if dead is not None:
                        was_dead = dead
                    if getattr(source, "read_now", False):   # poll saw a new map (mapwatch.py)
                        end = now
                    hub.emit("poll", now=now)
                    if hub.take_wake():                      # a component wants a reading (camera button, note)
                        end = now
                    if session.tr.box_pending is not None:   # read again when it settles
                        end = min(end, session.tr.box_pending + BOX_SETTLE_S + 0.1)
                    left = end - time.time()
                    if left <= 0:
                        break
                    # the poll itself may have waited for a frame (wgc min_update_ms): keep ~1/s;
                    # the regen clock's burst looks fit in between (HP/MP strip only)
                    next_poll = min(now + box_poll(), end)
                    wake = getattr(source, "wake_at", lambda: None)()   # level-up picture
                    if wake is not None:
                        next_poll = max(time.time(), min(next_poll, wake))
                    hub.idle(next_poll)
                    time.sleep(max(0.0, next_poll - time.time()))
                else:
                    time.sleep(min(left, 1.0))       # 1 s steps: the stop check above
    except KeyboardInterrupt:
        pass
    finally:
        close_session("tracker stopped")
        write_idle_status("stopped", "tracker not running")
        show("")
        for s in sinks:
            s.close()
        source.close()
        hub.emit("service_stop")
        perf.tick(last_state, source.checks_per_s(), force=True)
        H.build_digest()
        log("tracker service stopped")
        lock.close()


if __name__ == "__main__":
    # dev entry (python -m hub.tracker): the composition (components, source) lives in the launcher.
    # Loaded by name so the hub layer doesn't import upward.
    import importlib
    importlib.import_module("launcher.compose").service_main()
