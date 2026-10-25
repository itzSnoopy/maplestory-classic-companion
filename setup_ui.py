"""First-run setup window: character name, then calibration from the player's own game screen.

Opened by the helper on first run (calibrate.needs_setup()) and from its menu ("Run setup…").
Everything it cuts from the screen stays in the player's data folder (paths.DATA).

Steps: 1 welcome + name · 2 grab the game window, cut the minimap pieces · 3 type what the EXP bar
and level badge show · 4 done. (The character's name tag used to be boxed by hand here; since
2026-10-24 portrait.py takes it from the status bar while you play.)
"""
import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
import tkinter as tk

from PIL import Image, ImageTk

import calibrate
import helper_settings as HS
import paths
from helper_ui import BG, LINE, TEXT, MUTED, GOLD, GREEN, RED

PANEL = "#1d2026"
FIELD = "#24272e"
SEL = "#2b3038"
F = "Segoe UI"


class SetupWindow:
    def __init__(self, app, on_done=None):
        self.app = app
        self.on_done = on_done
        self.k = app.k
        self.cfg = paths.load_config()
        self.frame = None            # captured game frame (PIL)
        self.crops = None
        self._imgs = []              # keep PhotoImages alive

        self.w = w = tk.Toplevel(app.root)
        w.title("Maple Classic Companion setup")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close)
        try:
            from helper_ui import make_icon_image, GREEN
            self._icon = ImageTk.PhotoImage(make_icon_image(GREEN, 64))
            w.iconphoto(False, self._icon)
        except Exception:
            pass
        pos = f"+{app.dev_at[0] + self.px(320)}+{app.dev_at[1]}" if app.dev_at else ""
        w.geometry(f"{self.px(860)}x{self.px(660)}{pos}")
        w.minsize(self.px(760), self.px(600))

        bottom = tk.Frame(w, bg=BG)
        bottom.pack(side="bottom", fill="x", padx=self.px(20), pady=self.px(14))
        self.next_btn = self.button(bottom, "Next", self.next, primary=True)
        self.next_btn.pack(side="right")
        self.back_btn = self.button(bottom, "Back", self.back)
        self.back_btn.pack(side="right", padx=(0, self.px(8)))
        self.step_lbl = tk.Label(bottom, text="", font=(F, 9), fg=MUTED, bg=BG, anchor="w")
        self.step_lbl.pack(side="left")
        tk.Frame(w, bg=LINE, height=1).pack(side="bottom", fill="x")
        self.body = tk.Frame(w, bg=BG)
        self.body.pack(fill="both", expand=True, padx=self.px(24), pady=(self.px(18), self.px(8)))

        self.steps = [self.step_welcome, self.step_capture, self.step_digits, self.step_done]
        self.i = 0
        self.show()
        w.after(50, lambda: (w.lift(), w.focus_force()))

    # ---------------------------------------------------------------- widgets
    def px(self, n):
        return int(round(n * self.k))

    def button(self, parent, text, cmd, primary=False):
        b = tk.Label(parent, text=text, font=(F, 10, "bold" if primary else "normal"),
                     fg="#15171b" if primary else TEXT, bg=GOLD if primary else SEL,
                     padx=self.px(16), pady=self.px(5), cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd() if b.cget("state") != "disabled" else None)
        return b

    def enable(self, b, on):
        b.configure(state="normal" if on else "disabled",
                    cursor="hand2" if on else "arrow")

    def title(self, text, sub=None):
        tk.Label(self.body, text=text, font=(F, 15, "bold"), fg=TEXT, bg=BG,
                 anchor="w").pack(fill="x")
        if sub:
            self.text(sub, muted=True).pack(fill="x", pady=(self.px(4), self.px(14)))

    def text(self, s, muted=False, parent=None, size=10, color=None):
        return tk.Label(parent or self.body, text=s, font=(F, size), fg=color or (MUTED if muted else TEXT),
                        bg=BG, anchor="w", justify="left", wraplength=self.px(780))

    def entry(self, parent, value="", width=30):
        v = tk.StringVar(value=value)
        e = tk.Entry(parent, textvariable=v, width=width, font=(F, 12), bg=FIELD, fg=TEXT,
                     insertbackground=TEXT, relief="flat", highlightthickness=1,
                     highlightbackground=LINE, highlightcolor=GOLD)
        return e, v

    def photo(self, img):
        p = ImageTk.PhotoImage(img)
        self._imgs.append(p)
        return p

    def zoom(self, img, scale):
        return img.resize((img.width * scale, img.height * scale), Image.NEAREST)

    # ---------------------------------------------------------------- navigation
    def show(self):
        for c in self.body.winfo_children():
            c.destroy()
        self._imgs.clear()
        self.step_lbl.configure(text=f"Step {self.i + 1} of {len(self.steps)}")
        self.enable(self.back_btn, 0 < self.i < len(self.steps) - 1)
        self.next_btn.configure(text="Finish" if self.i == len(self.steps) - 1 else "Next")
        self.steps[self.i]()

    def next(self):
        check = getattr(self, f"leave_{self.steps[self.i].__name__[5:]}", None)
        if check and not check():
            return
        if self.i == len(self.steps) - 1:
            return self.finish()
        self.i += 1
        self.show()

    def back(self):
        if self.i > 0:
            self.i -= 1
            self.show()

    def close(self):
        self.w.destroy()

    def finish(self):
        self.w.destroy()
        if self.on_done:
            self.on_done()

    # ---------------------------------------------------------------- 1 welcome
    def step_welcome(self):
        self.title("Welcome",
                   "This app tracks your leveling in MapleStory Classic World: EXP per hour, time to "
                   "the next level, best maps, and a diary of milestones. It only looks at the game "
                   "window, the same way screen recording does. It never reads the game's memory, "
                   "never touches its network traffic, and never presses keys for you.")
        self.text("Setup takes about a minute. First, your character's name:").pack(fill="x")
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(self.px(8), self.px(4)))
        e, self.name_var = self.entry(row, HS.load()["character"].get("name", ""))
        e.pack(side="left")
        e.focus_set()
        self.text("Used for the diary and Discord status. You can change it later in Settings.",
                  muted=True, size=9).pack(fill="x")

    def leave_welcome(self):
        s = HS.load()
        s["character"]["name"] = self.name_var.get().strip()
        HS.save(s)
        return True

    # ---------------------------------------------------------------- 2 capture
    def step_capture(self):
        self.title("Show me your game",
                   "The app learns a few things from your own screen: the minimap, and the digits "
                   "on the EXP bar.")
        for line in ("1.  Start MapleStory and log in with your character.",
                     "2.  Play in a window (windowed mode), and don't minimize it.",
                     "3.  Open the minimap fully (not collapsed) and leave it in the top-left corner.",
                     "4.  Make sure the status bar at the bottom (level, EXP bar) is visible."):
            self.text(line).pack(fill="x", pady=self.px(2))
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(self.px(16), self.px(8)))
        self.button(row, "Capture game window", self.capture, primary=True).pack(side="left")
        self.cap_msg = self.text("", parent=row)
        self.cap_msg.pack(side="left", padx=self.px(12))
        self.cap_preview = tk.Label(self.body, bg=BG)
        self.cap_preview.pack(anchor="w", pady=self.px(8))
        if self.frame is not None:
            self.show_capture_result(self.last_result)

    def capture(self):
        self.cap_msg.configure(text="Capturing…", fg=MUTED)
        self.w.update_idletasks()
        try:
            from sources.wgc import WgcFrames, find_window
        except ImportError as e:
            return self.cap_msg.configure(text=f"Window capture isn't available: {e}", fg=RED)
        if not find_window(self.cfg.get("wgc", {}).get("window_class", "MapleStoryClass")):
            return self.cap_msg.configure(text="MapleStory window not found. Is the game running?",
                                          fg=RED)
        f = WgcFrames(self.cfg)
        try:
            img = f.grab(timeout=3.0)
        finally:
            f.close()
        if max(img.convert("L").getextrema()) < 10:
            return self.cap_msg.configure(
                text="Got a black picture. Is the game minimized or in full screen?", fg=RED)
        self.frame = img
        self.crops = calibrate.hud_crops(img, self.cfg)
        self.last_result = calibrate.cut_templates(img)
        self.show_capture_result(self.last_result)

    def show_capture_result(self, r):
        W, H = self.frame.size
        if r["world"] and r["header"]:
            self.cap_msg.configure(text=f"Got it ({W}x{H}). Minimap found.", fg=GREEN)
        elif r["world"]:
            self.cap_msg.configure(text="The minimap is collapsed. Open it fully and capture again.",
                                   fg=RED)
        else:
            self.cap_msg.configure(text="Minimap not found. Open it, keep it top-left, and capture again.",
                                   fg=RED)
        thumb = self.frame.copy()
        thumb.thumbnail((self.px(560), self.px(320)))
        self.cap_preview.configure(image=self.photo(thumb))

    def leave_capture(self):
        if self.frame is None or not calibrate.templates_ready():
            self.cap_msg.configure(text="Capture the game with the minimap open first.", fg=RED)
            return False
        return True

    # ---------------------------------------------------------------- 3 digits
    def step_digits(self):
        self.title("Read the numbers for me",
                   "Type exactly what these two pieces of your status bar show. This teaches the "
                   "app the game's digits; it learns any missing ones by itself later.")
        known = len(__import__("glyphs").load())
        grid = tk.Frame(self.body, bg=BG)
        grid.pack(fill="x")
        self.text("EXP bar", parent=grid, size=11).grid(row=0, column=0, sticky="w")
        tk.Label(grid, image=self.photo(self.zoom(self.crops["exp"], 5)), bg="#000").grid(
            row=1, column=0, sticky="w", pady=self.px(4))
        e1, self.exp_var = self.entry(grid, width=22)
        e1.grid(row=2, column=0, sticky="w")
        self.text('Like it\'s shown, for example  1,234[56.78%]', parent=grid, muted=True,
                  size=9).grid(row=3, column=0, sticky="w", pady=(self.px(2), self.px(14)))
        self.text("Level", parent=grid, size=11).grid(row=4, column=0, sticky="w")
        tk.Label(grid, image=self.photo(self.zoom(self.crops["level"], 5)), bg="#000").grid(
            row=5, column=0, sticky="w", pady=self.px(4))
        e2, self.lv_var = self.entry(grid, width=6)
        e2.grid(row=6, column=0, sticky="w")
        e1.focus_set()
        self.dig_msg = self.text("")
        self.dig_msg.pack(fill="x", pady=(self.px(14), 0))
        if known >= 3:
            self.dig_msg.configure(text=f"Already learned {known} digit shapes. Type them again to "
                                        "add more, or press Next to keep what's there.", fg=MUTED)

    def leave_digits(self):
        exp, lv = self.exp_var.get().strip(), self.lv_var.get().strip()
        if not exp and not lv and len(__import__("glyphs").load()) >= 3:
            return True
        ok, msg = calibrate.check_typed(self.crops, self.cfg, exp, lv)
        if not ok:
            self.dig_msg.configure(text=msg, fg=RED)
            return False
        try:
            calibrate.learn_typed(self.crops, self.cfg, exp, lv)
        except ValueError as e:
            self.dig_msg.configure(text=f"Couldn't learn from that: {e}", fg=RED)
            return False
        return True

    # ---------------------------------------------------------------- 4 done
    def step_done(self):
        self.title("All set",
                   "The tracker starts when you press Finish. It runs in the background and shows "
                   "up as an icon in the tray (bottom-right, near the clock). The small window shows "
                   "your level, EXP per hour and time to the next level.")
        for line in ("•  Right-click the small window for Settings, the journal and the diary.",
                     "•  Keep the minimap open and the status bar visible while you play.",
                     "•  If numbers look wrong later, run setup again from the right-click menu."):
            self.text(line).pack(fill="x", pady=self.px(2))
        self.text(f"Your data is saved in {paths.DATA}", muted=True, size=9).pack(
            fill="x", pady=(self.px(16), 0))
