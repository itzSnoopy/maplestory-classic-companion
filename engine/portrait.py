"""Find the player's character in a frame by its name tag, and crop a close-up.

No setup: the template is the character's name as the status bar draws it (bottom HUD, under the
job name). The tag under the character is the same font and size, so that text *is* the tag's
text. Measured 2026-10-24 (NOTES.md): 79x11 px on both at 1366x768; the name changes with the
character on its own.

Scales (Graphics > UI Size): the world is drawn at (window height / 768); the HUD either scales
with it ("Ideal Ratio") or stays native ("Default Ratio"). So the bar name is looked for at both
HUD scales, and the template is tried as-is and resized by world / HUD scale; the best wins.

Score: normalized cross-correlation of the grey (min of R, G, B) pattern, so brightness doesn't
matter: the backing is see-through (lighter over a bright sign) and scaled-up letters come out
dimmer, which broke fixed white/dark thresholds at 1920 Default Ratio (2026-10-24 camera tests).
Plain spots (std < FLAT_STD) can't match. Your tag: median 0.96-0.98, lowest 0.79 when clean;
best wrong spot 0.57 (1366x768, 1920x1080 Ideal and Default).

Hooks, best first (each hit says which one placed you):
  tag       the full name tag, score >= SURE
  partial   a weaker tag match (another tag or damage numbers over it) near where you were
            seen in the last few seconds
  look      your colours just above the tag (refreshed on every sure find, so new clothes are
            picked up while you play) near where you were last seen
Nothing found -> no close-up (better none than a wrong one). "Where you were a moment ago" was
tried and dropped: after a map change or behind a shop window it pointed at the wrong spot.

The template is the rendered name of the player's character (Nexon font): kept in memory only.

  python portrait.py test FRAME [FRAME...]      where the tag is, score, hook (dev check)
"""
import sys
import time

import numpy as np
from PIL import Image

NATIVE_TEXT_H = 11       # name text height at HUD scale 1 (letters with descenders)
# the status-bar name relative to the window's bottom-center, at HUD scale 1 (1366x768,
# 1024x768, 1920x1080 Default Ratio all agree; Ideal Ratio = the same times the world scale)
BAR_BOX = (-322, -19, -205, -3)
WHITE = 200              # letter pixels on the status bar (min of R, G, B): finds the name there
SURE = 0.70
PARTIAL = 0.58
LEARN_MIN = 0.80         # a resized template is swapped for the real tag only from a match this clear
NEAR_S = 15.0            # "near where you were": seen this recently (> TRACK_EVERY_S)...
NEAR_PX = 160            # ...and within this many pixels (times the world scale)
LOOK_S = 60.0            # the colour sample is used this long after the last sure find
LOOK_MIN = 0.80          # colour sample match needed (normalized cross-correlation)
FLAT_STD = 12            # a spot this plain (std of a channel) is not a character
TRACK_EVERY_S = 10.0     # track(): at most this often...
TRACK_MAX_S = 120.0      # ...doubling after each miss up to this (hidden in a shop, cash shop)
# close-up relative to the tag text's center (top), at world scale 1: 240 x 176 (room for hats,
# weapons and skill effects; the character itself is ~70 px tall)
CLOSE = (-120, -140, 120, 36)
LOOK_BOX = (-24, -70, 24, -8)     # colour sample: the body just above the tag, world scale 1


def _fast(n):
    """Smallest size >= n made of 2s, 3s and 5s (FFTs of such sizes are several times faster)."""
    while True:
        m = n
        for f in (2, 3, 5):
            while m % f == 0:
                m //= f
        if m == 1:
            return n
        n += 1


def _corr(a, k):
    """Valid cross-correlation of a with k (FFT)."""
    h, w = k.shape
    s = (_fast(a.shape[0] + h - 1), _fast(a.shape[1] + w - 1))
    c = np.fft.irfft2(np.fft.rfft2(a, s) * np.fft.rfft2(k[::-1, ::-1], s), s)
    return c[h - 1:a.shape[0], w - 1:a.shape[1]]


def _ncc(a, a2, k):
    """Normalized cross-correlation of grey image a (a2 = a*a) with grey template k: 1.0 = the
    same pattern at any brightness/contrast. Plain spots score 0."""
    h, w = k.shape
    n = h * w
    kz = k - k.mean()
    kn = float(np.sqrt((kz * kz).sum())) or 1.0
    one = np.ones((h, w), np.float32)
    s1, s2 = _corr(a, one), _corr(a2, one)
    var = np.maximum(s2 - s1 * s1 / n, 0.0)
    c = _corr(a, kz) / (np.sqrt(var) * kn + 1e-6)
    c[var < (FLAT_STD ** 2) * n] = 0.0
    return c


class Hit:
    def __init__(self, x, y, w, h, score, how, scale):
        self.x, self.y, self.w, self.h = x, y, w, h        # tag text box in frame pixels
        self.score, self.how, self.scale = score, how, scale

    @property
    def center(self):
        return self.x + self.w / 2, self.y

    def __repr__(self):
        return f"Hit({self.how} {self.score:.2f} at {self.x},{self.y} x{self.scale:.2f})"


class NameTag:
    def __init__(self):
        self._tpl = {}           # (name bytes, frame size) -> [(template, scale)]
        self.sure = None         # (t, Hit) of the last sure find
        self.look = None         # (t, small RGB array) colour sample from the last sure find
        self.track_at = 0.0      # next track()
        self.misses = 0

    # ---- the template, from the status bar
    def bar_name(self, frame):
        """(gray crop of the name's text, HUD scale) from the status bar, or (None, None)."""
        W, H = frame.size
        world = H / 768
        for s in sorted({1.0, round(world, 3)}, key=lambda v: abs(v - world)):
            x0, y0, x1, y1 = BAR_BOX
            box = (round(W / 2 + x0 * s), round(H + y0 * s), round(W / 2 + x1 * s), round(H + y1 * s))
            g = np.asarray(frame.crop(box).convert("RGB")).min(-1)
            m = g >= WHITE
            if m.sum() < 15:
                continue
            ys, xs = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
            th = ys[-1] - ys[0] + 1
            # one clean line of text at this scale, not touching the box edges (covered / wrong scale)
            if not (0.6 * NATIVE_TEXT_H * s <= th <= 1.25 * NATIVE_TEXT_H * s) or xs[-1] >= m.shape[1] - 1:
                continue
            # 1 px of the bar's dark background around the text: the pattern includes its edges
            return g[max(0, ys[0] - 1):ys[-1] + 2, max(0, xs[0] - 1):xs[-1] + 2], s
        return None, None

    def templates(self, frame):
        g, hud = self.bar_name(frame)
        if g is None:
            return []
        key = (g.tobytes(), g.shape, frame.size)
        if key not in self._tpl:
            world = frame.size[1] / 768
            out = []
            for k in sorted({1.0, round(world / hud, 3)}):
                img = Image.fromarray(g.astype(np.uint8))
                if k != 1.0:
                    img = img.resize((round(img.width * k), round(img.height * k)), Image.BILINEAR)
                t = np.asarray(img).astype(np.float32)
                out.append([t, hud * k, k == 1.0])     # [grey, scale, exact (game-drawn)]
            self._tpl = {key: out}            # one character / size at a time
        return self._tpl[key]

    def _learn(self, frame, hit):
        """A resized template found you for sure: swap it for the tag as the game drew it (the
        world scaling isn't the same as our resize). Only from a clear match (LEARN_MIN), so the
        cut is the same text and nothing is over it."""
        if hit.score < LEARN_MIN:
            return
        for tp in self.templates(frame):
            if tp[2] or abs(tp[1] - hit.scale) > 0.01:
                continue
            a = np.asarray(frame.crop((hit.x, hit.y, hit.x + hit.w, hit.y + hit.h)).convert("RGB"))
            if a.shape[:2] == tp[0].shape:
                tp[0], tp[2] = a.min(-1).astype(np.float32), True

    # ---- finding
    def _search(self, frame, area=None, scale=None):
        """Best (score, x, y, template, scale) in `area` (x0, y0, x1, y1) or the play area.
        scale: only the template of that scale (the one that found you last time)."""
        tpls = self.templates(frame)
        if scale is not None:
            tpls = [tp for tp in tpls if abs(tp[1] - scale) < 0.01] or tpls
        if not tpls:
            return None
        W, H = frame.size
        top_hud = round(H - 60 * H / 768)          # the status bar and chat line aren't the world
        x0, y0, x1, y1 = area or (0, 0, W, top_hud)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(top_hud, y1)
        a = np.asarray(frame.crop((x0, y0, x1, y1)).convert("RGB")).min(-1).astype(np.float32)
        a2 = a * a
        best = None
        for t, sc, _ in tpls:
            if t.shape[0] > a.shape[0] or t.shape[1] > a.shape[1]:
                continue
            c = _ncc(a, a2, t)
            y, x = np.unravel_index(np.argmax(c), c.shape)
            if best is None or c[y, x] > best[0]:
                best = (float(c[y, x]), x0 + int(x), y0 + int(y), t, sc)
        return best

    def find(self, frame, t=None, look=True):
        """Where the character is: a Hit, or None. look=False skips the colour sample (cheaper)."""
        t = time.time() if t is None else t
        W, H = frame.size
        world = H / 768
        near = self.sure if self.sure and t - self.sure[0] <= NEAR_S else None
        res = None
        if near:                                    # cheap first: around the last sure spot
            cx, cy = near[1].center
            r = NEAR_PX * world
            res = self._search(frame, (int(cx - 1.6 * r), int(cy - r), int(cx + 1.6 * r), int(cy + r)),
                               scale=near[1].scale)
            if res and res[0] >= PARTIAL and res[0] < SURE:
                hit = self._hit(res, "partial")
                if abs(hit.center[0] - cx) <= r and abs(hit.center[1] - cy) <= r:
                    full = self._search(frame)      # a sure tag elsewhere still wins
                    if full and full[0] >= SURE:
                        return self._sure(frame, t, self._hit(full, "tag"))
                    return hit
        if not res or res[0] < SURE:
            res = self._search(frame)
        if res and res[0] >= SURE:
            return self._sure(frame, t, self._hit(res, "tag"))
        return self._by_look(frame, t) if look else None

    @staticmethod
    def _hit(res, how):
        score, x, y, tpl, sc = res
        return Hit(x, y, tpl.shape[1], tpl.shape[0], score, how, sc)

    def _sure(self, frame, t, hit):
        self.sure = (t, hit)
        if hit.score >= SURE:
            self._learn(frame, hit)
        s = self._look_box(hit, frame.size)
        if s:
            self.look = (t, np.asarray(frame.crop(s).convert("RGB")).astype(np.float32))
        return hit

    @staticmethod
    def _look_box(hit, size):
        cx, cy = hit.center
        s = hit.scale
        b = (round(cx + LOOK_BOX[0] * s), round(cy + LOOK_BOX[1] * s),
             round(cx + LOOK_BOX[2] * s), round(cy + LOOK_BOX[3] * s))
        return b if b[0] >= 0 and b[1] >= 0 and b[2] <= size[0] and b[3] <= size[1] else None

    def _by_look(self, frame, t):
        """Your colours near the last sure spot (tag covered, you aren't)."""
        if not (self.look and self.sure and t - self.look[0] <= LOOK_S):
            return None
        p = self.look[1]
        h0 = self.sure[1]
        cx, cy = h0.center
        r = NEAR_PX * frame.size[1] / 768
        ph, pw = p.shape[:2]
        x0, y0 = int(cx - 1.6 * r), int(cy + LOOK_BOX[1] * h0.scale - r / 2)
        area = (max(0, x0), max(0, y0), min(frame.size[0], int(cx + 1.6 * r)), min(frame.size[1], int(y0 + r)))
        a = np.asarray(frame.crop(area).convert("RGB")).astype(np.float32)
        if a.shape[0] < ph or a.shape[1] < pw:
            return None
        # normalized cross-correlation per channel, averaged (zero-mean patch)
        score = None
        n = ph * pw
        for ch in range(3):
            k = p[..., ch] - p[..., ch].mean()
            kn = np.sqrt((k * k).sum()) or 1.0
            A = a[..., ch]
            s1 = _corr(A, np.ones((ph, pw), np.float32))
            s2 = _corr(A * A, np.ones((ph, pw), np.float32))
            var = s2 - s1 * s1 / n
            flat = var < (FLAT_STD ** 2) * n          # plain areas (a window, the sky) can't match
            c = _corr(A, k) / (np.sqrt(np.maximum(var, 1.0)) * kn)
            c[flat] = -1.0
            score = c if score is None else score + c
        score /= 3
        y, x = np.unravel_index(np.argmax(score), score.shape)
        best = float(score[y, x])
        if best < LOOK_MIN:
            return None
        # back from the sample's corner to the tag
        tx = area[0] + x - LOOK_BOX[0] * h0.scale - h0.w / 2
        ty = area[1] + y - LOOK_BOX[1] * h0.scale
        return Hit(int(tx), int(ty), h0.w, h0.h, best, "look", h0.scale)

    def track(self, frame, t=None):
        """Cheap upkeep while playing (call with full frames you already have): keeps the last
        spot and the colour sample fresh. Every TRACK_EVERY_S, longer after misses."""
        t = time.time() if t is None else t
        if t < self.track_at:
            return None
        hit = self.find(frame, t, look=False)
        # a miss means a full-frame search (0.3-0.9 s at 1920): wait longer after each one
        self.misses = 0 if hit else self.misses + 1
        self.track_at = t + min(TRACK_EVERY_S * 2 ** self.misses, TRACK_MAX_S)
        return hit

    # ---- the close-up
    def portrait(self, frame, t=None):
        """Close-up of the character (PIL image), or None if it isn't found."""
        hit = self.find(frame, t)
        if not hit:
            return None
        cx, cy = hit.center
        s = hit.scale
        W, H = frame.size
        box = (max(0, round(cx + CLOSE[0] * s)), max(0, round(cy + CLOSE[1] * s)),
               min(W, round(cx + CLOSE[2] * s)), min(H, round(cy + CLOSE[3] * s)))
        if box[2] - box[0] < 40 or box[3] - box[1] < 40:
            return None
        return frame.crop(box)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "test":
        tag = NameTag()
        for i, p in enumerate(sys.argv[2:]):
            f = Image.open(p).convert("RGB")
            t0 = time.perf_counter()
            hit = tag.find(f, t=float(i))
            print(f"{p}: {hit or 'not found'}  ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    else:
        print(__doc__)
