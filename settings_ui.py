"""Settings window for helper_ui: categories on the left, page on the right.

Opened from the helper's right-click menu or the tray ("Settings…"). Lives in the same
Tk process as the floating window (a Toplevel), so it can show live data in previews.
"""
import copy
import time
import tkinter as tk

import helper_settings as HS
from helper_ui import (BG, LINE, TEXT, MUTED, GOLD, GREEN, GREY, RED, Snapshot, demo_data,
                       make_icon_image, fmt_dur)

PANEL = "#1d2026"
FIELD = "#24272e"
SEL = "#2b3038"
F = "Segoe UI"

# Discord look for the preview card
DC_BG = "#232428"
DC_TEXT = "#f2f3f5"
DC_MUTED = "#b5bac1"
DC_GREEN = "#23a55a"


class SettingsWindow:
    CATEGORIES = ("General", "Character", "Discord", "Diary", "Internet", "About")

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
        self.preview_mode = tk.StringVar(value="playing")
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
        for name in self.CATEGORIES:
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

        self.page_host = tk.Frame(right, bg=BG)
        self.page_host.pack(fill="both", expand=True, padx=px(20), pady=(px(16), px(8)))
        self.pages = {n: getattr(self, "page_" + n.lower())(tk.Frame(self.page_host, bg=BG))
                      for n in self.CATEGORIES}

    def show(self, name):
        for n, f in self.pages.items():
            f.pack_forget()
            self.nav[n].configure(bg=SEL if n == name else PANEL, fg=TEXT if n == name else MUTED)
        self.pages[name].pack(fill="both", expand=True)

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
        self.s.setdefault("skillbar", copy.deepcopy(HS.DEFAULTS["skillbar"]))
        self.check(f, "skillbar.enabled", "Skill bar over the game (HP/MP regen, casts left)"
                   ).pack(anchor="w", pady=(0, self.px(2)))
        self.label(f, "Drag it anywhere; right-click to lock it (clicks then go to the game). "
                      "Unlock from the tray menu.", muted=True, wrap=self.px(380)).pack(anchor="w", pady=(0, self.px(6)))
        tk.Frame(f, bg=BG, height=self.px(10)).pack()
        self.capture_row(f)
        self.map_check_row(f)
        return f

    def capture_row(self, f):
        """Slider: screen checks per second (capture.py), with the CPU it costs on this PC."""
        import capture
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
        import internet
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
        from diary_popup import _num
        self.s.setdefault("updates", {})
        for k, v in HS.DEFAULTS["updates"].items():
            self.s["updates"].setdefault(k, v)
        self.s["updates"].pop("enabled", None)       # replaced by internet.features.updates
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w", pady=(self.px(4), self.px(2)), padx=(self.px(22), 0))
        self.label(row, "Check every").pack(side="left")
        _num(self, row, "updates.min_s", 30, 3600).pack(side="left", padx=self.px(6))
        self.label(row, "to").pack(side="left")
        _num(self, row, "updates.max_s", 30, 3600).pack(side="left", padx=self.px(6))
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
        from diary_popup import _num
        self.s.setdefault("map_check", {})
        for k, v in HS.DEFAULTS["map_check"].items():
            self.s["map_check"].setdefault(k, v)
        mc = self.s["map_check"]
        self.label(f, "Map check", muted=True).pack(anchor="w")
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w", pady=(self.px(2), self.px(2)))
        self.label(row, "Every").pack(side="left")
        _num(self, row, "map_check.min_s", 1, 60).pack(side="left", padx=self.px(6))
        self.label(row, "to").pack(side="left")
        _num(self, row, "map_check.max_s", 1, 120).pack(side="left", padx=self.px(6))
        self.label(row, "seconds").pack(side="left")
        self.label(f, f"Fast right after you enter a map, then slower the longer you stay "
                      f"(+{mc['step_s']:g} s for every {mc['every_s']:g} s in the map), since long "
                      f"grinds rarely change maps. The tracker picks up changes on its next check.",
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

    def page_discord(self, f):
        px = self.px
        self.title(f, "Discord status",
                   "Shows what you're doing on your Discord profile (Rich Presence). Text only: "
                   "no screenshots or game files are sent. Review the preview before turning it on.")
        cols = tk.Frame(f, bg=BG)
        cols.pack(fill="both", expand=True)
        left = tk.Frame(cols, bg=BG)
        left.pack(side="left", fill="y")
        right = tk.Frame(cols, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(px(24), 0))

        en = self.check(left, "discord.enabled", "Show on Discord")
        en.pack(anchor="w")
        self.en_note = self.label(left, "", muted=True, size=8, wrap=px(320))
        self.en_note.pack(anchor="w", pady=(0, px(8)))
        self.form_row(left, "Application ID", self.app_id_row,
                      "MapleStory's own ID adds your lines to the \"Playing MapleStory\" card "
                      "Discord already shows, with the game's icon. For a custom name and icon, "
                      "create an app at discord.com/developers and paste its Application ID.")
        self.form_row(left, "Line 1", lambda p: self.entry(p, "discord.details"))
        self.form_row(left, "Line 2", lambda p: self.entry(p, "discord.state"))
        self.form_row(left, "Icon hover text", lambda p: self.entry(p, "discord.large_text"))

        chips = tk.Frame(left, bg=BG)
        chips.pack(anchor="w", pady=(0, px(10)))
        self.label(chips, "Insert:", muted=True, size=8).pack(side="left", padx=(0, px(4)))
        for tok, desc in HS.PLACEHOLDERS.items():
            c = tk.Label(chips, text=tok, font=("Consolas", 8), fg=GOLD, bg=FIELD, padx=px(4),
                         cursor="hand2")
            c.pack(side="left", padx=(0, px(3)))
            c.bind("<Button-1>", lambda e, t=tok: self.insert_token(t))

        self.form_row(left, "Show", self.show_toggles)
        self.form_row(left, "EXP % style", lambda p: self.radios(
            p, "discord.pct_style", [(k, v) for k, v in HS.PCT_STYLES.items()]))
        self.check(left, "discord.show_elapsed", "Show elapsed play time").pack(anchor="w")
        self.form_row(left, "When you're not playing", lambda p: self.radios(
            p, "discord.when_not_playing", [("clear", "Hide status"), ("idle", 'Show "Idle"')]))

        # preview
        self.label(right, "PREVIEW", muted=True, size=8).pack(anchor="w")
        self.radios_preview(right).pack(anchor="w", pady=(px(2), px(6)))
        card = tk.Frame(right, bg=DC_BG, padx=px(14), pady=px(12))
        card.pack(anchor="nw", fill="x")
        self.pv_head = tk.Label(card, text="PLAYING A GAME", font=(F, 8, "bold"), fg=DC_MUTED,
                                bg=DC_BG, anchor="w")
        self.pv_head.pack(fill="x", pady=(0, px(8)))
        row = tk.Frame(card, bg=DC_BG)
        row.pack(fill="x")
        try:
            from PIL import ImageTk
            img = make_icon_image(GREEN, 256).resize((px(60), px(60)))
            self.pv_img = ImageTk.PhotoImage(img)
            self.pv_icon = tk.Label(row, image=self.pv_img, bg=DC_BG)
            self.pv_icon.pack(side="left", anchor="n")
        except Exception:
            pass
        txt = tk.Frame(row, bg=DC_BG)
        txt.pack(side="left", padx=(px(10), 0), anchor="n")
        self.pv = {}
        for key, font, color in (("app", (F, 10, "bold"), DC_TEXT), ("details", (F, 9), DC_TEXT),
                                 ("state", (F, 9), DC_TEXT), ("time", (F, 9), DC_GREEN)):
            l = tk.Label(txt, text="", font=font, fg=color, bg=DC_BG, anchor="w", justify="left",
                         wraplength=px(220))
            l.pack(anchor="w")
            self.pv[key] = l
        self.pv_hover = self.label(right, "", muted=True, size=8, wrap=px(300))
        self.pv_hover.pack(anchor="w", pady=(px(8), 0))
        self.pv_raw = tk.Label(right, text="", font=("Consolas", 8), fg=MUTED, bg=BG, anchor="w",
                               justify="left", wraplength=px(300))
        self.pv_raw.pack(anchor="w", pady=(px(8), 0))
        self.pv_status = self.label(right, "", muted=True, size=8, wrap=px(300))
        self.pv_status.pack(anchor="w", pady=(px(8), 0))
        return f

    def app_id_row(self, parent):
        row = tk.Frame(parent, bg=BG)
        self.entry(row, "discord.client_id", 22).pack(side="left")
        b = self.button(row, "Use MapleStory's", lambda: self.vars["discord.client_id"].set(
            HS.MAPLESTORY_APP_ID))
        b.configure(padx=self.px(8), pady=self.px(2), font=(F, 9))
        b.pack(side="left", padx=(self.px(6), 0))
        return row

    def show_toggles(self, parent):
        row = tk.Frame(parent, bg=BG)
        for key, text in HS.SHOW_FIELDS:
            self.check(row, f"discord.show.{key}", text).pack(side="left", padx=(0, self.px(10)))
        return row

    def radios_preview(self, parent):
        row = tk.Frame(parent, bg=BG)
        for val, text in (("playing", "Playing"), ("not", "Not playing")):
            tk.Radiobutton(row, text=text, value=val, variable=self.preview_mode, font=(F, 9),
                           fg=TEXT, bg=BG, activebackground=BG, activeforeground=TEXT,
                           selectcolor=FIELD, highlightthickness=0, bd=0).pack(side="left",
                                                                               padx=(0, self.px(10)))
        return row

    def page_diary(self, f):
        import diary_popup           # the diary component builds its own page
        return diary_popup.page(self, f)

    def page_about(self, f):
        from paths import DATA
        from version import VERSION
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
    def insert_token(self, tok):
        e = self.focused_entry
        if e is not None and e.winfo_exists():
            e.insert("insert", tok)
            e.focus_set()

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
            self.update_preview()
        except Exception as e:
            self.pv_status.configure(text=f"preview error: {e}", fg=RED)
        self.w.after(1000, self.tick)

    def update_preview(self):
        d = self.s["discord"]
        snap = self.snapshot()
        if self.preview_mode.get() == "not":
            snap = copy.copy(snap)
            snap.state = "idle"
        act = HS.build_activity(snap, self.s)
        game_app = HS.uses_game_app(d)
        self.pv["app"].configure(text="MapleStory" if game_app else
                                 (d.get("app_name") or "MapleStory Classic"))
        img = d.get("large_image") and not game_app   # game app: Discord shows the game's icon
        if hasattr(self, "pv_icon"):
            self.pv_icon.configure(image=self.pv_img if img else "", width=0 if img else 1)
        if act is None:
            self.pv_head.configure(text="(NO STATUS SHOWN)")
            for k in ("details", "state", "time"):
                self.pv[k].configure(text="")
            self.pv["app"].configure(text="Nothing is shown while you're not playing.")
            self.pv_hover.configure(text="")
        else:
            self.pv_head.configure(text="PLAYING A GAME")
            self.pv["details"].configure(text=act.get("details", ""))
            self.pv["state"].configure(text=act.get("state", ""))
            ts = act.get("timestamps", {}).get("start")
            self.pv["time"].configure(text=f"{fmt_dur(time.time() - ts)} elapsed" if ts else "")
            hover = act.get("assets", {}).get("large_text")
            self.pv_hover.configure(text=f'Icon hover: "{hover}"' if hover else "")
        if act:
            lines = ["Sent to Discord:",
                     f"  line 1: {act.get('details', '-')}",
                     f"  line 2: {act.get('state', '-')}",
                     f"  hover:  {act.get('assets', {}).get('large_text', '-')}",
                     f"  timer:  {'yes' if act.get('timestamps') else 'no'}"]
        else:
            lines = ["Sent to Discord:", "  (clear status)"]
        self.pv_raw.configure(text="\n".join(lines))

        cid = (d.get("client_id") or "").strip()
        if d.get("enabled") and not cid.isdigit():
            self.en_note.configure(text="Needs an Application ID before it can connect.", fg=GOLD)
        elif d.get("enabled"):
            self.en_note.configure(text="Will turn on when you press Save.", fg=GOLD)
        else:
            self.en_note.configure(text="Off. Nothing is sent to Discord.", fg=MUTED)
        read = getattr(self.app.last_snap, "job", None) if self.app.last_snap else None
        if self.s["character"].get("job_auto"):
            self.job_note.configure(text=f"Read from screen: {read}" if read else
                                    "Not read yet (the tracker reads it on its first frame in game).")
        else:
            self.job_note.configure(text="Off: using the job typed below.")
        status = getattr(getattr(self.app, "presence", None), "status", "off")
        self.pv_status.configure(text=f"Connection: {status}", fg=MUTED)

    def save(self):
        # the skill bar may have been moved / locked while this window was open: keep that
        bar = (self.app.settings.get("skillbar") or {})
        self.s.setdefault("skillbar", {}).update({k: bar[k] for k in ("x", "y", "locked") if k in bar})
        self.s["character"]["channel"] = str(self.s["character"]["channel"]).strip()
        self.s["discord"]["client_id"] = str(self.s["discord"]["client_id"]).strip()
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
