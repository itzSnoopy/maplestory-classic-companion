"""toast.py - the "Relic piece found!" notice over the game: bottom-right corner, 8 s, no sound.
A Maple-style box (dark navy, gold frame) with the puzzle piece on the left (hub.ui.art.piece: the local
placeholder picture in <data>/assets/art/, else a drawn piece), "Relic piece found!" and "17 / 48 found".
Stays SHOW_S (8 s; a click closes it sooner).
Helper side. Reads relic.json (written by the tracker's component) about once a second."""
import time
import tkinter as tk

from hub import history as H

CHECK_S = 1.0
SHOW_S = 8.0           # player 2026-11-07: 3 s was too short to notice
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
        self.show(str(t.get("text") or "+ Relic piece"), now, st.get("found_count"), st.get("pieces_total"))

    def _close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.win = None

    def image(self, found=None, total=None):
        """The notice as an RGBA PIL image (also used by dev/relic_sim.py to save a PNG)."""
        from PIL import Image, ImageDraw
        from hub.ui import art
        from hub.ui.overlay import font, text
        k = self.shell.k if self.shell else 1.0
        px = lambda n: int(round(n * k))
        f_head, f_sub = font(px(14)), font(px(11), bold=False)
        sub = f"{found} / {total} found" if found and total else "Added to the room's table"
        icon = px(40)
        tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        tw = max(tmp.textlength("Relic piece found!", font=f_head), tmp.textlength(sub, font=f_sub))
        W, H = int(px(14) + icon + px(12) + tw + px(18)), px(62)
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r = px(9)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=r, fill=(120, 84, 30, 255))                 # outer gold rim
        d.rounded_rectangle((px(2), px(2), W - 1 - px(2), H - 1 - px(2)), radius=r - px(2),
                            fill=(246, 206, 110, 255))
        d.rounded_rectangle((px(4), px(4), W - 1 - px(4), H - 1 - px(4)), radius=r - px(3),
                            fill=(28, 36, 64, 240))                                                  # navy body
        d.line((px(10), px(7), W - px(10), px(7)), fill=(70, 86, 140, 255))                         # top shine
        ix, iy = px(14), (H - icon) // 2
        d.ellipse((ix - px(3), iy - px(3), ix + icon + px(3), iy + icon + px(3)), fill=(50, 62, 104, 255))
        pc = art.piece(icon)
        im.alpha_composite(pc, (ix, iy))
        tx = ix + icon + px(12)
        text(d, (tx, H // 2 - px(9)), "Relic piece found!", f_head, fill=(255, 214, 96, 255), anchor="lm")
        text(d, (tx, H // 2 + px(11)), sub, f_sub, fill=(220, 228, 245, 255), anchor="lm")
        return im

    def show(self, text_, now, found=None, total=None):
        root = self.shell.root
        if root is None:
            return
        if self.win is not None:
            self._close()
        img = self.image(found, total)
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        at = self.shell.at
        x = at[0] + 20 if at else sw - img.width - self.shell.px(24)
        y = at[1] + 10 if at else sh - img.height - self.shell.px(80)
        w.geometry(f"{img.width}x{img.height}+{x}+{y}")
        w.update_idletasks()
        from hub.ui import layered
        if not layered.set_image(w, img):            # no per-pixel alpha: colour key instead
            from PIL import Image, ImageTk
            from hub.ui.overlay import KEY
            try:
                w.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass
            key = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
            flat = Image.new("RGB", img.size, key)
            flat.paste(img.convert("RGB"), (0, 0), img.getchannel("A").point(lambda v: 255 if v > 100 else 0))
            w.configure(bg=KEY)
            self.photo = ImageTk.PhotoImage(flat)
            tk.Label(w, image=self.photo, bd=0, bg=KEY).pack()
        w.bind("<Button-1>", lambda e: self._close())
        for c in w.winfo_children():
            c.bind("<Button-1>", lambda e: self._close())
        self.win, self.until = w, now + SHOW_S
