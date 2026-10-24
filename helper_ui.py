"""Floating info window + tray icon for the Maple EXP tracker.

Runs as its own process next to tracker.py and only *reads* its outputs
(status.json, digest.json). It never touches the game.

    pythonw helper_ui.py          normal start (start_helper.cmd does this)
    python  helper_ui.py --demo   show fake data, for testing the layout

Window: drag to move, double-click to show/hide details, right-click for the menu,
the x hides it to the tray. Tray: left-click shows/hides the window.
"""
import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path

from paths import APP, DATA as ROOT, self_command
STATUS = ROOT / "status.json"
DIGEST = ROOT / "digest.json"
JOURNAL = ROOT / "journal.md"
HISTORY = ROOT / "history"
TRACKER_LOCK = HISTORY / "tracker.lock"
HELPER_LOCK = HISTORY / "helper.lock"
UI_STATE = HISTORY / "ui_state.json"
STOP_FILE = ROOT / "stop"

QUIET_RESTART_S = 60         # an update installs after this long without play
STALE_AFTER_S = 120          # "playing" but no update for this long = tracker stuck or gone

# colors
BG = "#16181d"
LINE = "#2a2e36"
TEXT = "#e8e6e3"
MUTED = "#8a8f98"
GOLD = "#f2b632"
GREEN = "#4cc38a"
AMBER = "#e0a43a"
RED = "#e5534b"
GREY = "#6b7079"
STATE_COLOR = {"playing": GREEN, "idle": AMBER, "waiting_for_source": AMBER,
               "stale": RED, "stopped": GREY}
STATE_LABEL = {"playing": "Playing", "idle": "Idle",
               "waiting_for_source": "Waiting for data source",
               "stale": "No updates", "stopped": "Tracker stopped"}


# ------------------------------------------------------------------ helpers
def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, data):
    try:
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def try_lock(path):
    """Non-blocking lock on byte 0 (same scheme as tracker.py). Returns the open file or None."""
    import msvcrt
    HISTORY.mkdir(exist_ok=True)
    f = open(path, "a+")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


def tracker_running():
    """True if tracker.py holds its lock. We only hold the lock for a moment if it's free."""
    if not TRACKER_LOCK.exists():
        return False
    import msvcrt
    f = try_lock(TRACKER_LOCK)
    if f is None:
        return True
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        f.close()
    return False


def fmt_dur(secs):
    if secs is None:
        return "—"
    secs = max(0, int(secs))
    if secs < 60:
        return "<1m"
    h, m = divmod(secs // 60, 60)
    if h >= 48:
        return f"{h // 24}d {h % 24}h"
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_int(n):
    return f"{n:,}" if isinstance(n, (int, float)) else "—"


def parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def parse_secs(s):
    """'1h05m' / '9m' / '30s' (history.fmt_secs) -> seconds."""
    import re
    m = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?", (s or "").strip())
    if not s or not m or not any(m.groups()):
        return None
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se


def clock(dt):
    return dt.strftime("%H:%M") if dt else ""


# ------------------------------------------------------------------ data model
class Snapshot:
    """Everything the window shows, worked out from status.json + digest.json."""

    def __init__(self, status, digest, running, now=None):
        now = now or datetime.now()
        st = status or {}
        self.st, self.dg = st, digest or {}
        self.updated = parse_time(st.get("updated"))
        age = (now - self.updated).total_seconds() if self.updated else None
        state = st.get("state") or "stopped"
        if not running:
            state = "stopped"
        elif state == "playing" and (age is None or age > STALE_AFTER_S):
            state = "stale"
        self.state, self.age = state, age
        self.reason = st.get("idle_reason")
        self.level = st.get("level")
        self.pct = st.get("pct")
        self.cur, self.need = st.get("exp_current"), st.get("exp_needed")
        self.left = st.get("exp_to_level")
        self.rate = st.get("exp_per_hour_recent")
        self.map = st.get("map")
        self.job = st.get("job")
        self.map_rate = st.get("map_exp_per_hour")
        self.session = st.get("session_active")
        self.lvups = st.get("level_ups_this_session") or 0
        self.gained = st.get("exp_gained_session")
        # session start (for Discord's elapsed timer): last update minus active session time,
        # rounded to the minute so it doesn't jitter between frames
        secs = parse_secs(self.session)
        self.session_start = None
        if secs is not None and self.updated:
            self.session_start = (self.updated.timestamp() - secs) // 60 * 60

        # live ETA: count down from the last update instead of freezing between frames
        self.eta_s = None
        if self.left is not None and self.rate:
            self.eta_s = self.left / self.rate * 3600
            if state == "playing" and age:
                self.eta_s = max(0, self.eta_s - age)
        self.eta_at = now + timedelta(seconds=self.eta_s) if self.eta_s is not None else None

        # projection: cumulative times for the next few levels at the current rate
        self.proj = []
        proj = (self.dg.get("projection") or {}).get("levels") or []
        rate = self.rate or (self.dg.get("projection") or {}).get("basis_exp_per_hour")
        if rate and proj and self.level:
            total = 0.0
            for p in proj:
                if p.get("to_lv") == self.level + 1 and self.eta_s is not None:
                    total = self.eta_s
                else:
                    total += (p.get("exp") or 0) / rate * 3600
                self.proj.append((p.get("to_lv"), total, now + timedelta(seconds=total)))

        today = now.strftime("%Y-%m-%d")
        self.today = next((d for d in self.dg.get("last_7_days", []) if d.get("date") == today), None)

    def tooltip(self):
        if self.level is None:
            return "Maple helper: " + STATE_LABEL.get(self.state, self.state)
        t = f"Lv {self.level}  {self.pct:.2f}%" if self.pct is not None else f"Lv {self.level}"
        if self.state == "playing" and self.eta_s is not None:
            t += f"  ·  Lv {self.level + 1} in ~{fmt_dur(self.eta_s)}"
        else:
            t += f"  ·  {STATE_LABEL.get(self.state, self.state)}"
        return t[:120]


def demo_data():
    now = datetime.now()
    status = {"state": "playing", "updated": (now - timedelta(seconds=8)).isoformat(timespec="seconds"),
              "level": 7, "pct": 31.25, "exp_current": 175, "job": "Beginner", "exp_needed": 560, "exp_to_level": 385,
              "exp_per_hour_recent": 1621, "map": "A Split Road", "map_exp_per_hour": 1890,
              "level_ups_this_session": 1, "exp_gained_session": 259, "session_active": "9m"}
    digest = {"projection": {"basis_exp_per_hour": 1621, "levels": [
        {"to_lv": 8, "exp": 385}, {"to_lv": 9, "exp": 840}, {"to_lv": 10, "exp": 1242},
        {"to_lv": 11, "exp": 1716}]},
        "level_times": [{"lv": 5, "took": "?", "map": "A Split Road"},
                        {"lv": 6, "took": "6m", "map": "A Split Road"},
                        {"lv": 7, "took": "4m", "map": "Southperry"}],
        "best_maps": [{"map": "A Split Road", "exp_per_hour": 1890, "levels": "6-7"},
                      {"map": "Southperry", "exp_per_hour": 1569, "levels": "6-7"}],
        "last_7_days": [{"date": now.strftime("%Y-%m-%d"), "played": "42m", "exp": 1830, "level_ups": 3}]}
    return status, digest


# ------------------------------------------------------------------ tray icon
# custom artwork (from the user's artist friend): the player's own copy wins over the shipped one
ART_ICON = next((p for p in (ROOT / "art" / "icon.png", APP / "art" / "icon.png") if p.exists()),
                ROOT / "art" / "icon.png")


def make_icon_image(color, s=64):
    """Tray/preview icon: dark circle with a state-colored ring. Inside: art/icon.png if it
    exists (custom artwork), else the placeholder (arrow + gold EXP bar)."""
    from PIL import Image, ImageDraw
    k = s / 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2 * k, 2 * k, s - 2 * k, s - 2 * k), fill=(22, 24, 29, 255), outline=color,
              width=max(1, round(6 * k)))
    if ART_ICON.exists():
        try:
            art = Image.open(ART_ICON).convert("RGBA")
            art.thumbnail((int(s * 0.74), int(s * 0.74)), Image.LANCZOS)   # keeps aspect ratio
            img.alpha_composite(art, ((s - art.width) // 2, (s - art.height) // 2))
            return img
        except Exception:
            pass                                # unreadable file: fall back to the placeholder
    d.rounded_rectangle((14 * k, 36 * k, 50 * k, 46 * k), radius=4 * k, fill=(42, 46, 54, 255))
    d.rounded_rectangle((14 * k, 36 * k, 36 * k, 46 * k), radius=4 * k, fill=GOLD)
    d.polygon([(32 * k, 14 * k), (42 * k, 28 * k), (22 * k, 28 * k)], fill=color)   # up arrow
    return img


class Tray:
    def __init__(self, app):
        import pystray
        self.pystray = pystray
        self.app = app
        self.color = None
        M = pystray.MenuItem
        self.icon = pystray.Icon(
            "maple_helper", make_icon_image(GREY), "Maple helper",
            menu=pystray.Menu(
                M("Show / hide window", lambda: app.post("toggle"), default=True),
                M("Details", lambda: app.post("details"), checked=lambda _: app.details),
                M("Always on top", lambda: app.post("topmost"), checked=lambda _: app.topmost),
                M("Settings…", lambda: app.post("settings")),
                M("Skill bar", lambda: app.post("skillbar"),
                  checked=lambda _: app.bar_setting("enabled")),
                M("Lock skill bar (click-through)", lambda: app.post("skillbar_lock"),
                  checked=lambda _: app.bar_setting("locked"),
                  enabled=lambda _: app.bar_setting("enabled")),
                M("Party panel", lambda: app.post("partypanel"),
                  checked=lambda _: app.panel_setting("enabled")),
                M("Lock party panel (click-through)", lambda: app.post("partypanel_lock"),
                  checked=lambda _: app.panel_setting("locked"),
                  enabled=lambda _: app.panel_setting("enabled")),
                M(lambda _: f"Restart to update to {app.update_label()}", lambda: app.post("update"),
                  visible=lambda _: app.update_ready is not None),
                pystray.Menu.SEPARATOR,
                M("Start tracker", lambda: app.post("start"), enabled=lambda _: not app.running),
                M("Stop tracker", lambda: app.post("stop"), enabled=lambda _: app.running),
                pystray.Menu.SEPARATOR,
                M("Open diary", lambda: app.post("diary")),
                M("Open journal", lambda: app.post("journal")),
                M("Open folder", lambda: app.post("folder")),
                pystray.Menu.SEPARATOR,
                M("Exit helper", lambda: app.post("exit")),
                M("Exit and stop tracker", lambda: app.post("exit_all")),
            ))
        threading.Thread(target=self.icon.run, daemon=True).start()

    def update(self, snap):
        color = STATE_COLOR.get(snap.state, GREY)
        try:
            if color != self.color:
                self.icon.icon = make_icon_image(color)
                self.color = color
            self.icon.title = snap.tooltip()
            self.icon.update_menu()
        except Exception:
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass


# ------------------------------------------------------------------ window
class App:
    W = 290

    def __init__(self, demo=False, details=None, at=None, after_update=False):
        self.demo = demo
        self.dev_at = at              # (x, y) for dev/test runs: place windows there, don't save
        self.q = queue.Queue()
        ui = read_json(UI_STATE, {}) or {}
        self.details = ui.get("details", False) if details is None else details
        self.topmost = ui.get("topmost", True)
        self.visible = ui.get("visible", True)
        self.running = False
        self.last_lock_check = 0.0
        self.lock_quiet_until = 0.0
        self.prev_level = None
        self.flash_until = 0.0
        import helper_settings
        self.HS = helper_settings
        self.settings = helper_settings.load()
        self.settings_win = None
        self.presence = None          # discord_rpc.Presence, created only when Discord is on
        self.last_snap = None

        try:      # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.root = r = tk.Tk()
        r.title("Maple helper")
        r.overrideredirect(True)
        r.configure(bg=BG)
        r.attributes("-topmost", self.topmost)
        r.attributes("-alpha", ui.get("alpha", 0.95))
        self.k = r.winfo_fpixels("1i") / 96.0          # DPI scale for pixel sizes
        self.build()
        self.place(ui)
        self.round_corners()
        if not self.visible:
            r.withdraw()

        self.tray = None
        try:
            self.tray = Tray(self)
        except Exception as e:     # no pystray: the window still works, exit via right-click
            print(f"tray icon unavailable: {e}")

        self.update_ready = None      # version tuple installed and waiting for a restart
        self.updater = None           # updater.Updater (packaged build only)
        if not demo and (not at or os.environ.get("MAPLE_COMPANION_UPDATE_BASE")):
            self.start_updater()

        self.diary_popup = None       # diary pop-ups on new entries (diary_popup.py)
        if not demo:
            try:
                from diary_popup import DiaryPopup
                self.diary_popup = DiaryPopup(self)
            except Exception as e:
                print(f"diary pop-up unavailable: {e}")

        self.skillbar = None          # skillbar_ui.SkillBar over the game (Settings > General)
        self.partypanel = None        # party_ui.PartyPanel over the game (Settings > General)
        if not demo and not at:       # "Updated to x.y.z" card after an update (whats_new.py)
            def _whats_new():
                try:
                    import whats_new
                    self.whats_new = whats_new.check(self, after_update)
                except Exception as e:
                    print(f"what's new card failed: {e}")
            r.after(1500, _whats_new)
        self.at_bar = (at[0], at[1] + 400) if at else None
        r.after(50, self.pump)
        r.after(100, self.refresh)

    # ---------------------------------------------------------------- layout
    def px(self, n):
        return int(round(n * self.k))

    def build(self):
        r, px = self.root, self.px
        F = "Segoe UI"
        outer = tk.Frame(r, bg=BG, highlightthickness=1, highlightbackground=LINE)
        outer.pack(fill="both", expand=True)
        body = tk.Frame(outer, bg=BG, padx=px(12), pady=px(10))
        body.pack(fill="both", expand=True)
        self.body = body

        # header: state dot, level, pct, buttons
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        self.dot = tk.Canvas(head, width=px(10), height=px(10), bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, px(6)))
        self.dot_id = self.dot.create_oval(1, 1, px(10) - 1, px(10) - 1, fill=GREY, outline="")
        self.lv = tk.Label(head, text="Lv —", font=(F, 15, "bold"), fg=TEXT, bg=BG)
        self.lv.pack(side="left")
        self.job_lbl = tk.Label(head, text="", font=(F, 9), fg=MUTED, bg=BG)
        self.job_lbl.pack(side="left", padx=(px(6), 0), pady=(px(4), 0))
        close = tk.Label(head, text="×", font=(F, 12), fg=MUTED, bg=BG, cursor="hand2")
        close.pack(side="right", padx=(px(6), 0))
        close.bind("<Button-1>", lambda e: self.hide())
        self.more = tk.Label(head, text="▴" if self.details else "▾", font=(F, 10), fg=MUTED, bg=BG, cursor="hand2")
        self.more.pack(side="right", padx=(px(6), 0))
        self.more.bind("<Button-1>", lambda e: self.toggle_details())
        self.pct = tk.Label(head, text="", font=(F, 15, "bold"), fg=GOLD, bg=BG)
        self.pct.pack(side="right")

        self.map = tk.Label(body, text="", font=(F, 9), fg=MUTED, bg=BG, anchor="w")
        self.map.pack(fill="x")

        # EXP bar
        self.bar_w, self.bar_h = px(self.W - 26), px(9)
        self.bar = tk.Canvas(body, width=self.bar_w, height=self.bar_h, bg=BG, highlightthickness=0)
        self.bar.pack(fill="x", pady=(px(6), px(3)))
        self.bar.create_rectangle(0, 0, self.bar_w, self.bar_h, fill=LINE, outline="")
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, self.bar_h, fill=GOLD, outline="")
        self.exp = tk.Label(body, text="", font=(F, 9), fg=MUTED, bg=BG, anchor="w")
        self.exp.pack(fill="x")

        # stat grid
        grid = tk.Frame(body, bg=BG)
        grid.pack(fill="x", pady=(px(8), 0))
        grid.columnconfigure(0, weight=1, uniform="c")
        grid.columnconfigure(1, weight=1, uniform="c")
        self.cells = {}
        for i, key in enumerate(("next", "rate", "maprate", "session")):
            cell = tk.Frame(grid, bg=BG)
            cell.grid(row=i // 2, column=i % 2, sticky="w", pady=(0, px(6)))
            cap = tk.Label(cell, text="", font=(F, 8), fg=MUTED, bg=BG, anchor="w")
            cap.pack(anchor="w")
            val = tk.Label(cell, text="—", font=(F, 11, "bold"), fg=TEXT, bg=BG, anchor="w")
            val.pack(anchor="w")
            self.cells[key] = (cap, val)

        self.note_box = tk.Frame(body, bg=BG)
        self.note_box.pack(fill="x")
        self.note = tk.Label(self.note_box, text="", font=(F, 8), fg=MUTED, bg=BG, anchor="w", justify="left")

        # details (collapsible)
        self.det = tk.Frame(body, bg=BG)
        tk.Frame(self.det, bg=LINE, height=1).pack(fill="x", pady=(px(4), px(6)))
        self.det_text = tk.Label(self.det, text="", font=("Consolas", 9), fg=TEXT, bg=BG,
                                 anchor="w", justify="left")
        self.det_text.pack(fill="x")
        if self.details:
            self.det.pack(fill="x")

        # menu
        self.menu = tk.Menu(r, tearoff=0)
        self.menu.add_command(label="Hide to tray", command=self.hide)
        self.menu.add_command(label="Details", command=self.toggle_details)
        self.menu.add_command(label="Always on top", command=self.toggle_topmost)
        self.menu.add_command(label="Settings…", command=self.open_settings)
        self.menu.add_command(label="Run setup…", command=self.open_setup)
        self.menu.add_separator()
        self.menu.add_command(label="Start tracker", command=self.start_tracker)
        self.menu.add_command(label="Stop tracker", command=self.stop_tracker)
        self.menu.add_separator()
        self.menu.add_command(label="Open journal", command=lambda: self.open(JOURNAL))
        self.menu.add_command(label="Open folder", command=lambda: self.open(ROOT))
        self.menu.add_separator()
        self.menu.add_command(label="Exit helper", command=self.exit)
        self.menu.add_command(label="Exit and stop tracker", command=lambda: self.exit(stop=True))

        # drag / click bindings on every widget
        def bind_all(w):
            if w is not close and w is not self.more:
                w.bind("<ButtonPress-1>", self.drag_start)
                w.bind("<B1-Motion>", self.drag_move)
                w.bind("<ButtonRelease-1>", self.drag_end)
                w.bind("<Double-Button-1>", lambda e: self.toggle_details())
            w.bind("<Button-3>", self.popup)
            for c in w.winfo_children():
                bind_all(c)
        bind_all(r)

    def round_corners(self):
        try:
            import ctypes
            r = self.root
            r.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(r.winfo_id())
            pref = ctypes.c_int(2)    # DWMWCP_ROUND (Windows 11)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

    def place(self, ui):
        r = self.root
        r.update_idletasks()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w = self.px(self.W)
        x, y = self.dev_at or (ui.get("x"), ui.get("y"))
        if x is None or y is None:
            x, y = sw - w - self.px(24), self.px(80)
        try:                                         # whole desktop, all monitors
            import ctypes
            gm = ctypes.windll.user32.GetSystemMetrics
            vx, vy, vw, vh = gm(76), gm(77), gm(78), gm(79)
        except Exception:
            vx, vy, vw, vh = 0, 0, sw, sh
        x = min(max(vx, x), vx + vw - w)             # keep it on screen if monitors changed
        y = min(max(vy, y), vy + vh - self.px(60))
        r.geometry(f"{w}x{r.winfo_reqheight()}+{x}+{y}")

    def resize(self):
        r = self.root
        r.update_idletasks()
        r.geometry(f"{self.px(self.W)}x{r.winfo_reqheight()}")

    def save_ui(self):
        if self.demo or self.dev_at:
            return
        write_json(UI_STATE, {"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                              "details": self.details, "topmost": self.topmost,
                              "visible": self.visible,
                              "alpha": float(self.root.attributes("-alpha"))})

    # ---------------------------------------------------------------- interactions
    def drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag_move(self, e):
        if getattr(self, "_drag", None):
            dx, dy = self._drag
            self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def drag_end(self, e):
        self._drag = None
        self.save_ui()

    def popup(self, e):
        m = self.menu        # indices follow the add_* order in build()
        m.entryconfigure(1, label="Details" + ("  ✓" if self.details else ""))
        m.entryconfigure(2, label="Always on top" + ("  ✓" if self.topmost else ""))
        m.entryconfigure(5, state="disabled" if self.running else "normal")
        m.entryconfigure(6, state="normal" if self.running else "disabled")
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def toggle_details(self):
        self.details = not self.details
        if self.details:
            self.det.pack(fill="x")
        else:
            self.det.pack_forget()
        self.more.configure(text="▴" if self.details else "▾")
        self.resize()
        self.save_ui()

    def toggle_topmost(self):
        self.topmost = not self.topmost
        self.root.attributes("-topmost", self.topmost)
        self.save_ui()

    def hide(self):
        self.visible = False
        self.root.withdraw()
        self.save_ui()

    def show(self):
        self.visible = True
        self.root.deiconify()
        self.root.attributes("-topmost", self.topmost)
        self.round_corners()
        self.save_ui()

    def open_setup(self):
        """First-run setup / recalibration. The tracker (re)starts when it finishes, so it loads
        the new templates and digits."""
        win = getattr(self, "setup_win", None)
        if win is not None:
            try:
                win.w.deiconify()
                win.w.lift()
                return
            except tk.TclError:
                pass
        from setup_ui import SetupWindow
        self.setup_win = SetupWindow(self, on_done=self.restart_tracker)

    def restart_tracker(self):
        if not tracker_running():
            return self.start_tracker()
        self.stop_tracker()

        def wait(tries=0):
            if tracker_running() and tries < 30:
                return self.root.after(1000, lambda: wait(tries + 1))
            self.start_tracker()
        self.root.after(1000, wait)

    def open_settings(self, page="Discord"):
        if self.settings_win is not None:
            try:
                self.settings_win.w.deiconify()
                self.settings_win.w.lift()
                self.settings_win.show(page)
                return
            except tk.TclError:
                self.settings_win = None
        from settings_ui import SettingsWindow
        self.settings_win = SettingsWindow(self, page)

    def update_discord(self, snap):
        d = self.settings.get("discord", {})
        cid = str(d.get("client_id") or "").strip()
        if d.get("enabled") and cid.isdigit() and not self.demo:
            if self.presence is None:
                from discord_rpc import Presence
                self.presence = Presence()
            self.presence.update(cid, self.HS.build_activity(snap, self.settings))
        elif self.presence is not None:
            self.presence.disable()

    keep_tracker = False            # packaged app: restart the tracker whenever it isn't running

    def start_tracker(self):
        self.keep_tracker = bool(getattr(self, "watch_tracker", False))
        if tracker_running():
            return
        if STOP_FILE.exists():
            try:
                STOP_FILE.unlink()          # a stop meant for a tracker that's gone
            except OSError:
                pass
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(self_command("--service"), cwd=str(ROOT),
                         creationflags=flags, close_fds=True)
        self.lock_quiet_until = time.time() + 10   # don't poke the lock while it starts up
        self.running = True

    def stop_tracker(self):
        self.keep_tracker = False           # you stopped it: don't bring it back
        if tracker_running():
            STOP_FILE.touch()

    def open_diary(self):
        try:
            import diary
            diary.open_diary(in_process=True)
        except Exception as e:
            print(f"open diary failed: {e}")

    def open(self, path):
        try:
            os.startfile(str(path))
        except Exception:
            pass

    def exit(self, stop=False):
        if stop:
            self.stop_tracker()
        self.save_ui()
        if self.presence:
            self.presence.disable()
            time.sleep(1.5)           # let the worker clear the status before we go
            self.presence.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def post(self, cmd):          # called from the tray thread
        self.q.put(cmd)

    def pump(self):
        try:
            while True:
                cmd = self.q.get_nowait()
                if cmd == "toggle":
                    self.hide() if self.visible else self.show()
                elif cmd == "details":
                    self.toggle_details()
                elif cmd == "topmost":
                    self.toggle_topmost()
                elif cmd == "start":
                    self.start_tracker()
                elif cmd == "stop":
                    self.stop_tracker()
                elif cmd == "settings":
                    self.open_settings()
                elif cmd == "skillbar":
                    self.put_bar(enabled=not self.bar_setting("enabled"))
                elif cmd == "skillbar_lock":
                    self.put_bar(locked=not self.bar_setting("locked"))
                    if self.skillbar:
                        self.skillbar.set_locked(self.bar_setting("locked"))
                elif cmd == "partypanel":
                    self.put_panel(enabled=not self.panel_setting("enabled"))
                elif cmd == "partypanel_lock":
                    self.put_panel(locked=not self.panel_setting("locked"))
                    if self.partypanel:
                        self.partypanel.set_locked(self.panel_setting("locked"))
                elif cmd == "diary":
                    self.open_diary()
                elif cmd == "journal":
                    self.open(JOURNAL)
                elif cmd == "folder":
                    self.open(ROOT)
                elif cmd == "update_ready":
                    self.on_update_ready()
                elif cmd == "update":
                    self.restart_into_update("from the tray")
                    return
                elif cmd in ("exit", "exit_all"):
                    self.exit(stop=cmd == "exit_all")
                    return
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    # ---------------------------------------------------------------- refresh
    def refresh(self):
        now = time.time()
        if self.demo:
            status, digest = demo_data()
            self.running = True
        else:
            status, digest = read_json(STATUS, {}), read_json(DIGEST, {})
            if now - self.last_lock_check > 5 and now > self.lock_quiet_until:
                self.running = tracker_running()
                self.last_lock_check = now
        # the tracker should be running (packaged app) but isn't: an old one was still closing
        # when this helper started, or it stopped on its own. Start it again (after its own
        # start-up quiet time, so a slow start isn't doubled)
        if (self.keep_tracker and not self.running and not self.restarting
                and now > self.lock_quiet_until and now - self.last_lock_check < 1.5):
            self.start_tracker()
        snap = Snapshot(status, digest, self.running)
        self.last_snap = snap
        self.sync_skillbar()
        self.sync_partypanel()
        self.render(snap)
        self.update_discord(snap)
        if self.tray:
            self.tray.update(snap)
        if self.diary_popup:
            try:
                self.diary_popup.check()
            except Exception as e:
                print(f"diary pop-up check failed: {e}")
        # quiet moment: a whole minute of status files that say "not playing" (a failed read of
        # status.json or a loading screen isn't one: 2026-10-24 an update restarted mid-play)
        quiet = bool(status) and snap.state in ("idle", "stopped", "waiting_for_source")
        self.quiet_since = (self.quiet_since or now) if quiet else None
        if (self.update_ready is not None and not self.restarting and self.quiet_since
                and now - self.quiet_since >= QUIET_RESTART_S):
            return self.restart_into_update(f"not playing for {QUIET_RESTART_S} s")
        self.root.after(1000, self.refresh)

    # ---------------------------------------------------------------- skill bar
    def bar_setting(self, key):
        return (self.settings.get("skillbar") or {}).get(key)

    def put_bar(self, **kw):
        """Change the skill bar's settings (from the bar itself or the tray) and save them."""
        self.settings.setdefault("skillbar", {}).update(kw)
        if not self.dev_at:
            try:
                self.HS.save(self.settings)
            except Exception as e:
                print(f"skill bar settings save failed: {e}")
        self.sync_skillbar()

    def sync_skillbar(self):
        """Create / remove the bar to match the setting."""
        on = bool(self.bar_setting("enabled"))
        if on and self.skillbar is None:
            try:
                from skillbar_ui import SkillBar
                self.skillbar = SkillBar(self.root, lambda: self.settings.get("skillbar") or {},
                                         self.put_bar, k=self.k, demo=self.demo, at=self.at_bar)
            except Exception as e:
                print(f"skill bar unavailable: {e}")
                self.settings.setdefault("skillbar", {})["enabled"] = False
        elif not on and self.skillbar is not None:
            self.skillbar.destroy()
            self.skillbar = None

    # ---------------------------------------------------------------- party panel
    def panel_setting(self, key):
        return (self.settings.get("partypanel") or {}).get(key)

    def put_panel(self, **kw):
        """Change the party panel's settings (from the panel itself or the tray) and save them."""
        self.settings.setdefault("partypanel", {}).update(kw)
        if not self.dev_at:
            try:
                self.HS.save(self.settings)
            except Exception as e:
                print(f"party panel settings save failed: {e}")
        self.sync_partypanel()

    def sync_partypanel(self):
        """Create / remove the panel to match the setting (it hides itself with no party)."""
        on = bool(self.panel_setting("enabled"))
        if on and self.partypanel is None:
            try:
                from party_ui import PartyPanel
                at = (self.at_bar[0], self.at_bar[1] + 120) if self.at_bar else None
                self.partypanel = PartyPanel(self.root,
                                             lambda: self.settings.get("partypanel") or {},
                                             self.put_panel, k=self.k, demo=self.demo, at=at)
            except Exception as e:
                print(f"party panel unavailable: {e}")
                self.settings.setdefault("partypanel", {})["enabled"] = False
        elif not on and self.partypanel is not None:
            self.partypanel.destroy()
            self.partypanel = None

    # ---------------------------------------------------------------- updates (updater.py)
    restarting = False
    quiet_since = None              # since when the status files say "not playing"

    def start_updater(self):
        import paths
        if not (paths.FROZEN or os.environ.get("MAPLE_COMPANION_UPDATE_BASE")):
            return                    # running from source: git is the update path
        try:
            import updater
            lv, cur = updater.latest_version(), updater.current()
            exe, latest = os.path.realpath(sys.executable), os.path.realpath(updater.latest_exe())
            if lv and cur and lv > cur and exe.lower() != latest.lower():
                self.update_ready = lv           # installed earlier, not running yet
                # (when we *are* latest, a mismatch means a release with the wrong version.py:
                # never restart over it, or it would loop)
            self.updater = updater.Updater(on_ready=lambda v: self.post("update_ready"))
            self.updater.start()
            updater.log(f"helper {updater.fmt(cur) if cur else '?'} started (pid {os.getpid()}, "
                        f"{'latest' if exe.lower() == latest.lower() else 'not latest'}, from {sys.executable})")
        except Exception as e:
            print(f"updater unavailable: {e}")

    def update_label(self):
        return ".".join(map(str, self.update_ready)) if self.update_ready else ""

    def on_update_ready(self):
        if self.updater and self.updater.ready:
            self.update_ready = self.updater.ready
        try:
            if self.tray:
                self.tray.icon.notify(f"Version {self.update_label()} is installed. It starts when "
                                      "you're not playing, or now from the tray menu.",
                                      "Maple Classic Companion")
        except Exception:
            pass

    def restart_into_update(self, why="from the tray"):
        """Stop the tracker, start the new version (latest), and close this one."""
        import updater
        self.restarting = True
        self.stop_tracker()

        def go(tries=0):
            if tracker_running() and tries < 40:
                return self.root.after(500, lambda: go(tries + 1))
            try:
                exe = updater.real_latest_exe()      # the real folder, never via the junction
                if exe is None:
                    raise FileNotFoundError("latest version missing")
                at = [f"--at={self.dev_at[0]},{self.dev_at[1]}"] if self.dev_at else []
                updater._launch([str(exe), "--after-update", *at])
                updater.log(f"restarting into {self.update_label()} ({why})")
            except Exception as e:
                updater.log(f"restart into update failed: {e}")
                self.restarting = False
                self.update_ready = None
                return self.root.after(1000, self.refresh)
            if self.updater:
                self.updater.stop()
            self.exit(stop=False)
        go()

    def render(self, s):
        playing = s.state == "playing"
        color = STATE_COLOR.get(s.state, GREY)
        self.dot.itemconfigure(self.dot_id, fill=color)

        # level-up flash
        if s.level and self.prev_level and s.level > self.prev_level:
            self.flash_until = time.time() + 8
        if s.level:
            self.prev_level = s.level
        flashing = time.time() < self.flash_until
        self.lv.configure(text=f"Lv {s.level}" if s.level else "Lv —",
                          fg=GOLD if flashing else TEXT)
        self.job_lbl.configure(text=self.HS.job(s, self.settings))
        self.pct.configure(text=f"{s.pct:.2f}%" if s.pct is not None else "",
                           fg=GOLD if playing or flashing else MUTED)
        self.map.configure(text=("LEVEL UP!  " if flashing else "") + (s.map or "map unknown"))

        frac = max(0.0, min(1.0, (s.pct or 0) / 100))
        self.bar.coords(self.bar_fill, 0, 0, self.bar_w * frac, self.bar_h)
        self.bar.itemconfigure(self.bar_fill, fill=GOLD if playing else GREY)
        if s.cur is not None and s.need:
            self.exp.configure(text=f"{fmt_int(s.cur)} / {fmt_int(s.need)} EXP   ·   {fmt_int(s.left)} to go")
        else:
            self.exp.configure(text="")

        nxt = f"Lv {s.level + 1} in" if s.level else "Next level"
        if playing and s.eta_s is not None:
            eta = f"~{fmt_dur(s.eta_s)}  ({clock(s.eta_at)})"
        elif s.eta_s is not None:
            eta = f"~{fmt_dur(s.eta_s)} of play"
        else:
            eta = "—"
        cells = {
            "next": (nxt, eta),
            "rate": ("EXP / hr (recent)", fmt_int(s.rate)),
            "maprate": ("This map / hr", fmt_int(s.map_rate)),
            "session": ("Session", (s.session or "—") + (f"  ·  +{s.lvups} lv" if s.lvups else "")),
        }
        for key, (cap, val) in cells.items():
            c, v = self.cells[key]
            c.configure(text=cap)
            v.configure(text=val, fg=TEXT if playing else MUTED)

        note = ""
        if not playing:
            note = STATE_LABEL.get(s.state, s.state)
            if s.state in ("idle", "waiting_for_source") and s.reason:
                note += f": {s.reason}"
            if s.state == "stopped":
                note += "  (right-click → Start tracker)"
        elif s.age is not None and s.age > 45:
            note = f"last reading {fmt_dur(s.age)} ago"
        self.note.configure(text=note, fg=color if not playing else MUTED)
        if note:
            self.note.pack(fill="x")
        else:
            self.note.pack_forget()

        if self.details:
            self.det_text.configure(text=self.details_text(s))
        self.resize()

    def details_text(self, s):
        out = []
        if s.proj:
            out.append("Coming up (at current rate)")
            for lv, secs, at in s.proj[:4]:
                out.append(f"  Lv {lv:<3} {'~' + fmt_dur(secs):>8}   {clock(at) if secs < 86400 else ''}")
        lt = (s.dg.get("level_times") or [])[-3:]
        if lt:
            out.append("Recent levels")
            for x in reversed(lt):
                out.append(f"  Lv {x.get('lv'):<3} took {x.get('took') or '?':>6}  {(x.get('map') or '')[:14]}")
        bm = (s.dg.get("best_maps") or [])[:3]
        if bm:
            out.append("Best maps (EXP/hr)")
            for m in bm:
                out.append(f"  {(m.get('map') or '?')[:18]:<18} {fmt_int(m.get('exp_per_hour')):>7}")
        if s.today:
            t = s.today
            out.append(f"Today: {t.get('played')} played, {fmt_int(t.get('exp'))} EXP, "
                       f"+{t.get('level_ups', 0)} lv")
        return "\n".join(out) or "No history yet."

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="show fake data (layout test)")
    ap.add_argument("--details", action="store_true", help="start with details open")
    ap.add_argument("--settings", nargs="?", const="Discord", help="open the settings window (page)")
    ap.add_argument("--setup", action="store_true", help="open the first-run setup window")
    ap.add_argument("--at", help="dev: place the windows at X,Y (use --at=-2400,40 for the second monitor)")
    ap.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-handoff", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    lock = None
    at = tuple(int(v) for v in args.at.split(",")) if args.at else None
    if not args.demo and not at:
        lock = try_lock(HELPER_LOCK)
        for _ in range(60 if args.after_update else 0):   # the old version is still closing
            if lock is not None:
                break
            time.sleep(0.5)
            lock = try_lock(HELPER_LOCK)
        if lock is None:
            return            # already running
    try:
        app = App(demo=args.demo, details=True if args.details else None, at=at,
                  after_update=args.after_update)
        if args.settings:
            app.root.after(300, lambda: app.open_settings(args.settings))
        if not args.demo:
            import calibrate
            import paths
            # packaged app: the tracker comes with the helper and is kept running (refresh);
            # after a first-run setup it starts when setup finishes
            app.watch_tracker = paths.FROZEN
            if args.setup or calibrate.needs_setup():
                app.root.after(300, app.open_setup)
            elif paths.FROZEN:
                app.root.after(500, app.start_tracker)
        app.run()
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    main()
