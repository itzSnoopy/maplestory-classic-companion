"""meso_ui.py - the dev meso counter panel (see component.py). Plain tkinter, no PIL.

Ledger tails history/gains.csv (kind=meso rows) incrementally. State (last mark: time + inventory value)
is history/meso_counter.json, so a restart carries on; each Mark appends to history/meso_marks.csv:
estimate, inventory value, drift (+ = the corner missed mesos, - = counted too many), pickups since the
previous mark. Shop spending, drops and quest mesos aren't corner pickups: mark after those.
"""
import csv
import io
import json
import time
import tkinter as tk

POLL_MS = 500
MARKS_HEAD = "t,time,estimate,actual,drift,pickups,picked_sum,map\n"


class Ledger:
    """Meso rows of gains.csv after a given time, read incrementally."""

    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.rows = []            # (t, mesos, map, overflow)
        self.since = 0.0

    def reset(self, since):
        self.since = since
        self.rows = [r for r in self.rows if r[0] > since]

    def _parse(self, text):
        for rec in csv.reader(io.StringIO(text)):
            if len(rec) < 12 or rec[2] != "meso":
                continue
            try:
                t, m = float(rec[0]), int(rec[6])
            except ValueError:
                continue
            if t > self.since:
                self.rows.append((t, m, rec[1], rec[11] == "1"))

    def poll(self):
        """Read new whole lines; returns the rows added."""
        before = len(self.rows)
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.pos:                                # rotated to .csv.old: finish it, start over
            old = self.path.with_suffix(".csv.old")
            try:
                with open(old, "rb") as f:
                    f.seek(self.pos)
                    self._parse(f.read().decode("utf-8", errors="replace"))
            except OSError:
                pass
            self.pos = 0
        if size == self.pos:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            chunk = f.read()
        cut = chunk.rfind(b"\n") + 1
        self.pos += cut
        self._parse(chunk[:cut].decode("utf-8", errors="replace"))
        return self.rows[before:]


def fmt(n):
    return f"{n:,}"


class MesoPanel:
    BG, FG, DIM, GOLD, RED, GREEN = "#1d1b22", "#ece6d6", "#8d8798", "#f2c94c", "#ff7a6b", "#7bd88f"

    def __init__(self, root, get, put, data, k=1.0, demo=False, at=None, **_):
        self.get, self.put, self.k = get, put, k
        hist = data / "history"
        self.state_path, self.marks_path = hist / "meso_counter.json", hist / "meso_marks.csv"
        self.state = self._load() or {"t": time.time(), "actual": 0, "unset": True}
        self.ledger = Ledger(hist / "gains.csv")
        self.ledger.reset(self.state["t"])
        self.flash_until, self._d, self._job = 0.0, None, None

        self.win = w = tk.Toplevel(root)
        w.title("Meso counter")
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=self.BG)
        cfg = get()
        x, y = (at if at else (cfg.get("x"), cfg.get("y")))
        w.geometry(f"+{x if x is not None else 40}+{y if y is not None else 300}")
        w.bind("<ButtonPress-1>", self._drag_start)
        w.bind("<B1-Motion>", self._drag)
        w.bind("<ButtonRelease-1>", self._drag_end)

        def fnt(size, bold=False, fam="Segoe UI"):
            return (fam, max(6, int(round(size * k))), "bold") if bold else (fam, max(6, int(round(size * k))))
        f = tk.Frame(w, bg=self.BG, padx=int(10 * k), pady=int(6 * k), highlightthickness=1,
                     highlightbackground="#3a3644")
        f.pack(fill="both", expand=True)
        tk.Label(f, text="MESO  (dev)", bg=self.BG, fg=self.DIM, font=fnt(8, True), anchor="w").pack(fill="x")
        self.est = tk.Label(f, bg=self.BG, fg=self.GOLD, font=fnt(20, True), anchor="w")
        self.est.pack(fill="x")
        self.info = tk.Label(f, bg=self.BG, fg=self.DIM, font=fnt(9), anchor="w", justify="left")
        self.info.pack(fill="x")
        self.recent = tk.Label(f, bg=self.BG, fg=self.FG, font=fnt(9, fam="Consolas"), anchor="w")
        self.recent.pack(fill="x")
        self.drift = tk.Label(f, bg=self.BG, fg=self.DIM, font=fnt(9), anchor="w")
        self.drift.pack(fill="x")
        row = tk.Frame(f, bg=self.BG)
        row.pack(fill="x", pady=(int(4 * k), 0))
        self.entry = tk.Entry(row, width=10, bg="#2a2731", fg=self.FG, insertbackground=self.FG,
                              relief="flat", font=fnt(10))
        self.entry.pack(side="left", ipady=2)
        self.entry.bind("<Return>", lambda e: self.mark())
        self.btn = tk.Button(row, text="Mark", command=self.mark, bg="#3a3644", fg=self.FG, relief="flat",
                             activebackground="#4a4556", activeforeground=self.FG, padx=8, font=fnt(9))
        self.btn.pack(side="left", padx=(int(6 * k), 0))
        self.ledger.poll()
        self.tick()

    # -- PanelHost contract
    def destroy(self):
        if self._job:
            try:
                self.win.after_cancel(self._job)
            except tk.TclError:
                pass
        self.win.destroy()

    def set_locked(self, locked):
        pass                                    # lock = no dragging (checked in _drag_start)

    # -- state files
    def _load(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        except OSError:
            pass

    # -- dragging
    def _drag_start(self, e):
        if self.get().get("locked") or e.widget in (self.entry, self.btn):
            self._d = None
            return
        self._d = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag(self, e):
        if self._d:
            self.win.geometry(f"+{e.x_root - self._d[0]}+{e.y_root - self._d[1]}")

    def _drag_end(self, e):
        if self._d:
            self._d = None
            self.put(x=self.win.winfo_x(), y=self.win.winfo_y())

    # -- counting
    def picked(self):
        rows = self.ledger.rows
        return len(rows), sum(r[1] for r in rows)

    def tick(self):
        if self.ledger.poll():
            self.flash_until = time.time() + 1.2
        n, s = self.picked()
        if self.state.get("unset"):
            self.est.config(text=f"+{fmt(s)}", fg=self.GOLD)
        else:
            self.est.config(text=fmt(self.state["actual"] + s),
                            fg=self.GREEN if time.time() < self.flash_until else self.GOLD)
        since = time.strftime("%H:%M", time.localtime(self.state["t"]))
        over = sum(1 for r in self.ledger.rows if r[3])
        self.info.config(text=f"{n} pickups  +{fmt(s)} since {since}" + (f"  ({over} in overflow)" if over else ""))
        last = self.ledger.rows[-6:]
        self.recent.config(text="  ".join(f"+{r[1]}" for r in reversed(last)) or "waiting for pickups…")
        d = self.state.get("last_drift")
        if self.state.get("unset"):
            self.drift.config(text="type your inventory meso + Enter", fg=self.DIM)
        elif d is None:
            self.drift.config(text="mark again to see drift", fg=self.DIM)
        else:
            self.drift.config(text=f"last mark: drift {d:+,} over {self.state.get('last_pickups', 0)} pickups",
                              fg=self.GREEN if d == 0 else self.RED)
        self._job = self.win.after(POLL_MS, self.tick)

    def mark(self):
        raw = self.entry.get().replace(",", "").replace(".", "").strip()
        self.entry.delete(0, "end")
        if not raw.isdigit():
            return
        self.ledger.poll()
        actual, now = int(raw), time.time()
        n, s = self.picked()
        first = bool(self.state.get("unset"))
        if not first:
            est = self.state["actual"] + s
            drift = actual - est
            mp = self.ledger.rows[-1][2].replace('"', "'") if self.ledger.rows else ""
            try:
                new = not self.marks_path.exists()
                with open(self.marks_path, "a", encoding="utf-8", newline="") as f:
                    if new:
                        f.write(MARKS_HEAD)
                    f.write(f"{now:.2f},{time.strftime('%Y-%m-%d %H:%M:%S')},{est},{actual},{drift},{n},{s},\"{mp}\"\n")
            except OSError:
                pass
        self.state = {"t": now, "actual": actual,
                      "last_drift": None if first else drift, "last_pickups": n}
        self._save()
        self.ledger.reset(now)
