"""hangout_ui.py - the rooms panel over the game ("compact rows"): which rooms exist on your relay
and what they're doing.

One row per room:
  left    kind icon: green circle "H" = hangout, purple rounded square "P" = party
  middle  room name (bold) + small line: hangout "4 online", party "5/6", then " . activity"
  right   access mark (open ring / half circle = approval / padlock = private) and a small button
Header: "Rooms" + a refresh button. Empty: "No rooms listed right now." Errors show one quiet line.

The button is Join (or Leave on the room you are in): open rooms join at once, private ones ask for the PIN
or password in a small themed prompt, approval rooms join and then show "Waiting for approval" (polled every
10 s). The joined room sits in helper_settings.json "relay": {"room"} (hub/relay_room.py) and is shown at the
top ("In: <room> · <kind>" + Leave; owners and mods also get a Start button for a room activity, the Kill race,
with the current activity on a second line). All requests run on worker threads (components/hangout/join.py); the
Tk thread only draws, prompts and stores results. Polling (lobby.Poller) starts only while the panel is shown.

Side tags: while a relic hunt runs in the joined room, "1/32 <piece>" over "Relic Hunt" sits just right of the
panel, drawn into the same window so it moves with it (relic.json, written by the tracker's relic component:
join.read_relic). The header's "-" hides the side tags ("+" brings them back; hangout.side_min).

Drag to move (saved in helper_settings.json "hangout"), right-click for refresh / lock / hide.

  python -m components.hangout.hangout_ui --server http://127.0.0.1:8787 --at=-2400,40
"""
import json
import queue
import sys
import threading
import time
import tkinter as tk

from PIL import Image, ImageDraw

from base.paths import DATA
from components.hangout import join as J
from components.hangout import lobby
from hub import relay_addr, relay_room
from hub.ui import theme
from hub.ui.overlay import KEY, font as _font, text as _text

W_PANEL = 250
TICK_MS = 500
MAX_ROWS = 10
TOAST_S = 2.0
LONG_TOAST_S = 6.0
PENDING_POLL_S = 10.0           # waiting for approval: ask the relay this often
ACTIVE_POLL_S = 60.0            # joined: a slow check that the token still works (the tracker usually notices first)
STATUS_FILE_S = 2.0             # how often challenge.json is looked at (the activity line, command errors)
LOST_FILE_S = 2.0               # how often party.json is looked at for room_lost
RELIC_FILE_S = 2.0              # how often relic.json is looked at (the side tag)
SIDE_GAP = 8                    # space between the panel and its side tags
HANGOUT_COL = (70, 190, 110)
PARTY_COL = (150, 100, 220)
JOINED_COL = (60, 110, 80)


def _fit(d, s, font, maxw):
    if d.textlength(s, font=font) <= maxw:
        return s
    while s and d.textlength(s + "…", font=font) > maxw:
        s = s[:-1]
    return s + "…"


class Prompt:
    """A small themed prompt over the game: a title, an optional note (an error to fix), an entry (hidden for a
    PIN / password) and OK / Cancel. done(value) gets the typed text (entry=True) or True (a question); Cancel
    / Escape / clicking away does not call it."""

    def __init__(self, root, anchor, k, title, note, done, entry, secret, ok_label, start=""):
        self.done, self.entry = done, entry
        self.w = w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=theme.LINE)
        box = tk.Frame(w, bg=theme.BG)
        box.pack(padx=1, pady=1)
        px = lambda n: int(round(n * k))
        tk.Label(box, text=title, bg=theme.BG, fg=theme.TEXT, font=(theme.F, px(10), "bold"), wraplength=px(260),
                 justify="left").pack(anchor="w", padx=px(12), pady=(px(10), px(2)))
        if note:
            tk.Label(box, text=note, bg=theme.BG, fg=theme.AMBER, font=(theme.F, px(9)), wraplength=px(260),
                     justify="left").pack(anchor="w", padx=px(12))
        self.var = tk.StringVar(value=start)
        self.ent = None
        if entry:
            self.ent = tk.Entry(box, textvariable=self.var, show="*" if secret else "", width=26, bg=theme.FIELD,
                                fg=theme.TEXT, insertbackground=theme.TEXT, relief="flat",
                                font=(theme.F, px(10)))
            self.ent.pack(padx=px(12), pady=(px(8), 0), ipady=px(3))
            self.ent.bind("<Return>", lambda e: self.ok())
        row = tk.Frame(box, bg=theme.BG)
        row.pack(anchor="e", padx=px(12), pady=px(10))
        for text, cmd, bg in (("Cancel", self.close, theme.SEL), (ok_label, self.ok, "#30507a")):
            tk.Button(row, text=text, command=cmd, bg=bg, fg=theme.TEXT, relief="flat", activebackground=bg,
                      activeforeground=theme.TEXT, font=(theme.F, px(9)), padx=px(10)).pack(side="left", padx=(px(6), 0))
        w.bind("<Escape>", lambda e: self.close())
        try:
            w.update_idletasks()
            ax, ay = anchor.winfo_x(), anchor.winfo_y()
            w.geometry(f"+{max(0, ax + px(20))}+{max(0, ay + px(30))}")
            w.lift()
            (self.ent or w).focus_force()
        except tk.TclError:
            pass

    def ok(self):
        v = self.var.get().strip() if self.entry else True
        self.close()
        if v:
            self.done(v)

    def close(self):
        try:
            self.w.destroy()
        except tk.TclError:
            pass


class HangoutPanel:
    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None, server_fn=None,
                 room_get=None, room_put=None, party_path=None):
        self.root, self.get, self.put, self.demo, self.k = root, settings_get, settings_put, demo, k
        self.poller = lobby.Poller(server_fn or (lambda: ""))
        self.client = J.Client(server_fn or (lambda: ""))
        self._room = None                    # used when no room_get/room_put is wired (dev runs)
        self.room_get = room_get or (lambda: self._room)
        self.room_put = room_put or (lambda room: setattr(self, "_room", room))
        self.party_path = party_path or (DATA / "party.json")
        self.results = queue.Queue()         # worker thread -> Tk thread: callables
        self.joining = None                  # room id being joined (blocks a second click)
        self.prompt = None
        self.next_check, self.lost_t, self.checking = 0.0, 0.0, False
        self.status, self.status_t = {"error": None, "activity": None}, 0.0
        self.relic, self.relic_t = None, 0.0
        self.autofit = None                  # tk.BooleanVar: "Auto-fit pieces" in the Relic hunt menu (this run only)
        self.piece_img = None
        self.last_err = None
        self.photo, self.shown, self.drawn = None, None, None
        self.toast, self.toast_until = "", 0.0
        self.hot = []                        # (x0, y0, x1, y1, action, arg)
        self.f_name, self.f_small = _font(int(12 * k)), _font(int(10 * k), bold=False)
        self.f_head = _font(int(13 * k))
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
            x, y = 12, 560
        self.dev = at is not None
        w.geometry(f"+{int(x)}+{int(y)}")
        w.withdraw()
        self.after_tick = None
        self._apply_lock(cfg.get("locked", False))
        self._tick()

    # ---------------------------------------------------------------- drawing
    def _kind_icon(self, d, x, y, kind):
        k = self.k
        s = int(22 * k)
        col = PARTY_COL if kind == "party" else HANGOUT_COL
        box = (x, y, x + s, y + s)
        if kind == "party":
            d.rounded_rectangle(box, radius=int(5 * k), fill=col + (255,), outline=(235, 220, 255, 255))
        else:
            d.ellipse(box, fill=col + (255,), outline=(215, 255, 225, 255))
        _text(d, (x + s // 2, y + s // 2), "P" if kind == "party" else "H", self.f_small, anchor="mm")

    def _access_mark(self, d, cx, cy, access):
        k = self.k
        r = int(5 * k)
        c = (200, 210, 225, 255)
        if access == "private":                       # padlock
            d.rectangle((cx - r, cy - 1, cx + r, cy + r + 1), fill=(235, 190, 80, 255))
            d.arc((cx - r + 1, cy - r - 2, cx + r - 1, cy + 2), 180, 360, fill=(235, 190, 80, 255),
                  width=max(2, int(1.6 * k)))
        elif access == "approval":                    # half circle: needs a yes
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=c, width=max(1, int(1.5 * k)))
            d.pieslice((cx - r, cy - r, cx + r, cy + r), 90, 270, fill=c)
        else:                                         # open ring
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(120, 220, 150, 255),
                      width=max(1, int(1.5 * k)))

    def _refresh_icon(self, d, cx, cy):
        k = self.k
        r = int(5 * k)
        c = (200, 210, 230, 255)
        d.arc((cx - r, cy - r, cx + r, cy + r), 40, 330, fill=c, width=max(2, int(1.6 * k)))
        d.polygon([(cx + r + int(2 * k), cy - int(1 * k)), (cx + r - int(3 * k), cy - int(4 * k)),
                   (cx + r - int(3 * k), cy + int(3 * k))], fill=c)

    def activity(self, rooms=None):
        """The current room activity for the "In:" strip ("Kill race · 3 min left") or ''. From the lobby row of
        the joined room (what everyone sees), else from the tracker's challenge.json (unlisted rooms)."""
        cur = self.current()
        if not cur:
            return ""
        if rooms is None:
            rooms = self.poller.snapshot()[0]
        row = next((r for r in rooms if r["id"] == cur["id"]), None)
        return (row or {}).get("activity") or self.status.get("activity") or ""

    def _in_strip(self, d, W, pad, y, h, cur, act=""):
        """"In: <room> · <kind>" and a Leave button, under the header."""
        k = self.k
        d.line((pad, y, W - pad, y), fill=(60, 64, 76, 255))
        bw = int(44 * k)
        bx = W - pad - bw
        row_h = int(24 * k)
        left = bx
        if cur.get("status") != "pending" and relay_room.can_host(cur):
            sw = int(40 * k)
            sx = bx - int(4 * k) - sw
            d.rounded_rectangle((sx, y + int(3 * k), sx + sw, y + row_h - int(3 * k)), radius=int(4 * k),
                                fill=(40, 120, 60, 255), outline=(120, 220, 150, 255))
            _text(d, (sx + sw // 2, y + row_h // 2), "Start", self.f_small)
            self.hot.append((sx, y, sx + sw, y + row_h, "start", None))
            left = sx
        label = f"In: {cur.get('name')} · {cur.get('kind')}"
        if cur.get("status") == "pending":
            label = f"Waiting: {cur.get('name')}"
        _text(d, (pad, y + row_h // 2), _fit(d, label, self.f_small, left - pad - int(6 * k)), self.f_small,
              fill=(170, 235, 190, 255), anchor="lm")
        d.rounded_rectangle((bx, y + int(3 * k), bx + bw, y + row_h - int(3 * k)), radius=int(4 * k),
                            fill=(110, 56, 56, 255), outline=(220, 130, 130, 255))
        _text(d, (bx + bw // 2, y + row_h // 2), "Leave", self.f_small)
        self.hot.append((bx, y, bx + bw, y + row_h, "leave", None))
        if act:
            _text(d, (pad, y + row_h + int(7 * k)), _fit(d, act, self.f_small, W - 2 * pad), self.f_small,
                  fill=(240, 200, 120, 255), anchor="lm")

    def render(self, rooms, state):
        k = self.k
        W, pad = int(W_PANEL * k), int(6 * k)
        head_h, row_h = int(24 * k), int(36 * k)
        cur = self.room_get()
        cur = cur if isinstance(cur, dict) and cur.get("id") else None
        act = self.activity(rooms) if cur else ""
        in_h = (int(24 * k) + (int(14 * k) if act else 0)) if cur else 0
        shown = rooms[:MAX_ROWS]
        more = len(rooms) - len(shown)
        msg = lobby.LINES.get(state) if state != "ok" else ("No rooms listed right now." if not rooms else "")
        if state == "idle":
            msg = "Looking for rooms…"
        toast_on = time.time() < self.toast_until
        body = (row_h * len(shown) + (int(16 * k) if more > 0 else 0) + (int(20 * k) if msg else 0)
                + (int(20 * k) if toast_on else 0))
        H = pad + head_h + in_h + body + pad
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=int(7 * k), fill=(24, 26, 32, 235),
                            outline=(90, 96, 110, 255))
        self.hot = []
        y = pad
        _text(d, (pad, y + head_h // 2), "Rooms", self.f_head, anchor="lm")
        bx = W - pad - int(20 * k)
        d.rounded_rectangle((bx, y + int(2 * k), bx + int(20 * k), y + head_h - int(2 * k)),
                            radius=int(4 * k), fill=(40, 44, 54, 255), outline=(90, 96, 110, 255))
        self._refresh_icon(d, bx + int(9 * k), y + head_h // 2)
        self.hot.append((bx, y, bx + int(20 * k), y + head_h, "refresh", None))
        if self.side_tags():                            # "-" / "+": hide or show the side tags
            mx = bx - int(4 * k) - int(20 * k)
            d.rounded_rectangle((mx, y + int(2 * k), mx + int(20 * k), y + head_h - int(2 * k)),
                                radius=int(4 * k), fill=(40, 44, 54, 255), outline=(90, 96, 110, 255))
            cx, cy, r = mx + int(10 * k), y + head_h // 2, int(5 * k)
            lw = max(2, int(1.6 * k))
            d.line((cx - r, cy, cx + r, cy), fill=(200, 210, 230, 255), width=lw)
            if self.side_min():
                d.line((cx, cy - r, cx, cy + r), fill=(200, 210, 230, 255), width=lw)
            self.hot.append((mx, y, mx + int(20 * k), y + head_h, "side", None))
        y += head_h
        if cur:
            self._in_strip(d, W, pad, y, in_h, cur, act)
            y += in_h
        for r in shown:
            mine = bool(cur and cur["id"] == r["id"])
            if mine:
                d.rectangle((pad // 2, y + 1, W - pad // 2, y + row_h - 1), fill=JOINED_COL + (110,))
            d.line((pad, y, W - pad, y), fill=(60, 64, 76, 255))
            self._kind_icon(d, pad, y + (row_h - int(22 * k)) // 2, r["kind"])
            right_w = int(14 * k) + int(54 * k) + int(10 * k)
            tx = pad + int(22 * k) + int(8 * k)
            maxw = W - pad - right_w - tx
            _text(d, (tx, y + int(11 * k)), _fit(d, r["name"], self.f_name, maxw), self.f_name, anchor="lm")
            sub = ("Waiting for approval" if mine and cur.get("status") == "pending" else lobby.subline(r))
            _text(d, (tx, y + int(25 * k)), _fit(d, sub, self.f_small, maxw), self.f_small,
                  fill=(240, 200, 120, 255) if mine and cur.get("status") == "pending" else (160, 170, 190, 255),
                  anchor="lm")
            bw = int(54 * k)
            bx0 = W - pad - bw
            self._access_mark(d, bx0 - int(10 * k), y + row_h // 2, r["access"])
            d.rounded_rectangle((bx0, y + int(7 * k), bx0 + bw, y + row_h - int(7 * k)),
                                radius=int(4 * k), fill=(110, 56, 56, 255) if mine else (48, 70, 110, 255),
                                outline=(220, 130, 130, 255) if mine else (110, 150, 220, 255))
            _text(d, (bx0 + bw // 2, y + row_h // 2), "Leave" if mine else "Join", self.f_small)
            self.hot.append((bx0, y, bx0 + bw, y + row_h, "join", r))
            y += row_h
        if more > 0:
            _text(d, (pad, y + int(8 * k)), f"+{more} more", self.f_small, fill=(150, 160, 180, 255), anchor="lm")
            y += int(16 * k)
        if msg:
            _text(d, (pad, y + int(10 * k)), msg, self.f_small, fill=(150, 160, 180, 255), anchor="lm")
            y += int(20 * k)
        if toast_on:
            _text(d, (pad, y + int(10 * k)), _fit(d, self.toast, self.f_small, W - 2 * pad), self.f_small,
                  fill=(150, 235, 175, 255), anchor="lm")
        tags = [] if self.side_min() else self.side_tags()
        if tags:                                        # side tags: same window, right of the panel
            side = self._side_image(tags)
            full = Image.new("RGBA", (W + int(SIDE_GAP * k) + side.width, max(H, side.height)), (0, 0, 0, 0))
            full.alpha_composite(im, (0, 0))
            full.alpha_composite(side, (W + int(SIDE_GAP * k), int(4 * k)))
            im = full
        key = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
        out = Image.new("RGB", im.size, key)
        out.paste(im.convert("RGB"), (0, 0), im.getchannel("A").point(lambda v: 255 if v > 100 else 0))
        return out

    # ---------------------------------------------------------------- side tags
    def side_min(self):
        return bool(self.get().get("side_min", False))

    def side_tags(self):
        """What sits next to the panel: [(big, small)] (today only the relic hunt: "1/32", "Relic Hunt")."""
        if not self.current() or not self.relic:
            return []
        return [(f"{self.relic['found']}/{self.relic['total']}", "Relic Hunt")]

    def _side_image(self, tags):
        from hub.ui import art
        k = self.k
        icon = int(20 * k)
        if self.piece_img is None or self.piece_img.width != icon:
            self.piece_img = art.piece(icon)
        tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        w = int(max(max(tmp.textlength(a, font=self.f_head) + int(4 * k) + icon,
                        tmp.textlength(b, font=self.f_name)) for a, b in tags)) + int(4 * k)
        line = int(40 * k)
        im = Image.new("RGBA", (w, line * len(tags)), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for i, (big, small) in enumerate(tags):
            y = i * line
            _text(d, (int(1 * k), y + int(10 * k)), big, self.f_head, fill=(255, 214, 96, 255), anchor="lm")
            ix = int(1 * k + d.textlength(big, font=self.f_head) + 4 * k)
            im.alpha_composite(self.piece_img, (ix, y + int(10 * k) - icon // 2))
            _text(d, (int(1 * k), y + int(29 * k)), small, self.f_name, anchor="lm")
        return im

    # ---------------------------------------------------------------- loop
    def _tick(self):
        try:
            show = bool(self.get().get("enabled", True))
            if show:
                self.poller.tick()                       # polls only while shown
                rooms, state, ver = self.poller.snapshot()
                self._room_tick()
                cur = self.room_get() or {}
                sig = (ver, state, time.time() < self.toast_until, cur.get("id"), cur.get("status"), self.joining,
                       cur.get("role"), self.activity(rooms), str(self.relic), self.side_min())
                if sig != self.drawn:
                    from PIL import ImageTk
                    self.photo = ImageTk.PhotoImage(self.render(rooms, state))
                    self.lbl.configure(image=self.photo)
                    self.drawn = sig
            if show != self.shown:
                (self.w.deiconify if show else self.w.withdraw)()
                if show:
                    self._apply_lock(self.get().get("locked", False))
                    self.drawn = None
                self.shown = show
        except Exception as e:
            print(f"hangout panel draw failed: {e}")
        self.after_tick = self.root.after(TICK_MS, self._tick)

    # ---------------------------------------------------------------- actions
    def say(self, s, secs=TOAST_S):
        self.toast, self.toast_until, self.drawn = s, time.time() + secs, None

    def current(self):
        r = self.room_get()
        return r if isinstance(r, dict) and r.get("id") and r.get("token") else None

    def _worker(self, work, done):
        """Run work() off the Tk thread; done(result) runs on the Tk thread (in _room_tick)."""
        def run():
            try:
                res = work()
            except Exception as e:                      # never lose the click silently
                res = e
            self.results.put(lambda: done(res))
        threading.Thread(target=run, daemon=True, name="hangout-join").start()

    # ---- the buttons
    def on_join(self, room):
        """The row button: Leave on the room you are in; else join (asking to leave the current room first)."""
        cur = self.current()
        if self.joining:
            return
        if cur and cur["id"] == room["id"]:
            return self.on_leave()
        if cur:
            self._ask(f"Leave {cur.get('name')} and join {room.get('name')}?", None,
                      lambda ok: ok and self._leave_then(lambda: self._begin_join(room)),
                      ok_label="Leave and join")
        else:
            self._begin_join(room)

    def on_leave(self):
        cur = self.current()
        if cur and not self.joining:
            self._leave_then(None)

    def _leave_then(self, after):
        cur = self.current()
        if not cur:
            return after() if after else None
        self.joining = cur["id"]
        self.drawn = None

        def done(res):
            self.joining = None
            ok, msg = res if isinstance(res, tuple) else (False, "Couldn't leave")
            if ok:
                self.room_put(None)
                self.say(msg or f"Left {cur.get('name')}")
                self.poller.refresh()
                if after:
                    after()
            else:
                self.say(msg, LONG_TOAST_S)
            self.drawn = None
        self._worker(lambda: self.client.leave(cur), done)

    # ---- joining: name, secret, attempt, then what the relay said
    def _begin_join(self, room, name=None, secret=None, note=""):
        name = name or J.own_name()
        if not name:
            return self._ask("Your character name in this room", note or "Letters and digits, up to 16.",
                             lambda v: v and self._begin_join(room, name=v), entry=True, ok_label="Next")
        if room.get("access") == "private" and secret is None:
            return self._ask(f"PIN or password for {room.get('name')}", note,
                             lambda v: v and self._begin_join(room, name=name, secret=v),
                             entry=True, secret=True, ok_label="Join")
        self._attempt(room, name, secret)

    def _attempt(self, room, name, secret):
        self.joining = room["id"]
        self.drawn = None

        def done(res):
            self.joining = None
            self.drawn = None
            if isinstance(res, Exception):
                return self.say("Couldn't join", LONG_TOAST_S)
            self._joined(room, name, res)
        self._worker(lambda: self.client.join(room, name, secret or None), done)

    def _joined(self, room, name, out):
        if out.kind in (J.JOINED, J.PENDING):
            self.room_put(out.room)
            self.next_check = time.time() + (PENDING_POLL_S if out.kind == J.PENDING else ACTIVE_POLL_S)
            self.poller.refresh()
            return self.say("Waiting for approval" if out.kind == J.PENDING else f"Joined {room.get('name')}",
                            LONG_TOAST_S)
        if out.kind == J.WRONG_SECRET:                   # let them retry
            return self._begin_join(room, name=name, secret=None, note=out.message)
        if out.kind in (J.NAME_TAKEN, J.NAME_BAD):       # another name
            return self._ask("Another character name for this room", out.message,
                             lambda v: v and self._begin_join(room, name=v), entry=True, ok_label="Join",
                             start=name)
        self.say(out.message or "Couldn't join", LONG_TOAST_S)   # locked (429), gone, offline, refused

    # ---- keeping the joined room honest (Tk thread, from _tick)
    def _room_tick(self):
        while True:
            try:
                self.results.get_nowait()()
            except queue.Empty:
                break
        cur, now = self.current(), time.time()
        if not cur:
            return
        self._read_lost(cur, now)
        self._read_status(now)
        self._read_relic(now)
        cur = self.current()
        if not cur or self.joining or now < self.next_check or self.checking:
            return
        self.next_check = now + (PENDING_POLL_S if cur["status"] == "pending" else ACTIVE_POLL_S)
        self.checking = True

        def done(res):
            self.checking = False
            self.drawn = None
            now_cur = self.current()
            if not now_cur or now_cur["id"] != cur["id"] or not isinstance(res, tuple):
                return
            role = self.client.last_role if res[0] == J.ACTIVE else None
            if res[0] == J.ACTIVE and now_cur["status"] != "active":
                self.room_put({**now_cur, "status": "active", **({"role": role} if role else {})})
                self.say(f"You're in {now_cur.get('name')}", LONG_TOAST_S)
            elif role and role != now_cur.get("role"):            # promoted / demoted: the Start button follows
                self.room_put({**now_cur, "role": role})
            elif res[0] == J.LOST:                       # one 401 ends it: never retry a bad token
                self._lost(now_cur, denied=now_cur["status"] == "pending")
        self._worker(lambda: self.client.check_me(cur), done)

    def _read_status(self, now):
        """challenge.json (the tracker's): the activity line, and a command's error shown once."""
        if now - self.status_t < STATUS_FILE_S:
            return
        self.status_t = now
        st = J.read_status(self.party_path.parent, now)
        if st != self.status:
            self.status, self.drawn = st, None
        if st["error"] and st["error"] != self.last_err:
            self.say(st["error"], LONG_TOAST_S)
        self.last_err = st["error"]

    def _read_relic(self, now):
        """relic.json (the tracker's): the side tag's numbers while a hunt runs in my room."""
        if now - self.relic_t < RELIC_FILE_S:
            return
        self.relic_t = now
        r = J.read_relic(self.party_path.parent, now)
        if r != self.relic:
            self.relic, self.drawn = r, None

    def _read_lost(self, cur, now):
        """The tracker reports a refused token in party.json (room_lost): clear the room and say so."""
        if now - self.lost_t < LOST_FILE_S:
            return
        self.lost_t = now
        try:
            pj = json.loads(self.party_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(pj, dict) and pj.get("room_lost") and pj.get("room_lost_id") == cur["id"]:
            self._lost(cur)

    def _lost(self, cur, denied=False):
        self.room_put(None)
        self.say(f"The owner didn't approve you in {cur.get('name')}" if denied
                 else f"You're no longer in {cur.get('name')}", LONG_TOAST_S)
        self.poller.refresh()
        self.drawn = None

    # ---- the small themed prompt
    def _ask(self, title, note, done, entry=False, secret=False, ok_label="OK", start=""):
        if self.prompt is not None:
            self.prompt.close()
        self.prompt = Prompt(self.root, self.w, self.k, title, note or "", done, entry, secret, ok_label, start)

    def _click(self, action, arg):
        if action == "refresh":
            self.poller.refresh()
            self.say("Refreshing…")
        elif action == "join" and arg:
            self.on_join(arg)
        elif action == "leave":
            self.on_leave()
        elif action == "start":
            self.on_start()
        elif action == "side":
            self.put(side_min=not self.side_min())
            self.drawn = None

    # ---- the Start button (owner / mod): pick an activity + minutes, hand it to the tracker
    def on_start(self):
        cur = self.current()
        if not cur or not relay_room.can_host(cur):
            return
        m = tk.Menu(self.w, tearoff=0)
        self._fill_activity_menu(m)
        m.tk_popup(self.w.winfo_pointerx(), self.w.winfo_pointery())

    def _fill_activity_menu(self, m):
        """The activities an owner / mod can start (Start button and right-click > Start activity): Kill race
        (minutes), Relic hunt (difficulty, a submenu), and "Stop the current activity" while one is on."""
        for minutes in J.RACE_MINUTES:
            m.add_command(label=f"Kill race · {minutes} min", command=lambda mi=minutes: self.start_race(mi))
        relic = tk.Menu(m, tearoff=0)
        if self.autofit is None:
            self.autofit = tk.BooleanVar(master=self.w, value=False)
        relic.add_checkbutton(label="Auto-fit pieces (they go straight into place)", variable=self.autofit)
        relic.add_separator()
        for key, label in J.RELIC_LEVELS:
            relic.add_command(label=label, command=lambda k=key: self.start_relic(k))
        m.add_cascade(label="Relic hunt", menu=relic)
        m.add_separator()
        m.add_command(label="Stop the current activity", command=self.stop_current,
                      state="normal" if self.activity() else "disabled")

    def stop_current(self):
        """Stop whatever runs: the relic hunt (relic component) or the kill race (challenge component)."""
        if "Relic hunt" in self.activity() or self.relic:
            self.stop_relic()
        else:
            self.stop_activity()

    def _send(self, cmd, msg):
        if self.demo:
            print("room command:", cmd)
            return
        try:
            J.write_command(cmd, self.party_path.parent)
        except OSError:
            return self.say("Couldn't reach the tracker", LONG_TOAST_S)
        self.say(msg, LONG_TOAST_S)

    def start_race(self, minutes):
        self._send({"action": "start", "kind": "race", "minutes": minutes}, "Starting the kill race…")

    def start_relic(self, difficulty):
        fit = bool(self.autofit.get()) if self.autofit is not None else False
        self._send_relic({"action": "start", "difficulty": difficulty, "autofit": fit},
                         "Starting the relic hunt (auto-fit)…" if fit else "Starting the relic hunt…")

    def stop_relic(self):
        self._send_relic({"action": "stop"}, "Stopping the relic hunt…")

    def _send_relic(self, cmd, msg):
        if self.demo:
            print("room command:", cmd)
            return
        try:
            J.write_relic_command(cmd, self.party_path.parent)
        except OSError:
            return self.say("Couldn't reach the tracker", LONG_TOAST_S)
        self.say(msg, LONG_TOAST_S)

    def stop_activity(self):
        self._send({"action": "stop"}, "Stopping…")

    # ---------------------------------------------------------------- move / lock
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.w.winfo_x(), e.y_root - self.w.winfo_y()
        self._press = (e.x_root, e.y_root)

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        px, py = getattr(self, "_press", (e.x_root, e.y_root))
        if abs(e.x_root - px) < 4 and abs(e.y_root - py) < 4:        # a click, not a drag
            for x0, y0, x1, y1, action, arg in self.hot:
                if x0 <= e.x < x1 and y0 <= e.y < y1:
                    return self._click(action, arg)
        if not self.dev:
            self.put(x=self.w.winfo_x(), y=self.w.winfo_y())

    def _menu(self, e):
        m = tk.Menu(self.w, tearoff=0)
        m.add_command(label="Refresh", command=lambda: self._click("refresh", None))
        cur = self.current()
        if cur and cur.get("status") != "pending" and relay_room.can_host(cur):
            act = tk.Menu(m, tearoff=0)
            self._fill_activity_menu(act)
            m.add_cascade(label="Start activity", menu=act)
        else:
            m.add_command(label="Start activity (room owner or mod)", state="disabled")
        m.add_command(label="Lock (click-through)", command=lambda: self.set_locked(True))
        m.add_command(label="Hide rooms panel", command=lambda: self.put(enabled=False))
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
            print(f"hangout panel lock failed: {e}")

    def destroy(self):
        if self.prompt is not None:
            self.prompt.close()
        if self.after_tick:
            self.root.after_cancel(self.after_tick)
        self.w.destroy()


def run_standalone(server, at=None, force_allowed=False):
    """Dev: the panel alone against `server` (the sim's --show). force_allowed: this process only."""
    if force_allowed:
        from base import internet
        internet.allowed = lambda name, path=None: True
    root = tk.Tk()
    root.withdraw()
    cfg = {"enabled": True, "locked": False, "x": None, "y": None}
    HangoutPanel(root, lambda: cfg, lambda **kw: cfg.update(kw), at=at or (40, 40),
                 server_fn=lambda: relay_addr.normalize(server))        # room kept in memory only (dev)
    root.mainloop()


def main():
    from base import lean  # noqa: F401
    server = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--server=")), "")
    at = next((tuple(int(v) for v in a.split("=", 1)[1].split(",")) for a in sys.argv
               if a.startswith("--at=")), None)
    run_standalone(server, at, force_allowed=True)


if __name__ == "__main__":
    main()
