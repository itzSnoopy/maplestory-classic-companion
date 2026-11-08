"""skillbar_ui.py - a movable hotbar-style strip over the game (FF14-like), fed by live.json.

Magic Guard's slot shows what it covers instead (right-click, skillbar.guard_show): effective HP,
damage MP can still absorb, or the MP the last hit took.
Skill corner number (right-click, helper_settings skillbar.show): casts left (default), MP per
cast, or mesos per cast (feed.py: the MP at what your MP potion costs per point, training's
mp_price via hub.ask, plus items like Magic Rock at your own price, plus mesos like Shadow Meso).

Party skills (partyfx.py): after a wider gap and a thin divider, one slot per known party skill whose
caster class is in party.json (today Heal): green pulse + "+amount" when it lands on me.

Slots, left to right:
  MP regen   tick size ("+6"), seconds to the next tick, and a dark sweep that empties as the
             tick comes (meter.Meter measures it from the status bar; nothing is guessed)
  HP regen   the same for HP
  skills     every skill with an MP cost at level >= 1: its icon (cut from your own Skill window,
             templates/skill_icons/) and how many casts your current MP allows

Lives in the helper process (helper_ui.App creates it when Settings > General > "Skill bar" is
on). Drag it anywhere (e.g. the top of the screen). Lock makes it click-through so it never takes
a game click; unlock from the tray menu. Hidden while live.json is stale (not playing).
Position/lock: helper_settings.json "skillbar". Removed skills, corner and Magic Guard number are per
character (skillbar.chars[live.json "who"]; right-click a skill: "Remove ... from the bar"; "Add back"
lists the removed ones). Draws at ~10 frames a second, reads live.json
once a second.

  python -m components.skillbar.skillbar_ui --demo [--at=X,Y]     fake data, for trying it out
"""
import json
import math
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from base.paths import DATA
from hub.ui.overlay import KEY, font as _font, text as _text      # what the overlay windows share
from . import partyfx

try:
    from . import skillicons
except Exception:               # never let a missing module break the bar
    skillicons = None

HEAL_ID = "2301001"         # Heal: its slot shows the average heal under it (feed.py "heal")
NIMBLE_ID = "0001002"       # Nimble Feet: while on, a sweep + "+speed" with a boot + a pulse each second; then the cooldown
NIMBLE_PULSE_S = 0.6        # its once-a-second flash
PULSE_SPEED = (120, 230, 140)
SWEEP_CD = (0, 0, 0, 140)                   # sweep of a cooldown: dark
SWEEP_TICK = (242, 182, 50, 120)            # sweep to the next tick / while a buff runs: gold (player, 2026-11-07)               # Nimble Feet's flash and caption colour
RECOVERY_ID = "0001001"     # Recovery: a sweep + red flash per HP tick and "+N" under it while it heals, then the cooldown (feed.py "recovery")
EATER_IDS = ("2100000", "2200000", "2300000")   # MP Eater: last absorb under the slot (feed.py "eater")
GUARD_ID = "2001000"        # Magic Guard: its slot shows what it covers (feed.py "guard"), not casts

LIVE = DATA / "live.json"
CASTS = DATA / "live_casts.json"     # casts per skill, written the moment a cast is booked (feed.py)
CASTS_EVERY_MS = 100
PARTY = DATA / "party.json"          # the party link's file (others only)
STATUS = DATA / "status.json"        # my map
GROUP_GAP = 3                        # gap between my skills and the party's skills, in GAPs
PULSE_GET = (90, 220, 120)           # a party skill landed on me: green
RECEIVED_S = 15.0                    # the last amount stays under a party Heal this long
CREDIT_COLOR = (120, 230, 150, 255)  # "+total" my Heal did on others (own Heal slot): green, apart from the red "~avg"
CREDIT_MENU_CASTS = 3                # casts listed in the own Heal slot's right-click menu
SLOT, GAP, PAD = 44, 4, 4       # px at 96 dpi
PULSE_S = 0.9                   # an attack's slot flashes this long after a cast is seen (player, 2026-10-31)
PULSE_MP, PULSE_HP = (80, 160, 255), (255, 80, 80)   # what the cast cost: MP blue, HP red, both = two rings
STALE_S = 6.0
FPS = 10


# -- caption marks: simple shapes, one small function each (swap for artist art later) ---------
def _mark_clock(d, x, y, m, color):
    w = max(1, m // 8)
    d.ellipse((x, y, x + m - 1, y + m - 1), outline=color, width=w)
    c = x + (m - 1) / 2, y + (m - 1) / 2
    d.line((c[0], c[1], c[0], y + m * 0.2), fill=color, width=w)             # minute hand, up
    d.line((c[0], c[1], x + m * 0.76, c[1] + m * 0.1), fill=color, width=w)  # hour hand, right


def _mark_shield(d, x, y, m, color):
    r = m - 1
    d.polygon([(x, y), (x + r, y), (x + r, y + r * 0.5), (x + r / 2, y + r), (x, y + r * 0.5)],
              fill=color)


def _mark_cross(d, x, y, m, color):
    t = max(2, m // 3)
    a, b = (m - t) // 2, (m - t) // 2 + t - 1
    d.rectangle((x + a, y, x + b, y + m - 1), fill=color)
    d.rectangle((x, y + a, x + m - 1, y + b), fill=color)


def _mark_users(size, color=(255, 255, 255)):
    """Three person silhouettes (big one in front, two smaller behind): circle head + rounded body.
    Returns an RGBA image size x size. Swap for artist art later."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = tuple(color[:3]) + (255,)

    def person(cx, bottom, w):             # body = rounded top half-capsule, head above it
        h = w * 0.7
        d.rounded_rectangle((cx - w / 2, bottom - h, cx + w / 2, bottom + h), radius=w * 0.45,
                            fill=col)
        r = w * 0.3
        d.ellipse((cx - r, bottom - h - r * 2.3, cx + r, bottom - h - r * 0.3), fill=col)
    bottom = size                           # bodies run off the bottom edge (cropped by the image)
    person(size * 0.2, bottom - size * 0.12, size * 0.3)
    person(size * 0.8, bottom - size * 0.12, size * 0.3)
    person(size * 0.5, bottom, size * 0.42)
    return im


BOOT_PIXELS = (                 # Nimble Feet's mark: a boot (cuff, shaft, heel, arch, toe right) + speed lines
    "..XXXXXXX...",
    "..XXXXXXX...",
    "...XXXXX....",
    "XX.XXXXX....",
    "...XXXXX....",
    "...XXXXX....",
    "XX.XXXXXX...",
    "...XXXXXXXX.",
    "XX.XXXXXXXXX",
    "...XXXXXXXXX",
    "...XXXXXXXXX",
    "...XXX..XXXX",
)


def _mark_boot(d, x, y, m, color):
    """Pixel-art boot with speed lines, 1.5 m square (taller than the other marks: at 8 px a boot
    read as an "L", player 2026-11-07), scaled without smoothing so it stays crisp."""
    col = tuple(color[:3]) + (255,)
    rows = BOOT_PIXELS
    im = Image.new("RGBA", (len(rows[0]), len(rows)), (0, 0, 0, 0))
    im.putdata([col if c == "X" else (0, 0, 0, 0) for r in rows for c in r])
    n = round(m * 1.5)
    d._image.alpha_composite(im.resize((n, n), Image.NEAREST), (int(x), int(y - (n - m) / 2)))


_mark_boot.wide = 1.5           # marks are m x m unless they say how much wider they are


MARKS = {"clock": _mark_clock, "shield": _mark_shield, "cross": _mark_cross, "boot": _mark_boot}
# not a caption mark: _mark_users(size, color) is a slot watermark (party skills)

def _recovery_next(rc, now):
    """Recovery's slot state: (phase, seconds left, segment length, ticks done).
    phase "heal": ticks come `first` s after the cast, then every `every` s, `n` in all; each booked
    tick re-syncs the clock (a missed one is assumed on time); left = to the next tick.
    phase "cd": the ticks are over, left = cooldown (`cd` s from the cast), segment = from the last
    tick to its end. phase None: not cast lately."""
    t0, first, every, n = rc.get("t"), rc.get("first") or 5.9, rc.get("every") or 5.1, rc.get("n") or 6
    if t0 is None:
        return None, None, None, 0
    ticks = sorted(x for x in (rc.get("ticks") or []) if x >= t0)
    done = min(len(ticks), n)
    nxt = ticks[-1] + every if ticks else t0 + first
    if done < n and now >= nxt:         # past the expected tick without one booked: assume it came
        k = min(int((now - nxt) // every) + 1, n - done)
        done, nxt = done + k, nxt + k * every
    if done < n:
        return "heal", nxt - now, (first if done == 0 else every), done
    end = t0 + (rc.get("cd") or 0)
    if now >= end:
        return None, None, None, n
    last = ticks[-1] if len(ticks) >= n else nxt - every    # when the last tick came (booked or assumed)
    return "cd", end - now, max(end - min(last, now), 1e-6), n


def _secs(left):
    """Whole seconds for the middle of a slot, also for long cooldowns ('150', not '2:30'; player)."""
    return f"{int(math.ceil(left))}"


def _timer_next(tm, now):
    """Nimble Feet's slot state: (phase, seconds left, segment length). phase "on": the buff runs
    (`dur` s from the cast); "cd": the rest of the cooldown (`cd` s from the cast), segment = from the
    buff's end; None: not cast lately."""
    t0, dur, cd = tm.get("t"), tm.get("dur") or 0, tm.get("cd") or 0
    if t0 is None:
        return None, None, None
    if now < t0 + dur:
        return "on", t0 + dur - now, dur
    if now < t0 + cd:
        return "cd", t0 + cd - now, max(cd - dur, 1e-6)
    return None, None, None


# -- per-character settings: skillbar.chars[fingerprint] = {hidden, show, guard_show} -------------
PER_CHAR = ("hidden", "show", "guard_show")
CHAR_DEFAULTS = {"hidden": [], "show": "casts", "guard_show": "ehp"}   # a new character starts here


def char_cfg(settings, who, key):
    """Value of a per-character key. who None = no character known: the shared top-level key.
    A character without the key (new, or never changed it) gets the default, not the shared value."""
    if not who:
        return settings.get(key, CHAR_DEFAULTS[key])
    mine = (settings.get("chars") or {}).get(who) or {}
    return mine.get(key, CHAR_DEFAULTS[key])


def char_put(settings, who, **kw):
    """Settings changes to save for per-character keys: {chars: {...all characters...}} for a known
    character (the others' entries are kept), the plain keys when who is None."""
    if not who:
        return dict(kw)
    chars = {k: dict(v) for k, v in (settings.get("chars") or {}).items()}
    chars.setdefault(who, {}).update(kw)
    return {"chars": chars}


def main_char(characters_db):
    """Fingerprint of the highest-level character in characters.json's data, or None."""
    best, best_lv = None, None
    chars = (characters_db or {}).get("characters") if isinstance(characters_db, dict) else None
    for fp, c in (chars or {}).items():
        try:
            lv = float(c["stats"]["level"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if best_lv is None or lv > best_lv:
            best, best_lv = fp, lv
    return best


def migrate(settings, characters_db):
    """One time: the old shared hidden/show/guard_show were set on the player's main (the highest
    level character). Returns the new `chars` dict to save, or None when there is nothing to do."""
    if settings.get("chars"):
        return None
    if all(settings.get(k, CHAR_DEFAULTS[k]) == CHAR_DEFAULTS[k] for k in PER_CHAR):
        return None
    main = main_char(characters_db)
    if not main:
        return None
    return {main: {k: settings[k] for k in PER_CHAR if k in settings}}


class SkillBar:
    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None):
        self.root, self.get, self.put, self.demo = root, settings_get, settings_put, demo
        self.k = k
        self.s = int(round(SLOT * k))
        self.live, self.live_t = {}, 0.0
        self.party, self.my_map = {}, None      # party.json, status.json map (read with live.json)
        self.pslots = []                # partyfx slots
        self.party_seen = {}            # party skill id -> last_t already pulsed
        self.heal_casts = []            # when my own Heal cast was seen (cast_n went up), last 20
        self.credit_state, self.credits = {}, []     # partyfx.credit_heals state / its credits
        self.credit_seen = None         # t of the newest credit already pulsed
        self.pulses = {}                # skill id -> when its pulse started (a new cast seen)
        self.icons = {}                 # path -> PIL image (slot size)
        self.photo = None
        self.shown = None
        self.f_big, self.f_small = _font(int(15 * k)), _font(int(11 * k))
        self.w = w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=KEY)
        try:
            w.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.lbl = tk.Label(w, bg=KEY, bd=0)
        self.lbl.pack()
        for ev, fn in (("<ButtonPress-1>", self._drag_start), ("<B1-Motion>", self._drag),
                       ("<ButtonRelease-1>", self._drag_end), ("<Button-3>", self._menu)):
            self.lbl.bind(ev, fn)
        cfg = self.get()
        x, y = at or (cfg.get("x"), cfg.get("y"))
        if x is None or y is None:
            x, y = root.winfo_screenwidth() // 2 - 150, 8           # top-center by default
        self.dev = at is not None
        w.geometry(f"+{int(x)}+{int(y)}")
        w.withdraw()
        self.after_tick = None
        self._apply_lock(cfg.get("locked", False))
        if not demo:
            self._migrate()
        self._read()
        if not demo:
            self._read_casts()
        self._tick()

    # ---------------------------------------------------------------- per-character settings
    def _who(self):
        return (self.live or {}).get("who")

    def cfg(self, key):
        return char_cfg(self.get(), self._who(), key)

    def put_char(self, **kw):
        self.put(**char_put(self.get(), self._who(), **kw))

    def _migrate(self):
        """Old shared hidden/show/guard_show -> the main character's entry (once; skipped when
        characters.json is missing or has no levels, retried at the next start)."""
        try:
            db = json.loads((DATA / "history" / "characters.json").read_text(encoding="utf-8"))
            new = migrate(self.get(), db)
            if new:
                self.put(chars=new)
        except Exception as e:
            print(f"skill bar settings migration skipped: {e}")

    # ---------------------------------------------------------------- data
    def _read(self):
        if self.demo:
            self.live, self.live_t = demo_live(), time.time()
            self._new_casts()
            self._demo_party(self.live_t)
            self._credit(self.live_t)
        else:
            try:
                st = LIVE.stat()
                if st.st_mtime != self.live_t:
                    self.live = json.loads(LIVE.read_text(encoding="utf-8"))
                    self.live_t = st.st_mtime
                    self._new_casts()
            except (OSError, ValueError):
                pass
            self._read_party()
        self.root.after(1000, self._read)

    @staticmethod
    def _json(path):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _read_party(self):
        """party.json + status.json (my map) -> the party skills slots; missing / broken = none."""
        self.party = self._json(PARTY)
        self.my_map = self._json(STATUS).get("map")
        ev = ((self.live or {}).get("usage") or {}).get("recent") or []
        self._set_party(partyfx.party_slots(self.party, ev, time.time(), self.my_map))
        self._credit(time.time())

    def _credit(self, now):
        """My own Heal slot (only when my skills list has Heal): credit the party members' reports to my casts."""
        if not any(sk.get("id") == HEAL_ID for sk in (self.live or {}).get("skills") or []):
            self.credit_state, self.credits = {}, []
            return
        p = self.party if isinstance(self.party, dict) else {}
        ok = bool(p.get("ok")) and now - float(p.get("t") or 0) <= partyfx.STALE_S
        self.credits = partyfx.credit_heals(self.credit_state, (p.get("members") or []) if ok else [],
                                            self.heal_casts, now)
        if self.credits and self.credits[-1]["t"] != self.credit_seen:
            if self.credit_seen is not None:
                self.pulses["heal_credit"] = now        # a new credit: green pulse (the first read only remembers)
            self.credit_seen = self.credits[-1]["t"]

    def _credit_total(self):
        """(total, newest credit time) for the latest cast with credits, or None."""
        if not self.credits:
            return None
        cast = self.credits[-1]["cast_t"]
        return sum(c["amount"] for c in self.credits if c["cast_t"] == cast), self.credits[-1]["t"]

    def _set_party(self, slots):
        """Keep the slots; a newer matching event than the one seen starts the green pulse
        (the first read only remembers)."""
        now = time.time()
        for p in slots:
            t = p["last_t"]
            if p["id"] in self.party_seen and t is not None and t != self.party_seen[p["id"]]:
                self.pulses["party:" + p["id"]] = now
            self.party_seen[p["id"]] = t
        self.pslots = slots

    def _demo_party(self, now):
        """Demo only: a fake Cleric whose Heal lands every 8 s, a fake Assassin whose level 5
        Haste (its real duration) is cast again when it runs out."""
        self.party = {"ok": True, "t": now, "members": [
            {"name": "FakeCleric", "job": "Cleric", "map": "Demo"},
            {"name": "FakeSin", "job": "Assassin", "map": "Demo"},
            # a healed player: its seq rises ~1.5 s after each of the demo's own Heal casts (every 4 s)
            {"name": "FakeBob", "job": "Fighter", "map": "Demo",
             "healed": [max(0, int((now - 1.5) / 4)), 128 + int((now - 1.5) / 4) * 7 % 9, 130]}]}
        self.my_map = "Demo"
        dur = partyfx.duration("4101001", 5) or 140
        t0 = getattr(self, "demo_t0", None) or now
        self.demo_t0 = t0
        skew = getattr(self, "demo_skew", 0)            # tests move the Haste clock with this
        n = int((now - t0) // 8)
        k = int((now - t0 + skew) // dur)
        ev = [{"t": t0 + n * 8 + 0.0, "meter": "hp", "kind": "heal", "name": "Heal (party?)",
               "amount": 128 + (n * 7) % 11}] if n >= 1 else []
        ev.append({"t": t0 - skew + k * dur, "meter": "hp", "kind": "buff",
                   "name": "Haste (party?)", "amount": 0})
        self._set_party(partyfx.party_slots(self.party, ev, now, "Demo",
                                            {**partyfx.PARTY_SKILLS, **partyfx.DEMO_BUFF}))

    def _new_casts(self, counts=None):
        """An attack whose cast count went up since the last read starts a pulse (the first read
        only remembers the counts). counts: from live_casts.json (fast), else live.json's skills."""
        now = time.time()
        skills = {sk.get("id"): sk for sk in (self.live or {}).get("skills") or []}
        if counts is None:
            counts = {sid: sk.get("cast_n") or 0 for sid, sk in skills.items()}
        seen = getattr(self, "cast_seen", None)
        if seen is not None:
            for sid, n in counts.items():
                sk = skills.get(sid)
                if sid == HEAL_ID and n > seen.get(sid, n):
                    self.heal_casts = (self.heal_casts + [now])[-20:]
                if sk and (sk.get("kind") == "attack" or sk.get("attacks")) and n > seen.get(sid, n):
                    self.pulses[sid] = now
        self.cast_seen = {**(seen or {}), **counts}

    def _read_casts(self):
        """Every CASTS_EVERY_MS: a newer live_casts.json starts pulses at once."""
        try:
            st = CASTS.stat()
            if st.st_mtime != getattr(self, "casts_t", None):
                self.casts_t = st.st_mtime
                d = json.loads(CASTS.read_text(encoding="utf-8"))
                if self.live:
                    self._new_casts({k: int(v) for k, v in (d.get("n") or {}).items()})
        except (OSError, ValueError, TypeError):
            pass
        self.root.after(CASTS_EVERY_MS, self._read_casts)

    def _pulse(self, sk, now):
        """(fraction done, [ring colours]) while the slot's pulse runs, else None."""
        start = self.pulses.get(sk.get("id"))
        if start is None or now - start >= PULSE_S:
            return None
        cols = ([PULSE_HP] if sk.get("hp_cost") else []) + ([PULSE_MP] if sk.get("mp") else [])
        return (now - start) / PULSE_S, cols or [PULSE_MP]

    def _fresh(self):
        return self.live and time.time() - (self.live.get("t") or 0) < STALE_S

    # ---------------------------------------------------------------- drawing
    def _skill_pic(self, sid, cut):
        """Picture path for a skill: my own cut (templates/skill_icons, via feed) first, else the one
        downloaded from MeowDB (skillicons.py; starts the download if allowed), else None (initials)."""
        if cut:
            return cut
        own = DATA / "templates" / "skill_icons" / f"{sid}.png"
        if own.exists():
            return str(own)
        if skillicons is not None and sid:
            try:
                p = skillicons.path(sid)
            except Exception:
                p = None
            return str(p) if p else None
        return None

    def _icon(self, path, fallback, color):
        key = (path, fallback, color)
        if key not in self.icons:
            img = None
            if path:
                try:
                    img = Image.open(path).convert("RGBA")
                    img = img.resize((self.s - 6, self.s - 6), Image.NEAREST)
                except OSError:
                    img = None
            if img is None:                 # plain tile until the icon is cut from the game
                img = Image.new("RGBA", (self.s - 6, self.s - 6), color + (255,))
                if fallback:
                    _text(ImageDraw.Draw(img), (img.width // 2, img.height // 2), fallback,
                          self.f_small)
            self.icons[key] = img
        return self.icons[key]

    def _slot(self, icon, frac_left=None, top=None, mid=None, corner=None, dim=False,
              corner_color=(255, 255, 255, 255), label=None, pulse=None, sweep=None):
        s = self.s
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(6 * self.k), fill=(24, 26, 32, 235),
                            outline=(90, 96, 110, 255))
        im.alpha_composite(icon, (3, 3))
        if dim:
            im.alpha_composite(Image.new("RGBA", icon.size, (0, 0, 0, 150)), (3, 3))
        if frac_left is not None and frac_left > 0:
            # the dark sweep: what's left until the tick, emptying clockwise from 12 o'clock
            ov = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            r = s * 0.75
            ImageDraw.Draw(ov).pieslice((s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r),
                                        -90 + 360 * (1 - frac_left), 270, fill=sweep or SWEEP_CD)
            mask = Image.new("L", (s, s), 0)
            ImageDraw.Draw(mask).rounded_rectangle((3, 3, s - 4, s - 4), radius=int(5 * self.k),
                                                   fill=255)
            ov.putalpha(Image.composite(ov.getchannel("A"), Image.new("L", (s, s), 0), mask))
            im.alpha_composite(ov)
        d = ImageDraw.Draw(im)
        if top:
            _text(d, (int(4 * self.k), int(3 * self.k)), top, self.f_small, anchor="lt")
        if mid:
            _text(d, (s // 2, s // 2 + int(2 * self.k)), mid, self.f_big)
        if label:
            _text(d, (int(4 * self.k), s - int(2 * self.k)), label, self.f_small, anchor="lb",
                  fill=(200, 210, 230, 255))
        if corner is not None:
            _text(d, (s - int(3 * self.k), s - int(2 * self.k)), corner, self.f_small,
                  fill=corner_color, anchor="rb")
        if pulse:
            # a flash: the whole slot tinted in the cost colour, fading out (ease-out), plus thick
            # rings that cool to the border colour (the window has no half-transparency, so the
            # fade is in the colour); HP + MP cost: red outside, blue inside, tint in between
            frac, cols = pulse
            k = (1 - frac) ** 1.5
            tint = tuple(sum(c[j] for c in cols) // len(cols) for j in range(3))
            flash = Image.new("RGBA", (s, s), tint + (255,))
            mask = Image.new("L", (s, s), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(6 * self.k),
                                                   fill=int(150 * k))
            im.paste(Image.composite(flash, im, mask), (0, 0), im.getchannel("A"))
            d = ImageDraw.Draw(im)
            base, w = (90, 96, 110), max(4, int(4 * self.k))
            for i, col in enumerate(cols):
                c = tuple(int(base[j] + (min(255, col[j] + 60) - base[j]) * k) for j in range(3)) + (255,)
                o = i * w
                d.rounded_rectangle((o, o, s - 1 - o, s - 1 - o), radius=int(6 * self.k),
                                    outline=c, width=w)
        return im

    def _regen_slot(self, m, icon, now, label):
        if not m or m.get("cur") is None:
            return None
        nxt, per = m.get("next"), m.get("period")
        if m.get("cur") >= (m.get("max") or 0) and m.get("max"):
            return self._slot(icon, mid="full", label=label)
        if not nxt or not per:
            return self._slot(icon, mid="…", label=label)       # still learning the tick
        left = nxt - now
        if left <= 0:               # overdue: what you're doing pushed the tick back; hold at 0
            return self._slot(icon, mid="0", corner=f"{per:g}s", label=label)
        return self._slot(icon, frac_left=min(1.0, left / per), mid=f"{math.ceil(left):.0f}", sweep=SWEEP_TICK,
                          corner=f"{per:g}s", label=label)

    def _caption(self, text, color, mark=None):
        """A small dark pill under a slot, e.g. '+9' under MP (the measured gain per tick).
        mark: None, "clock", "shield", "cross" or "boot": a tiny code-drawn icon right of the text."""
        s, h = self.s, int(16 * self.k)
        im = Image.new("RGBA", (s, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, s - 1, h - 1), radius=int(5 * self.k), fill=(24, 26, 32, 235),
                            outline=(90, 96, 110, 255))
        draw = MARKS.get(mark)
        if draw is None:
            _text(d, (s // 2, h // 2), text, self.f_small, fill=color)
            return im
        m = max(6, round((h - int(6 * self.k)) * 0.8))  # icon size: caption height minus padding, -20 % (player)
        gap = max(2, int(2 * self.k))
        tw = int(d.textlength(text, font=self.f_small))
        mw = round(m * getattr(draw, "wide", 1.0))
        x0 = (s - (tw + gap + mw)) // 2               # text + icon centred together
        _text(d, (x0 + tw // 2, h // 2), text, self.f_small, fill=color)
        draw(d, x0 + tw + gap, (h - m) // 2, m, color)
        return im

    def _party_slot(self, ps, now):
        """(slot, caption) for a party skill: heal = green pulse + last amount; buff = sweep + seconds."""
        initials = "".join(w[0] for w in ps["name"].split()[:2])
        icon = self._icon(self._skill_pic(ps["id"], None), initials, (50, 90, 70))
        icon = icon.copy()                  # watermark: "party" at a glance (40 % white users mark)
        m = _mark_users(int(self.s * 0.65))
        m.putalpha(m.getchannel("A").point(lambda v: v * 4 // 10))
        icon.alpha_composite(m, ((icon.width - m.width) // 2, icon.height - m.height - 1))
        who = ", ".join(n[:7] for n in ps["from"][:2]) + ("+" if len(ps["from"]) > 2 else "")
        cap = self._caption(who, (170, 220, 180, 255))
        pk = self.pulses.get("party:" + ps["id"])
        pulse = None
        if pk is not None and now - pk < PULSE_S:
            pulse = ((now - pk) / PULSE_S, [PULSE_GET])
        if ps["kind"] == "buff":
            left = (ps["ends"] or 0) - now
            if left > 0:
                return (self._slot(icon, frac_left=min(1.0, left / (ps["dur"] or left)),
                                   mid=f"{math.ceil(left)}", pulse=pulse), cap)
            return self._slot(icon, dim=True, pulse=pulse), cap
        t = ps["last_t"]
        ph = (((self.live or {}).get("usage") or {}).get("party_heal")) or {}
        if t is not None and now - t < RECEIVED_S and ps["amount"]:
            cap = self._caption(f"+{ps['amount']}", (255, 140, 140, 255), "cross")
        elif ph.get("avg"):                 # what party Heals did to me on average (uncapped ones)
            cap = self._caption(f"~{ph['avg']}", (255, 140, 140, 255), "cross")
        return self._slot(icon, dim=ps["dim"], pulse=pulse), cap

    def render(self):
        now = time.time()
        L = self.live or {}
        slots = []                  # (slot image, caption image or None)
        ids = []                    # skill id per slot (None = regen), for the right-click menu
        reg = L.get("regen") or {}
        # a regen slot only for a meter the character has a recovery skill for (feed.py REGEN_SKILLS)
        for k, label, color, text_color in (("mp", "MP", (30, 90, 200), (140, 190, 255, 255)),
                                            ("hp", "HP", (190, 40, 40), (255, 140, 140, 255))):
            if k not in reg:
                continue
            m = L.get(k) or {}
            img = self._regen_slot(m, self._icon(reg[k].get("icon"), "", color), now, label)
            if img is not None:
                cap = f"+{m['tick']}" if m.get("tick") else "+?"
                slots.append((img, self._caption(cap, text_color, "clock")))
                ids.append(None)
        hidden = set(self.cfg("hidden") or [])
        for sk in L.get("skills") or []:
            if sk.get("id") in hidden:          # removed from the bar by the player
                continue
            casts = sk.get("casts")
            initials = "".join(w[0] for w in (sk.get("name") or "?").split()[:2])
            icon = self._icon(self._skill_pic(sk.get("id"), sk.get("icon")), initials, (70, 70, 90))
            color = (255, 90, 90, 255) if casts is not None and casts < 5 else (255, 255, 255, 255)
            corner = str(casts) if casts is not None else "?"
            show = self.cfg("show")
            gd = sk.get("guard")
            if gd:                              # Magic Guard: what it covers, not casts
                gs = self.cfg("guard_show")
                v = gd.get(gs if gs in ("ehp", "absorb", "last") else "ehp")
                corner = str(v) if v is not None else "-"
                if gs == "ehp":                 # HP numbers in red, MP numbers in blue (player)
                    color = (255, 140, 140, 255)
                else:
                    color = (140, 140, 150, 255) if gd.get("absorb") == 0 else (140, 190, 255, 255)
                # a toggle buff's number sits under the slot like the regen "+13" (player, 2026-10-31)
                slots.append((self._slot(icon), self._caption(corner, color, "shield")))
                ids.append(sk.get("id"))
                continue
            if sk.get("id") in EATER_IDS:       # MP Eater: the last absorb (it varies by mob), blue cross
                ea = sk.get("eater")
                seen = getattr(self, "eater_seen", {})
                if ea and sk["id"] in seen and ea.get("t") != seen[sk["id"]]:
                    self.pulses[sk["id"]] = now     # a new absorb: pulse like a cast
                self.eater_seen = {**seen, sk["id"]: (ea or {}).get("t")}
                cap = f"+{ea['last']}" if ea else "?"
                left = (ea["t"] + ea["cd"] - now) if (ea and ea.get("t") and ea.get("cd")) else 0
                if left > 0:                    # cooldown: a sweep and the seconds, like the regen slot
                    img = self._slot(icon, frac_left=min(1.0, left / ea["cd"]), mid=f"{math.ceil(left):.0f}",
                                     pulse=self._pulse(sk, now))
                else:
                    img = self._slot(icon, pulse=self._pulse(sk, now))
                slots.append((img,
                              self._caption(cap, (140, 190, 255, 255), "cross")))
                ids.append(sk.get("id"))
                continue
            if sk.get("id") == RECOVERY_ID and sk.get("recovery"):
                rc = sk["recovery"]             # heals in ticks: one sweep per tick + HP per tick, then the cooldown
                phase, left, seg, done = _recovery_next(rc, now)
                last = getattr(self, "rec_done", None)
                if phase == "heal" and last is not None and done > last:
                    self.pulses["recovery_tick"] = now      # a tick (booked or due): red flash
                self.rec_done = done if phase == "heal" else 0
                kw = dict(corner=corner, dim=casts == 0, corner_color=color, pulse=self._pulse(sk, now))
                rp = self.pulses.get("recovery_tick")
                if rp is not None and now - rp < PULSE_S:
                    kw["pulse"] = ((now - rp) / PULSE_S, [PULSE_HP])
                cap = None
                if phase:
                    kw.update(sweep=SWEEP_TICK if phase == "heal" else None, frac_left=min(1.0, left / seg), dim=False,
                              mid=_secs(left))
                if phase == "heal":             # the per-tick heal shows only while it heals
                    cap = self._caption(f"+{rc['tick']}" if rc.get("tick") else "+?",
                                        (255, 140, 140, 255), "cross")
                slots.append((self._slot(icon, **kw), cap))
                ids.append(sk.get("id"))
                continue
            if sk.get("id") == NIMBLE_ID and sk.get("nimble"):
                tm = sk["nimble"]               # speed buff: sweep + "+speed" while on, then the cooldown
                phase, left, seg = _timer_next(tm, now)
                kw = dict(corner=corner, dim=casts == 0, corner_color=color, pulse=self._pulse(sk, now))
                cap = None
                if phase:
                    kw.update(frac_left=min(1.0, left / seg), dim=False, mid=_secs(left),
                              sweep=SWEEP_TICK if phase == "on" else None)
                if phase == "on":
                    beat = (now - tm["t"]) % 1.0        # a flash every second while it runs
                    if beat < NIMBLE_PULSE_S:
                        kw["pulse"] = (beat / NIMBLE_PULSE_S, [PULSE_SPEED])
                    cap = self._caption(f"+{tm['speed']}" if tm.get("speed") else "+?",
                                        PULSE_SPEED + (255,), "boot")
                slots.append((self._slot(icon, **kw), cap))
                ids.append(sk.get("id"))
                continue
            hl = sk.get("heal") if sk.get("id") == HEAL_ID else None
            if sk.get("id") == HEAL_ID:         # Heal: its average rise under the slot (? until one is seen)
                cap = self._caption(f"~{hl['avg']}" if hl else "?", (255, 140, 140, 255), "cross")
                pulse = self._pulse(sk, now)
                ct = self._credit_total()
                if ct and now - ct[1] < RECEIVED_S:     # what my last Heal did on the party: green "+total"
                    cap = self._caption(f"+{ct[0]}", CREDIT_COLOR, "cross")
                    cp = self.pulses.get("heal_credit")
                    if cp is not None and now - cp < PULSE_S:
                        pulse = ((now - cp) / PULSE_S, [PULSE_GET])
                slots.append((self._slot(icon, corner=corner, dim=casts == 0, corner_color=color, pulse=pulse), cap))
                ids.append(sk.get("id"))
                continue
            if show == "mp" and sk.get("mp"):
                corner, color = str(sk["mp"]), (140, 190, 255, 255)
            elif show == "mesos":
                corner, color = self._meso_text(sk), (242, 182, 50, 255)
            slots.append((self._slot(icon, corner=corner, dim=casts == 0, corner_color=color,
                                     pulse=self._pulse(sk, now)), None))
            ids.append(sk.get("id"))
        n_own = len(slots)
        for ps in self.pslots:
            if "party:" + ps["id"] in hidden:
                continue
            slots.append(self._party_slot(ps, now))
            ids.append("party:" + ps["id"])
        self.slot_ids = ids
        if not slots:
            return None
        g, p = int(GAP * self.k), int(PAD * self.k)
        gg = int(GAP * GROUP_GAP * self.k)          # the wider gap between my skills and the party's

        def gap_after(i):
            return gg if (n_own and i == n_own - 1 and i < len(slots) - 1) else g
        self.xs = []
        x = p
        for i in range(len(slots)):
            self.xs.append(x)
            x += self.s + gap_after(i)
        caps = any(c is not None for _, c in slots)
        ch = int(16 * self.k) + int(2 * self.k) if caps else 0
        W = x - gap_after(len(slots) - 1) + p
        H = p * 2 + self.s + ch
        bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for i, (im, cap) in enumerate(slots):
            x = self.xs[i]
            bar.alpha_composite(im, (x, p))
            if cap is not None:
                bar.alpha_composite(cap, (x, p + self.s + int(2 * self.k)))
        if 0 < n_own < len(slots):      # thin divider in the middle of the wide gap
            dx = self.xs[n_own - 1] + self.s + gg // 2
            ImageDraw.Draw(bar).line((dx, p + 4, dx, p + self.s - 4), fill=(90, 96, 110, 255),
                                     width=max(1, int(self.k)))
        # flatten onto the window's transparent key colour (no half-transparent edges to fringe)
        key = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
        out = Image.new("RGB", (W, H), key)
        a = bar.getchannel("A").point(lambda v: 255 if v > 100 else 0)
        out.paste(bar.convert("RGB"), (0, 0), a)
        return out

    def _tick(self):
        try:
            show = bool(self.get().get("enabled", True)) and self._fresh()
            if show:
                img = self.render()
                if img is not None:
                    self.photo = ImageTk.PhotoImage(img)
                    self.lbl.configure(image=self.photo)
                else:
                    show = False
            if show != self.shown:
                (self.w.deiconify if show else self.w.withdraw)()
                if show:
                    self._apply_lock(self.get().get("locked", False))
                self.shown = show
        except Exception as e:
            print(f"skill bar draw failed: {e}")
        self.after_tick = self.root.after(int(1000 / FPS), self._tick)

    # ---------------------------------------------------------------- move / lock
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.w.winfo_x(), e.y_root - self.w.winfo_y()

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if not self.dev:
            self.put(x=self.w.winfo_x(), y=self.w.winfo_y())

    def _slot_at(self, x):
        """Index of the slot under window x, or None (padding / gaps)."""
        ids = getattr(self, "slot_ids", [])
        for i, x0 in enumerate(getattr(self, "xs", [])):
            if i < len(ids) and x0 <= x < x0 + self.s:
                return i
        return None

    def _names(self):
        d = {sk.get("id"): sk.get("name") for sk in (self.live or {}).get("skills") or []}
        for ps in self.pslots:
            d["party:" + ps["id"]] = "party " + ps["name"]
        return d

    @staticmethod
    def _meso_text(sk):
        """Mesos per cast: MP at your MP potion's price + items + mesos. "~" when it depends on
        the fight (Meso Guard); "+R" when it needs an item without a price (set one in Settings)."""
        v = sk.get("mesos")
        if v is None:
            return "?"
        txt = f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        if any(price is None for _, _, price in sk.get("items") or []):
            txt += "+R"
        if sk.get("meso_varies"):
            txt = "~" + txt
        return txt

    def _menu(self, e):
        m = tk.Menu(self.w, tearoff=0)
        show = tk.StringVar(value=self.cfg("show"))
        m._show = show                            # keep the variable alive with the menu
        for val, label in (("casts", "Show casts left"), ("mp", "Show MP per cast"),
                           ("mesos", "Show mesos per cast")):
            m.add_radiobutton(label=label, value=val, variable=show,
                              command=lambda v=val: self.put_char(show=v))
        m.add_separator()
        i = self._slot_at(e.x)
        sid = self.slot_ids[i] if i is not None else None
        names = self._names()
        if sid == GUARD_ID:                       # Magic Guard's own number
            gs = tk.StringVar(value=self.cfg("guard_show"))
            m._gs = gs
            for val, label in (("ehp", "Magic Guard: effective HP (HP + guard % of MP)"),
                               ("absorb", "Magic Guard: absorb (guard % of current MP)"),
                               ("last", "Magic Guard: MP used by the last hit")):
                m.add_radiobutton(label=label, value=val, variable=gs,
                                  command=lambda v=val: self.put_char(guard_show=v))
            m.add_separator()
        if sid == HEAL_ID and self.credits:       # what my Heal did on the others, newest cast first
            casts = sorted({c["cast_t"] for c in self.credits}, reverse=True)[:CREDIT_MENU_CASTS]
            for cast in casts:
                for c in sorted((c for c in self.credits if c["cast_t"] == cast), key=lambda c: c["name"]):
                    m.add_command(label=f"{c['name']} +{c['amount']}" + (f" (avg ~{c['avg']})" if c["avg"] else ""),
                                  state="disabled")
                if cast != casts[-1]:
                    m.add_separator()
            m.add_separator()
        if sid:
            m.add_command(label=f"Remove {names.get(sid) or 'skill'} from the bar",
                          command=lambda: self.set_hidden(sid, True))
        hidden = [h for h in (self.cfg("hidden") or []) if h in names]
        if hidden:
            back = tk.Menu(m, tearoff=0)
            for h in hidden:
                back.add_command(label=names[h], command=lambda h=h: self.set_hidden(h, False))
            if len(hidden) > 1:
                back.add_separator()
                back.add_command(label="All of them", command=lambda: self.put_char(hidden=[]))
            m.add_cascade(label="Add back", menu=back)
        if sid or hidden:
            m.add_separator()
        m.add_command(label="Lock (click-through)", command=lambda: self.set_locked(True))
        m.add_command(label="Hide skill bar", command=lambda: self.put(enabled=False))
        m.tk_popup(e.x_root, e.y_root)

    def set_hidden(self, sid, hide):
        """Take a skill off the bar (or put it back). Saved in helper_settings.json
        skillbar.chars[character].hidden, by skill id."""
        cur = list(self.cfg("hidden") or [])
        if hide and sid not in cur:
            cur.append(sid)
        elif not hide and sid in cur:
            cur.remove(sid)
        self.put_char(hidden=cur)

    def set_locked(self, locked):
        self.put(locked=locked)
        self._apply_lock(locked)

    def _apply_lock(self, locked):
        """Locked: the window ignores the mouse (clicks go to the game underneath)."""
        try:
            import ctypes
            u = ctypes.windll.user32
            self.w.update_idletasks()
            hwnd = u.GetParent(self.w.winfo_id()) or self.w.winfo_id()
            GWL_EXSTYLE, LAYERED, TRANSPARENT, TOOL = -20, 0x80000, 0x20, 0x80
            st = u.GetWindowLongW(hwnd, GWL_EXSTYLE) | LAYERED | TOOL
            st = st | TRANSPARENT if locked else st & ~TRANSPARENT
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, st)
        except Exception as e:
            print(f"skill bar lock failed: {e}")

    def destroy(self):
        if self.after_tick:
            self.root.after_cancel(self.after_tick)
        self.w.destroy()


def demo_live():
    t = time.time()
    return {"t": t, "character": "Demo", "who": None,
            "usage": {"party_heal": {"avg": 131, "n": 5, "last": 133, "last_t": t - 20, "seq": 6}},
            "mp": {"cur": 412, "max": 606, "tick": 6, "period": 10.0, "next": t - (t % 10) + 6.3},
            "hp": {"cur": 380, "max": 408, "tick": 9, "period": 10.0, "next": t - (t % 10) + 7},
            "regen": {"mp": {"skill": "Improved MP Recovery", "level": 5, "icon": None}},  # no HP skill
            "skills": [{"id": "2001003", "name": "Magic Claw", "level": 6, "mp": 11, "casts": 37,
                        "kind": "attack", "cast_n": int(t / 2)},
                       {"id": "2001002", "name": "Energy Bolt", "level": 1, "mp": 8, "casts": 51,
                        "kind": "attack", "cast_n": int(t / 5)},
                       {"id": "1001002", "name": "Slash Blast", "level": 1, "mp": 8, "hp_cost": 5,
                        "casts": 51, "kind": "attack", "cast_n": int(t / 3)},
                       {"id": "0001001", "name": "Recovery", "level": 3, "mp": 15, "casts": 3,
                        "recovery": {"tick": 12, "t": t - (t % 45), "ticks": [], "first": 5.9,
                                     "every": 5.1, "n": 6, "cd": 40}},   # cast every 45 s, 6 ticks of +12, 40 s cooldown (demo)
                       {"id": "0001002", "name": "Nimble Feet", "level": 3, "mp": 10, "casts": 41,
                        "nimble": {"t": t - (t % 50), "dur": 30, "cd": 45, "speed": 10}},  # demo: 30 s on, 45 s cooldown
                       {"id": "2301001", "name": "Heal", "level": 3, "mp": 40, "casts": 12,
                        "kind": "buff", "attacks": "undead", "cast_n": int(t / 4),
                        "heal": {"avg": 135, "recent": 136, "n": 4, "level": 3}},
                       {"id": "2300000", "name": "MP Eater", "level": 1, "mp": None, "casts": None,
                        "eater": {"last": 9 + int(t // 8) % 3, "t": 8 * int(t // 8) + 1.5, "cd": 5.0}},   # an absorb every 8 s, 5 s cooldown
                       {"id": "2001000", "name": "Magic Guard", "level": 2, "mp": 8, "casts": 52,
                        "guard": {"pct": 33, "absorb": 270, "ehp": 818, "last": 43}}]}


def main():
    from base import lean  # noqa: F401
    demo = "--demo" in sys.argv
    at = next((tuple(int(v) for v in a.split("=", 1)[1].split(",")) for a in sys.argv
               if a.startswith("--at=")), None)
    root = tk.Tk()
    root.withdraw()
    state = {"enabled": True, "locked": False}
    bar = SkillBar(root, lambda: state, lambda **kw: state.update(kw), demo=demo, at=at)
    root.mainloop()


if __name__ == "__main__":
    main()
