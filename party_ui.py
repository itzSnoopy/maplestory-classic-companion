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

  python party_ui.py --demo [--at=X,Y]      fake data, for trying it out
"""
import json
import sys
import time
import tkinter as tk

from PIL import Image, ImageDraw

from paths import DATA
from skillbar_ui import KEY, _font, _text

LIVE, STATUS, PARTY = DATA / "live.json", DATA / "status.json", DATA / "party.json"
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
                     playing=m.get("state") == "playing", age=m.get("age_s") or 0, app=True)
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
                            "app": True})
    friends.sort(key=lambda f: (not f["same_map"], not f["playing"], f["name"].lower()))
    return cards + friends


class PartyPanel:
    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None):
        self.root, self.get, self.put, self.demo, self.k = root, settings_get, settings_put, demo, k
        self.photo, self.shown, self.cards = None, None, []
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
        if right:
            _text(d, (W - pad, y + int(8 * k)), right, self.f_small, fill=(200, 210, 230, 255),
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
            if right:
                _text(d, (W - pad, y + int(7 * k)), right, self.f_small,
                      fill=(220, 220, 150, 255), anchor="rm")
            where = f.get("map") or ("not playing" if not f.get("playing") else "?")
            if f.get("same_map"):
                where = "here · " + where
            ch = f" · ch {f['channel']}" if f.get("channel") else ""
            mcol = (140, 220, 160, 255) if f.get("same_map") else (150, 160, 180, 255)
            text = (where + ch)
            maxw = W - 2 * pad - bw - int(16 * k)
            while text and d.textlength(text, font=self.f_small) > maxw:
                text = text[:-2] + "…" if not text.endswith("…") else text[:-2] + "…"
            _text(d, (pad + int(10 * k), y + int(21 * k)), text, self.f_small, fill=mcol,
                  anchor="lm")
            bx = W - pad - bw
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

    def render(self, cards):
        if not cards:
            return None
        full = [c for c in cards if not c.get("friend")]
        friends = [c for c in cards if c.get("friend")]
        if not self.get().get("friends", True):
            friends = []
        imgs = [self._card(c) for c in full]
        if friends:
            imgs.append(self._friends(friends))
        if not imgs:
            return None
        g = int(4 * self.k)
        W = max(i.width for i in imgs)
        H = sum(i.height for i in imgs) + g * (len(imgs) - 1)
        out_a = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        y = 0
        for i in imgs:
            out_a.alpha_composite(i, (0, y))
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
            else:
                live, status, pj = _read(LIVE), _read(STATUS), _read(PARTY)
            cards = members(live, status, pj, now)
            show = bool(self.get().get("enabled", True)) and bool(cards)
            if show:
                from PIL import ImageTk
                self.photo = ImageTk.PhotoImage(self.render(cards))
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

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if not self.dev:
            self.put(x=self.w.winfo_x(), y=self.w.winfo_y())

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
        if self.after_tick:
            self.root.after_cancel(self.after_tick)
        self.w.destroy()


def demo_data(now):
    live = {"t": now, "character": "SnoopySparda",
            "hp": {"cur": 380, "max": 424}, "mp": {"cur": 512, "max": 630},
            "party": {"t": now, "rows": [
                {"name": "SnoopySparda", "me": True, "on_map": True, "hp": 89.0, "leader": True},
                {"name": "DSius", "me": False, "on_map": True, "hp": 22.0, "leader": False},
                {"name": "Friend", "me": False, "on_map": False, "hp": None, "leader": False}]}}
    status = {"state": "playing", "level": 15, "pct": 41.28, "exp_per_hour_recent": 3820,
              "eta_to_level": "1h05m", "map": "L Forest I"}
    pj = {"t": now, "ok": True, "members": [
        {"name": "DSius", "level": 16, "hp": [101, 460], "mp": [280, 410], "exp_pct": 12.5,
         "exp_hr": 4100, "eta": "1h40m", "map": "L Forest I", "state": "playing", "age_s": 1.2},
        {"name": "Mapler99", "level": 22, "hp": [600, 700], "exp_pct": 71.0, "map": "L Forest I",
         "channel": 40, "state": "playing", "age_s": 2.0},
        {"name": "ArcherJen", "level": 31, "hp": [150, 900], "exp_pct": 8.3,
         "map": "The Forest of Wisdom", "channel": 12, "state": "playing", "age_s": 0.5},
        {"name": "SleepyThief", "level": 18, "exp_pct": 44.4, "map": "Kerning City",
         "state": "idle", "age_s": 5.0}]}
    return live, status, pj


def main():
    import lean  # noqa: F401
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
