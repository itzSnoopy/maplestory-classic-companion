"""board.py - the versus board over the game: a black "cage match" panel with bulb-board names, scores and timer.

Three phases in one board (plus a tie), all drawn from challenge.json (the tracker's, components/challenge/duel.py):
  start   timer -0:05 .. -0:01, middle text STARTING IN / READY / SET / GRIND, names and both sprites shown
  live    names, sprites (the opponent on the left, mirrored, you on the right), 000 VS 000, timer counts down
  winner  [SCORE] WINNER <NAME> [sprite], timer held at 0:00, ~10 s, then the board closes
  tie     TIE in big bulbs between both sprites, each score in its housing
The timer box is bolted under the panel. Flames (setting challenge.flames: blaze, tongues, embers, arcane, poison,
painted) lick up both outer sides. Sprites: sprites.py (ClassicRank, fetched per client). No sound, no flashing.

  python -m components.challenge.board --demo [--at=X,Y] [--phase=start|live|winner|tie] [--scale=50|75|100]   cycles the phases
  python -m components.challenge.board --shot OUT_DIR                                        PNGs of every phase / flame style / scale

Size: setting challenge.scale (50 | 75 | 100 %, default 50) = the window's size relative to the 920 x 250 design. The
picture is drawn at a supersample (s, >= 2) and resized down with Lanczos, so bulbs, flames and the bolted timer stay
smooth; sprites are nearest-neighbour when enlarged, Lanczos when reduced. The bulb NAMES keep a minimum on-screen
bulb pitch (MIN_NAME_PX), so at 50 % they are drawn a little bigger than the design (still fits: NAME_MAX_PITCH).
"""
import json
import math
import sys
import time

from base.paths import DATA
from components.challenge import bulbs
from hub.ui.overlay import KEY

CHALLENGE = DATA / "challenge.json"
FLAME_STYLES = ("blaze", "tongues", "embers", "arcane", "poison", "painted")
W, H = 920, 250                 # the whole picture, design pixels (flames included)
PX, PY, PW, PH = 40, 8, 840, 176    # the panel inside it
CX = W // 2
NAME_COLS = 13                  # characters per side
SPRITE = 96
HOUSE_W, HOUSE_H = 190, 90      # a 3-digit score housing at pitch 10
SCORE_PITCH, NAME_PITCH, TIMER_PITCH = 10, 4.4, 7
SCALES = (50, 75, 100)          # challenge.scale choices (percent of the design size)
DEFAULT_SCALE = 50
MIN_NAME_PX = 2.6               # smallest on-screen bulb pitch for the names (the digits and timer are bigger anyway)
NAME_MAX_PITCH = 5.0            # 13 name columns must fit in their half of the panel
SHOW_WINNER_S = 10.0
STALE_S = 15.0
FPS = 5
BLOCK = 4                       # a flame block (pixel-art style)
STEEL = (190, 196, 208)
DARK = (16, 16, 20)

PALETTES = {
    "blaze": [(122, 12, 12), (205, 28, 20), (255, 112, 22), (255, 192, 44), (255, 242, 176)],
    "tongues": [(122, 12, 12), (205, 28, 20), (255, 112, 22), (255, 192, 44), (255, 242, 176)],
    "embers": [(92, 12, 10), (160, 32, 16), (228, 82, 22), (255, 152, 42), (255, 214, 120)],
    "arcane": [(10, 20, 112), (30, 62, 204), (62, 142, 255), (132, 222, 255), (232, 250, 255)],
    "poison": [(10, 70, 22), (30, 142, 42), (92, 212, 52), (192, 250, 92), (240, 255, 190)],
    "painted": [(150, 18, 14), (232, 62, 22), (255, 150, 30), (255, 214, 70), (255, 246, 190)],
}


# ------------------------------------------------------------------ the static layer
def _noise(i, seed):
    n = (i * 1103515245 + seed * 12345 + 12345) & 0x7FFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0x7FFFFFFF
    return (n % 1000) / 1000.0


def _widths(style, rows, nc):
    """Flame width in blocks for every row from the bottom (0) up; the flame hugs the panel's outer edge."""
    seed = FLAME_STYLES.index(style) + 1
    out = []
    for r in range(rows):
        t = r / rows
        jag = (_noise(r // 2, seed) - 0.5) * 3.4 + 1.6 * math.sin(r * 0.55 + seed)
        if style == "tongues":
            base = nc * (1 - t ** 1.3) * (1.0, 0.35, 0.8)[(r // 6 + seed) % 3]
        elif style == "embers":
            base = nc * 0.62 * max(0.0, 1 - t / 0.5)
        else:
            base = nc * (1 - t ** 1.15)
        out.append(max(0, int(round(base + jag))) if base > 0.3 else 0)
    return out


def _flames(d, s, style, side):
    """Pixel-art flames (painted: smooth shapes) on one side of the panel. side: -1 left, +1 right."""
    nc = PX // BLOCK
    rows = PH // BLOCK
    pal = PALETTES[style]
    ws = _widths(style, rows, nc)
    edge = PX if side < 0 else PX + PW
    bottom = PY + PH
    if style == "painted":
        for fac, col in ((1.0, pal[0]), (0.78, pal[1]), (0.55, pal[2]), (0.34, pal[3]), (0.16, pal[4])):
            pts = [(edge, bottom)]
            for r in range(rows):
                w = ws[r] * BLOCK * fac
                pts.append((edge + side * w, bottom - r * BLOCK))
            pts.append((edge, bottom - rows * BLOCK))
            d.polygon([(x * s, y * s) for x, y in pts], fill=col)
        return
    for r in range(rows):
        w = ws[r]
        for c in range(w):
            ratio = (c + 0.5) / w
            lvl = 4 if ratio < 0.2 else 3 if ratio < 0.45 else 2 if ratio < 0.7 else 1 if ratio < 0.92 else 0
            if r + 1 < rows and ws[r + 1] <= c:                     # the tip of a lick is outlined
                lvl = min(lvl, 1)
            x0 = edge + side * c * BLOCK if side > 0 else edge - (c + 1) * BLOCK
            y0 = bottom - (r + 1) * BLOCK
            d.rectangle((x0 * s, y0 * s, (x0 + BLOCK) * s - 1, (y0 + BLOCK) * s - 1), fill=pal[lvl])
    if style == "embers":                                           # a few sparks drifting up
        for i in range(9):
            r = int(rows * (0.45 + 0.5 * _noise(i, 7)))
            c = int(nc * 0.75 * _noise(i, 9))
            x0 = edge + side * c * BLOCK if side > 0 else edge - (c + 1) * BLOCK
            y0 = bottom - (r + 1) * BLOCK
            d.rectangle((x0 * s, y0 * s, (x0 + BLOCK) * s - 1, (y0 + BLOCK) * s - 1), fill=pal[2 + i % 2])


def _shade(rgb, delta):
    return tuple(max(0, min(255, c + delta)) for c in rgb)


def _hex(c, default=(0, 0, 0)):
    try:
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError, IndexError):
        return default


def _bolt(d, x, y, r, s):
    d.ellipse(((x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s), fill=(120, 124, 134), outline=(40, 42, 48))
    d.line(((x - r * 0.6) * s, (y - r * 0.2) * s, (x + r * 0.6) * s, (y + r * 0.2) * s), fill=(40, 42, 48),
           width=max(1, int(s * 0.8)))


_static = {}


def static_layer(bg, flames, s):
    """Flames, the cage panel with its frame and bolts, and the timer box with its brackets (cached)."""
    key = (bg, flames, s)
    if key in _static:
        return _static[key]
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (W * s, H * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for side in (-1, 1):
        _flames(d, s, flames, side)
    # the panel: background + chain-link lattice, clipped to rounded corners
    pan = Image.new("RGBA", (PW * s, PH * s), _hex(bg) + (255,))
    pd = ImageDraw.Draw(pan)
    line = _shade(_hex(bg), 16 if sum(_hex(bg)) < 380 else -16)
    step = 18
    for i in range(-PH, PW + PH, step):
        pd.line((i * s, 0, (i + PH) * s, PH * s), fill=line, width=max(1, s // 2))
        pd.line((i * s, PH * s, (i + PH) * s, 0), fill=line, width=max(1, s // 2))
    mask = Image.new("L", pan.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, PW * s - 1, PH * s - 1), radius=14 * s, fill=255)
    im.paste(pan, (PX * s, PY * s), mask)
    d.rounded_rectangle((PX * s, PY * s, (PX + PW) * s - 1, (PY + PH) * s - 1), radius=14 * s,
                        outline=STEEL, width=max(1, s))                         # the light hairline
    d.rounded_rectangle(((PX + 2) * s, (PY + 2) * s, (PX + PW - 2) * s - 1, (PY + PH - 2) * s - 1), radius=12 * s,
                        outline=DARK, width=2 * s)                              # the dark inner line
    for bx in (PX + 12, PX + PW - 12):
        for by in (PY + 12, PY + PH - 12):
            _bolt(d, bx, by, 3.2, s)
    # the timer box, bolted under the panel by two steel brackets (two bolts each)
    tw, th = 214, 70
    tx, ty = CX - tw // 2, PY + PH - 12
    for bx in (CX - 70, CX + 70):
        d.rectangle(((bx - 13) * s, (PY + PH - 6) * s, (bx + 13) * s, (ty + 14) * s), fill=(98, 102, 112),
                    outline=(40, 42, 48), width=max(1, s // 2))
        _bolt(d, bx - 7, PY + PH + 1, 2.4, s)
        _bolt(d, bx + 7, PY + PH + 1, 2.4, s)
    d.rounded_rectangle((tx * s, ty * s, (tx + tw) * s, (ty + th) * s), radius=10 * s, fill=(12, 12, 14),
                        outline=STEEL, width=max(1, s))
    d.rounded_rectangle(((tx + 3) * s, (ty + 3) * s, (tx + tw - 3) * s, (ty + th - 3) * s), radius=8 * s,
                        outline=DARK, width=2 * s)
    _static[key] = im
    if len(_static) > 12:
        _static.pop(next(iter(_static)))
    return im


# ------------------------------------------------------------------ the dynamic layer
def _house(d, s, x, y, n, color=bulbs.AMBER):
    """A 3-digit score housing at (x, y) (design px, top-left)."""
    d.rounded_rectangle((x * s, y * s, (x + HOUSE_W) * s, (y + HOUSE_H) * s), radius=8 * s, fill=(10, 10, 12),
                        outline=(120, 124, 134), width=max(1, s))
    bulbs.draw_digits(d, (x + 10) * s, (y + 10) * s, min(int(n), 999), 3, SCORE_PITCH * s, color)


def scale_of(cfg):
    """challenge.scale from the settings -> one of SCALES as a fraction (0.5 / 0.75 / 1.0)."""
    try:
        v = int(cfg.get("scale", DEFAULT_SCALE))
    except (TypeError, ValueError, AttributeError):
        v = DEFAULT_SCALE
    return min(SCALES, key=lambda c: abs(c - v)) / 100.0


def name_pitch(k):
    """Bulb pitch (design px) of the names at on-screen scale k: the design's, raised so a bulb stays >= MIN_NAME_PX."""
    return min(NAME_MAX_PITCH, max(NAME_PITCH, MIN_NAME_PX / max(k, 0.1)))


def _name(d, s, text, right, pitch=NAME_PITCH):
    """A name in bulbs, NAME_COLS characters wide (unlit cells show), left or right aligned."""
    t = (text or "").upper()[:NAME_COLS]
    t = t.rjust(NAME_COLS) if right else t.ljust(NAME_COLS)
    y = (PY + 16) * s
    if right:
        bulbs.draw_text(d, (PX + PW - 22) * s, y, t, pitch * s, anchor="r")
    else:
        bulbs.draw_text(d, (PX + 22) * s, y, t, pitch * s, anchor="l")


def _fit(text, room, top):
    return max(3, min(top, int(room // bulbs.cols(text))))


def _paste_sprite(im, s, img, x, y, mirror):
    from PIL import Image, ImageOps
    img = img.convert("RGBA")
    if mirror:
        img = ImageOps.mirror(img)
    n = int(SPRITE * s)
    # pixel pictures stay crisp when enlarged (nearest); a bigger picture is reduced smoothly (Lanczos)
    big = img.resize((n, n), Image.NEAREST if img.width < n and img.height < n else Image.LANCZOS)
    im.alpha_composite(big, (int(x * s), int(y * s)))


def draw_state(st, flames="blaze", bg="#000000", s=2, k=1.0):
    """The whole board for one state (see view_state) -> RGBA at s x design size.
    st: phase (start|live|winner|tie), left / right {name, score, img}, count (start), timer "M:SS", colon (bool),
    winner "left" | "right" (winner phase). k: the on-screen scale the result will be shown at (only the names' bulb
    size depends on it, see name_pitch)."""
    from PIL import ImageDraw
    im = static_layer(bg, flames if flames in FLAME_STYLES else "blaze", s).copy()
    d = ImageDraw.Draw(im)
    ph = st["phase"]
    L, R = st["left"], st["right"]
    cy = PY + 58 + SPRITE / 2                        # middle of the sprite row
    top = cy - SPRITE / 2
    house_y = cy - HOUSE_H / 2
    timer = st.get("timer", "0:00")
    if not st.get("colon", True):
        timer = timer.replace(":", ";")
    bulbs.draw_text(d, CX * s, (PY + PH - 12 + 12) * s, timer, TIMER_PITCH * s, anchor="m")
    if ph == "winner":
        win = R if st.get("winner") == "right" else L
        total = HOUSE_W + 16 + 424 + 16 + SPRITE
        x0 = CX - total / 2
        _house(d, s, x0, house_y, win["score"])
        mid = x0 + HOUSE_W + 16 + 212
        bulbs.draw_text(d, mid * s, (cy - 50) * s, "WINNER", _fit("WINNER", 400, 8) * s, bulbs.GREEN, anchor="m")
        nm = (win["name"] or "?").upper()[:NAME_COLS]
        bulbs.draw_text(d, mid * s, (cy + 12) * s, nm, _fit(nm, 410, 6) * s, bulbs.AMBER, anchor="m")
        _paste_sprite(im, s, win["img"], x0 + total - SPRITE, top, False)
        return im
    npitch = name_pitch(k)
    _name(d, s, L["name"], False, npitch)
    _name(d, s, R["name"], True, npitch)
    if ph == "tie":
        tie_p = 9
        total = SPRITE + 14 + HOUSE_W + 16 + bulbs.width("TIE", tie_p) + 16 + HOUSE_W + 14 + SPRITE
    else:
        total = SPRITE + 14 + HOUSE_W + 16 + 110 + 16 + HOUSE_W + 14 + SPRITE
    x0 = CX - total / 2
    mid_x = CX
    _paste_sprite(im, s, L["img"], x0, top, True)
    _paste_sprite(im, s, R["img"], x0 + total - SPRITE, top, False)
    lx = x0 + SPRITE + 14
    rx = x0 + total - SPRITE - 14 - HOUSE_W
    if ph == "start":
        n = st.get("count", 5)
        word, col = (("STARTING IN", bulbs.AMBER) if n >= 4 else ("READY", bulbs.RED) if n == 3
                     else ("SET", bulbs.AMBER) if n == 2 else ("GRIND", bulbs.GREEN))
        room = total - 2 * (SPRITE + 14) - 16
        bulbs.draw_text(d, mid_x * s, (cy - 3.5 * 10) * s, word, _fit(word, room, 10) * s, col, anchor="m")
        return im
    _house(d, s, lx, house_y, L["score"])
    _house(d, s, rx, house_y, R["score"])
    if ph == "tie":
        bulbs.draw_text(d, mid_x * s, (cy - 3.5 * tie_p) * s, "TIE", tie_p * s, bulbs.AMBER, anchor="m")
    else:
        bulbs.draw_text(d, mid_x * s, (cy - 35) * s, "VS", 10 * s, bulbs.RED, anchor="m")
    return im


def to_rgb(im, k):
    """RGBA at 2k -> the window picture (RGB over the transparent key, hard alpha like the other panels)."""
    from PIL import Image
    w, h = max(1, int(W * k)), max(1, int(H * k))
    small = im if im.size == (w, h) else im.resize((w, h), Image.LANCZOS)
    key = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
    out = Image.new("RGB", (w, h), key)
    out.paste(small.convert("RGB"), (0, 0), small.getchannel("A").point(lambda v: 255 if v > 100 else 0))
    return out


# ------------------------------------------------------------------ challenge.json -> a state to draw
def _timer(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def view_state(snap, now, seen, imgs):
    """What to draw from challenge.json, or None (no board). seen: {challenge id: time the result was first seen}
    (kept by the caller); imgs(name) -> PIL image. The opponent (the best other player in a group) is on the left."""
    a = (snap or {}).get("active")
    if not a or now - (snap.get("t") or 0) > STALE_S:
        return None
    me = snap.get("me")
    sn = now + (snap.get("server_off") or 0.0)
    players = {p["name"]: p["score"] for p in a["players"] if p["accepted"]}
    mine = max(players.get(me, 0), a.get("mine_local") or 0)
    others = sorted(((sc, n) for n, sc in players.items() if n != me), reverse=True)
    sc_o, opp = others[0] if others else (0, "?")
    st = {"left": {"name": opp, "score": sc_o, "img": imgs(opp)},
          "right": {"name": me, "score": mine, "img": imgs(me)}, "colon": int(now * 2) % 2 == 0}
    if a["state"] == "done" and a.get("result"):
        t0 = seen.setdefault(a["id"], now)
        if now - t0 > SHOW_WINNER_S:
            return None
        res = a["result"]
        st.update(timer="0:00", colon=True)
        if res["tie"]:
            st["phase"] = "tie"
            return st
        win_me = res["winner"] == me
        if not win_me:
            st["left"].update(name=res["winner"], score=next(r["score"] for r in res["ranking"]
                                                               if r["name"] == res["winner"]),
                              img=imgs(res["winner"]))
        st.update(phase="winner", winner="right" if win_me else "left")
        return st
    if a["state"] != "running":
        return None
    if sn < a["start_t"]:
        n = max(1, math.ceil(a["start_t"] - sn))
        st.update(phase="start", count=min(n, 5), timer="-" + _timer(n))
    else:
        st.update(phase="live", timer=_timer(math.ceil(max(0.0, a["end_t"] - sn))))
    return st


# ------------------------------------------------------------------ the window
class Board:
    def __init__(self, root, settings_get, settings_put, k=1.0, demo=False, at=None, phase=None):
        import tkinter as tk
        self.tk = tk
        self.root, self.get, self.put, self.demo, self.k = root, settings_get, settings_put, demo, k
        self.phase = phase
        self.scale = scale_of(settings_get())           # the size now on screen (settings: challenge.scale)
        self.photo, self.shown, self.key = None, None, None
        self.seen = {}
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
            x, y = max(0, (root.winfo_screenwidth() - int(W * k * self.scale)) // 2), 16
        self.dev = at is not None
        w.geometry(f"+{int(x)}+{int(y)}")
        w.withdraw()
        self.after_tick = None
        self._apply_lock(cfg.get("locked", False))
        self._tick()

    def _imgs(self, name):
        from components.challenge import sprites
        if self.demo:
            return sprites.silhouette()
        return sprites.get(name) or sprites.silhouette()

    def _state(self, now):
        if self.demo:
            return demo_state(now, self._imgs, self.phase)
        try:
            snap = json.loads(CHALLENGE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return view_state(snap, now, self.seen, self._imgs)

    def _tick(self):
        try:
            cfg = self.get()
            now = time.time()
            self._rescale(scale_of(cfg))
            st = self._state(now) if cfg.get("enabled", True) else None
            if st is not None:
                key = (self.scale, st["phase"], st.get("count"), st["timer"], st["colon"], st["left"]["name"], st["left"]["score"],
                       st["right"]["name"], st["right"]["score"], st.get("winner"), cfg.get("flames"), cfg.get("bg"),
                       id(st["left"]["img"]), id(st["right"]["img"]))
                if key != self.key:
                    from PIL import ImageTk
                    ke = self.k * self.scale                    # on-screen scale of the picture
                    s = max(2, int(round(ke * 2)))              # supersample, then Lanczos down
                    self.photo = ImageTk.PhotoImage(to_rgb(draw_state(st, cfg.get("flames", "blaze"),
                                                                      cfg.get("bg", "#000000"), s, ke), ke))
                    self.lbl.configure(image=self.photo)
                    self.key = key
            show = st is not None
            if show != self.shown:
                (self.w.deiconify if show else self.w.withdraw)()
                if show:
                    self._apply_lock(cfg.get("locked", False))
                self.shown = show
        except Exception as e:
            print(f"challenge board draw failed: {e}")
        self.after_tick = self.root.after(int(1000 / FPS), self._tick)

    def _rescale(self, new):
        """The size setting changed: keep the board's top centre where it was (x moves by half the width change),
        so a saved position stays sensible; a board that was never moved stays centred."""
        if new == self.scale:
            return
        dw = int(W * self.k * (new - self.scale))
        old_x, y = self.w.winfo_x(), self.w.winfo_y()
        self.scale = new
        cfg = self.get()
        if cfg.get("x") is None or cfg.get("y") is None:
            x = max(0, (self.root.winfo_screenwidth() - int(W * self.k * new)) // 2)
        else:
            x = max(0, old_x - dw // 2)
        self.w.geometry(f"+{x}+{y}")
        if not self.dev and cfg.get("x") is not None:
            self.put(x=x, y=y)

    # ---------------------------------------------------------------- move / lock
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.w.winfo_x(), e.y_root - self.w.winfo_y()

    def _drag(self, e):
        self.w.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if not self.dev:
            self.put(x=self.w.winfo_x(), y=self.w.winfo_y())

    def _menu(self, e):
        m = self.tk.Menu(self.w, tearoff=0)
        m.add_command(label="Lock (click-through)", command=lambda: self.set_locked(True))
        m.add_command(label="Hide the challenge board", command=lambda: self.put(enabled=False))
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
            print(f"challenge board lock failed: {e}")

    def destroy(self):
        if self.after_tick:
            self.root.after_cancel(self.after_tick)
        self.w.destroy()


# ------------------------------------------------------------------ demo
def demo_state(now, imgs, phase=None):
    """A looping fake duel: start 5 s, live 30 s (scores climbing, a 5:00 clock run fast), winner 10 s, a short live
    stretch, tie 10 s. phase freezes one of them."""
    names = ("Robin", "MagicianAlex")
    t = now % 60
    if phase is None:
        phase = "start" if t < 5 else "live" if t < 35 else "winner" if t < 45 else "live" if t < 50 else "tie"
    left = {"name": names[0], "score": 0, "img": imgs(names[0])}
    right = {"name": names[1], "score": 0, "img": imgs(names[1])}
    st = {"phase": phase, "left": left, "right": right, "colon": int(now * 2) % 2 == 0, "timer": "5:00"}
    if phase == "start":
        n = max(1, 5 - int(t))
        st.update(count=n, timer="-" + _timer(n))
    elif phase == "live":
        f = max(0.0, min(1.0, (t - 5) / 30)) if t < 45 else 0.9
        left["score"], right["score"] = int(51 * f), int(62 * f)
        st["timer"] = _timer(300 * (1 - f))
    elif phase == "winner":
        left["score"], right["score"] = 51, 62
        st.update(timer="0:00", winner="right", colon=True)
    elif phase == "tie":
        left["score"] = right["score"] = 57
        st.update(timer="0:00", colon=True)
    return st


def main():
    from base import lean  # noqa: F401
    argv = sys.argv[1:]
    opt = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in argv if a.startswith("--") and "=" in a}
    if "--shot" in argv:
        from pathlib import Path
        from components.challenge import sprites
        out = Path(argv[argv.index("--shot") + 1])
        out.mkdir(parents=True, exist_ok=True)
        sil = sprites.silhouette()
        for sc in SCALES:
            ke = sc / 100.0
            for fl in FLAME_STYLES:
                for ph in ("start", "live", "winner", "tie"):
                    if fl != "blaze" and ph != "live":
                        continue
                    if sc != 50 and fl != "blaze":
                        continue
                    st = demo_state(0, lambda n: sil, ph) | ({"count": 5} if ph == "start" else {})
                    to_rgb(draw_state(st, fl, "#000000", max(2, int(round(ke * 2))), ke), ke
                           ).save(out / f"board_{sc}_{fl}_{ph}.png")
        print("wrote", out)
        return
    at = tuple(int(v) for v in opt["--at"].split(",")) if "--at" in opt else None
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    state = {"enabled": True, "locked": False, "flames": opt.get("--flames", "blaze"), "bg": "#000000",
             "scale": int(opt.get("--scale", DEFAULT_SCALE))}
    Board(root, lambda: state, lambda **kw: state.update(kw), demo="--demo" in argv, at=at, phase=opt.get("--phase"))
    root.mainloop()


if __name__ == "__main__":
    main()
