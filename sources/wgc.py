"""Frames straight from the game window via Windows Graphics Capture.

The same outside-the-game method screen recorders use: Windows composites the window and hands
us a copy. Nothing is injected into the game and nothing is sent to it. Works when the window
is covered or moved; not when it's minimized (Windows stops drawing it).

Needs `pip install windows-capture`. Use with config: "source": {"type": "wgc"}.
Optional config "wgc": {"window_class": "MapleStoryClass", "max_age_s": 5}.

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
        self.fallback = tuple(cfg.get("source_resolution") or (1920, 1080))
        self.hwnd = self.ctl = None
        self.lock = threading.Lock()
        self.want = threading.Event()
        self.ready = threading.Event()
        self.buf, self.buf_t, self.closed = None, 0.0, False

    # -- capture session ----------------------------------------------------------
    def _start(self, hwnd):
        from windows_capture import WindowsCapture     # imported late: optional dependency
        self.stop()
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd,
                             minimum_update_interval=100)

        @cap.event
        def on_frame_arrived(frame, control):
            if self.want.is_set():
                with self.lock:
                    self.buf, self.buf_t = frame.frame_buffer.copy(), time.time()
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
    def grab(self, timeout=2.0):
        hwnd = find_window(self.cls)
        if not hwnd or win32gui.IsIconic(hwnd):
            self.stop()
            return self._black()
        if hwnd != self.hwnd or self.closed:
            self._start(hwnd)
        self.ready.clear()
        self.want.set()
        if not self.ready.wait(timeout) or self.closed:
            # no new frame (the game draws constantly, so this means it's stuck or gone);
            # fall back to the last frame if it's recent
            self.want.clear()
            with self.lock:
                if self.buf is None or time.time() - self.buf_t > self.max_age:
                    return self._black()
        with self.lock:
            buf = self.buf
        img = Image.fromarray(buf[..., 2::-1])               # BGRA -> RGB
        x, y, w, h = client_box(hwnd)
        if (x, y, w, h) != (0, 0, img.width, img.height) and w > 0 and h > 0:
            img = img.crop((x, y, x + w, y + h))
        return img

    def _black(self):
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
