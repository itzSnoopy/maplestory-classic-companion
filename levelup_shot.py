"""levelup_shot.py - when to take the level-up diary picture.

The level-up is a fixed animation, so nothing on screen is read for it. Measured 2026-10-24 on
Lv 18 -> 19 at 4 frames a second (NOTES.md), counting from the frame where the EXP bar resets
(Lv+1, 0.00%):
  +0.6 s  light pillars rise around the character
  +1.4 s  "LEVEL UP" fades in
  +1.6 to +2.1 s  "LEVEL UP" fully drawn (the shot we want)
  +2.6 s  faded out
So the picture is taken a fixed time after the reset. Damage numbers or mobs in it are part of
the moment: no checking what else is on screen.

The reset is seen by the quick look (~1/s, sources/screen.py) or a reading: it happened somewhere
between the previous EXP read and this one, so we take the middle. The capture rate is boosted
until the shot so the grab gets a frame within ~0.2 s of the target.
"""

BEST_AFTER_RESET_S = 1.75   # middle of the fully drawn text (~1.9) minus the wait for the next frame
BOOST_FPS = 5               # frames a second until the shot (a grab waits for the next frame)
DROP_PCT = 40               # the EXP % falls at least this much at a level-up (deaths cost <= 12%)
GAP_MAX_S = 3.0             # previous read older than this: assume the reset was ~1 s ago
KEEP_S = 120                # the diary entry may ask for the picture up to this long after


class Timer:
    def __init__(self):
        self.prev = None        # (t, pct) of the last EXP read
        self.reset_t = None     # estimated moment the EXP bar reset
        self.shot_at = None     # when to grab the picture (None: nothing pending)
        self.frame = None       # the picture once taken
        self.waiting = []       # [(out_dir, stem)] diary entries asking before it was taken

    def see(self, t, pct):
        """An EXP % read at time t. True when it shows a level-up that just happened."""
        if pct is None:
            return False
        p, self.prev = self.prev, (t, pct)
        if p is None or t <= p[0] or p[1] - pct < DROP_PCT:
            return False
        before = p[0] if t - p[0] <= GAP_MAX_S else t - 1.0
        self.reset_t = (before + t) / 2
        self.shot_at = self.reset_t + BEST_AFTER_RESET_S
        self.frame, self.waiting = None, []
        return True

    def due(self, t):
        return self.shot_at is not None and t >= self.shot_at

    def took(self, frame):
        """The picture was grabbed now. Returns the diary entries waiting for it."""
        self.frame, self.shot_at = frame, None
        w, self.waiting = self.waiting, []
        return w

    def recent(self, t):
        return self.reset_t is not None and t - self.reset_t < KEEP_S
