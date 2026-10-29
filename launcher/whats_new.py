"""whats_new.py - "Updated to 0.5.0" card after the app updated itself (information only).

The helper remembers the last version it ran (history/app_version.json). When it starts on a
newer one, a card slides in next to the floating window with the lines from whats_new.json for
every version since then. Click it (or right-click) to close; otherwise it fades after
HOLD_S. Nothing goes to the diary or the journal.

A fresh install (no data yet) doesn't get the card; an install that predates this file (0.4.0)
gets it when it was started by the updater (--after-update).

  python -m launcher.whats_new --demo [--at=X,Y]       show the card for the current version
"""
import json
import time
import tkinter as tk

from base.paths import DATA, asset
from base.version import VERSION

NOTES = asset("launcher/whats_new.json")
SEEN = DATA / "history" / "app_version.json"
F = "Segoe UI"
CARD_W = 320
HOLD_S = 25
COLOR = "#5b9bd5"


def _v(s):
    try:
        return tuple(int(x) for x in str(s).split("."))
    except ValueError:
        return (0,)


def notes_since(last, current=VERSION):
    """[(version, [lines])] newer than `last` up to `current`, newest first."""
    try:
        data = json.loads(NOTES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = [(v, lines) for v, lines in data.items()
           if (last is None or _v(v) > _v(last)) and _v(v) <= _v(current)]
    if last is None:
        out = [x for x in out if x[0] == current]
    return sorted(out, key=lambda x: _v(x[0]), reverse=True)


def check(app, after_update=False):
    """Call once at helper start. Shows the card when the version moved up; remembers it."""
    try:
        last = json.loads(SEEN.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        last = None
    if last == VERSION:
        return None
    try:
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps({"version": VERSION}), encoding="utf-8")
    except OSError:
        pass
    if last is None and not after_update:
        return None                 # fresh install (or first run of this feature): nothing new
    if last is not None and _v(last) >= _v(VERSION):
        return None                 # went back a version: say nothing
    items = notes_since(last)
    if not items:
        return None
    return Card(app, items)


class Card:
    def __init__(self, app, items):
        self.app, self.k = app, app.k
        px = self.px
        self.w = w = tk.Toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.0)
        w.configure(bg=COLOR)                                   # 2 px coloured frame
        body = tk.Frame(w, bg="#16181d", padx=px(12), pady=px(10))
        body.pack(fill="both", expand=True, padx=px(2), pady=px(2))
        head = tk.Frame(body, bg="#16181d")
        head.pack(fill="x")
        tk.Label(head, text=f"⬆  UPDATED TO {VERSION}", font=(F, 8, "bold"), fg=COLOR,
                 bg="#16181d").pack(side="left")
        tk.Label(head, text="click to close", font=(F, 8), fg="#8a8f98",
                 bg="#16181d").pack(side="right")
        for i, (ver, lines) in enumerate(items):
            if len(items) > 1:
                tk.Label(body, text=ver, font=(F, 9, "bold"), fg="#e8e6e3", bg="#16181d",
                         anchor="w").pack(fill="x", pady=(px(6 if i else 4), 0))
            for line in lines:
                tk.Label(body, text=f"•  {line}", font=(F, 10), fg="#e8e6e3", bg="#16181d",
                         justify="left", anchor="w", wraplength=px(CARD_W - 30)
                         ).pack(fill="x", pady=(px(3), 0))
        for wid in [w, body, head, *body.winfo_children(), *head.winfo_children()]:
            wid.bind("<Button-1>", lambda ev: self.close())
            wid.bind("<Button-3>", lambda ev: self.close())
        self.place()
        self.animate_in(0)

    def px(self, n):
        return int(round(n * self.k))

    def place(self):
        """Beside the floating window (left of it if there's room, else below); helper hidden:
        bottom-right of the screen. Same spot as the diary cards."""
        app, w = self.app, self.w
        w.update_idletasks()
        cw, ch = w.winfo_reqwidth(), w.winfo_reqheight()
        r = app.root
        if getattr(app, "visible", True) and r.winfo_viewable():
            x, y = r.winfo_x() - cw - self.px(10), r.winfo_y()
            if x < self._left_edge():
                x, y = r.winfo_x(), r.winfo_y() + r.winfo_height() + self.px(10)
        else:
            x = r.winfo_screenwidth() - cw - self.px(24)
            y = r.winfo_screenheight() - ch - self.px(70)
        self.base = (x, y)
        w.geometry(f"{cw}x{ch}+{x}+{y}")

    @staticmethod
    def _left_edge():
        try:
            import ctypes
            return ctypes.windll.user32.GetSystemMetrics(76)       # virtual desktop left
        except Exception:
            return 0

    def animate_in(self, step, steps=12):
        if not self.w.winfo_exists():
            return
        t = (step + 1) / steps
        ease = 1 - (1 - t) ** 3
        x, y = self.base
        self.w.geometry(f"+{x}+{int(y + self.px(18) * (1 - ease))}")
        self.w.attributes("-alpha", 0.96 * ease)
        if step + 1 < steps:
            self.w.after(16, lambda: self.animate_in(step + 1, steps))
        else:
            self.w.after(HOLD_S * 1000, lambda: self.animate_out(0))

    def animate_out(self, step, steps=15):
        if not self.w.winfo_exists():
            return
        self.w.attributes("-alpha", 0.96 * (1 - (step + 1) / steps))
        if step + 1 < steps:
            self.w.after(25, lambda: self.animate_out(step + 1, steps))
        else:
            self.close()

    def close(self):
        if self.w.winfo_exists():
            self.w.destroy()


if __name__ == "__main__":
    import sys

    class _App:                                  # minimal stand-in for helper_ui.App
        visible = False

        def __init__(self, at):
            self.root = tk.Tk()
            self.root.withdraw()
            self.k = self.root.winfo_fpixels("1i") / 96.0
            if at:
                self.root.geometry(f"1x1+{at[0]}+{at[1]}")

    at = next((tuple(int(v) for v in a.split("=", 1)[1].split(",")) for a in sys.argv
               if a.startswith("--at=")), None)
    a = _App(at)
    c = Card(a, notes_since(None))
    if at:
        c.w.geometry(f"+{at[0]}+{at[1]}")
        c.base = at
    a.root.after((HOLD_S + 2) * 1000, a.root.destroy)
    a.root.mainloop()
