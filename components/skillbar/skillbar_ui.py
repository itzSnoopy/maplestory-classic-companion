"""skillbar_ui.py - a movable hotbar-style strip over the game (FF14-like), fed by live.json.

Skill corner number (right-click, helper_settings skillbar.show): casts left (default), MP per
cast, or mesos per cast (feed.py: the MP at what your MP potion costs per point, training's
mp_price via hub.ask, plus items like Magic Rock at your own price, plus mesos like Shadow Meso).

Slots, left to right:
  MP regen   tick size ("+6"), seconds to the next tick, and a dark sweep that empties as the
             tick comes (meter.Meter measures it from the status bar; nothing is guessed)
  HP regen   the same for HP
  skills     every skill with an MP cost at level >= 1: its icon (cut from your own Skill window,
             templates/skill_icons/) and how many casts your current MP allows

Lives in the helper process (helper_ui.App creates it when Settings > General > "Skill bar" is
on). Drag it anywhere (e.g. the top of the screen). Lock makes it click-through so it never takes
a game click; unlock from the tray menu. Hidden while live.json is stale (not playing).
Position/lock/removed skills: helper_settings.json "skillbar" (right-click a skill: "Remove ... from
the bar"; "Add back" lists the removed ones). Draws at ~10 frames a second, reads live.json
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

LIVE = DATA / "live.json"
SLOT, GAP, PAD = 44, 4, 4       # px at 96 dpi
STALE_S = 6.0
FPS = 10


class SkillBar:
    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None):
        self.root, self.get, self.put, self.demo = root, settings_get, settings_put, demo
        self.k = k
        self.s = int(round(SLOT * k))
        self.live, self.live_t = {}, 0.0
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
        self._read()
        self._tick()

    # ---------------------------------------------------------------- data
    def _read(self):
        if self.demo:
            self.live, self.live_t = demo_live(), time.time()
        else:
            try:
                st = LIVE.stat()
                if st.st_mtime != self.live_t:
                    self.live = json.loads(LIVE.read_text(encoding="utf-8"))
                    self.live_t = st.st_mtime
            except (OSError, ValueError):
                pass
        self.root.after(1000, self._read)

    def _fresh(self):
        return self.live and time.time() - (self.live.get("t") or 0) < STALE_S

    # ---------------------------------------------------------------- drawing
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
              corner_color=(255, 255, 255, 255), label=None):
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
                                        -90 + 360 * (1 - frac_left), 270, fill=(0, 0, 0, 140))
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
        return self._slot(icon, frac_left=min(1.0, left / per), mid=f"{math.ceil(left):.0f}",
                          corner=f"{per:g}s", label=label)

    def _caption(self, text, color):
        """A small dark pill under a slot, e.g. '+9' under MP (the measured gain per tick)."""
        s, h = self.s, int(16 * self.k)
        im = Image.new("RGBA", (s, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, s - 1, h - 1), radius=int(5 * self.k), fill=(24, 26, 32, 235),
                            outline=(90, 96, 110, 255))
        _text(d, (s // 2, h // 2), text, self.f_small, fill=color)
        return im

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
                slots.append((img, self._caption(cap, text_color)))
                ids.append(None)
        hidden = set(self.get().get("hidden") or [])
        for sk in L.get("skills") or []:
            if sk.get("id") in hidden:          # removed from the bar by the player
                continue
            casts = sk.get("casts")
            initials = "".join(w[0] for w in (sk.get("name") or "?").split()[:2])
            icon = self._icon(sk.get("icon"), initials, (70, 70, 90))
            color = (255, 90, 90, 255) if casts is not None and casts < 5 else (255, 255, 255, 255)
            corner = str(casts) if casts is not None else "?"
            show = self.get().get("show", "casts")
            if show == "mp" and sk.get("mp"):
                corner, color = str(sk["mp"]), (140, 190, 255, 255)
            elif show == "mesos":
                corner, color = self._meso_text(sk), (242, 182, 50, 255)
            slots.append((self._slot(icon, corner=corner, dim=casts == 0, corner_color=color),
                          None))
            ids.append(sk.get("id"))
        self.slot_ids = ids
        if not slots:
            return None
        g, p = int(GAP * self.k), int(PAD * self.k)
        caps = any(c is not None for _, c in slots)
        ch = int(16 * self.k) + int(2 * self.k) if caps else 0
        W = p * 2 + len(slots) * self.s + (len(slots) - 1) * g
        H = p * 2 + self.s + ch
        bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for i, (im, cap) in enumerate(slots):
            x = p + i * (self.s + g)
            bar.alpha_composite(im, (x, p))
            if cap is not None:
                bar.alpha_composite(cap, (x, p + self.s + int(2 * self.k)))
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
        g, p = int(GAP * self.k), int(PAD * self.k)
        i, off = divmod(x - p, self.s + g)
        ids = getattr(self, "slot_ids", [])
        return int(i) if 0 <= i < len(ids) and off < self.s else None

    def _names(self):
        return {sk.get("id"): sk.get("name") for sk in (self.live or {}).get("skills") or []}

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
        show = tk.StringVar(value=self.get().get("show", "casts"))
        m._show = show                            # keep the variable alive with the menu
        for val, label in (("casts", "Show casts left"), ("mp", "Show MP per cast"),
                           ("mesos", "Show mesos per cast")):
            m.add_radiobutton(label=label, value=val, variable=show,
                              command=lambda v=val: self.put(show=v))
        m.add_separator()
        i = self._slot_at(e.x)
        sid = self.slot_ids[i] if i is not None else None
        names = self._names()
        if sid:
            m.add_command(label=f"Remove {names.get(sid) or 'skill'} from the bar",
                          command=lambda: self.set_hidden(sid, True))
        hidden = [h for h in (self.get().get("hidden") or []) if h in names]
        if hidden:
            back = tk.Menu(m, tearoff=0)
            for h in hidden:
                back.add_command(label=names[h], command=lambda h=h: self.set_hidden(h, False))
            if len(hidden) > 1:
                back.add_separator()
                back.add_command(label="All of them", command=lambda: self.put(hidden=[]))
            m.add_cascade(label="Add back", menu=back)
        if sid or hidden:
            m.add_separator()
        m.add_command(label="Lock (click-through)", command=lambda: self.set_locked(True))
        m.add_command(label="Hide skill bar", command=lambda: self.put(enabled=False))
        m.tk_popup(e.x_root, e.y_root)

    def set_hidden(self, sid, hide):
        """Take a skill off the bar (or put it back). Saved in helper_settings.json
        skillbar.hidden, by skill id."""
        cur = list(self.get().get("hidden") or [])
        if hide and sid not in cur:
            cur.append(sid)
        elif not hide and sid in cur:
            cur.remove(sid)
        self.put(hidden=cur)

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
    return {"t": t, "character": "Demo",
            "mp": {"cur": 412, "max": 606, "tick": 6, "period": 10.0, "next": t - (t % 10) + 6.3},
            "hp": {"cur": 380, "max": 408, "tick": 9, "period": 10.0, "next": t - (t % 10) + 7},
            "regen": {"mp": {"skill": "Improved MP Recovery", "level": 5, "icon": None}},  # no HP skill
            "skills": [{"id": "2001003", "name": "Magic Claw", "level": 6, "mp": 11, "casts": 37},
                       {"id": "2001002", "name": "Energy Bolt", "level": 1, "mp": 8, "casts": 51},
                       {"id": "0001001", "name": "Recovery", "level": 3, "mp": 15, "casts": 3}]}


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
