"""Settings window for helper_ui: categories on the left, page on the right.

Opened from the helper's right-click menu or the tray ("Settings…"). Lives in the same
Tk process as the floating window (a Toplevel), so it can show live data in previews.
"""
import copy
import tkinter as tk

from hub import helper_settings as HS
from hub.helper_ui import Snapshot, demo_data
from hub.ui.theme import BG, LINE, TEXT, MUTED, GOLD, GREEN, GREY, RED, PANEL, FIELD, SEL, F


class SettingsWindow:
    """The core pages (General, Character, Internet, About) plus the pages the components add
    (shell.add_settings_page: Discord, Diary), in the order the shell works out."""
    CORE = ("General", "Character", "Internet", "About")

    def __init__(self, app, start="Discord"):
        self.app = app
        self.s = copy.deepcopy(app.settings)
        self.orig_alpha = float(app.root.attributes("-alpha"))
        self.w = w = tk.Toplevel(app.root)
        w.title("Maple helper settings")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.cancel)
        self.k = app.k
        pos = ""
        if app.dev_at:
            pos = f"+{app.dev_at[0] + self.px(320)}+{app.dev_at[1]}"   # "+-2080" = left of primary
        w.geometry(f"{self.px(940)}x{self.px(720)}{pos}")
        w.minsize(self.px(900), self.px(680))
        self.vars = {}
        self.focused_entry = None
        self.shell = app.shell
        self.ticks = []                 # fn() once a second while the window is open (win.on_tick)
        self.build()
        self.show(start)
        self.tick()
        w.after(50, lambda: (w.lift(), w.focus_force()))

    def px(self, n):
        return int(round(n * self.k))

    # ---------------------------------------------------------------- frame
    def build(self):
        w, px = self.w, self.px
        side = tk.Frame(w, bg=PANEL, width=px(160))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Settings", font=(F, 13, "bold"), fg=TEXT, bg=PANEL,
                 anchor="w").pack(fill="x", padx=px(16), pady=(px(16), px(10)))
        self.nav = {}
        self.categories = self.shell.page_names(self.CORE)
        for name in self.categories:
            b = tk.Label(side, text=name, font=(F, 10), fg=MUTED, bg=PANEL, anchor="w",
                         padx=px(16), pady=px(7), cursor="hand2")
            b.pack(fill="x")
            b.bind("<Button-1>", lambda e, n=name: self.show(n))
            self.nav[name] = b

        right = tk.Frame(w, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        bottom = tk.Frame(right, bg=BG)
        bottom.pack(side="bottom", fill="x", padx=px(20), pady=px(14))
        self.button(bottom, "Save", self.save, primary=True).pack(side="right")
        self.button(bottom, "Cancel", self.cancel).pack(side="right", padx=(0, px(8)))
        self.msg = tk.Label(bottom, text="", font=(F, 9), fg=MUTED, bg=BG, anchor="w")
        self.msg.pack(side="left", fill="x", expand=True)
        tk.Frame(right, bg=LINE, height=1).pack(side="bottom", fill="x")

        # the page scrolls when it is taller than the window (Internet grows with every online feature)
        body = tk.Frame(right, bg=BG)
        body.pack(fill="both", expand=True, padx=(px(20), 0), pady=(px(16), px(8)))
        self.canvas = cv = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        self.vbar = tk.Scrollbar(body, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=self.vbar.set)
        cv.pack(side="left", fill="both", expand=True)
        self.page_host = tk.Frame(cv, bg=BG)
        self.page_win = cv.create_window(0, 0, window=self.page_host, anchor="nw")
        self.page_host.bind("<Configure>", lambda e: self.fit_page())
        cv.bind("<Configure>", lambda e: self.fit_page())
        w.bind("<MouseWheel>", self.wheel)       # the Toplevel tag: every widget in this window
        self.pages = {}
        for n in self.categories:
            try:
                build = self.shell.page_builder(n)
                frame = tk.Frame(self.page_host, bg=BG)
                self.pages[n] = (build(self, frame) if build else getattr(self, "page_" + n.lower())(frame))
            except Exception:
                self.app.log_error(f"settings page {n}")
                self.nav.pop(n).destroy()

    def show(self, name):
        for n, f in self.pages.items():
            f.pack_forget()
            self.nav[n].configure(bg=SEL if n == name else PANEL, fg=TEXT if n == name else MUTED)
        self.pages[name].pack(fill="both", expand=True)
        self.canvas.yview_moveto(0)
        self.w.after_idle(self.fit_page)

    def fit_page(self):
        """Page as wide as the canvas, at least as tall; scrollbar only when the page doesn't fit."""
        cv, px = self.canvas, self.px
        cw, ch = cv.winfo_width(), cv.winfo_height()
        need = self.page_host.winfo_reqheight()
        cv.itemconfigure(self.page_win, width=max(1, cw - px(20)), height=max(need, ch))
        cv.configure(scrollregion=(0, 0, cw, max(need, ch)))
        fits = need <= ch
        if fits and self.vbar.winfo_ismapped():
            self.vbar.pack_forget()
            cv.yview_moveto(0)
        elif not fits and not self.vbar.winfo_ismapped():
            self.vbar.pack(side="right", fill="y", before=cv)

    def wheel(self, e):
        if self.page_host.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")

    # ---------------------------------------------------------------- widgets
    def button(self, parent, text, cmd, primary=False):
        b = tk.Label(parent, text=text, font=(F, 10, "bold" if primary else "normal"),
                     fg="#15171b" if primary else TEXT, bg=GOLD if primary else SEL,
                     padx=self.px(16), pady=self.px(5), cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def title(self, parent, text, sub=None):
        tk.Label(parent, text=text, font=(F, 14, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x")
        if sub:
            tk.Label(parent, text=sub, font=(F, 9), fg=MUTED, bg=BG, anchor="w", justify="left",
                     wraplength=self.px(560)).pack(fill="x", pady=(self.px(2), self.px(12)))

    def label(self, parent, text, muted=False, size=9, wrap=None):
        return tk.Label(parent, text=text, font=(F, size), fg=MUTED if muted else TEXT, bg=BG,
                        anchor="w", justify="left", wraplength=wrap or 0)

    def entry(self, parent, key, width=34):
        v = tk.StringVar(value=self.get(key))
        v.trace_add("write", lambda *a: self.set(key, v.get()))
        e = tk.Entry(parent, textvariable=v, width=width, font=(F, 10), bg=FIELD, fg=TEXT,
                     insertbackground=TEXT, relief="flat", highlightthickness=1,
                     highlightbackground=LINE, highlightcolor=GOLD)
        e.bind("<FocusIn>", lambda ev: setattr(self, "focused_entry", e))
        self.vars[key] = v
        return e

    def check(self, parent, key, text):
        v = tk.BooleanVar(value=bool(self.get(key)))
        v.trace_add("write", lambda *a: self.set(key, v.get()))
        c = tk.Checkbutton(parent, text=text, variable=v, font=(F, 10), fg=TEXT, bg=BG,
                           activebackground=BG, activeforeground=TEXT, selectcolor=FIELD,
                           anchor="w", highlightthickness=0, bd=0)
        self.vars[key] = v
        return c

    def radios(self, parent, key, options):
        v = tk.StringVar(value=self.get(key))
        v.trace_add("write", lambda *a: self.set(key, v.get()))
        row = tk.Frame(parent, bg=BG)
        for val, text in options:
            tk.Radiobutton(row, text=text, value=val, variable=v, font=(F, 10), fg=TEXT, bg=BG,
                           activebackground=BG, activeforeground=TEXT, selectcolor=FIELD,
                           highlightthickness=0, bd=0).pack(side="left", padx=(0, self.px(12)))
        self.vars[key] = v
        return row

    def form_row(self, parent, caption, widget_fn, hint=None):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(0, self.px(10)))
        self.label(row, caption, muted=True).pack(anchor="w")
        widget_fn(row).pack(anchor="w", pady=(self.px(2), 0))
        if hint:
            self.label(row, hint, muted=True, size=8, wrap=self.px(320)).pack(anchor="w")
        return row

    def num(self, parent, key, lo, hi):
        """Small whole-number field that keeps the setting an int within [lo, hi]."""
        v = tk.StringVar(value=str(self.get(key)))

        def changed(*a):
            try:
                n = int(float(v.get()))
            except ValueError:
                return
            self.set(key, max(lo, min(hi, n)))
        v.trace_add("write", changed)
        e = tk.Entry(parent, textvariable=v, width=5, font=(F, 10), bg=FIELD, fg=TEXT,
                     insertbackground=TEXT, relief="flat", highlightthickness=1,
                     highlightbackground=LINE, highlightcolor=GOLD, justify="center")
        self.vars[key] = v
        return e

    def on_tick(self, fn):
        self.ticks.append(fn)

    def get(self, key):
        node = self.s
        for p in key.split("."):
            node = node[p]
        return node

    def set(self, key, value):
        *path, last = key.split(".")
        node = self.s
        for p in path:
            node = node[p]
        node[last] = value

    # ---------------------------------------------------------------- pages
    def page_general(self, f):
        self.title(f, "General", "How the floating window behaves, and how often the tracker "
                                 "looks at the game.")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(0, self.px(12)))
        self.label(row, "Window opacity", muted=True).pack(anchor="w")
        self.alpha = tk.DoubleVar(value=round(self.orig_alpha * 100))
        tk.Scale(row, from_=40, to=100, orient="horizontal", variable=self.alpha, length=self.px(300),
                 bg=SEL, fg=TEXT, troughcolor=FIELD, highlightthickness=0, bd=0, width=self.px(10),
                 sliderlength=self.px(22), activebackground=GOLD, sliderrelief="flat",
                 command=lambda v: self.app.root.attributes("-alpha", float(v) / 100)).pack(anchor="w")
        self.topmost = tk.BooleanVar(value=self.app.topmost)
        self.details = tk.BooleanVar(value=self.app.details)
        for var, text in ((self.topmost, "Always on top"), (self.details, "Show details section")):
            tk.Checkbutton(f, text=text, variable=var, font=(F, 10), fg=TEXT, bg=BG,
                           activebackground=BG, activeforeground=TEXT, selectcolor=FIELD,
                           anchor="w", highlightthickness=0, bd=0).pack(anchor="w", pady=(0, self.px(6)))
        for build in self.shell.extras_for("General"):      # skill bar, training and party panels
            try:
                build(self, f)
            except Exception:
                self.app.log_error("settings General extra")
        tk.Frame(f, bg=BG, height=self.px(10)).pack()
        self.capture_row(f)
        self.map_check_row(f)
        self.regen_check_row(f)
        return f

    def capture_row(self, f):
        """Slider: screen checks per second (capture.py), with the CPU it costs on this PC."""
        capture = self.app.services.capture
        self.s.setdefault("capture", {}).setdefault("fps", HS.DEFAULTS["capture"]["fps"])
        self.label(f, "Screen checks per second (while playing)", muted=True).pack(anchor="w")
        v = tk.IntVar(value=capture.clamp(self.s["capture"]["fps"]))
        note = self.label(f, "", muted=True, size=8, wrap=self.px(560))

        def changed(*a):
            n = capture.clamp(v.get())
            self.s["capture"]["fps"] = n
            note.configure(text=capture.describe(n))
        tk.Scale(f, from_=capture.MIN_FPS, to=capture.MAX_FPS, orient="horizontal", variable=v,
                 length=self.px(300), bg=SEL, fg=TEXT, troughcolor=FIELD, highlightthickness=0,
                 bd=0, width=self.px(10), sliderlength=self.px(22), activebackground=GOLD,
                 sliderrelief="flat").pack(anchor="w")
        v.trace_add("write", changed)
        note.pack(anchor="w")
        self.label(f, "Higher catches short moments sooner and leaves room for more readings later. "
                      "Up to 5 for now (the tracker measures what's needed; see perf.py). EXP readings "
                      "keep their own pace. The tracker follows a change within a second.",
                   muted=True, size=8, wrap=self.px(560)).pack(anchor="w", pady=(0, self.px(10)))
        self.vars["capture.fps"] = v
        changed()

    def page_internet(self, f):
        """Master switch + one toggle per online feature (internet.FEATURES). Feature toggles are
        greyed out while the master switch is off."""
        from base import internet
        self.s.setdefault("internet", {})
        self.s["internet"].setdefault("allowed", HS.DEFAULTS["internet"]["allowed"])
        feats = self.s["internet"].setdefault("features", {})
        for n in internet.FEATURES:
            feats.setdefault(n, HS.DEFAULTS["internet"]["features"].get(n, True))
        self.title(f, "Internet", "What the app may connect to online. Reading the game never "
                                  "uses the internet: the app only looks at the game window.")
        self.check(f, "internet.allowed", "Allow internet connection").pack(anchor="w")
        self.label(f, "Off: the app makes no internet connections at all. Every feature below "
                      "is then off too.", muted=True, size=8, wrap=self.px(560)).pack(
            anchor="w", padx=(self.px(22), 0), pady=(0, self.px(12)))
        boxes = []
        for name, spec in internet.FEATURES.items():
            box = tk.Frame(f, bg=BG)
            box.pack(fill="x", padx=(self.px(22), 0), pady=(0, self.px(10)))
            c = self.check(box, f"internet.features.{name}", spec["label"])
            c.pack(anchor="w")
            self.label(box, f"{spec['what']} Connects to: {', '.join(spec['hosts'])}.",
                       muted=True, size=8, wrap=self.px(540)).pack(anchor="w", padx=(self.px(22), 0))
            extra = getattr(self, f"internet_{name}_extra", None)
            if extra:
                extra(box)
            for build in self.shell.extras_for(f"internet.{name}"):      # a component's own fields
                try:
                    build(self, box)
                except Exception:
                    self.app.log_error(f"settings Internet extra {name}")
            boxes.append(box)

        def sync(*a):
            on = bool(self.vars["internet.allowed"].get())
            for b in boxes:
                for wdg in b.winfo_children() + [g for c in b.winfo_children() for g in c.winfo_children()]:
                    try:
                        wdg.configure(state="normal" if on else "disabled")
                        if isinstance(wdg, tk.Entry):       # keep the dark field when greyed out
                            wdg.configure(disabledbackground=FIELD, disabledforeground=MUTED)
                    except tk.TclError:
                        pass
        self.vars["internet.allowed"].trace_add("write", sync)
        sync()
        return f

    def internet_updates_extra(self, f):
        """Under "Automatic updates" on the Internet page: how often, and what's happening."""
        self.s.setdefault("updates", {})
        for k, v in HS.DEFAULTS["updates"].items():
            self.s["updates"].setdefault(k, v)
        self.s["updates"].pop("enabled", None)       # replaced by internet.features.updates
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w", pady=(self.px(4), self.px(2)), padx=(self.px(22), 0))
        self.label(row, "Check every").pack(side="left")
        self.num(row, "updates.min_s", 30, 3600).pack(side="left", padx=self.px(6))
        self.label(row, "to").pack(side="left")
        self.num(row, "updates.max_s", 30, 3600).pack(side="left", padx=self.px(6))
        self.label(row, "seconds").pack(side="left")
        up = getattr(self.app, "updater", None)
        ready = getattr(self.app, "update_ready", None)
        if ready:
            note = (f"Version {'.'.join(map(str, ready))} is installed and starts when you're not "
                    f"playing (or now: tray menu > Restart to update).")
        elif up is None:
            note = "Off while running from source (the packaged app updates itself)."
        else:
            note = ("Checks GitHub for a new release (soon after start, then less often), "
                    "downloads it in the background and switches over when you're not playing. "
                    "Your data is kept.")
            if up.last_error:
                note += f" Last check failed: {up.last_error}"
        self.label(f, note, muted=True, size=8, wrap=self.px(540)).pack(
            anchor="w", padx=(self.px(22), 0))

    def map_check_row(self, f):
        self.s.setdefault("map_check", {})
        for k, v in HS.DEFAULTS["map_check"].items():
            self.s["map_check"].setdefault(k, v)
        mc = self.s["map_check"]
        self.label(f, "Map check", muted=True).pack(anchor="w")
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w", pady=(self.px(2), self.px(2)))
        self.label(row, "Every").pack(side="left")
        self.num(row, "map_check.min_s", 1, 60).pack(side="left", padx=self.px(6))
        self.label(row, "to").pack(side="left")
        self.num(row, "map_check.max_s", 1, 120).pack(side="left", padx=self.px(6))
        self.label(row, "seconds").pack(side="left")
        self.label(f, f"Fast right after you enter a map, then slower the longer you stay "
                      f"(+{mc['step_s']:g} s for every {mc['every_s']:g} s in the map), since long "
                      f"grinds rarely change maps. The tracker picks up changes on its next check.",
                   muted=True, size=8, wrap=self.px(560)).pack(anchor="w", pady=(0, self.px(10)))

    def regen_check_row(self, f):
        """Stickiness of the HP/MP regen clock's checks (vitals.Meter, helper_settings
        regen_check), like the map check: often after a map change, rarer while on time."""
        self.s.setdefault("regen_check", {})
        for k, v in HS.DEFAULTS["regen_check"].items():
            self.s["regen_check"].setdefault(k, v)
        rc = self.s["regen_check"]
        self.label(f, "HP/MP regen check", muted=True).pack(anchor="w")
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w", pady=(self.px(2), self.px(2)))
        self.label(row, "Every").pack(side="left")
        self.num(row, "regen_check.min_ticks", 1, 12).pack(side="left", padx=self.px(6))
        self.label(row, "to").pack(side="left")
        self.num(row, "regen_check.max_ticks", 1, 30).pack(side="left", padx=self.px(6))
        self.label(row, "ticks (a tick is 10 s)").pack(side="left")
        self.label(f, f"Regen comes on your own 10-second clock, which only moves when you change "
                      f"maps. After a map or channel change the tracker looks closely at your HP/MP "
                      f"once to catch a tick, then only checks the clock now and then: every tick at "
                      f"first, one tick less often for every {rc['step']:g} checks on time.",
                   muted=True, size=8, wrap=self.px(560)).pack(anchor="w", pady=(0, self.px(10)))

    def page_character(self, f):
        self.title(f, "Character",
                   "Used by Discord and future features. The tracker reads your job from the status "
                   "bar when it starts and while a job advancement is possible. Channel isn't shown "
                   "on screen, so type it (leave empty to hide it).")
        self.form_row(f, "Character name", lambda p: self.entry(p, "character.name", 28))
        self.check(f, "character.job_auto", "Read job from the screen").pack(anchor="w")
        self.job_note = self.label(f, "", muted=True, size=8, wrap=self.px(520))
        self.job_note.pack(anchor="w", pady=(0, self.px(10)))
        self.form_row(f, "Job (used when not reading from screen, or before the first read)",
                      lambda p: self.entry(p, "character.job", 28))
        self.form_row(f, "Channel", lambda p: self.entry(p, "character.channel", 8))
        return f

    def page_about(self, f):
        from base.paths import DATA
        from base.version import VERSION
        self.title(f, "About")
        for line in (
            f"Maple Classic Companion {VERSION}: leveling tracker for MapleStory Classic World.",
            "It only looks at the game window. It never reads the game's memory, never touches its "
            "network traffic, and never presses keys.",
            f"Your data and settings: {DATA}",
            "Discord gets text only, through the Discord app on this PC.",
            "MapleStory is a trademark of Nexon. This app is not affiliated with Nexon.",
        ):
            self.label(f, line, wrap=self.px(560)).pack(anchor="w", pady=(0, self.px(6)))
        return f

    # ---------------------------------------------------------------- behavior
    def snapshot(self):
        snap = getattr(self.app, "last_snap", None)
        if snap is None or snap.level is None:      # nothing tracked yet: show sample data
            st, dg = demo_data()
            snap = Snapshot(st, dg, True)
        return snap

    def tick(self):
        if not self.w.winfo_exists():
            return
        try:
            self.update_character_note()
        except Exception:
            pass
        for fn in self.ticks:           # the components' live previews
            fn()
        self.w.after(1000, self.tick)

    def update_character_note(self):
        read = getattr(self.app.last_snap, "job", None) if self.app.last_snap else None
        if self.s["character"].get("job_auto"):
            self.job_note.configure(text=f"Read from screen: {read}" if read else
                                    "Not read yet (the tracker reads it on its first frame in game).")
        else:
            self.job_note.configure(text="Off: using the job typed below.")

    def save(self):
        for fn in self.shell.save_hooks:        # the components keep or tidy their own settings
            fn(self)
        self.s["character"]["channel"] = str(self.s["character"]["channel"]).strip()
        try:
            HS.save(self.s)
        except Exception as e:
            self.msg.configure(text=f"Couldn't save: {e}", fg=RED)
            return
        self.app.settings = copy.deepcopy(self.s)
        self.app.root.attributes("-alpha", self.alpha.get() / 100)
        if self.topmost.get() != self.app.topmost:
            self.app.toggle_topmost()
        if self.details.get() != self.app.details:
            self.app.toggle_details()
        self.app.save_ui()
        self.close()

    def cancel(self):
        self.app.root.attributes("-alpha", self.orig_alpha)
        self.close()

    def close(self):
        self.app.settings_win = None
        self.w.destroy()
