"""Find the player's character in a frame by its name tag, and crop a close-up.

The name tag (white text on a semi-transparent dark box under the character) is matched with a
binary template, `templates/nametag.png`: text pixels (gray > 150) in the tag's text area, cut
from a clean frame. Matching is FFT cross-correlation of the frame's bright-pixel mask with
(+1 text, -1 non-text), normalized so a perfect match = 1.0.
Measured 2026-10-23 on 5 level-up frames: the true tag scores 0.46-1.0 (overlapping tags and
level-up effects lower it), the best wrong spot ≤ 0.14. MIN_SCORE 0.3.

The template is the rendered name of the player's character (Nexon font): keep it local.
A new character needs a new template: `python portrait.py learn FRAME.png X1 Y1 X2 Y2` with the
box around the tag's text.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

TEMPLATE = Path(__file__).parent / "templates" / "nametag.png"
THRESHOLD = 150
MIN_SCORE = 0.30
# close-up box relative to the tag text's top-left corner (character stands above the tag)
BOX = (-103, -190, 217, 40)            # 320 x 230


class NameTag:
    def __init__(self, path=TEMPLATE):
        self.T = None
        if Path(path).exists():
            self.T = (np.asarray(Image.open(path).convert("L")) > 127).astype(np.float32)
            self.K = (2 * self.T - 1)[::-1, ::-1]
            self.n = self.T.sum()

    def find(self, frame):
        """(x, y, score) of the tag text's top-left corner, or None if not found."""
        if self.T is None:
            return None
        B = (np.asarray(frame.convert("L"), dtype=np.float32) > THRESHOLD).astype(np.float32)
        H, W = B.shape
        h, w = self.K.shape
        s = (H + h - 1, W + w - 1)
        c = np.fft.irfft2(np.fft.rfft2(B, s) * np.fft.rfft2(self.K, s), s)[h - 1:H, w - 1:W]
        y, x = np.unravel_index(np.argmax(c), c.shape)
        score = float(c[y, x] / self.n)
        return (int(x), int(y), score) if score >= MIN_SCORE else None

    def portrait(self, frame):
        """Close-up of the character (PIL image), or None if the tag isn't found."""
        hit = self.find(frame)
        if not hit:
            return None
        x, y, _ = hit
        W, H = frame.size
        x1, y1 = max(0, x + BOX[0]), max(0, y + BOX[1])
        x2, y2 = min(W, x + BOX[2]), min(H, y + BOX[3])
        return frame.crop((x1, y1, x2, y2))


def learn(frame_path, box, out=TEMPLATE):
    g = np.asarray(Image.open(frame_path).convert("L").crop(box))
    T = (g > THRESHOLD).astype(np.uint8) * 255
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(T).save(out)
    print(f"template saved: {out} ({(T > 0).sum()} text pixels)")


if __name__ == "__main__":
    if len(sys.argv) == 7 and sys.argv[1] == "learn":
        learn(sys.argv[2], tuple(int(v) for v in sys.argv[3:7]))
    elif len(sys.argv) == 3 and sys.argv[1] == "test":
        hit = NameTag().find(Image.open(sys.argv[2]))
        print(hit or "name tag not found")
    else:
        print(__doc__)
