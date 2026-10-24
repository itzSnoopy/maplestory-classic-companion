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
    for n, m in mates.items():        # partners with the app who aren't in the box we see
        if n not in used:
            cards.append({"name": n, "on_map": False, "hp": m.get("hp"), "mp": m.get("mp"),
                          "level": m.get("level"), "exp_pct": m.get("exp_pct"),
                          "exp_hr": m.get("exp_hr"), "eta": m.get("eta"), "map": m.get("map"),
                          "playing": m.get("state") == "playing", "age": m.get("age_s") or 0,
                          "app": True})
    return cards


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

    def render(self, cards):
        if not cards:
            return None
        imgs = [self._card(c) for c in cards]
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
         "exp_hr": 4100, "eta": "1h40m", "map": "L Forest I", "state": "playing", "age_s": 1.2}]}
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
