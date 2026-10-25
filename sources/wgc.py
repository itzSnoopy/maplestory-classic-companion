"""Frames straight from the game window via Windows Graphics Capture.

The same outside-the-game method screen recorders use: Windows composites the window and hands
us a copy. Nothing is injected into the game and nothing is sent to it. Works when the window
is covered or moved; not when it's minimized (Windows stops drawing it).

Needs `pip install windows-capture`. Use with config: "source": {"type": "wgc"}.
Optional config "wgc": {"window_class": "MapleStoryClass", "max_age_s": 5, "min_update_ms": 1000}.

Frames are the game's *client area* (title bar and borders cut off in windowed mode), so crop
boxes stay in game pixels. When the game window isn't there, grab()
returns a black frame, so the screen source reports ABSENT ("EXP bar not visible") exactly as
the game being closed.
"""
import ctypes
import threading
import time
from ctypes import wintypes

import numpy as np
import win32gui
from PIL import Image

user32 = ctypes.windll.user32
try:   # real pixel coordinates on scaled displays; harmless if already set
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

DWMWA_EXTENDED_FRAME_BOUNDS = 9


def find_window(cls="MapleStoryClass"):
    h = win32gui.FindWindow(cls, None)
    return h or None


def client_box(hwnd):
    """Client area inside the captured image: (x, y, w, h). WGC captures the window's visible
    frame (DWM extended frame bounds), which excludes the invisible resize borders."""
    fr = wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(fr), ctypes.sizeof(fr))
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, w, h = win32gui.GetClientRect(hwnd)
    return cx - fr.left, cy - fr.top, w, h


class WgcFrames:
    """Frame provider for ScreenSource. Keeps one capture session open on the game window and
    copies a frame only when grab() asks for one."""
    name = "wgc"
    pollable = True         # live window: ScreenSource.poll may grab extra frames between reads

    def __init__(self, cfg):
        c = cfg.get("wgc", {})
        self.cls = c.get("window_class", "MapleStoryClass")
        self.max_age = c.get("max_age_s", 5)      # a frame older than this is "no picture"
        # how often Windows may hand us a frame. Each delivered frame costs ~4 ms CPU even if
        # nobody asked for it; skipped ones cost nothing. Follows Settings > General "Screen
        # checks per second" (capture.py, default 1/s = 1000 ms); config "wgc.min_update_ms"
        # pins it instead (dev tools, bursts). A grab waits for the next frame, so up to this long.
        self.fixed_ms = c.get("min_update_ms")
        self.min_update_ms = self._wanted_ms()
        self.fallback = tuple(cfg.get("source_resolution") or (1920, 1080))
        self.hwnd = self.ctl = None
        self.lock = threading.Lock()
        self.want = threading.Event()
        self.ready = threading.Event()
        self.want_parts = []                     # [((x0, y0, x1, y1), step)] in captured-image px
        self.buf, self.closed = None, False      # raw BGRA copies of just those parts
        self.last = (None, 0.0, None)            # (parts, time, RGB images) of the last good grab

    def _wanted_ms(self):
        if self.fixed_ms is not None:
            return int(self.fixed_ms)
        try:
            import capture
            return int(1000 / capture.live_fps())     # the slider, or a regen burst
        except Exception:
            return 1000

    # -- capture session ----------------------------------------------------------
    def _start(self, hwnd):
        from windows_capture import WindowsCapture     # imported late: optional dependency
        self.stop()
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd,
                             minimum_update_interval=self.min_update_ms)

        @cap.event
        def on_frame_arrived(frame, control):
            if self.want.is_set():
                # the buffer is only valid inside this call: copy just the part asked for
                fb = frame.frame_buffer
                bufs = []
                for (x0, y0, x1, y1), step in self.want_parts:
                    x1, y1 = min(x1, fb.shape[1]), min(y1, fb.shape[0])
                    # step > 1: every step-th pixel (a small overview, ~1/step^2 of the bytes)
                    bufs.append(fb[y0:y1:step, x0:x1:step].copy() if x1 > x0 and y1 > y0 else None)
                with self.lock:
                    self.buf = bufs if all(b is not None for b in bufs) else None
                self.want.clear()
                self.ready.set()

        @cap.event
        def on_closed():
            self.closed = True
            self.ready.set()

        self.closed = False
        self.ctl = cap.start_free_threaded()
        self.hwnd = hwnd

    def stop(self):
        if self.ctl is not None:
            try:
                self.ctl.stop()
            except Exception:
                pass
        self.ctl, self.hwnd = None, None

    # -- provider API -------------------------------------------------------------
    def grab(self, timeout=2.0, box=None, center=None):
        """The game's client area as an RGB image. box=(x0, y0, x1, y1) in client pixels, or
        center=n for the 2n x 2n square around the client's center, grabs only that part: a
        few hundred KB copied instead of the whole frame (8 MB at 1920x1080)."""
        return self.grab_parts([{"box": box, "center": center}], timeout)[0]

    def grab_parts(self, parts, timeout=2.0):
        """Several parts of the *same* frame in one go (one wait for Windows, not one per part).
        Each part is {"box": (x0, y0, x1, y1)} or {"center": n} or {} (whole client area), plus
        optional "step": k for every k-th pixel (step 4 = a 1/4-scale overview, ~0.5 MB).
        Returns one RGB image per part."""
        hwnd = find_window(self.cls)
        if not hwnd or win32gui.IsIconic(hwnd):
            self.stop()
            return [self._black_part(p) for p in parts]
        want_ms = self._wanted_ms()
        if want_ms != self.min_update_ms:           # the setting changed: new session at that rate
            self.min_update_ms = want_ms
            self.stop()
        if hwnd != self.hwnd or self.closed:
            self._start(hwnd)
        cx, cy, cw, ch = client_box(hwnd)
        if cw <= 0 or ch <= 0:
            return [self._black_part(p) for p in parts]
        want = []
        for p in parts:
            box, center = p.get("box"), p.get("center")
            if center is not None:
                box = (cw // 2 - center, ch // 2 - center, cw // 2 + center, ch // 2 + center)
            x0, y0, x1, y1 = box or (0, 0, cw, ch)
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(cw, x1), min(ch, y1)
            want.append(((cx + x0, cy + y0, cx + x1, cy + y1), max(1, int(p.get("step", 1)))))
        self.want_parts = want
        self.ready.clear()
        self.want.set()
        if not self.ready.wait(timeout) or self.closed or self.buf is None:
            # no new frame (the game draws constantly, so this means it's stuck or gone);
            # fall back to the last grab of the same parts if it's recent
            self.want.clear()
            lp, lt, limgs = self.last
            if lp == want and limgs is not None and time.time() - lt <= self.max_age:
                return limgs
            return [self._black_part(p) for p in parts]
        with self.lock:
            bufs, self.buf = self.buf, None
        imgs = []
        for buf in bufs:
            h, w = buf.shape[:2]
            imgs.append(Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1))  # one pass
        self.last = (want, time.time(), imgs)
        return imgs

    def _black_part(self, p):
        img = self._black(p.get("box"), p.get("center"))
        k = max(1, int(p.get("step", 1)))
        return img if k == 1 else Image.new("RGB", (max(1, img.width // k), max(1, img.height // k)))

    def _black(self, box=None, center=None):
        if center is not None:
            return Image.new("RGB", (2 * center, 2 * center))
        if box:
            return Image.new("RGB", (max(1, box[2] - box[0]), max(1, box[3] - box[1])))
        return Image.new("RGB", self.fallback)

    def close(self):
        self.stop()


if __name__ == "__main__":     # quick check: python -m sources.wgc out.png
    import sys
    f = WgcFrames({})
    t0 = time.perf_counter()
    im = f.grab()
    print(f"hwnd={f.hwnd} size={im.size} {1000 * (time.perf_counter() - t0):.0f} ms")
    t0 = time.perf_counter()
    im = f.grab()
    print(f"second grab {1000 * (time.perf_counter() - t0):.0f} ms")
    if len(sys.argv) > 1:
        im.save(sys.argv[1])
    f.close()
