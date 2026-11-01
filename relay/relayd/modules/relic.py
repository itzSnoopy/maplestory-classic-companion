"""relic: the room's relic hunt, a slow shared jigsaw. State lives in SQLite (module_state), so it survives restarts.

The app reports ELIGIBLE kills (already weighted by mobs' worth and the level band) in the sync part
`relic: {"kills": x}`; the relay caps them (CAP_PER_MIN per member), applies the together bonus, rolls with the
chance of the NEXT piece on the curve (a U shape, see `curve`) and deals the pieces from a shuffled deck of the
missing ones, so nobody gets duplicates and the puzzle finishes after exactly N drops.

Found pieces lie on a shared table around the board; any room member can move any found, unplaced piece
(`POST relic/move`); dropped within a tolerance of its real slot it snaps and locks. The first move of a piece
wins for MOVE_LOCK_S, another member's move in that window gets 409.

Actions (POST /v2/rooms/<id>/relic/<action>): start (owner / mod; also enables the module in an older room),
stop (owner / mod), move (any active member). Reads (GET .../relic/state): the board.
Sync part: body "relic": {"kills": x} -> reply {active, difficulty, pieces_total, found_count, placed_count,
next_chance_pct, together {n, bonus_pct}, found [piece ids new for me], finished, counted}.
"""
import math
import random
import threading

from .. import clock
from ..errors import ApiError
from . import Module, register

KILLS_PER_HOUR = 550            # the design rate of one person (plan: measured 642/h, nerfed 15 %)
CAP_PER_MIN = 40                # eligible kills per member per minute the relay believes
MAX_KILLS_POST = 1000.0         # a single post bigger than this is cut (a link that was down for hours)
MOVE_LOCK_S = 1.0               # the first move of a piece wins for this long
MOVE_RATE = (8, 2.0)            # at most 8 moves per member in 2 s
TOGETHER = {1: 0.0, 2: 0.05, 3: 0.12}       # bonus by members on the same map and channel; 4+ = 0.20
TOGETHER_MAX = 0.20
ASPECT = 1.6                    # table width / height; positions are normalized 0..1 on both axes
GALLERY_MAX = 20
COMPLETE_SHOW_S = 86400         # "Relic hunt complete" stays on the lobby card this long
MIN_PIECES, MAX_PIECES = 6, 400
MAX_PEOPLE, MAX_HOURS = 50, 2000.0
MAX_BASE = 0.12                 # a base chance above 12 % per kill is not a hunt, it is a gift

# key: (label, pieces, cols, rows, hours, people, R = first piece's chance as a multiple of the base)
DIFFICULTIES = {
    "easy": ("Easy", 12, 4, 3, 0.5, 2, 1.3),
    "medium": ("Medium", 24, 6, 4, 1.0, 4, 1.8),
    "challenging": ("Challenging", 48, 8, 6, 2.0, 6, 2.5),
    "epic": ("Epic", 96, 12, 8, 8.0, 6, 3.5),
    "hardcore": ("Hardcore", 192, 16, 12, 14 * 2.0, 6, 4.5),
}
_R_AT = [(0, 1.3), (1, 1.8), (2, 2.5), (3, 3.5), (4, 4.5)]      # R by log2(pieces / 12), for custom hunts
TAU, EXPO = 0.07, 6


# ---- the curve -------------------------------------------------------------------------------------------
def _shoulder(x):
    """Zero at 0, 0.5 and 1, positive between: the fourth degree of freedom that makes the total come out."""
    u = x * (1.0 - x)
    return u * (1.0 - 4.0 * u)


def _shape(n, b, r, a):
    """The U curve for n pieces, base chance b, first = r*b, middle (index n//2) = 0.85*b, last = 1.15*b, with
    shoulder weight a. Returns the list of chances."""
    xm = (n // 2) / (n - 1)
    s = r * b
    e1, em, q = math.exp(-1.0 / TAU), math.exp(-xm / TAU), xm ** EXPO
    den = 1.0 - em - q + e1 * q
    m = (0.85 * b - s * em - (1.15 * b - s * e1) * q - a * b * _shoulder(xm)) / den
    e = 1.15 * b - (s - m) * e1
    out = []
    for i in range(n):
        x = i / (n - 1)
        out.append(m + (s - m) * math.exp(-x / TAU) + (e - m) * x ** EXPO + a * b * _shoulder(x))
    return out


def curve(pieces, people, hours, r):
    """Per-kill chance of each piece in turn. `b = pieces / (people * hours * 550)` is the flat rate; the curve has
    first = r*b, middle = 0.85*b, last = 1.15*b and sum(1/p) == pieces/b (the same expected kills as flat), solved
    exactly by bisection on the shoulder weight."""
    n = pieces
    kills = people * hours * KILLS_PER_HOUR
    b = n / kills

    def total(a):
        ps = _shape(n, b, r, a)
        return float("inf") if min(ps) <= 0 else sum(1.0 / p for p in ps)

    lo, hi = -3.0, 12.0
    for _ in range(120):
        mid = (lo + hi) / 2
        if total(mid) > kills:
            lo = mid
        else:
            hi = mid
    ps = _shape(n, b, r, lo)
    if min(ps) <= 0 or abs(sum(1.0 / p for p in ps) / kills - 1.0) > 0.005:       # cannot happen for sane input
        c = (sum(1.0 / v for v in ps) / n * b) if min(ps) > 0 else 1.0
        ps = [b * (r + (1.0 - r) * min(1.0, i / max(1, n - 1) * 8)) for i in range(n)] if min(ps) <= 0 else [p * c for p in ps]
    return [min(0.95, p) for p in ps]


def r_for(pieces):
    t = math.log2(max(pieces, 1) / 12.0)
    pts = _R_AT
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
        if t <= t1:
            return r0 + (r1 - r0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def grid_for(pieces):
    """(cols, rows) with cols*rows == pieces, closest to 4:3, none wider than 3:1; None when pieces is a bad number."""
    best = None
    for rows in range(2, int(math.sqrt(pieces)) + 1):
        if pieces % rows == 0:
            cols = pieces // rows
            score = abs(math.log((cols / rows) / (4 / 3)))
            if cols / rows <= 3.0 and (best is None or score < best[0]):
                best = (score, cols, rows)
    return (best[1], best[2]) if best else None


def nearest_grid_pieces(pieces):
    for d in range(0, 60):
        for p in (pieces - d, pieces + d):
            if MIN_PIECES <= p <= MAX_PIECES and grid_for(p):
                return p
    return 12


def board_rect(cols, rows):
    """The board inside the table (normalized coordinates) and the tile size s in table-height units."""
    s = min(0.52 * ASPECT / cols, 0.80 / rows)
    w, h = cols * s / ASPECT, rows * s
    return {"x": round((1 - w) / 2, 5), "y": round((1 - h) / 2, 5), "w": round(w, 5), "h": round(h, 5)}, s


def slot_center(hunt, pid):
    cols, rows = hunt["cols"], hunt["rows"]
    b, _ = board_rect(cols, rows)
    return (b["x"] + (pid % cols + 0.5) * b["w"] / cols, b["y"] + (pid // cols + 0.5) * b["h"] / rows)


def together_bonus(n):
    return TOGETHER_MAX if n >= 4 else TOGETHER.get(max(1, n), 0.0)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@register
class Relic(Module):
    name = "relic"
    persistent = True
    offered = True              # owner_view offers "start" even where the module is not enabled yet
    enable_on = ("start",)      # these actions switch the module on in a room that lacks it (owner / mod only)

    def __init__(self):
        self.lock = threading.RLock()
        self.rng = random.SystemRandom()        # tests put a seeded random.Random here
        self.state = {}                         # room id -> hunt state dict ({} = nothing ever started)
        self.curves = {}                        # (room id, hunt id) -> chances
        self.cap = {}                           # (room id, name) -> [tokens, t]
        self.moves = {}                         # (room id, name) -> [move times]

    # ---- state
    def _load(self, room_id):
        st = self.state.get(room_id)
        if st is None:
            st = self.relay.store.get_module_state(room_id, "relic") if self.relay else None
            st = st if isinstance(st, dict) else {}
            self.state[room_id] = st
        return st

    def _save(self, room_id):
        if self.relay:
            self.relay.store.set_module_state(room_id, "relic", self.state.get(room_id) or {})

    def _hunt(self, room_id):
        """The current hunt (running or just finished) or None."""
        h = self._load(room_id).get("hunt")
        return h if isinstance(h, dict) else None

    def _chances(self, room_id, hunt):
        key = (room_id, hunt["id"])
        c = self.curves.get(key)
        if c is None:
            c = self.curves[key] = curve(hunt["pieces"], hunt["people"], hunt["hours"], hunt["r"])
            for k in [k for k in self.curves if k[0] == room_id and k != key]:
                del self.curves[k]
        return c

    @staticmethod
    def _running(h):
        return bool(h) and h.get("active") and not h.get("finished")

    # ---- sync
    def on_sync(self, room, member, part, now):
        rid, name = room["id"], member["name"]
        together = self._together(rid, name, now)
        with self.lock:
            hunt = self._hunt(rid)
            counted = 0
            found_now = []
            if self._running(hunt) and isinstance(part, dict) and "kills" in part:
                counted, found_now = self._roll(rid, hunt, name, part["kills"], together, now)
            new = hunt.get("new", {}).pop(name, []) if hunt else []
            if found_now or new:
                self._save(rid)
            return self._reply(rid, hunt, together, new, counted, now)

    def _together(self, rid, name, now):
        pres = self.relay.modules.get("presence") if self.relay else None
        rows = pres.live_rows(rid, now) if pres else {}
        me = rows.get(name) or {}
        here, ch = me.get("map"), me.get("channel")
        n = 1
        if here and ch not in (None, ""):
            n = sum(1 for k, r in rows.items() if k != name and r.get("map") == here
                    and str(r.get("channel")) == str(ch)) + 1
        return {"n": n, "bonus_pct": round(together_bonus(n) * 100)}

    def _allowed(self, rid, name, k, now):
        """The member's rate cap as a token bucket: CAP_PER_MIN kills per minute, a burst of one minute."""
        key = (rid, name)
        tok, t = self.cap.get(key, (float(CAP_PER_MIN), now))
        tok = min(float(CAP_PER_MIN), tok + max(0.0, now - t) * CAP_PER_MIN / 60.0)
        take = min(k, tok)
        self.cap[key] = [tok - take, now]
        if len(self.cap) > 20000:
            self.cap.clear()
        return take

    def _roll(self, rid, hunt, name, kills, together, now):
        if not _num(kills) or kills <= 0:
            return 0, []
        k = self._allowed(rid, name, min(float(kills), MAX_KILLS_POST), now)
        acc = hunt.setdefault("acc", {})
        total = acc.get(name, 0.0) + k
        whole = int(total + 1e-9)
        acc[name] = max(0.0, total - whole)
        chances = self._chances(rid, hunt)
        bonus = together_bonus(together["n"])
        found = []
        for _ in range(whole):
            if len(hunt["found"]) >= hunt["pieces"]:
                break
            p = min(1.0, chances[len(hunt["found"])] * (1.0 + bonus))
            if self.rng.random() < p:
                found.append(self._deal(rid, hunt, name, now))
        if found:
            hunt.setdefault("new", {}).setdefault(name, []).extend(found)
        return whole, found

    def _deal(self, rid, hunt, name, now):
        pid = hunt["deck"].pop(0)
        x, y = self._scatter(hunt)
        hunt["found"][str(pid)] = {"by": name, "t": round(now, 1), "x": x, "y": y, "placed": False}
        hunt["order"].append(pid)
        return pid

    def _scatter(self, hunt):
        """A spot on the table beside the board (left or right band) for a new piece."""
        b, s = board_rect(hunt["cols"], hunt["rows"])
        half = s / ASPECT / 2
        hy = s / 2
        left = self.rng.random() < 0.5
        lo_x = half + 0.01
        hi_x = max(lo_x, b["x"] - half - 0.005)
        x = self.rng.uniform(lo_x, hi_x)
        if not left:
            x = 1.0 - x
        y = self.rng.uniform(hy + 0.01, max(hy + 0.01, 1.0 - hy - 0.01))
        return round(x, 4), round(y, 4)

    def _reply(self, rid, hunt, together, new, counted, now):
        if not hunt:
            return {"active": False, "together": together}
        chances = self._chances(rid, hunt)
        nf = len(hunt["found"])
        nxt = chances[nf] if nf < hunt["pieces"] else None
        out = {"active": self._running(hunt), "difficulty": hunt["difficulty"], "pieces_total": hunt["pieces"],
               "found_count": nf, "placed_count": hunt["placed"],
               "next_chance_pct": round(nxt * 100, 3) if nxt is not None else None,
               "together": together, "found": new, "counted": counted}
        if hunt.get("finished"):
            out["finished"] = True
        return out

    # ---- actions and reads
    def actions(self):
        return {"start": self._start, "stop": self._stop, "move": self._move}

    def reads(self):
        return {"state": self._state}

    @staticmethod
    def _host_only(m):
        if m["role"] not in ("owner", "mod"):
            raise ApiError(403, "owner or mod only")

    def _spec(self, d):
        key = d.get("difficulty")
        if key in DIFFICULTIES:
            label, pieces, cols, rows, hours, people, r = DIFFICULTIES[key]
            return key, label, pieces, cols, rows, hours, people, r
        if key != "custom":
            raise ApiError(400, "difficulty must be easy, medium, challenging, epic, hardcore or custom")
        pieces, hours, people = d.get("pieces"), d.get("hours"), d.get("people")
        if isinstance(pieces, bool) or not isinstance(pieces, int) or not MIN_PIECES <= pieces <= MAX_PIECES:
            raise ApiError(400, f"pieces must be a whole number from {MIN_PIECES} to {MAX_PIECES}")
        grid = grid_for(pieces)
        if not grid:
            raise ApiError(400, f"{pieces} pieces do not make a grid; try {nearest_grid_pieces(pieces)}")
        if not _num(hours) or not 0.25 <= hours <= MAX_HOURS:
            raise ApiError(400, f"hours must be from 0.25 to {int(MAX_HOURS)}")
        if isinstance(people, bool) or not isinstance(people, int) or not 1 <= people <= MAX_PEOPLE:
            raise ApiError(400, f"people must be from 1 to {MAX_PEOPLE}")
        r = r_for(pieces)
        if pieces / (people * hours * KILLS_PER_HOUR) * max(r, 1.15) > MAX_BASE:
            raise ApiError(400, "that is too easy: add hours or people, or fewer pieces")
        return "custom", "Custom", pieces, grid[0], grid[1], float(hours), people, r

    def _start(self, room, m, d, now):
        self._host_only(m)
        rid = room["id"]
        key, label, pieces, cols, rows, hours, people, r = self._spec(d)
        with self.lock:
            st = self._load(rid)
            old = st.get("hunt")
            if self._running(old):
                raise ApiError(409, "a relic hunt is already on in this room")
            hid = int(st.get("next_id", 1))
            deck = list(range(pieces))
            self.rng.shuffle(deck)
            st["next_id"] = hid + 1
            st["hunt"] = {"id": hid, "active": True, "difficulty": key, "label": label, "pieces": pieces,
                          "cols": cols, "rows": rows, "hours": hours, "people": people, "r": r,
                          "started_by": m["name"], "started_t": round(now, 1), "deck": deck, "found": {},
                          "order": [], "placed": 0, "acc": {}, "new": {}}
            for k in [k for k in self.cap if k[0] == rid]:
                del self.cap[k]
            self._save(rid)
            return self._board(rid, st["hunt"], m["name"], now)

    def _stop(self, room, m, d, now):
        self._host_only(m)
        rid = room["id"]
        with self.lock:
            hunt = self._hunt(rid)
            if not self._running(hunt):
                raise ApiError(404, "no relic hunt to stop")
            st = self._load(rid)
            st.pop("hunt", None)
            self._save(rid)
            return {"ok": True, "stopped": True}

    def _move(self, room, m, d, now):
        rid, name = room["id"], m["name"]
        pid, x, y = d.get("piece"), d.get("x"), d.get("y")
        if isinstance(pid, bool) or not isinstance(pid, int) or not _num(x) or not _num(y) \
                or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ApiError(400, "piece, x and y are needed (x and y from 0 to 1)")
        with self.lock:
            hunt = self._hunt(rid)
            if not hunt or hunt.get("finished"):
                raise ApiError(404, "no relic hunt is on")
            n, window = MOVE_RATE
            ts = [t for t in self.moves.get((rid, name), []) if now - t < window]
            if len(ts) >= n:
                raise ApiError(429, "slow down")
            self.moves[(rid, name)] = ts + [now]
            if len(self.moves) > 20000:
                self.moves.clear()
            p = hunt["found"].get(str(pid))
            if p is None:
                raise ApiError(404, "that piece has not been found")
            if p["placed"]:
                raise ApiError(409, "that piece is already in place", conflict=False, x=p["x"], y=p["y"])
            last = p.get("mv")
            if last and last[0] != name and now - last[1] < MOVE_LOCK_S:
                raise ApiError(409, f"{last[0]} is moving that piece", conflict=True, x=p["x"], y=p["y"])
            p["mv"] = [name, round(now, 2)]
            cx, cy = slot_center(hunt, pid)
            _, s = board_rect(hunt["cols"], hunt["rows"])
            snapped = math.hypot((x - cx) * ASPECT, y - cy) <= max(0.4 * s, 0.02)
            if snapped:
                p.update(x=round(cx, 4), y=round(cy, 4), placed=True, placed_by=name, placed_t=round(now, 1))
                hunt["placed"] += 1
                if hunt["placed"] >= hunt["pieces"]:
                    self._finish(rid, hunt, name, now)
            else:
                p.update(x=round(x, 4), y=round(y, 4))
            self._save(rid)
            return {"ok": True, "placed": snapped, "x": p["x"], "y": p["y"], "finished": bool(hunt.get("finished"))}

    def _finish(self, rid, hunt, last_by, now):
        by = {}
        for pid in hunt["order"]:
            n = hunt["found"][str(pid)]["by"]
            by[n] = by.get(n, 0) + 1
        top = max(by.values()) if by else 0
        most = next((n for n in (hunt["found"][str(pid)]["by"] for pid in hunt["order"]) if by[n] == top), None)
        hunt["finished"], hunt["active"], hunt["finished_t"] = True, False, round(now, 1)
        hunt["most_found"], hunt["last_placed_by"] = most, last_by
        st = self._load(rid)
        entry = {"id": hunt["id"], "difficulty": hunt["difficulty"], "label": hunt["label"],
                 "pieces": hunt["pieces"], "started_t": hunt["started_t"], "finished_t": hunt["finished_t"],
                 "found_by": by, "most_found": most, "last_placed_by": last_by}
        st["gallery"] = (st.get("gallery") or [])[-(GALLERY_MAX - 1):] + [entry]

    def _board(self, rid, hunt, me, now):
        """The state for GET relic/state (and the start answer)."""
        st = self._load(rid)
        gallery = list(reversed(st.get("gallery") or []))
        if not hunt:
            return {"active": False, "finished": False, "gallery": gallery, "server_t": now}
        b, s = board_rect(hunt["cols"], hunt["rows"])
        chances = self._chances(rid, hunt)
        nf = len(hunt["found"])
        pieces = []
        for pid in range(hunt["pieces"]):
            p = hunt["found"].get(str(pid))
            if p is None:
                pieces.append({"id": pid, "state": "hidden"})
            else:
                pieces.append({"id": pid, "state": "placed" if p["placed"] else "found", "x": p["x"], "y": p["y"],
                               "by": p["by"], "placed_by": p.get("placed_by")})
        finders = {}
        for pid in hunt["order"]:
            n = hunt["found"][str(pid)]["by"]
            finders[n] = finders.get(n, 0) + 1
        nxt = chances[nf] if nf < hunt["pieces"] else None
        out = {"active": self._running(hunt), "finished": bool(hunt.get("finished")), "difficulty": hunt["difficulty"],
               "label": hunt["label"], "pieces_total": hunt["pieces"], "cols": hunt["cols"], "rows": hunt["rows"],
               "aspect": ASPECT, "board": b, "tile": round(s, 5), "found_count": nf, "placed_count": hunt["placed"],
               "next_chance_pct": round(nxt * 100, 3) if nxt is not None else None,
               "together": self._together(rid, me, now), "pieces": pieces, "finders": finders,
               "started_by": hunt["started_by"], "gallery": gallery, "server_t": now}
        if hunt.get("finished"):
            out.update(finished_t=hunt["finished_t"], most_found=hunt["most_found"],
                       last_placed_by=hunt["last_placed_by"])
        return out

    def _state(self, room, m, now):
        with self.lock:
            return self._board(room["id"], self._hunt(room["id"]), m["name"], now)

    # ---- housekeeping / views
    def lobby_activity(self, room):
        with self.lock:
            h = self._hunt(room["id"])
            if not h:
                return None
            if h.get("finished"):
                return "Relic hunt complete" if clock.now() - h["finished_t"] < COMPLETE_SHOW_S else None
            if h.get("active"):
                return f"Relic hunt · {len(h['found'])}/{h['pieces']}"
        return None

    def active_rooms(self):
        with self.lock:
            return [r for r, st in self.state.items() if st.get("hunt")]

    def admin_view(self, room):
        with self.lock:
            h = self._hunt(room["id"])
            return {"hunt": ({"difficulty": h["difficulty"], "found": len(h["found"]), "placed": h["placed"],
                              "pieces": h["pieces"], "finished": bool(h.get("finished"))} if h else None)}

    def owner_view(self, room, now):
        acts = {"relic": {"label": "Relic hunt", "difficulties": [
            {"key": k, "label": v[0], "pieces": v[1], "grid": f"{v[2]}x{v[3]}", "hours": v[4], "people": v[5]}
            for k, v in DIFFICULTIES.items()], "custom": {"min_pieces": MIN_PIECES, "max_pieces": MAX_PIECES,
                                                         "max_people": MAX_PEOPLE, "max_hours": MAX_HOURS}}}
        with self.lock:
            h = self._hunt(room["id"])
            cur = None
            if h:
                chances = self._chances(room["id"], h)
                nf = len(h["found"])
                cur = {"state": "finished" if h.get("finished") else "running", "difficulty": h["difficulty"],
                       "label": h["label"], "pieces": h["pieces"], "found": nf, "placed": h["placed"],
                       "next_chance_pct": round(chances[nf] * 100, 3) if nf < h["pieces"] else None,
                       "started_by": h["started_by"], "server_t": now}
                if h.get("finished"):
                    cur.update(most_found=h["most_found"], last_placed_by=h["last_placed_by"])
        return {"activities": acts, "current": None, "extra": {"relic": cur}}

    def on_member_removed(self, room_id, name):
        with self.lock:
            for d in (self.cap, self.moves):
                d.pop((room_id, name), None)
            h = self._hunt(room_id)
            if h:
                (h.get("acc") or {}).pop(name, None)
                (h.get("new") or {}).pop(name, None)

    def on_room_deleted(self, room_id):
        with self.lock:
            self.state.pop(room_id, None)
            for d in (self.cap, self.moves, self.curves):
                for k in [k for k in d if k[0] == room_id]:
                    del d[k]
