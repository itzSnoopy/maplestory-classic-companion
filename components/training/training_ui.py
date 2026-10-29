"""training_ui.py - the training cost panel over the game: what this map costs you.

Rows (live.json "training", training.py; this map visit):
  header     map, minutes here
  Kills      mobs' worth of EXP (~), whole kills, per hour
  <skill>    MP per cast and what that MP costs in mesos (at the MP potion you use), casts
  MP         spent on casts, from regen, from potions
  Potions    used here, by name, with their shop price
  Per kill   MP, casts, mesos (after a few kills)
  Per 1k EXP the same per 1,000 EXP (stays right on crowded maps)
  To level   potions you'd need at this rate (with your regen / with none), mesos, time
Meso income isn't read yet (user, 2026-10-24: costs first).

Lives in the helper process like the skill bar and party panel (Settings > General
"Training cost panel", off by default); drag to move, right-click to lock (click-through) or
hide. Position: helper_settings.json "trainingpanel". Hidden while live.json is stale.

  python -m components.training.training_ui --demo [--at=X,Y]      fake data, for trying it out
"""
import json
import sys
import time
import tkinter as tk

from PIL import Image, ImageDraw

from base.paths import DATA
from hub.ui.overlay import KEY, font as _font

LIVE = DATA / "live.json"
W = 300
FPS = 1
STALE_S = 6.0
BG, LINE = (22, 24, 29, 235), (52, 56, 66, 255)
TEXT, MUTED, GOLD, BLUE, RED, GREEN = ((232, 230, 227, 255), (138, 143, 152, 255),
                                       (242, 182, 50, 255), (110, 160, 255, 255),
                                       (240, 100, 100, 255), (120, 200, 120, 255))


def _n(x):
    if x is None:
        return "?"
    x = float(x)
    if abs(x) >= 10000:
        return f"{x / 1000:.1f}k".replace(".0k", "k")
    return f"{x:,.0f}" if abs(x) >= 10 or x == int(x) else f"{x:.1f}"


def _hm(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


def exp_hr_text(r):
    """'~14.2k (+3.1k party)' from training "rate" (exprate.py); early numbers get a '~'."""
    if not r.get("exp_hr"):
        n = r.get("gains") or 0
        return f"after 3 kills ({n} so far)"
    t = ("~" if r.get("early") else "") + _n(r["exp_hr"])
    if r.get("party_hr"):
        t += f" (+{_n(r['party_hr'])} party)"
    if r.get("break_min", 0) >= 2:
        t += f" · {r['break_min']:g} min of breaks left out"
    if r.get("lump_exp"):
        t += f" · {_n(r['lump_exp'])} quest EXP left out"
    return t


def rows(tr):
    """[(label, text, color)] for the panel; tr = live.json "training"."""
    out = []
    kills = tr.get("kills_total") or 0
    seen = tr.get("kills_seen") or 0
    per_h = tr.get("per_hour") or {}
    # "kills" = mobs' worth of EXP (crowded maps split EXP by damage); whole kills apart
    out.append(("Kills", f"~{kills}" + (f" · {seen} whole" if seen != kills else "")
                + (f" · {_n(per_h.get('kills'))}/hr" if kills else ""), TEXT))
    for name, c in sorted((tr.get("cast_cost") or {}).items(),
                          key=lambda kv: -(tr["casts"].get(kv[0], {}).get("n", 0))):
        n = (tr.get("casts") or {}).get(name, {}).get("n", 0)
        cost = f"{c['mp']} MP" + (f" = {c['mesos']:.1f} mesos" if c.get("mesos") else "")
        # the potion that prices the MP: its picture after the text (row icon, see render)
        out.append((name, f"{cost}  ×{n}", BLUE, "mp_item"))
    mp = tr.get("mp") or {}
    out.append(("MP", f"-{_n(mp.get('casts'))} casts · +{_n(mp.get('regen'))} regen · "
                      f"+{_n(mp.get('potions'))} pots", MUTED))
    pots = tr.get("potions") or {}
    if pots:
        txt = ", ".join(f"{name} ×{p['n']}" for name, p in sorted(pots.items(),
                                                                   key=lambda kv: -kv[1]["n"]))
        out.append(("Potions", f"{txt}  = {_n(tr.get('mesos'))} mesos", GOLD))
    else:
        out.append(("Potions", "none yet", MUTED))
    pk = tr.get("per_kill")
    if pk:
        out.append(("Per kill", f"{_n(pk['mp'])} MP · {pk['casts']:g} casts · "
                                f"{_n(pk['mesos'])} mesos", TEXT))
    else:
        out.append(("Per kill", "after 3 kills", MUTED))
    p1k = tr.get("per_1k_exp")
    if p1k:
        out.append(("Per 1k EXP", f"{_n(p1k['mp'])} MP · {p1k['casts']:g} casts · "
                                  f"{_n(p1k['mesos'])} mesos", TEXT))
    out.append(("EXP / hr", exp_hr_text(tr.get("rate") or {}), TEXT))
    out.append(("Per hour", f"{_n(per_h.get('mesos'))} mesos", TEXT))
    to = tr.get("to_level")
    if to:
        mpp = to.get("mp_pots") or {}
        hpp = to.get("hp_pots") or {}
        bits = []
        if mpp.get("with_regen") is not None:
            bits.append(f"{mpp['with_regen']} {mpp['item']}"
                        + (f" (no regen {mpp['no_regen']})" if mpp.get("no_regen") != mpp.get(
                            "with_regen") else ""))
        if hpp.get("with_regen"):
            bits.append(f"{hpp['with_regen']} {hpp['item']}")
        line = " + ".join(bits) or "no potions"
        extra = []
        if to.get("mesos") is not None:
            extra.append(f"{_n(to['mesos'])} mesos")
        if to.get("minutes") is not None:
            extra.append(_hm(to["minutes"]))
            if to.get("minutes_with_breaks") and to["minutes_with_breaks"] > to["minutes"] * 1.05:
                extra.append(f"~{_hm(to['minutes_with_breaks'])} with your breaks")
        out.append(("To level", line, GREEN))
        if extra:
            out.append(("", " · ".join(extra), GREEN))
    return out


class TrainingPanel:
    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None):
        self.root, self.get, self.put, self.demo, self.k = root, settings_get, settings_put, demo, k
        self.photo, self.shown = None, None
        self.icons = {}
        self.f_head, self.f = _font(int(12 * k)), _font(int(10 * k), bold=False)
        self.f_lbl = _font(int(10 * k))
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
            x, y = 12, 560                   # left side, under the party panel's default spot
        self.dev = at is not None
        w.geometry(f"+{int(x)}+{int(y)}")
        w.withdraw()
        self.after_tick = None
        self._apply_lock(cfg.get("locked", False))
        self._tick()

    def render(self, tr):
        k = self.k
        pad, lw, lh = int(8 * k), int(72 * k), int(16 * k)
        Wp = int(W * k)
        lines = rows(tr)
        # wrap long values onto extra lines
        d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        maxw = Wp - 2 * pad - lw
        wrapped = []
        icons = {}                                   # wrapped row index -> picture
        for row in lines:
            label, text, color = row[:3]
            if len(row) > 3:
                icons[len(wrapped)] = self._icon((tr.get(row[3]) or {}).get("key"))
            words, cur = text.split(" "), ""
            for wd in words:
                nxt = (cur + " " + wd).strip()
                if cur and d0.textlength(nxt, font=self.f) > maxw:
                    wrapped.append((label, cur, color))
                    label, cur = "", wd
                else:
                    cur = nxt
            wrapped.append((label, cur, color))
        H = pad * 2 + int(20 * k) + lh * len(wrapped)
        img = Image.new("RGBA", (Wp, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, Wp - 1, H - 1), radius=int(6 * k), fill=BG, outline=LINE)
        mins = tr.get("minutes") or 0
        d.text((pad, pad), (tr.get("map") or "?") + ("  · party" if tr.get("party") else ""),
               font=self.f_head, fill=TEXT)
        d.text((Wp - pad, pad), f"{int(mins)} min", font=self.f, fill=MUTED, anchor="ra")
        y = pad + int(20 * k)
        for label, text, color in wrapped:
            if label:
                d.text((pad, y), label, font=self.f_lbl, fill=MUTED)
            d.text((pad + lw, y), text, font=self.f, fill=color)
            ic = icons.get(wrapped.index((label, text, color)))
            if ic is not None:
                x = pad + lw + int(d.textlength(text, font=self.f)) + int(4 * k)
                img.alpha_composite(ic, (min(x, Wp - pad - ic.width), y - int(1 * k)))
            y += lh
        key = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
        out = Image.new("RGB", (Wp, H), key)
        out.paste(img.convert("RGB"), (0, 0), img.getchannel("A").point(
            lambda v: 255 if v > 100 else 0))
        return out

    def _icon(self, key):
        """The potion's picture, 16 px (itemicons.py: downloaded once, if allowed), or None."""
        if not key:
            return None
        if key not in self.icons:
            from components.training import itemicons
            p = itemicons.path(key)
            if p is None:
                return None                         # not there yet (or not allowed)
            try:
                s = int(16 * self.k)
                self.icons[key] = Image.open(p).convert("RGBA").resize((s, s), Image.LANCZOS)
            except OSError:
                return None
        return self.icons[key]

    def _tick(self):
        try:
            now = time.time()
            live = demo_live(now) if self.demo else _read(LIVE)
            tr = live.get("training")
            fresh = now - (live.get("t") or 0) < STALE_S
            show = bool(self.get().get("enabled", False)) and bool(tr) and fresh
            if show:
                from PIL import ImageTk
                self.photo = ImageTk.PhotoImage(self.render(tr))
                self.lbl.configure(image=self.photo)
            if show != self.shown:
                (self.w.deiconify if show else self.w.withdraw)()
                if show:
                    self._apply_lock(self.get().get("locked", False))
                self.shown = show
        except Exception as e:
            print(f"training panel draw failed: {e}")
        self.after_tick = self.root.after(int(1000 / FPS), self._tick)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.w.winfo_x(), e.y_root - self.w.winfo_y()

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if not self.dev:
            self.put(x=self.w.winfo_x(), y=self.w.winfo_y())

    def _menu(self, e):
        m = tk.Menu(self.w, tearoff=0)
        m._vars = []
        for k, label in (("mp", "Price MP with"), ("hp", "Price HP with")):
            sub = tk.Menu(m, tearoff=0)
            v = tk.StringVar(value=self.get().get(k + "_item") or "")
            m._vars.append(v)
            sub.add_radiobutton(label="The one I use most", value="", variable=v,
                                command=lambda k=k: self.put(**{k + "_item": None}))
            sub.add_separator()
            for name in _item_names(k):
                sub.add_radiobutton(label=name, value=name, variable=v,
                                    command=lambda k=k, n=name: self.put(**{k + "_item": n}))
            m.add_cascade(label=label, menu=sub)
        m.add_separator()
        m.add_command(label="Lock (click-through)", command=lambda: self.set_locked(True))
        m.add_command(label="Hide training panel", command=lambda: self.put(enabled=False))
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
            print(f"training panel lock failed: {e}")

    def destroy(self):
        if self.after_tick:
            self.root.after_cancel(self.after_tick)
        self.w.destroy()


def _item_names(k):
    """Shop potions / food for meter k, plus % potions (Elixirs), by name."""
    try:
        from base.paths import asset
        items = json.loads((asset("gamedata/potions_classic.json")).read_text(encoding="utf-8"))["items"]
    except (OSError, ValueError, KeyError):
        return []
    return sorted({it["name"] for it in items.values()
                   if (it.get(k) or it.get(k + "_pct")) and "(Alt)" not in it["name"]
                   and (it.get("shop") or it.get(k + "_pct"))},
                  key=lambda n: next((it.get("shop") or 10 ** 6 for it in items.values()
                                      if it["name"] == n), 0))


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def demo_live(now):
    return {"t": now, "training": {
        "map": "The Forest of Wisdom", "minutes": 23.4,
        "kills": {"Wild Boar": 12, "Fire Boar": 3}, "kills_seen": 15, "kills_total": 50,
        "casts": {"Magic Claw": {"n": 86, "mp": 1462}},
        "cast_cost": {"Magic Claw": {"mp": 17, "mesos": 16.4}},
        "potions": {"Blue Potion": {"n": 5, "mp": 1140, "hp": 0, "mesos": 1100},
                    "Red Potion": {"n": 3, "mp": 0, "hp": 300, "mesos": 150}},
        "mp": {"casts": 1462, "regen": 1400, "potions": 1140}, "mesos": 1250,
        "per_kill": {"mp": 29.2, "casts": 1.72, "hp": 7.1, "mesos": 25.0, "exp": 48},
        "per_hour": {"mesos": 3205, "kills": 128, "exp": 6150},
        "rate": {"exp_hr": 6150, "party_hr": 1400, "early": False, "gains": 140, "active_min": 21.5,
                 "break_min": 2.4, "lump_exp": 0, "with_breaks_hr": 5600},
        "to_level": {"exp_left": 9100, "kills": 190, "minutes": 89,
                     "mp_pots": {"item": "Blue Potion", "no_regen": 25, "with_regen": 1},
                     "hp_pots": {"item": "Red Potion", "no_regen": 12, "with_regen": 6},
                     "mesos": 520}}}


def main():
    from base import lean  # noqa: F401
    demo = "--demo" in sys.argv
    at = next((tuple(int(v) for v in a.split("=", 1)[1].split(",")) for a in sys.argv
               if a.startswith("--at=")), None)
    root = tk.Tk()
    root.withdraw()
    state = {"enabled": True, "locked": False}
    TrainingPanel(root, lambda: state, lambda **kw: state.update(kw), demo=demo, at=at)
    root.mainloop()


if __name__ == "__main__":
    main()
