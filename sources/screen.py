"""Screen source: reads the game HUD from frames (OBS today, saved images for replay/tests).

Frame providers (where frames come from) are separate from the HUD reader (how they're read),
so the same reading runs on live OBS frames and on saved screenshots.

HUD reading:
  exp   -> glyphs.py exact pixel-font match; unknown digits solved from EXP needed (hints),
           Tesseract fallback only when it agrees with every known glyph
  level -> glyphs.py (glyphs_level.json); new badge digits learned right after a level-up
  map   -> Tesseract on the minimap title, cached by crop hash
"""
import hashlib
import itertools
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from PIL import Image

import glyphs
import jobread
import maplist
import ocr
import parse
from capture_obs import crop, from_config
from layout import anchor_for, layouts
import minimap
from sources.base import ABSENT, OK, UNAVAILABLE, Observation, Source


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}")


# ------------------------------------------------------------------ frame providers
class ObsFrames:
    """Live frames from an OBS source over WebSocket. Raises if OBS isn't reachable."""
    name = "obs"

    def __init__(self, cfg):
        self.cfg, self.src, self.client = cfg, cfg["obs"]["source"], None

    def grab(self):
        if self.client is None:
            self.client = from_config(self.cfg)
            log("connected to OBS")
        try:
            return self.client.screenshot(self.src)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.client:
            self.client.close()
        self.client = None


class ImageFrames:
    """Replay: a list of image paths/PIL images; None means 'game not on screen' (black frame).
    Raises StopIteration when exhausted."""
    name = "replay"

    def __init__(self, items, size=(1920, 1080)):
        self.items, self.i, self.size = list(items), 0, size

    def grab(self):
        if self.i >= len(self.items):
            raise StopIteration
        it = self.items[self.i]
        self.i += 1
        if it == "unavailable":                 # simulate OBS being down
            raise ConnectionError("replay: source unavailable")
        if it is None:
            return Image.new("RGB", self.size)
        return it if isinstance(it, Image.Image) else Image.open(it).convert("RGB")

    def close(self):
        pass


# ------------------------------------------------------------------ EXP reading helpers
def _agrees(glyph_text, tess_text):
    return len(glyph_text) == len(tess_text) and all(
        g == "?" or g == t for g, t in zip(glyph_text, tess_text))


# the whole EXP bar text as the game draws it: '1,234[56.78%]' (no leading zeros, 3-digit groups)
EXP_TEXT_RE = re.compile(r"^(0|[1-9]\d{0,2}(,\d{3})*)\[(0|[1-9]\d?|100)\.\d\d%\]$")


def solve_unknowns(g, need):
    """Fill '?' glyphs by arithmetic: with EXP needed known, the only digits that make
    cur/need match the displayed % (to 2 decimals) are the right ones. Unique answer or None."""
    q = [i for i, ch in enumerate(g) if ch == "?"]
    if not q or len(q) > 3 or not need:
        return None
    sols = []
    for combo in itertools.product("0123456789,", repeat=len(q)):
        s = list(g)
        for i, d in zip(q, combo):
            s[i] = d
        s = "".join(s)
        if not EXP_TEXT_RE.match(s):        # e.g. '0[,.74%]' parses loosely as 0 EXP, 0%
            continue
        cur, _, pct = parse.parse_exp(s)
        if cur is None or pct is None or cur >= need:
            continue
        if abs(cur * 100 / need - pct) <= 0.0101:
            sols.append(s)
    return sols[0] if len(sols) == 1 else None


def read_exp(img, rc, hints):
    """Returns (text, suspect)."""
    if not rc.get("glyphs"):
        return ocr.read(img, rc, kind="exp"), False
    wm = rc.get("white_min", 230)
    g = glyphs.read(img, wm)
    if "?" not in g:
        return g, False
    solved = solve_unknowns(g, hints.get("need"))
    if solved:
        try:
            new = glyphs.learn(img, solved, wm)
            log(f"learned glyph(s) {sorted(new.values())} from {solved!r} (solved from EXP needed)")
            return solved, False
        except ValueError as e:
            log(f"glyph learn skipped: {e}")
    if g.count("?") > 3:
        # too little is known to check Tesseract against (it misreads this font: '15' -> '45'),
        # and trusting it poisons the EXP-needed estimate. Skip; a solvable frame comes soon.
        return g, True
    t = ocr.read(img, {**rc, "glyphs": False}, kind="exp")
    if not _agrees(g, t):
        return g, True          # Tesseract contradicts known glyphs: don't trust it
    cur, _, pct = parse.parse_exp(t)
    plausible = hints.get("plausible")
    if plausible and plausible(cur, pct):
        try:
            new = glyphs.learn(img, t, wm)
            if new:
                log(f"learned glyph(s) {sorted(new.values())} from {t!r}")
                return t, False
        except ValueError as e:
            log(f"glyph learn skipped: {e}")
    return t, True


def exp_bar_visible(img, white_min=230):
    """True when the EXP bar text is on screen: both green brackets and some white glyphs."""
    seg = glyphs.segment(img, white_min)
    return sum(k == "green" for k, _ in seg) >= 2 and sum(k == "glyph" for k, _ in seg) >= 3


# ------------------------------------------------------------------ the source
class ScreenSource(Source):
    name = "screen"

    def __init__(self, cfg, frames=None):
        self.cfg = cfg
        self.frames = frames or ObsFrames(cfg)
        self.name = f"screen/{self.frames.name}"
        self.retry_s = cfg.get("service", {}).get("obs_retry_s", 20)
        self.regions = cfg["regions"]
        self.res = cfg.get("source_resolution")
        self.save_mode = cfg.get("save_frames", "suspect")    # True | False | "suspect"
        self.map_cache = (None, "")
        self.recent = deque(maxlen=3)       # last full frames, saved if a level-up shows up
        self.save_after, self.after_dir = 0, None
        self.session_dir = self.gallery = None
        self.frame = self.crops = self.stamp = None
        self.warned_res = None          # last frame size logged
        self.jobs = jobread.JobWatcher(cfg.get("job_read"), tuple(self.res or (1920, 1080)), log)
        self.pending_shots = []             # [(due_t, out_dir, stem)] for diary snapshots
        self._nametag = None

    def read(self, hints):
        t, now = time.time(), datetime.now()
        try:
            frame = self.frames.grab()
        except StopIteration:
            raise
        except Exception as e:
            return Observation(t, UNAVAILABLE, source=self.name, note=f"{type(e).__name__}: {e}")

        exp_rc = self.regions["exp"]
        crops = {"exp": crop(frame, exp_rc["box"], self.res, anchor_for("exp", exp_rc), "exp")}
        if not exp_bar_visible(crops["exp"], exp_rc.get("white_min", 230)):
            return Observation(t, ABSENT, source=self.name, note="EXP bar not visible")
        if frame.size != self.warned_res:          # log each window-size change once
            how = ("recorded boxes from layouts.json" if f"{frame.size[0]}x{frame.size[1]}" in layouts()
                   else "boxes moved by anchor (not in layouts.json yet: python layout.py record)")
            if self.res and tuple(self.res) != frame.size:
                log(f"frame is {frame.size[0]}x{frame.size[1]}; using {how}")
            elif self.warned_res:
                log(f"frame is back to {frame.size[0]}x{frame.size[1]} (config boxes)")
            self.warned_res = frame.size

        raw, suspect = {}, False
        for name, rc in self.regions.items():
            if not rc.get("box"):
                continue
            read_rc = rc
            if name == "map":      # full or collapsed minimap, found wherever it is (minimap.py)
                state, box, read_rc = minimap.locate(frame, rc, self.res)
                if box is None:            # no minimap, or one without a name (compact view)
                    raw[name] = ""
                    continue
                crops[name] = frame.crop(tuple(box))
            elif name not in crops:
                crops[name] = crop(frame, rc["box"], self.res, anchor_for(name, rc), name)
            if name == "exp":
                raw[name], suspect = read_exp(crops[name], rc, hints)
            elif name == "map":
                h = hashlib.md5(crops[name].tobytes()).hexdigest()
                if h != self.map_cache[0]:
                    text = ocr.read(crops[name], read_rc, kind="map")
                    if state == "collapsed":
                        text = minimap.to_two_lines(text)
                    self.map_cache = (h, text)
                raw[name] = self.map_cache[1]
            else:
                raw[name] = ocr.read(crops[name], rc, kind=name)

        self.frame, self.crops, self.stamp = frame, crops, now.strftime("%H%M%S_%f")[:-4]
        self._take_due_shots(t)
        exp_text = raw.get("exp", "")
        # unknown digits ('?8[??.74%]') would parse loosely into wrong numbers: no reading instead
        cur, _, pct = parse.parse_exp(exp_text) if "?" not in exp_text else (None, None, None)
        level = parse.parse_level(raw["level"]) if raw.get("level") else None
        job = self.jobs.update(frame, level, t)       # reads only when due (start / advancement)
        # map: snap to the real Classic map list (maps_classic.json); else the cleaned raw read
        known = maplist.snap(raw["map"]) if raw.get("map") else None
        map_name = known["name"] if known else (parse.parse_map(raw["map"]) if raw.get("map") else None)
        rawlog = {k: v.replace("\n", " / ") for k, v in raw.items()}
        if known:
            rawlog["map_id"] = known["id"]
        return Observation(
            t, OK, exp_cur=cur, exp_pct=pct, level=level, job=job, map=map_name,
            suspect=suspect, source=self.name, raw=rawlog)

    def begin_session(self, session_dir, gallery_dir):
        self.session_dir, self.gallery = Path(session_dir), Path(gallery_dir)
        self.recent.clear()
        self.save_after = 0

    def after_step(self, obs, event, ctx):
        if self.frame is None or self.session_dir is None:
            return
        now = datetime.fromtimestamp(obs.t)
        level = ctx.get("level")

        # teach the level badge right after a level-up (or with a forced level on the first frame)
        lrc = self.regions.get("level", {})
        lu = ctx.get("last_levelup_t")
        recent_lvlup = lu is not None and obs.t - lu < 60
        if lrc.get("glyphs") and "?" in obs.raw.get("level", "") and level and \
                (recent_lvlup or (ctx.get("forced_level") and ctx.get("exp_total") == 0)):
            try:
                new = glyphs.learn(self.crops["level"], str(level), lrc.get("white_min", 230),
                                   lrc.get("templates"))
                log(f"learned level glyph(s) {sorted(new.values())}")
            except ValueError as e:
                log(f"level glyph learn skipped: {e}")

        if self.save_mode is True or (self.save_mode == "suspect"
                                      and (obs.suspect or event == "rejected")):
            d = self.session_dir / "frames"
            d.mkdir(parents=True, exist_ok=True)
            for name, img in self.crops.items():
                img.save(d / f"{self.stamp}_{name}.png")

        # level-up evidence: frames just before, the detecting frame, and 2 after
        if event == "levelup":
            d = self.session_dir / "levelups" / f"Lv{level or 'up'}_{now:%H%M%S}"
            d.mkdir(parents=True, exist_ok=True)
            for s, img in self.recent:
                img.save(d / f"before_{s}.png")
            self.frame.save(d / f"at_{self.stamp}.png")
            self.gallery.mkdir(parents=True, exist_ok=True)
            self.frame.save(self.gallery / f"Lv{level or 'up'}_{now:%Y-%m-%d_%H%M}.png")
            self.save_after, self.after_dir = 2, d
        elif self.save_after:
            self.frame.save(self.after_dir / f"after_{self.stamp}.png")
            self.save_after -= 1
        self.recent.append((self.stamp, self.frame))

    # ---- diary snapshots
    def snapshot(self, out_dir, stem, delays=(0,)):
        out_dir, paths = Path(out_dir), []
        for i, d in enumerate(delays):
            s = stem if i == 0 else f"{stem}_later{i}"
            paths += [out_dir / f"{s}_char.png", out_dir / f"{s}.jpg"]
            if d <= 0:
                self._save_shot(self.frame, out_dir, s)
            else:
                self.pending_shots.append((time.time() + d, out_dir, s))
        return paths

    def _save_shot(self, frame, out_dir, stem):
        if frame is None:
            return
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            frame.convert("RGB").save(out_dir / f"{stem}.jpg", quality=88)
            if self._nametag is None:
                from portrait import NameTag
                self._nametag = NameTag()
            p = self._nametag.portrait(frame)
            if p is not None:
                p.save(out_dir / f"{stem}_char.png")
        except Exception as e:
            log(f"snapshot failed: {e}")

    def _take_due_shots(self, t):
        due = [p for p in self.pending_shots if p[0] <= t]
        if due:
            self.pending_shots = [p for p in self.pending_shots if p[0] > t]
            for _, out_dir, stem in due:
                self._save_shot(self.frame, out_dir, stem)

    def end_session(self):
        self.session_dir = None
        self.recent.clear()

    def close(self):
        self.frames.close()
