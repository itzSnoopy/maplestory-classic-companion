"""party_ui.py - the party panel over the game: bigger HP bars for everyone, and EXP / MP for
partners who run the app too.

One card per party member, in the game's party box order:
  name, level, star for the leader, the map when they're somewhere else
  HP   big bar. Fill from the party box (party.py, live each second) while they're on your map,
       else from their own app. Text: exact "380 / 424" when known (you: status bar; partners:
       party link), else the box's %. Outlined red under 30 %.
  MP   exact, only when known (you, or a partner with the app)
  EXP  % bar + EXP/hr + time to level, only when known

Sources: live.json (your HP/MP + the party box rows, tracker ~1/s), status.json (your EXP),
party.json (partners, partylink.py). Hidden when none of them shows a party. Lives in the helper
process like the skill bar (helper_ui.App, Settings > General "Party panel"); drag to move,
right-click to lock (click-through) or hide. Position: helper_settings.json "partypanel".

Challenges (components/challenge/): when some member's app says it can ("ch" in its caps) and
challenge.json (the tracker's, "enabled") is on:
  - every such member / friend row has a small crossed-swords "Duel" button: one click opens a compact picker next to
    the row (2 / 5 / 10 / 15 min, then "Challenge <Name>") that writes the 1v1 create command with the target;
    greyed (a click shows the reason) while you are in a duel or that member is
  - a top line "Race everyone here" / "Race the party" (a map / everyone race, small picker)
  - busy states: "Waiting for <Name>..." while your invite is open (a Cancel button when challenge.json has
    `waiting.id`), the relay's error text (e.g. the other player is already in a duel) for a few seconds
  - an invitation addressed to you (challenge.json "invite") shows as one line with Accept / x; the obvious place
    to answer is the popup in components/challenge/invite_ui.py (this line is the second place).
Everything goes through challenge_cmd.json for the tracker; the panel never talks to the relay.

  python -m components.party.party_ui --demo [--at=X,Y]      fake data, for trying it out
"""
import json
import sys
import time
import tkinter as tk

from PIL import Image, ImageDraw

from base.paths import DATA
from hub.ui.overlay import KEY, font as _font, text as _text

LIVE, STATUS, PARTY = DATA / "live.json", DATA / "status.json", DATA / "party.json"
CHALLENGE, CMD = DATA / "challenge.json", DATA / "challenge_cmd.json"
MINUTES = (2, 5, 10, 15)
W_CARD = 236
FPS = 4
BOX_FRESH_S = 6.0           # party box rows in live.json
MATE_FRESH_S = 15.0         # a partner's own numbers (their app posts every 3 s)
LOW_HP = 30.0
MAX_FRIENDS = 12           # rows shown; the rest is a "+N more" line (same map / playing first)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _short(n):
    n = float(n)
    return f"{n / 1000:.1f}k" if n >= 10000 else f"{n:,.0f}"


def _star(d, cx, cy, r):
    """The leader star (a drawn shape: the UI fonts have no star glyph)."""
    import math
    pts = [(cx + (r if i % 2 == 0 else r * 0.45) * math.cos(math.pi / 2 + i * math.pi / 5),
            cy - (r if i % 2 == 0 else r * 0.45) * math.sin(math.pi / 2 + i * math.pi / 5))
           for i in range(10)]
    d.polygon(pts, fill=(255, 200, 60, 255), outline=(120, 80, 0, 255))


def members(live, status, pj, now):
    """Merge the box rows with your own and your partners' numbers -> [card dicts]."""
    rows = []
    pt = (live.get("party") or {}).get("t")
    if pt and now - pt < BOX_FRESH_S and now - (live.get("t") or 0) < BOX_FRESH_S:
        rows = list((live.get("party") or {}).get("rows") or [])
    mates = {}
    if pj.get("ok") and now - (pj.get("t") or 0) < MATE_FRESH_S:
        mates = {m["name"]: m for m in pj.get("members") or []
                 if m.get("name") and (m.get("age_s") or 0) < MATE_FRESH_S * 4}
    me_name = live.get("character")
    if not rows and not mates:
        return []
    cards, used = [], set()
    if not rows:                     # no box on screen, but partners are online: you first
        rows = [{"name": me_name, "me": True, "on_map": True, "hp": None, "leader": False}]
    for r in rows:
        c = {"name": r.get("name") or "?", "leader": r.get("leader"), "on_map": r.get("on_map"),
             "box_hp": r.get("hp"), "me": r.get("me") or (me_name and r.get("name") == me_name)}
        if c["me"]:
            for k in ("hp", "mp"):
                m = live.get(k) or {}
                if m.get("cur") is not None and m.get("max"):
                    c[k] = [m["cur"], m["max"]]
            c.update(level=status.get("level"), exp_pct=status.get("pct"),
                     exp_hr=status.get("exp_per_hour_recent"), eta=status.get("eta_to_level"),
                     map=status.get("map"), playing=status.get("state") == "playing", age=0)
        elif c["name"] in mates:
            m = mates[c["name"]]
            used.add(c["name"])
            c.update(hp=m.get("hp"), mp=m.get("mp"), level=m.get("level"), exp_pct=m.get("exp_pct"),
                     exp_hr=m.get("exp_hr"), eta=m.get("eta"), map=m.get("map"),
                     playing=m.get("state") == "playing", age=m.get("age_s") or 0, app=True,
                     busy=bool(m.get("busy")))
        cards.append(c)
    # everyone else with the code: friends in the hangout, not in your in-game party (or the box
    # isn't on screen). A compact list: same map first, then who's playing, then by name
    my_map = status.get("map")
    friends = []
    for n, m in mates.items():
        if n not in used:
            friends.append({"name": n, "friend": True, "hp": m.get("hp"), "mp": m.get("mp"),
                            "level": m.get("level"), "exp_pct": m.get("exp_pct"),
                            "exp_hr": m.get("exp_hr"), "eta": m.get("eta"), "map": m.get("map"),
                            "job": m.get("job"), "channel": m.get("channel"),
                            "same_map": bool(my_map and m.get("map") == my_map),
                            "playing": m.get("state") == "playing", "age": m.get("age_s") or 0,
                            "app": True, "busy": bool(m.get("busy"))})
    friends.sort(key=lambda f: (not f["same_map"], not f["playing"], f["name"].lower()))
    return cards + friends


def challenge_ui(pj, ch, now):
    """What the challenge strips show: {"able": names that run a build with challenges, "button": bool (the group
    entry is shown), "busy": I am in / waiting for a duel, "waiting": {id?, target?} | None (challenge.json
    `waiting` when the tracker writes it), "error": the relay's last refusal or None, "invite": the open invitation
    to you or None}. Needs challenge.json from the tracker (it says enabled)."""
    if not ch.get("enabled") or not pj.get("ok"):
        return {"able": [], "button": False, "invite": None, "busy": False, "waiting": None, "error": None,
                "active": False}
    able = [m["name"] for m in pj.get("members") or []
            if "ch" in str(m.get("caps") or "").split(",") and (m.get("age_s") or 0) < MATE_FRESH_S * 4]
    inv = ch.get("invite")
    if inv and (inv.get("expires") or 0) < now:
        inv = None
    return {"able": able, "button": bool(able), "invite": inv, "busy": bool(ch.get("busy")),
            "waiting": ch.get("waiting") if isinstance(ch.get("waiting"), dict) else None,
            "error": ch.get("error"), "active": bool(ch.get("active"))}


def _swords(d, cx, cy, r, k):
    """Two crossed swords (drawn shapes; the UI fonts have no such glyph)."""
    for sx in (-1, 1):
        x0, y0, x1, y1 = cx + sx * r, cy - r, cx - sx * r, cy + r
        d.line((x0, y0, x1, y1), fill=(215, 220, 232, 255), width=max(2, int(2.4 * k)))
        gx, gy = cx - sx * r * 0.45, cy + r * 0.45          # the guard, a short bar across the blade
        d.line((gx - sx * 3.5 * k, gy - 3.5 * k, gx + sx * 3.5 * k, gy + 3.5 * k),
               fill=(230, 180, 60, 255), width=max(2, int(2 * k)))
        d.ellipse((x1 - 2 * k, y1 - 2 * k, x1 + 2 * k, y1 + 2 * k), fill=(230, 180, 60, 255))


class PartyPanel:
    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None):
        self.root, self.get, self.put, self.demo, self.k = root, settings_get, settings_put, demo, k
        self.photo, self.shown, self.cards = None, None, []
        self.hot = []                        # clickable spots of the image: (x0, y0, x1, y1, action)
        self.picker = None
        self.ch = {}
        self.pending = None                  # my last create command: {name | None, t}, until the relay shows it
        self.note = None                     # (text, until) a blocked Duel button's reason
        self.last_min = 5                    # the minutes the Duel picker starts on
        self._h = []                         # hot regions a drawing helper reports (image coordinates)
        self.f_name, self.f_small = _font(int(13 * k)), _font(int(10 * k))
        self.f_bar = _font(int(10 * k))
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
            x, y = 12, 300                   # left side, below the minimap + party box
        self.dev = at is not None
        w.geometry(f"+{int(x)}+{int(y)}")
        w.withdraw()
        self.after_tick = None
        self._apply_lock(cfg.get("locked", False))
        self._tick()

    # ---------------------------------------------------------------- drawing
    def _bar(self, d, x, y, w, h, frac, color, text=None, warn=False, dim=False):
        k = self.k
        d.rounded_rectangle((x, y, x + w, y + h), radius=int(3 * k), fill=(40, 42, 50, 255),
                            outline=(255, 70, 70, 255) if warn else (90, 96, 110, 255),
                            width=2 if warn else 1)
        if frac is not None and frac > 0:
            fw = max(2, int((w - 2) * min(1.0, frac)))
            c = tuple(int(v * (0.55 if dim else 1)) for v in color) + (255,)
            d.rounded_rectangle((x + 1, y + 1, x + 1 + fw, y + h - 1), radius=int(2 * k), fill=c)
        if text:
            _text(d, (x + w // 2, y + h // 2 + 1), text, self.f_bar)

    def _card(self, c):
        k = self.k
        W = int(W_CARD * k)
        pad, bw = int(6 * k), int(W_CARD * k) - int(12 * k)
        hp_h, mp_h, ex_h = int(14 * k), int(10 * k), int(8 * k)
        has_mp = bool(c.get("mp"))
        has_exp = c.get("exp_pct") is not None
        away = not c.get("on_map") and not c.get("me")
        H = pad + int(17 * k) + (int(13 * k) if away else 0) + hp_h + int(3 * k)
        H += (mp_h + int(3 * k)) if has_mp else 0
        H += (ex_h + int(16 * k)) if has_exp else 0
        H += pad
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        stale = c.get("app") and (c.get("age") or 0) > MATE_FRESH_S
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=int(7 * k), fill=(24, 26, 32, 235),
                            outline=(200, 170, 80, 255) if c.get("me") else (90, 96, 110, 255))
        y = pad
        name = c.get("name") or "?"
        x = pad
        if c.get("leader"):
            _star(d, x + int(6 * k), y + int(8 * k), int(6 * k))
            x += int(16 * k)
        col = (255, 255, 255, 255) if not away else (190, 195, 205, 255)
        _text(d, (x, y + int(8 * k)), name, self.f_name, fill=col, anchor="lm")
        right = f"Lv {c['level']}" if c.get("level") else ""
        if c.get("app") and c.get("playing") is False:
            right = (right + " · away").strip(" ·")
        rx = W - pad
        if c.get("duel_ok") is not None:
            bt = int(18 * k)
            self._duel_button(d, W - pad - bt, y - int(1 * k), bt, c, c["duel_ok"])
            rx -= bt + int(4 * k)
        if right:
            _text(d, (rx, y + int(8 * k)), right, self.f_small, fill=(200, 210, 230, 255),
                  anchor="rm")
        y += int(17 * k)
        if away:
            where = c.get("map") or "another map"
            _text(d, (pad, y + int(5 * k)), where, self.f_small, fill=(150, 160, 180, 255),
                  anchor="lm")
            y += int(13 * k)
        # HP: live fill from the party box on your map, else the member's own numbers
        hp = c.get("hp")
        frac = None
        if c.get("on_map") and c.get("box_hp") is not None and not c.get("me"):
            frac = c["box_hp"] / 100
        elif hp:
            frac = hp[0] / hp[1]
        elif c.get("box_hp") is not None:
            frac = c["box_hp"] / 100
        if hp:
            text = f"{hp[0]:,} / {hp[1]:,}"
        elif frac is not None:
            text = f"{frac * 100:.0f}%"
        else:
            text = "HP unknown"
        self._bar(d, pad, y, bw, hp_h, frac, (210, 40, 40), text,
                  warn=frac is not None and frac * 100 < LOW_HP, dim=stale)
        y += hp_h + int(3 * k)
        if has_mp:
            mp = c["mp"]
            self._bar(d, pad, y, bw, mp_h, mp[0] / mp[1], (40, 110, 230),
                      f"{mp[0]:,} / {mp[1]:,}", dim=stale)
            y += mp_h + int(3 * k)
        if has_exp:
            pct = c["exp_pct"]
            self._bar(d, pad, y, bw, ex_h, pct / 100, (200, 200, 60), dim=stale)
            y += ex_h + int(2 * k)
            bits = [f"EXP {pct:.2f}%"]
            if c.get("exp_hr"):
                bits.append(f"{_short(c['exp_hr'])}/hr")
            if c.get("eta"):
                bits.append(f"Lv up ~{c['eta']}")
            _text(d, (pad, y + int(6 * k)), "  ·  ".join(bits), self.f_small,
                  fill=(220, 220, 150, 255), anchor="lm")
        return im

    def _friends(self, friends):
        """Compact list: two lines a friend (name, level, EXP %; map + thin HP/EXP bars)."""
        k = self.k
        W, pad = int(W_CARD * k), int(6 * k)
        row_h, head_h = int(30 * k), int(18 * k)
        more = max(0, len(friends) - MAX_FRIENDS)
        on = sum(1 for f in friends if f.get("playing"))
        friends = friends[:MAX_FRIENDS]
        H = pad + head_h + row_h * len(friends) + (int(16 * k) if more else 0) + pad // 2
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=int(7 * k), fill=(24, 26, 32, 235),
                            outline=(90, 96, 110, 255))
        _text(d, (pad, pad + head_h // 2), f"Friends · {on} playing", self.f_small,
              fill=(200, 210, 230, 255), anchor="lm")
        y = pad + head_h
        bw = int(70 * k)
        bt = int(22 * k)
        for f in friends:
            stale = (f.get("age") or 0) > MATE_FRESH_S
            dot = (90, 210, 120, 255) if f.get("playing") and not stale else (120, 125, 135, 255)
            r = int(3 * k)
            d.ellipse((pad, y + int(7 * k) - r, pad + 2 * r, y + int(7 * k) + r), fill=dot)
            col = (255, 255, 255, 255) if f.get("playing") else (175, 180, 190, 255)
            _text(d, (pad + int(10 * k), y + int(7 * k)), f["name"], self.f_small, fill=col,
                  anchor="lm")
            right = " · ".join(x for x in (f"Lv {f['level']}" if f.get("level") else "",
                                             f"{f['exp_pct']:.1f}%" if f.get("exp_pct") is not None
                                             else "") if x)
            has_btn = f.get("duel_ok") is not None
            edge = W - pad - ((bt + int(4 * k)) if has_btn else 0)
            if has_btn:
                self._duel_button(d, W - pad - bt, y + int(3 * k), bt, f, f["duel_ok"])
            if right:
                _text(d, (edge, y + int(7 * k)), right, self.f_small,
                      fill=(220, 220, 150, 255), anchor="rm")
            where = f.get("map") or ("not playing" if not f.get("playing") else "?")
            if f.get("same_map"):
                where = "here · " + where
            if f.get("busy"):
                where = "in a duel"
            ch = f" · ch {f['channel']}" if f.get("channel") else ""
            mcol = (140, 220, 160, 255) if f.get("same_map") else (150, 160, 180, 255)
            text = (where + ch)
            maxw = edge - 2 * pad - bw - int(16 * k)
            while text and d.textlength(text, font=self.f_small) > maxw:
                text = text[:-2] + "…" if not text.endswith("…") else text[:-2] + "…"
            _text(d, (pad + int(10 * k), y + int(21 * k)), text, self.f_small, fill=mcol,
                  anchor="lm")
            bx = edge - bw
            hp = f.get("hp")
            hb = int(4 * k)
            if hp:
                frac = hp[0] / hp[1]
                d.rectangle((bx, y + int(16 * k), bx + bw, y + int(16 * k) + hb), fill=(40, 42, 50, 255))
                d.rectangle((bx, y + int(16 * k), bx + int(bw * frac), y + int(16 * k) + hb),
                            fill=(255, 70, 70, 255) if frac * 100 < LOW_HP else (210, 40, 40, 255))
            if f.get("exp_pct") is not None:
                yy = y + int(16 * k) + hb + int(2 * k)
                d.rectangle((bx, yy, bx + bw, yy + hb), fill=(40, 42, 50, 255))
                d.rectangle((bx, yy, bx + int(bw * f["exp_pct"] / 100), yy + hb), fill=(200, 200, 60, 255))
            y += row_h
        if more:
            _text(d, (pad, y + int(7 * k)), f"+{more} more", self.f_small,
                  fill=(150, 160, 180, 255), anchor="lm")
        return im

    def _duel_button(self, d, x, y, size, who, reason):
        """The small crossed-swords Duel button of a row at (x, y) (size px square) in this image. `reason` is True
        when it works, else the text a click shows (greyed look). Registers its hot region in self._h."""
        k = self.k
        ok = reason is True
        d.rounded_rectangle((x, y, x + size, y + size), radius=int(5 * k),
                            fill=(46, 44, 30, 255) if ok else (34, 36, 42, 255),
                            outline=(230, 180, 60, 255) if ok else (80, 84, 94, 255))
        cx, cy, r = x + size // 2, y + size // 2, int(size * 0.28)
        if ok:
            _swords(d, cx, cy, r, k)
        else:                                                   # greyed: two plain grey blades
            for sx in (-1, 1):
                d.line((cx + sx * r, cy - r, cx - sx * r, cy + r), fill=(96, 100, 112, 255), width=max(2, int(2 * k)))
        self._h.append((x, y, x + size, y + size, ("duel", who["name"]) if ok else ("blocked", reason)))

    def busy_reason(self, ch):
        """Why my Duel buttons are off, or None."""
        if self.waiting_view(ch):
            return "waiting for an answer"
        if ch.get("busy"):
            return "you're in a duel"
        return None

    def waiting_view(self, ch):
        """My open invitation: {text, id?} from challenge.json `waiting` ({id, target | name}) or, until the tracker
        writes it, from the create command this panel sent (no id then: no Cancel button)."""
        w = ch.get("waiting")
        p = self.pending
        if w:
            who = w.get("target") or w.get("name") or (p or {}).get("name")
            return {"text": f"Waiting for {who}..." if who else "Waiting for answers...", "id": w.get("id")}
        if p and ch.get("busy") and not ch.get("active") and not ch.get("invite"):
            return {"text": f"Waiting for {p['name']}..." if p.get("name") else "Waiting for answers...", "id": None}
        return None

    def _strip(self, ch):
        """The top lines (invitation, waiting, the group race entry, a note) -> (image, [(x0, y0, x1, y1, action)])
        or None."""
        k = self.k
        W, pad = int(W_CARD * k), int(6 * k)
        now = time.time()
        inv, wait = ch.get("invite"), self.waiting_view(ch)
        note = self.note[0] if self.note and self.note[1] > now else None
        err = ch.get("error") if not note else None
        if not (inv or wait or ch.get("button") or note or err):
            return None
        rows = []
        if inv:
            rows.append("invite")
        if wait:
            rows.append("wait")
        if ch.get("button"):
            rows.append("group")
        if note or err:
            rows.append("note")
        rh = int(24 * k)
        im = Image.new("RGBA", (W, rh * len(rows) + pad // 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        hot, y = [], 0
        for kind in rows:
            if kind == "invite":
                d.rounded_rectangle((0, y, W - 1, y + rh - 2), radius=int(6 * k), fill=(24, 26, 32, 235),
                                    outline=(230, 180, 60, 255))
                who = {"1v1": "", "map": " (your map)", "all": " (everyone)"}.get(inv.get("mode"), "")
                if inv.get("host"):
                    who = " (room race)"
                _text(d, (pad, y + rh // 2), f"{inv.get('from', '?')}: Kills {inv.get('minutes', '?')} min{who}",
                      self.f_small, anchor="lm")
                bw = int(46 * k)
                ax = W - pad - int(16 * k) - bw
                d.rounded_rectangle((ax, y + int(3 * k), ax + bw, y + rh - int(5 * k)), radius=int(4 * k),
                                    fill=(40, 120, 60, 255), outline=(120, 220, 150, 255))
                _text(d, (ax + bw // 2, y + rh // 2 - 1), "Accept", self.f_small)
                xx = W - pad - int(12 * k)
                _text(d, (xx + int(6 * k), y + rh // 2 - 1), "x", self.f_name, fill=(190, 195, 205, 255))
                hot += [(ax, y, ax + bw, y + rh, "accept"), (xx - int(4 * k), y, W, y + rh, "decline")]
            elif kind == "wait":
                d.rounded_rectangle((0, y, W - 1, y + rh - 2), radius=int(6 * k), fill=(24, 26, 32, 235),
                                    outline=(120, 160, 230, 255))
                _text(d, (pad, y + rh // 2 - 1), wait["text"], self.f_small, fill=(190, 210, 245, 255), anchor="lm")
                if wait.get("id"):
                    bw = int(52 * k)
                    ax = W - pad - bw
                    d.rounded_rectangle((ax, y + int(3 * k), ax + bw, y + rh - int(5 * k)), radius=int(4 * k),
                                        fill=(92, 50, 52, 255), outline=(220, 130, 130, 255))
                    _text(d, (ax + bw // 2, y + rh // 2 - 1), "Cancel", self.f_small)
                    hot.append((ax, y, W, y + rh, "cancel"))
            elif kind == "group":
                off = self.busy_reason(ch)
                d.rounded_rectangle((0, y, W - 1, y + rh - 2), radius=int(6 * k), fill=(24, 26, 32, 235),
                                    outline=(90, 96, 110, 255) if off else (200, 170, 80, 255))
                _swords(d, pad + int(10 * k), y + rh // 2 - 1, int(6 * k) if off else int(7 * k), k)
                _text(d, (pad + int(24 * k), y + rh // 2 - 1), ch.get("label") or "Race everyone here",
                      self.f_small, fill=(130, 136, 148, 255) if off else (200, 210, 230, 255), anchor="lm")
                if off:
                    _text(d, (W - pad, y + rh // 2 - 1), off, self.f_small, fill=(150, 160, 180, 255), anchor="rm")
                    hot.append((0, y, W, y + rh, ("blocked", off)))
                else:
                    hot.append((0, y, W, y + rh, "new"))
            else:
                _text(d, (pad, y + rh // 2 - 1), note or err, self.f_small, fill=(240, 150, 140, 255), anchor="lm")
            y += rh
        return im, hot

    def _mark_duel(self, cards, now):
        """Set each card's `duel_ok` (None = no button, True = works, text = why it is off) and the group entry's
        label; forget a `pending` invite once the relay shows it (or it timed out)."""
        ch = self.ch
        p = self.pending
        if p and ((not ch.get("busy") and now - p["t"] > 4) or ch.get("active") or ch.get("invite")
                  or now - p["t"] > 70):
            self.pending = None
        off = self.busy_reason(ch)
        able = set(ch.get("able") or [])
        for c in cards:
            if c.get("me") or c["name"] not in able:
                c["duel_ok"] = None
            elif c.get("friend") and not c.get("playing"):
                c["duel_ok"] = None                         # not playing: no button (it would only time out)
            elif off:
                c["duel_ok"] = off[0].upper() + off[1:]
            elif c.get("busy"):
                c["duel_ok"] = f"{c['name']} is in a duel"
            else:
                c["duel_ok"] = True
        party = sum(1 for c in cards if not c.get("friend")) > 1
        ch["label"] = "Race the party" if party else "Race everyone here"

    def render(self, cards, ch=None):
        self.hot = []
        if not cards:
            return None
        full = [c for c in cards if not c.get("friend")]
        friends = [c for c in cards if c.get("friend")]
        if not self.get().get("friends", True):
            friends = []
        imgs, hots = [], []
        strip = self._strip(ch) if ch else None
        if strip:
            imgs.append(strip[0])
            hots.append(strip[1])
        for c in full:
            self._h = []
            imgs.append(self._card(c))
            hots.append(self._h)
        if friends:
            self._h = []
            imgs.append(self._friends(friends))
            hots.append(self._h)
        if not imgs:
            return None
        g = int(4 * self.k)
        W = max(i.width for i in imgs)
        H = sum(i.height for i in imgs) + g * (len(imgs) - 1)
        out_a = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        y = 0
        for i, hs in zip(imgs, hots):
            out_a.alpha_composite(i, (0, y))
            self.hot += [(x0, y + y0, x1, y + y1, a) for x0, y0, x1, y1, a in hs]
            y += i.height + g
        key = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
        out = Image.new("RGB", (W, H), key)
        out.paste(out_a.convert("RGB"), (0, 0), out_a.getchannel("A").point(
            lambda v: 255 if v > 100 else 0))
        return out

    def _tick(self):
        try:
            now = time.time()
            if self.demo:
                live, status, pj = demo_data(now)
                chj = demo_waiting(now) if "--waiting" in sys.argv else demo_challenge(now)
            else:
                live, status, pj = _read(LIVE), _read(STATUS), _read(PARTY)
                chj = _read(CHALLENGE)
            cards = members(live, status, pj, now)
            self.ch = challenge_ui(pj, chj, now)
            self._mark_duel(cards, now)
            show = bool(self.get().get("enabled", True)) and bool(cards)
            if show:
                from PIL import ImageTk
                self.photo = ImageTk.PhotoImage(self.render(cards, self.ch))
                self.lbl.configure(image=self.photo)
            if show != self.shown:
                (self.w.deiconify if show else self.w.withdraw)()
                if show:
                    self._apply_lock(self.get().get("locked", False))
                self.shown = show
        except Exception as e:
            print(f"party panel draw failed: {e}")
        self.after_tick = self.root.after(int(1000 / FPS), self._tick)

    # ---------------------------------------------------------------- move / lock
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.w.winfo_x(), e.y_root - self.w.winfo_y()
        self._press = (e.x_root, e.y_root)

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        px, py = getattr(self, "_press", (e.x_root, e.y_root))
        if abs(e.x_root - px) < 4 and abs(e.y_root - py) < 4:        # a click, not a drag
            for x0, y0, x1, y1, action in self.hot:
                if x0 <= e.x < x1 and y0 <= e.y < y1:
                    return self._click(action, y0)
        if not self.dev:
            self.put(x=self.w.winfo_x(), y=self.w.winfo_y())

    # ---------------------------------------------------------------- challenges
    def _command(self, **cmd):
        """Hand a command to the tracker's challenge component (it reads and deletes the file)."""
        if self.demo:
            print("challenge command:", cmd)
            return
        from hub import history as H
        H.write_json(CMD, {"t": round(time.time(), 2), **cmd}, indent=None)

    def _click(self, action, y=None):
        inv = self.ch.get("invite") or {}
        if action == "new":
            self._open_picker()
        elif action in ("accept", "decline") and inv.get("id"):
            self._command(action=action, id=inv["id"])
        elif action == "cancel":
            w = self.ch.get("waiting") or {}
            if w.get("id"):
                self._command(action="cancel", id=w["id"])
                self.pending = None
        elif isinstance(action, tuple) and action[0] == "blocked":
            self.note = (action[1], time.time() + 4)
        elif isinstance(action, tuple) and action[0] == "duel":
            self._open_duel_picker(action[1], y)

    def _open_duel_picker(self, name, y):
        """The compact 1v1 picker next to the clicked row: minutes, then 'Challenge <Name>'."""
        self._close_picker()
        k = self.k
        p = self.picker = tk.Toplevel(self.root)
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        bg, fg = "#1a1c22", "#e8e8e8"
        p.configure(bg="#e6b43c")
        box = tk.Frame(p, bg=bg)
        box.pack(padx=2, pady=2)
        top = tk.Frame(box, bg=bg)
        top.pack(fill="x", padx=int(8 * k), pady=(int(6 * k), int(2 * k)))
        tk.Label(top, text=f"Duel {name}", bg=bg, fg=fg, font=("Segoe UI", int(10 * k), "bold")).pack(side="left")
        tk.Button(top, text="x", bg=bg, fg="#aab", bd=0, relief="flat", activebackground=bg, activeforeground=fg,
                  command=self._close_picker).pack(side="right")
        mins = tk.IntVar(value=self.last_min)
        row = tk.Frame(box, bg=bg)
        row.pack(padx=int(8 * k), pady=int(2 * k))
        for m in MINUTES:
            tk.Radiobutton(row, text=f"{m} min", variable=mins, value=m, indicatoron=False, width=6,
                           bg="#2a2d36", fg=fg, selectcolor="#3a6a48", activebackground="#3a3d46",
                           activeforeground=fg, bd=0, relief="flat", pady=int(3 * k)).pack(side="left", padx=2)

        def send():
            self.last_min = mins.get()
            self._command(action="create", mode="1v1", minutes=mins.get(), target=name)
            self.pending = {"name": name, "t": time.time()}
            self._close_picker()

        tk.Button(box, text=f"Challenge {name}", command=send, bg="#2a8a46", fg="white", bd=0, relief="flat",
                  activebackground="#33a556", activeforeground="white", font=("Segoe UI", int(10 * k), "bold"),
                  pady=int(5 * k)).pack(fill="x", padx=int(8 * k), pady=(int(4 * k), int(8 * k)))
        p.update_idletasks()
        pw, ph = p.winfo_reqwidth(), p.winfo_reqheight()
        x = self.w.winfo_x() + int(W_CARD * k) + 6
        if x + pw > self.root.winfo_screenwidth():          # no room on the right: open on the left
            x = max(0, self.w.winfo_x() - pw - 6)
        yy = max(0, min(self.w.winfo_y() + (y or 0) - int(4 * k), self.root.winfo_screenheight() - ph - 8))
        p.geometry(f"+{x}+{yy}")
        p.after(30000, self._close_picker)                  # a forgotten picker goes away on its own

    def _close_picker(self):
        if self.picker is not None:
            try:
                self.picker.destroy()
            except tk.TclError:
                pass
            self.picker = None

    def _open_picker(self):
        """The group race picker next to the panel: minutes and who (my map / everyone online)."""
        self._close_picker()
        k = self.k
        p = self.picker = tk.Toplevel(self.root)
        p.title(self.ch.get("label") or "Race")
        p.attributes("-topmost", True)
        p.resizable(False, False)
        p.geometry(f"+{self.w.winfo_x() + int((W_CARD + 12) * k)}+{self.w.winfo_y()}")
        pad = dict(padx=int(8 * k), pady=int(2 * k), anchor="w")
        tk.Label(p, text="Kills: most kills wins", font=("Segoe UI", int(10 * k), "bold")).pack(**pad)
        mins = tk.IntVar(value=self.last_min)
        row = tk.Frame(p)
        row.pack(**pad)
        for m in MINUTES:
            tk.Radiobutton(row, text=f"{m} min", variable=mins, value=m).pack(side="left")
        who = tk.StringVar(value="map")
        for val, text in (("map", "Everyone on my map"), ("all", "Everyone online")):
            tk.Radiobutton(p, text=text, variable=who, value=val).pack(**pad)

        def send():
            self.last_min = mins.get()
            self._command(action="create", minutes=mins.get(), mode=who.get())
            self.pending = {"name": None, "t": time.time()}
            self._close_picker()

        row = tk.Frame(p)
        row.pack(padx=int(8 * k), pady=int(6 * k))
        tk.Button(row, text="Start race", width=10, command=send).pack(side="left", padx=4)
        tk.Button(row, text="Cancel", width=8, command=self._close_picker).pack(side="left", padx=4)
        p.protocol("WM_DELETE_WINDOW", self._close_picker)

    def _menu(self, e):
        m = tk.Menu(self.w, tearoff=0)
        show = self.get().get("friends", True)
        m.add_command(label="Hide friends list" if show else "Show friends list",
                      command=lambda: self.put(friends=not show))
        m.add_command(label="Lock (click-through)", command=lambda: self.set_locked(True))
        m.add_command(label="Hide party panel", command=lambda: self.put(enabled=False))
        m.tk_popup(e.x_root, e.y_root)

    def set_locked(self, locked):
        self.put(locked=locked)
        self._apply_lock(locked)

    def _apply_lock(self, locked):
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
            print(f"party panel lock failed: {e}")

    def destroy(self):
        self._close_picker()
        if self.after_tick:
            self.root.after_cancel(self.after_tick)
        self.w.destroy()


def demo_data(now):
    live = {"t": now, "character": "MagicianAlex",
            "hp": {"cur": 380, "max": 424}, "mp": {"cur": 512, "max": 630},
            "party": {"t": now, "rows": [
                {"name": "MagicianAlex", "me": True, "on_map": True, "hp": 89.0, "leader": True},
                {"name": "Robin", "me": False, "on_map": True, "hp": 22.0, "leader": False},
                {"name": "Friend", "me": False, "on_map": False, "hp": None, "leader": False}]}}
    status = {"state": "playing", "level": 15, "pct": 41.28, "exp_per_hour_recent": 3820,
              "eta_to_level": "1h05m", "map": "L Forest I"}
    pj = {"t": now, "ok": True, "members": [
        {"name": "Robin", "caps": "ch", "level": 16, "hp": [101, 460], "mp": [280, 410], "exp_pct": 12.5,
         "exp_hr": 4100, "eta": "1h40m", "map": "L Forest I", "state": "playing", "age_s": 1.2},
        {"name": "Mapler99", "level": 22, "hp": [600, 700], "exp_pct": 71.0, "map": "L Forest I",
         "channel": 40, "state": "playing", "age_s": 2.0},
        {"name": "ArcherJen", "caps": "ch", "level": 31, "hp": [150, 900], "exp_pct": 8.3,
         "map": "The Forest of Wisdom", "channel": 12, "state": "playing", "age_s": 0.5},
        {"name": "SleepyThief", "level": 18, "exp_pct": 44.4, "map": "Kerning City",
         "state": "idle", "age_s": 5.0}]}
    return live, status, pj


def demo_challenge(now):
    return {"enabled": True, "busy": False,
            "invite": {"id": "demo", "from": "Robin", "mode": "1v1", "minutes": 5, "expires": now + 3600}}


def demo_waiting(now):
    """--waiting: my invite to Robin is open (what challenge.json `waiting` will look like)."""
    return {"enabled": True, "busy": True, "invite": None, "waiting": {"id": "w1", "target": "Robin"}}


def main():
    from base import lean  # noqa: F401
    demo = "--demo" in sys.argv
    at = next((tuple(int(v) for v in a.split("=", 1)[1].split(",")) for a in sys.argv
               if a.startswith("--at=")), None)
    root = tk.Tk()
    root.withdraw()
    state = {"enabled": True, "locked": False}
    PartyPanel(root, lambda: state, lambda **kw: state.update(kw), demo=demo, at=at)
    root.mainloop()


if __name__ == "__main__":
    main()
