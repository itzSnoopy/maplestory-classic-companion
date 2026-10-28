"""diary_popup.py - the diary's pieces inside the helper window (helper_ui.py, same Tk process).

DiaryPopup: when the tracker adds a diary entry (level-up, death, job, first visit, note, random
moment), a small card slides in next to the floating window with the caption, the game tag and the
character close-up, then fades away. Click it to open the diary. It only reads
history/timeline.jsonl; entries that were there when the helper started never pop up.

page(win, frame): the "Diary" page of the helper's Settings window (settings_ui.py). The values
live in helper_settings.json -> "diary"; the tracker reads the moment timing from there on every
check, so changes apply without a restart.

The close-ups are Nexon game imagery: shown locally only.
"""
import time
import tkinter as tk

from components.diary import diary

F = "Segoe UI"
CARD_W = 300
KIND_LABEL = {"levelup": "LEVEL UP", "death": "DEATH", "job": "JOB ADVANCEMENT",
              "first_visit": "NEW PLACE", "first_kill": "FIRST KILL", "note": "NOTE", "moment": "MOMENT"}
KIND_COLOR = {"levelup": "#4cc38a", "death": "#e5534b", "job": "#f2b632", "first_visit": "#5b9bd5",
              "note": "#a68fd8", "moment": "#e58fbf", "first_kill": "#d49a5e"}


def diary_settings(settings):
    return settings.get("diary", {})


# ------------------------------------------------------------------ pop-up card
class DiaryPopup:
    def __init__(self, app):
        self.app = app
        self.sig = self._sig()
        self.seen = {diary.entry_id(e) for e in diary.entries()}
        self.queue = []
        self.card = None
        self.last_check = 0.0

    @staticmethod
    def _sig():
        try:
            st = diary.TIMELINE.stat()
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    def check(self):
        """Called from the helper's refresh loop (about once a second); cheap when nothing changed."""
        now = time.time()
        if now - self.last_check < 2:
            return
        self.last_check = now
        sig = self._sig()
        if sig == self.sig:
            return
        self.sig = sig
        try:
            items = diary.with_context(diary.entries())
        except Exception:
            return
        new = [e for e in items if diary.entry_id(e) not in self.seen]
        self.seen.update(diary.entry_id(e) for e in items)
        if not diary_settings(self.app.settings).get("popup", True):
            return
        self.queue.extend(e for e in new if not e.get("hidden"))
        if self.card is None:
            self._next()

    def test(self):
        """Show the latest entry again (Settings > Diary > Show a test pop-up)."""
        items = [e for e in diary.with_context(diary.entries()) if not e.get("hidden")]
        if items:
            self.queue.append(items[-1])
            if self.card is None:
                self._next()

    def _next(self):
        if not self.queue:
            return
        e = self.queue.pop(0)
        try:
            self.card = Card(self, e)
        except Exception as ex:          # never let the diary break the helper
            print(f"diary pop-up failed: {ex}")
            self.card = None
            self.app.root.after(100, self._next)

    def done(self):
        self.card = None
        self.app.root.after(400, self._next)


class Card:
    def __init__(self, owner, e):
        self.owner, self.e = owner, e
        app = owner.app
        self.k = app.k
        px = self.px
        s = diary_settings(app.settings)
        self.hold_ms = int(max(3, min(30, float(s.get("popup_seconds", 8)))) * 1000)
        text, source = diary.caption_info(e)
        color = KIND_COLOR.get(e["kind"], "#f2b632")

        self.w = w = tk.Toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.0)
        w.configure(bg=color)                                   # 2 px colored frame
        body = tk.Frame(w, bg="#16181d", padx=px(12), pady=px(10))
        body.pack(fill="both", expand=True, padx=px(2), pady=px(2))
        head = tk.Frame(body, bg="#16181d")
        head.pack(fill="x")
        tk.Label(head, text=f"{diary.ICON.get(e['kind'], '•')}  {KIND_LABEL.get(e['kind'], e['kind'].upper())}",
                 font=(F, 8, "bold"), fg=color, bg="#16181d").pack(side="left")
        tk.Label(head, text=diary.clock(_dt(e["time"])), font=(F, 8), fg="#8a8f98",
                 bg="#16181d").pack(side="right")
        tk.Label(body, text=text, font=(F, 11, "bold"), fg="#e8e6e3", bg="#16181d", justify="left",
                 anchor="w", wraplength=px(CARD_W - 28)).pack(fill="x", pady=(px(4), 0))
        meta = " · ".join(x for x in (f"— {source}" if source else "", e.get("map") or "") if x)
        if meta:
            tk.Label(body, text=meta, font=(F, 8, "italic"), fg="#8a8f98", bg="#16181d",
                     anchor="w", justify="left", wraplength=px(CARD_W - 28)).pack(fill="x")
        self.pic = tk.Label(body, bg="#16181d")
        self.pic.pack(pady=(px(6), 0))
        self.img = None
        self._load_picture(tries=6)

        for wid in [w, body, head, self.pic, *body.winfo_children(), *head.winfo_children()]:
            wid.bind("<Button-1>", lambda ev: self.open_diary())
            wid.bind("<Button-3>", lambda ev: self.close())
        self.place()
        self.t0 = time.time()
        self.animate_in(0)

    def px(self, n):
        return int(round(n * self.k))

    def _load_picture(self, tries):
        """The close-up may land a few seconds after the entry (delayed shots): retry a little."""
        chars = [s for s in self.e.get("shots", []) if s.endswith("_char.png")]
        path = diary.ROOT / chars[0] if chars else None
        if path is not None and path.exists():
            try:
                from PIL import Image, ImageTk
                im = Image.open(path).convert("RGB")
                im.thumbnail((self.px(CARD_W - 28), self.px(200)))
                self.img = ImageTk.PhotoImage(im)
                self.pic.configure(image=self.img)
                self.place()
                return
            except Exception:
                pass
        if tries > 0 and chars:
            self.w.after(1000, lambda: self.w.winfo_exists() and self._load_picture(tries - 1))

    def place(self):
        """Beside the floating window: to its left if there's room, else below it."""
        app, w = self.owner.app, self.w
        w.update_idletasks()
        cw, ch = w.winfo_reqwidth(), w.winfo_reqheight()
        r = app.root
        if app.visible and r.winfo_viewable():
            x, y = r.winfo_x() - cw - self.px(10), r.winfo_y()
            if x < self._left_edge():
                x, y = r.winfo_x(), r.winfo_y() + r.winfo_height() + self.px(10)
        else:                                       # helper hidden: bottom-right of the screen
            x = r.winfo_screenwidth() - cw - self.px(24)
            y = r.winfo_screenheight() - ch - self.px(70)
        self.base = (x, y)
        w.geometry(f"{cw}x{ch}+{x}+{y}")

    def _left_edge(self):
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
            self.w.after(self.hold_ms, lambda: self.animate_out(0))

    def animate_out(self, step, steps=15):
        if not self.w.winfo_exists():
            return self.owner.done()
        self.w.attributes("-alpha", 0.96 * (1 - (step + 1) / steps))
        if step + 1 < steps:
            self.w.after(25, lambda: self.animate_out(step + 1, steps))
        else:
            self.close()

    def open_diary(self):
        open_it(self.owner.app)
        self.close()

    def close(self):
        if self.w.winfo_exists():
            self.w.destroy()
            self.owner.done()


def _dt(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso)


# ------------------------------------------------------------------ Settings > Diary page
def page(win, f):
    """Built inside settings_ui.SettingsWindow (uses its widgets and its copy of the settings)."""
    from hub import helper_settings as HS
    px = win.px
    win.s.setdefault("diary", {})
    for k, v in HS.DEFAULTS["diary"].items():
        win.s["diary"].setdefault(k, v)
    win.title(f, "Diary",
              "The diary records level-ups, deaths, job advancements, important new places and "
              "notes, each with a picture. Random moments add a picture and a funny line now and "
              "then while you play.")

    win.check(f, "diary.moments", "Random moments while playing").pack(anchor="w")
    row = tk.Frame(f, bg=win.w["bg"])
    row.pack(anchor="w", pady=(px(4), px(2)), padx=(px(22), 0))
    win.label(row, "Every").pack(side="left")
    _num(win, row, "diary.moment_min", 5, 600).pack(side="left", padx=px(6))
    win.label(row, "to").pack(side="left")
    _num(win, row, "diary.moment_max", 5, 600).pack(side="left", padx=px(6))
    win.label(row, "minutes of play (random in between)").pack(side="left")
    win.label(f, "Skipped when something else went into the diary in the last 10 minutes. The tracker "
                 "picks up changes on its next reading.", muted=True, size=8,
              wrap=px(560)).pack(anchor="w", padx=(px(22), 0), pady=(0, px(12)))

    win.check(f, "diary.popup", "Pop-up next to this window when something goes into the diary").pack(anchor="w")
    row = tk.Frame(f, bg=win.w["bg"])
    row.pack(anchor="w", pady=(px(4), px(12)), padx=(px(22), 0))
    win.label(row, "Show it for").pack(side="left")
    _num(win, row, "diary.popup_seconds", 3, 30).pack(side="left", padx=px(6))
    win.label(row, "seconds").pack(side="left")
    pop = getattr(win.app, "diary_popup", None)
    if pop is not None:
        win.button(row, "Show a test pop-up", pop.test).pack(side="left", padx=(px(16), 0))

    win.form_row(f, "Open the diary",
                 lambda p: win.radios(p, "diary.open_via",
                                      [("localhost", "Through localhost"), ("file", "As a file")]),
                 "Localhost opens http://127.0.0.1 in the browser: no folder paths in the address "
                 "bar, and the page has Edit / Generate buttons for captions and the game filter. "
                 "As a file opens diary.html from your data folder (read-only page).")
    btns = tk.Frame(f, bg=win.w["bg"])
    btns.pack(anchor="w", pady=(px(2), 0))
    win.button(btns, "Open diary", lambda: open_it(win.app, win.s["diary"].get("open_via"))).pack(side="left")
    return f


def open_it(app, via=None):
    """Open the diary the way Settings > Diary says (served from the helper's own process)."""
    try:
        diary.open_diary(via=via, in_process=True)
    except Exception as ex:
        print(f"open diary failed: {ex}")


def _num(win, parent, key, lo, hi):
    """Small whole-number field that keeps the setting an int within [lo, hi]."""
    v = tk.StringVar(value=str(win.get(key)))

    def changed(*a):
        try:
            n = int(float(v.get()))
        except ValueError:
            return
        win.set(key, max(lo, min(hi, n)))
    v.trace_add("write", changed)
    e = tk.Entry(parent, textvariable=v, width=5, font=(F, 10), bg="#24272e", fg="#e8e6e3",
                 insertbackground="#e8e6e3", relief="flat", highlightthickness=1,
                 highlightbackground="#2a2e36", highlightcolor="#f2b632", justify="center")
    win.vars[key] = v
    return e
