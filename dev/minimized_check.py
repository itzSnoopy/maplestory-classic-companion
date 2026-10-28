"""Does Windows Graphics Capture still give frames while the game is minimized?

sources/wgc.py returns a black frame as soon as the window is minimized (IsIconic) without asking
Windows. This test skips that shortcut and asks anyway, once a second, so the real answer shows.

  python dev/minimized_check.py [SECONDS]     then minimize the game for a while, and restore it

Prints per second: minimized yes/no, whether a frame arrived, and whether it has a picture
(not black) with the EXP bar readable. Saves nothing.
"""
import json
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
from base import lean  # noqa: E402,F401

import win32gui  # noqa: E402

import engine.sources.wgc as W  # noqa: E402
from engine.layout import anchor_for, crop  # noqa: E402
from engine.sources.screen import exp_bar_visible  # noqa: E402


class AskAnyway(W.WgcFrames):
    def grab(self, timeout=2.0, box=None, center=None):
        hwnd = W.find_window(self.cls)
        if not hwnd:
            return None
        if hwnd != self.hwnd or self.closed:
            self._start(hwnd)
        self.want_box = (0, 0, 10 ** 5, 10 ** 5)      # whole captured image, whatever its size
        self.ready.clear()
        self.want.set()
        if not self.ready.wait(timeout) or self.buf is None:
            self.want.clear()
            return None
        with self.lock:
            buf, self.buf = self.buf, None
        h, w = buf.shape[:2]
        return W.Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    cfg = json.loads((CODE / "config.json").read_text(encoding="utf-8"))
    rc = cfg["regions"]["exp"]
    f = AskAnyway({"wgc": {"min_update_ms": 100}})
    print("minimize the game now, wait a few seconds, then restore it")
    t0 = time.time()
    while time.time() - t0 < secs:
        hwnd = W.find_window(f.cls)
        mini = bool(hwnd and win32gui.IsIconic(hwnd))
        img = f.grab()
        if img is None:
            what = "no frame from Windows"
        elif img.getbbox() is None:
            what = f"frame {img.size[0]}x{img.size[1]}, all black"
        else:
            # minimized windows report no usable client rect: try the frame as it is
            x, y, cw, ch = W.client_box(hwnd) if not mini else (0, 0, img.width, img.height)
            ok = False
            if cw and ch:
                c = img.crop((x, y, x + cw, y + ch))
                ok = exp_bar_visible(crop(c, rc["box"], cfg.get("source_resolution"),
                                          anchor_for("exp", rc), "exp"), rc.get("white_min", 230))
            what = f"frame {img.size[0]}x{img.size[1]}, picture" + (", EXP bar readable" if ok else "")
        print(f"{time.time() - t0:5.1f}s  minimized={'yes' if mini else 'no ':<3}  {what}", flush=True)
        time.sleep(1)
    f.close()


if __name__ == "__main__":
    main()
