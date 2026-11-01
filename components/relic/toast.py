"""toast.py - the small "+ Relic piece 17/48" note over the game: bottom-right corner, 3 s, no sound.
Helper side. Reads relic.json (written by the tracker's component) about once a second."""
import time
import tkinter as tk

from hub import history as H

CHECK_S = 1.0
SHOW_S = 3.0
STALE_S = 15.0          # a toast older than this when first seen is not shown (the helper just started)


class Toaster:
    def __init__(self, shell):
        self.shell = shell
        self.seen = None        # last toast seq handled (None = not yet looked: the first look only syncs)
        self.last_check = 0.0
        self.win = None
        self.until = 0.0

    def tick(self, now=None):
        now = now or time.time()
        if self.win is not None and now >= self.until:
            self._close()
        if now - self.last_check < CHECK_S:
            return
        self.last_check = now
        st = H.read_json(H.ROOT / "relic.json", {})
        t = st.get("toast") if isinstance(st, dict) else None
        seq = t.get("seq") if isinstance(t, dict) else 0
        if self.seen is None:
            self.seen = seq
            return
        if seq == self.seen or not isinstance(t, dict):
            return
        self.seen = seq
        cfg = self.shell.settings("relic")
        if not cfg.get("toast", True) or not cfg.get("enabled", True):
            return
        if now - (t.get("t") or 0) > STALE_S:
            return
        self.show(str(t.get("text") or "+ Relic piece"), now)

    def _close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.win = None

    def show(self, text, now):
        root = self.shell.root
        if root is None:
            return
        if self.win is not None:
            self._close()
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        tk.Label(w, text=text, fg="#f2c35a", bg="#26211b", font=("Segoe UI", max(9, self.shell.px(11)), "bold"),
                 padx=self.shell.px(12), pady=self.shell.px(6), bd=1, relief="solid").pack()
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        at = self.shell.at
        x = at[0] + 20 if at else sw - w.winfo_width() - self.shell.px(24)
        y = at[1] + 10 if at else sh - w.winfo_height() - self.shell.px(80)
        w.geometry(f"+{x}+{y}")
        self.win, self.until = w, now + SHOW_S
