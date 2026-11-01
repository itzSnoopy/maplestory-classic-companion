"""invite_ui.py - the challenge invitation popup over the game: "<Name> challenges you!", the mode and minutes,
big Accept (green) / Decline buttons and a bar counting down to the invitation's expiry.

It reads challenge.json (the tracker's: `invite {id, from, mode, minutes, expires, host?}`) and writes the same
`challenge_cmd.json` commands the party panel's invitation line writes ({action: accept|decline, id}); the
challenge component (tracker) sends them. The party panel's line stays as a second place to answer.

State logic (no tkinter, no PIL): `InviteState.update(snap, cfg, now)` -> what to show or None, `answer(action)` ->
the command. Rules: shown while challenge.json is fresh, the board is enabled, don't disturb is off (the
setting and challenge.json's own `dnd`), the invitation exists and has not expired, and it was not answered here.

Focus: any click on a helper window normally takes keyboard focus from the game. This popup is shown without
activating (Tk's overrideredirect window plus WS_EX_NOACTIVATE on Windows), so it neither steals focus when it
appears nor when clicked; where that style cannot be set, a click would still take focus (as the party panel's does).
Position: setting challenge.invite_x / invite_y (None = top centre), drag to move.

  python -m components.challenge.invite_ui --demo [--at=X,Y]    a fake invitation every 25 s
  python -m components.challenge.invite_ui --selftest           the state logic, offline, no window
"""
import json
import sys
import time

from base.paths import DATA
from hub.ui.overlay import KEY, PanelHost, font as _font, text as _text

CHALLENGE, CMD = DATA / "challenge.json", DATA / "challenge_cmd.json"
STALE_S = 15.0              # challenge.json older than this: the tracker is not running, show nothing
TOTAL_S = 60.0              # length of an open invitation (relay CH_PENDING_S): the bar's full width
W, H = 360, 132             # design px (times the shell's k)
FPS = 4
MODE_TEXT = {"1v1": "1v1", "map": "Map race", "all": "Group race"}


def mode_line(inv):
    """'1v1 · 5 min' / 'Room race · 10 min' (host races) / 'Map race · 5 min'."""
    what = "Room race" if inv.get("host") else MODE_TEXT.get(inv.get("mode"), "Race")
    return f"{what} · {inv.get('minutes', '?')} min"


class InviteState:
    """Which invitation the popup shows. Pure logic, one instance per popup."""

    def __init__(self):
        self.answered = set()           # invitation ids answered (or declined) from here

    def update(self, snap, cfg, now):
        """-> {id, who, mode, minutes, left, frac} to show, or None to hide."""
        if not cfg.get("enabled", True) or cfg.get("dnd"):
            return None
        if not snap or not snap.get("enabled", True) or snap.get("dnd"):
            return None
        if now - (snap.get("t") or 0) > STALE_S:
            return None
        inv = snap.get("invite")
        if not inv or not inv.get("id") or inv["id"] in self.answered:
            return None
        left = (inv.get("expires") or 0) - now
        if left <= 0:
            return None
        return {"id": inv["id"], "who": inv.get("from") or "?", "mode": mode_line(inv),
                "left": left, "frac": max(0.0, min(1.0, left / TOTAL_S))}

    def answer(self, view, action):
        """Accept / decline the shown invitation -> the command dict (without the time), and hide it for good."""
        if view is None or action not in ("accept", "decline"):
            return None
        self.answered.add(view["id"])
        if len(self.answered) > 50:
            self.answered = set(list(self.answered)[-20:])
        return {"action": action, "id": view["id"]}


def _swords(d, cx, cy, r, k):
    """Two crossed swords (drawn shapes; the UI fonts have no such glyph). Own copy: components never import each other."""
    for sx in (-1, 1):
        x0, y0, x1, y1 = cx + sx * r, cy - r, cx - sx * r, cy + r
        d.line((x0, y0, x1, y1), fill=(215, 220, 232, 255), width=max(2, int(2.4 * k)))
        gx, gy = cx - sx * r * 0.45, cy + r * 0.45
        d.line((gx - sx * 3.5 * k, gy - 3.5 * k, gx + sx * 3.5 * k, gy + 3.5 * k),
               fill=(230, 180, 60, 255), width=max(2, int(2 * k)))
        d.ellipse((x1 - 2 * k, y1 - 2 * k, x1 + 2 * k, y1 + 2 * k), fill=(230, 180, 60, 255))


def _draw(view, k, f_title, f_sub, f_btn, f_small):
    """The popup picture (RGBA at k x design size) and its hot regions [(x0, y0, x1, y1, action)]."""
    from PIL import Image, ImageDraw
    w, h = int(W * k), int(H * k)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=int(10 * k), fill=(20, 22, 28, 242),
                        outline=(240, 190, 70, 255), width=max(2, int(2 * k)))
    _swords(d, int(26 * k), int(28 * k), int(10 * k), k)
    _text(d, (int(48 * k), int(20 * k)), f"{view['who']} challenges you!", f_title, anchor="lm")
    _text(d, (int(48 * k), int(42 * k)), view["mode"], f_sub, fill=(240, 200, 100, 255), anchor="lm")
    by, bh = int(58 * k), int(40 * k)
    bw = int(150 * k)
    ax, dx = int(18 * k), w - int(18 * k) - bw
    d.rounded_rectangle((ax, by, ax + bw, by + bh), radius=int(8 * k), fill=(38, 138, 66, 255),
                        outline=(130, 235, 160, 255), width=max(1, int(2 * k)))
    _text(d, (ax + bw // 2, by + bh // 2), "Accept", f_btn)
    d.rounded_rectangle((dx, by, dx + bw, by + bh), radius=int(8 * k), fill=(92, 50, 52, 255),
                        outline=(220, 130, 130, 255), width=max(1, int(2 * k)))
    _text(d, (dx + bw // 2, by + bh // 2), "Decline", f_btn)
    # countdown bar until the invitation expires
    ty, th = int(110 * k), int(8 * k)
    x0, x1 = int(18 * k), w - int(18 * k)
    d.rounded_rectangle((x0, ty, x1, ty + th), radius=int(3 * k), fill=(48, 50, 58, 255))
    fw = int((x1 - x0) * view["frac"])
    if fw > 2:
        col = (240, 190, 70, 255) if view["left"] > 10 else (235, 90, 70, 255)
        d.rounded_rectangle((x0, ty, x0 + fw, ty + th), radius=int(3 * k), fill=col)
    _text(d, (w - int(18 * k), ty - int(4 * k)), f"{int(view['left'])}s", f_small, fill=(190, 195, 205, 255),
          anchor="rb")
    hot = [(ax, by, ax + bw, by + bh, "accept"), (dx, by, dx + bw, by + bh, "decline")]
    return im, hot


class InvitePopup:
    """PanelHost factory object: creates its window hidden, shows it while InviteState says so."""

    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None):
        import tkinter as tk
        self.tk = tk
        self.root, self.get, self.put, self.demo, self.k = root, settings_get, settings_put, demo, k
        self.state = InviteState()
        self.view, self.hot, self.photo, self.shown, self.key = None, [], None, False, None
        self.f_title, self.f_sub = _font(int(15 * k)), _font(int(12 * k))
        self.f_btn, self.f_small = _font(int(15 * k)), _font(int(10 * k))
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
        for ev, fn in (("<ButtonPress-1>", self._press), ("<B1-Motion>", self._drag),
                       ("<ButtonRelease-1>", self._release)):
            self.lbl.bind(ev, fn)
        cfg = self.get()
        x, y = at or (cfg.get("invite_x"), cfg.get("invite_y"))
        if x is None or y is None:
            x, y = max(0, (root.winfo_screenwidth() - int(W * k)) // 2), int(80 * k)
        self.dev = at is not None
        w.geometry(f"+{int(x)}+{int(y)}")
        w.withdraw()
        self._no_activate()
        self.after_tick = None
        self._tick()

    def _no_activate(self):
        """WS_EX_NOACTIVATE (+ tool window): showing or clicking the popup does not take focus from the game."""
        try:
            import ctypes
            u = ctypes.windll.user32
            self.w.update_idletasks()
            hwnd = u.GetParent(self.w.winfo_id()) or self.w.winfo_id()
            st = u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80
            u.SetWindowLongW(hwnd, -20, st)
        except Exception as e:
            print(f"invite popup focus style failed: {e}")

    def _snap(self, now):
        if self.demo:
            n = int(now // 25)
            return {"t": now, "enabled": True, "dnd": False,
                    "invite": {"id": f"demo{n}", "from": "Robin", "mode": "1v1", "minutes": 5,
                               "expires": n * 25 + 20 if now - n * 25 < 20 else 0}}
        try:
            return json.loads(CHALLENGE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _tick(self):
        try:
            now = time.time()
            self.view = self.state.update(self._snap(now), self.get(), now)
            if self.view is not None:
                key = (self.view["id"], int(self.view["left"] * FPS))
                if key != self.key:
                    from PIL import Image, ImageTk
                    im, self.hot = _draw(self.view, self.k, self.f_title, self.f_sub, self.f_btn, self.f_small)
                    rgb = Image.new("RGB", im.size, tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5)))
                    rgb.paste(im.convert("RGB"), (0, 0), im.getchannel("A").point(lambda v: 255 if v > 100 else 0))
                    self.photo = ImageTk.PhotoImage(rgb)
                    self.lbl.configure(image=self.photo)
                    self.key = key
            show = self.view is not None
            if show != self.shown:
                (self.w.deiconify if show else self.w.withdraw)()
                if show:
                    self._no_activate()
                self.shown, self.key = show, None if not show else self.key
        except Exception as e:
            print(f"invite popup draw failed: {e}")
        self.after_tick = self.root.after(int(1000 / FPS), self._tick)

    # ---------------------------------------------------------------- mouse
    def _press(self, e):
        self._dx, self._dy = e.x_root - self.w.winfo_x(), e.y_root - self.w.winfo_y()
        self._p0 = (e.x_root, e.y_root)

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _release(self, e):
        px, py = getattr(self, "_p0", (e.x_root, e.y_root))
        if abs(e.x_root - px) < 4 and abs(e.y_root - py) < 4:           # a click, not a drag
            for x0, y0, x1, y1, action in self.hot:
                if x0 <= e.x < x1 and y0 <= e.y < y1:
                    return self._answer(action)
        elif not self.dev:
            self.put(invite_x=self.w.winfo_x(), invite_y=self.w.winfo_y())

    def _answer(self, action):
        cmd = self.state.answer(self.view, action)
        if not cmd:
            return
        self.view = None
        self.w.withdraw()
        self.shown = False
        if self.demo:
            print("challenge command:", cmd)
            return
        from hub import history as HI
        HI.write_json(CMD, {"t": round(time.time(), 2), **cmd}, indent=None)

    def set_locked(self, locked):           # PanelHost interface; the popup is never click-through
        pass

    def destroy(self):
        if self.after_tick:
            self.root.after_cancel(self.after_tick)
        self.w.destroy()


class InviteHost(PanelHost):
    """The popup's PanelHost: it follows the challenge slice's `enabled` (the same switch as the board; hidden
    while the helper is an icon) but never switches that setting off if it fails, and has no menu entries."""

    def __init__(self, shell, dy=220):
        super().__init__(shell, "challenge", "challenge invitation popup", make_popup, dy=dy)

    def put(self, **kw):
        self.shell.save_settings(self.name, **kw)

    def sync(self):
        on = bool(self.view().get("enabled"))
        if on and self.panel is None:
            sh = self.shell
            try:
                at = (sh.at[0], sh.at[1] + self.dy) if sh.at else None
                self.panel = self.factory(sh.root, self.view, self.put, k=sh.k, demo=sh.demo, at=at)
            except Exception as e:
                print(f"{self.what} unavailable: {e}")
        elif not on and self.panel is not None:
            self.panel.destroy()
            self.panel = None


def make_popup(root, get, put, **kw):
    return InvitePopup(root, get, put, **kw)


# ------------------------------------------------------------------ checks
def selftest():
    s = InviteState()
    now = 1000.0
    inv = {"id": "a", "from": "Robin", "mode": "1v1", "minutes": 5, "expires": now + 40}
    snap = {"t": now, "enabled": True, "dnd": False, "invite": inv}
    cfg = {"enabled": True, "dnd": False}
    v = s.update(snap, cfg, now)
    assert v and v["who"] == "Robin" and v["mode"] == "1v1 · 5 min", v                       # shows on invite
    assert abs(v["frac"] - 40 / TOTAL_S) < 1e-9
    assert mode_line({**inv, "host": True, "minutes": 10}) == "Room race · 10 min"
    assert s.update({**snap, "invite": None}, cfg, now) is None                              # invite goes away
    assert s.update(snap, {**cfg, "dnd": True}, now) is None                                 # dnd (setting)
    assert s.update({**snap, "dnd": True}, cfg, now) is None                                 # dnd (snapshot)
    assert s.update(snap, {**cfg, "enabled": False}, now) is None                            # board off
    assert s.update(snap, cfg, now + 41) is None                                             # expired
    assert s.update(snap, cfg, now + 16) is None                                             # stale challenge.json
    assert s.update({**snap, "t": now + 30}, cfg, now + 30) is not None                      # fresh again
    assert s.answer(None, "accept") is None and s.answer(v, "maybe") is None
    cmd = s.answer(v, "accept")                                                              # answering
    assert cmd == {"action": "accept", "id": "a"}, cmd
    assert s.update(snap, cfg, now) is None                                                  # hidden for good
    v2 = s.update({**snap, "invite": {**inv, "id": "b"}}, cfg, now)                          # a new invitation shows
    assert v2 and s.answer(v2, "decline") == {"action": "decline", "id": "b"}
    assert s.update({"t": now, "enabled": True, "invite": {**inv, "id": "c", "expires": now}}, cfg, now) is None
    # the picture itself
    from PIL import ImageFont  # noqa: F401
    im, hot = _draw(v, 1.0, *[_font(12)] * 4)
    assert im.size == (W, H) and [h[4] for h in hot] == ["accept", "decline"]
    print("invite_ui selftest ok")


def main():
    from base import lean  # noqa: F401
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    at = next((tuple(int(v) for v in a.split("=", 1)[1].split(",")) for a in argv if a.startswith("--at=")), None)
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    state = {"enabled": True, "dnd": False}
    InvitePopup(root, lambda: state, lambda **kw: state.update(kw), demo="--demo" in argv, at=at)
    root.mainloop()


if __name__ == "__main__":
    main()
