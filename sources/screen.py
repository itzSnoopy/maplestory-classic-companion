"""Screen source: reads the game HUD from frames (the game window, or saved images for replay/tests).

Frame providers (where frames come from: sources/wgc.py, ImageFrames below) are separate from the
HUD reader (how they're read), so the same reading runs on live frames and on saved screenshots.

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

import charname
import deathbox
import glyphs
import hpbar
import jobread
import maplist
import mapwatch
import ocr
import panels
import parse
import perf
import vitals
import party
from layout import anchor_for, crop, layouts
import minimap
from sources.base import ABSENT, OK, UNAVAILABLE, Observation, Source


PARTY_EVERY_S = 1.0     # party box look (party.py, ~4 ms)


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}")


# ------------------------------------------------------------------ frame providers
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
        if it == "unavailable":                 # simulate the source being unreachable
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

    def __init__(self, cfg, frames):
        self.cfg = cfg
        self.frames = frames                # sources/wgc.WgcFrames live, ImageFrames in tests
        self.name = f"screen/{self.frames.name}"
        self.retry_s = cfg.get("service", {}).get("source_retry_s", 20)
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
        self.maps = mapwatch.MapWatch()     # when to check the map name (sticky, mapwatch.py)
        self.read_now = False               # a poll saw a new map: the tracker reads right away
        self.facts = []                     # [(t, kind, value)] for the character profile (panels.py)
        self.panels_due = False             # the overview showed a window: next poll grabs it whole
        self.panels_seen = frozenset()      # windows open at the last look (for the log)
        self.spots = self._load_spots()     # {kind: box} where each window was last seen
        self.who = None                     # status-bar name fingerprint (charname.py)
        self._name_guess = {}               # fingerprint -> Tesseract's guess (once each)
        self.away_since_who = True          # game left the screen since the last name check
        self.last_level = (None, 0.0)       # (HUD level, time) for scoring stat-window reads
        self.size = tuple(self.res or (1920, 1080))     # last full frame size (for part boxes)
        self.party_t = 0.0                  # last party box look (party.py, about once a second)
        self.party = party.Finder()         # remembers where the box is

    def read(self, hints):
        t, now = time.time(), datetime.now()
        try:
            with perf.timed("grab_full"):
                frame = self.frames.grab()
        except StopIteration:
            raise
        except Exception as e:
            return Observation(t, UNAVAILABLE, source=self.name, note=f"{type(e).__name__}: {e}")

        exp_rc = self.regions["exp"]
        crops = {"exp": crop(frame, exp_rc["box"], self.res, anchor_for("exp", exp_rc), "exp")}
        self.read_now = False
        if not exp_bar_visible(crops["exp"], exp_rc.get("white_min", 230)):
            self.maps.reset()               # loading / cash shop / gone: likely a new map after
            self._away(t)
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
            if name == "map":      # only when due (mapwatch.py); else the confirmed name
                if self.maps.due(t):
                    self._check_map(frame, t, crops)
                raw[name] = self.maps.raw or ""
                continue
            if name not in crops:
                crops[name] = crop(frame, rc["box"], self.res, anchor_for(name, rc), name)
            if name == "exp":
                with perf.timed("exp_read"):
                    raw[name], suspect = read_exp(crops[name], rc, hints)
            else:
                raw[name] = ocr.read(crops[name], rc, kind=name)

        self.frame, self.crops, self.stamp = frame, crops, now.strftime("%H%M%S_%f")[:-4]
        self._take_due_shots(t)
        exp_text = raw.get("exp", "")
        # unknown digits ('?8[??.74%]') would parse loosely into wrong numbers: no reading instead
        cur, _, pct = parse.parse_exp(exp_text) if "?" not in exp_text else (None, None, None)
        level = parse.parse_level(raw["level"]) if raw.get("level") else None
        job = self.jobs.update(frame, level, t)       # reads only when due (start / advancement)
        map_name = self.maps.name
        self._whoami(t, frame)
        if level:
            self.last_level = (level, t)
        self.size = frame.size
        self._vitals(t, frame)
        self._party(t, frame.crop(party.search_box(frame.size)), (0, 0))
        self._panels(t, frame, hud=(cur, pct, level), hud_img=(frame, None))
        with perf.timed("hp_bar"):
            hp = hpbar.hp_pct(frame, tuple(self.res or (1920, 1080)))     # HP bar fill, no OCR
        rawlog = {k: v.replace("\n", " / ") for k, v in raw.items()}
        rawlog["hp"] = hp
        if self.maps.map_id:
            rawlog["map_id"] = self.maps.map_id
        return Observation(
            t, OK, exp_cur=cur, exp_pct=pct, level=level, job=job, map=map_name, hp_pct=hp,
            map_confirmed=map_name is not None, suspect=suspect, source=self.name, raw=rawlog)

    def _check_map(self, frame, t, crops=None):
        """Read the minimap title (full or collapsed, found wherever it is: minimap.py) and tell
        the MapWatch. Tesseract runs only when the title crop changed. True on a new map."""
        rc = self.regions.get("map", {})
        if not rc.get("box"):
            return False
        with perf.timed("map_check"):
            changed = self._check_map_inner(frame, t, rc, crops)
        if changed:
            perf.count("map_change")
        return changed

    def _check_map_inner(self, frame, t, rc, crops):
        state, box, read_rc = minimap.locate(frame, rc, self.res)
        if box is None:                # no minimap, or one without a name (compact view)
            return self.maps.update(t, None)
        c = frame.crop(tuple(box))
        if crops is not None:
            crops["map"] = c
        h = hashlib.md5(c.tobytes()).hexdigest()
        if h != self.map_cache[0]:
            with perf.timed("ocr_map"):
                text = ocr.read(c, read_rc, kind="map")
            if state == "collapsed":
                text = minimap.to_two_lines(text)
            self.map_cache = (h, text)
        text = self.map_cache[1]
        if not text:
            return self.maps.update(t, None)
        # snap to the real Classic map list (maps_classic.json); else the cleaned raw read
        known = maplist.snap(text)
        name = known["name"] if known else parse.parse_map(text)
        return self.maps.update(t, name or None, text, known["id"] if known else None)

    def poll(self, t):
        """Between readings: is the "You have died." box up? (deathbox.py, ~20 ms a frame).
        Also takes diary pictures that came due (the ghost + tombstone shot ~2 s after death)."""
        if not getattr(self.frames, "pollable", False):
            return None                 # saved frames in tests: polling would eat them
        due = any(p[0] <= t for p in self.pending_shots)
        map_due = self.maps.due(t)
        try:
            # only the center square is needed for the box, unless a diary picture or a map
            # check is due (then the whole frame, read once for all of them)
            # a game window (stat/skills/channel) seen in the last overview: read it whole now
            full = due or map_due or self.panels_due
            small, spot_imgs, strip = None, {}, None
            with perf.timed("grab_full" if full else "grab_center"):
                if full:
                    frame = self.frames.grab()
                elif hasattr(self.frames, "grab_parts"):
                    # same frame: the center square for the box, a 1/4-scale overview to spot
                    # windows, and full-size copies of where each window was last seen (the game
                    # reopens them there), so a window open for a single frame is still read
                    # + the HP/MP text strip (vitals.py: exact numbers every look)
                    # + the top-left area with the party box (party.py), about once a second
                    spots = list(self.spots.items())
                    sbox = vitals.strip_box(self.size, tuple(self.res or (1920, 1080)))
                    pbox = party.search_box(self.size) if t - self.party_t >= PARTY_EVERY_S else None
                    imgs = self.frames.grab_parts(
                        [{"center": deathbox.PAD}, {"step": panels.SMALL}, {"box": sbox}]
                        + [{"box": b} for _, b in spots] + ([{"box": pbox}] if pbox else []))
                    frame, small, strip = imgs[0], imgs[1], imgs[2]
                    spot_imgs = {k: (img, b[:2]) for (k, b), img in zip(spots, imgs[3:])}
                    if pbox:
                        self._party(t, imgs[-1], pbox[:2])
                else:
                    frame = self.frames.grab(center=deathbox.PAD)
        except Exception:
            return None
        if frame.getbbox() is None:
            self._away(t)
            return None                 # game not on screen (black frame: gone / minimized)
        # a channel switch (and a portal) fades the whole screen to black: mean < 30 for ~1.2 s,
        # so a look once a second lands in it (character.py: dark right after the channel window)
        if sum(frame.resize((16, 16), Image.NEAREST).convert("L").getdata()) < 30 * 256:
            self._away(t)
        if due:
            self._take_due_shots(t, frame)
        if map_due and self._check_map(frame, t):
            self.read_now = True        # new map: take a reading now, so the tracker sees it
        if full:
            self._vitals(t, frame)
            if t - self.party_t >= PARTY_EVERY_S:
                self._party(t, frame.crop(party.search_box(frame.size)), (0, 0))
            hud_img = (frame, None)
        elif strip is not None:
            self._vitals(t, strip, origin=True)
            hud_img = (strip, True)
        else:
            hud_img = None
        self._panels(t, frame if full else None, small, spot_imgs=spot_imgs, hud_img=hud_img)
        with perf.timed("deathbox"):
            return deathbox.is_dead(frame)

    # ---- game windows with long-lived facts (panels.py -> character.py)
    def _away(self, t):
        self.facts.append((t, "away", None))
        self.away_since_who = True

    def _whoami(self, t, frame):
        """Who is playing: the status-bar name's pixel fingerprint (charname.py), sent on every
        reading so a new one can be confirmed by the next screenshot (character.py scores it).
        after_away: the game left the screen since the last check (login screen = a character
        switch is possible); the tracker starting counts as that too."""
        try:
            with perf.timed("charname"):
                fp, c, clean = charname.fingerprint(frame, tuple(self.res or (1920, 1080)))
            if not fp:
                return
            if fp != self.who:
                guess = self._name_guess.get(fp)
                if guess is None:
                    guess = self._name_guess[fp] = charname.guess(c) if clean else ""
                log(f"character name on screen: {fp} (status bar reads {guess!r}"
                    f"{'' if clean else ', looks covered'})")
                self.who = fp
            self.facts.append((t, "who", {"fp": fp, "guess": self._name_guess.get(fp, ""),
                                          "clean": clean, "after_away": self.away_since_who}))
            self.away_since_who = False
        except Exception as e:
            log(f"character name check failed: {type(e).__name__}: {e}")

    @staticmethod
    def _spots_path():
        from paths import DATA
        return DATA / "history" / "panel_spots.json"

    def _load_spots(self):
        try:
            import json
            d = json.loads(self._spots_path().read_text(encoding="utf-8"))
            return {k: list(v) for k, v in d.items() if k in panels.EXTENT}
        except (OSError, ValueError):
            return {}

    def _remember_spots(self, res):
        changed = False
        for kind, r in res.items():
            b = panels.window_box(kind, r)
            b = [max(0, b[0]), max(0, b[1]), b[2], b[3]]
            if self.spots.get(kind) != b:
                self.spots[kind] = b
                changed = True
        if changed:
            try:
                import json
                p = self._spots_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(self.spots), encoding="utf-8")
            except OSError:
                pass

    def _vitals(self, t, img, origin=None):
        """Exact HP / MP numbers from the status bar (vitals.py) -> a 'vitals' fact."""
        try:
            with perf.timed("vitals"):
                r = vitals.read(img, tuple(self.res or (1920, 1080)), origin)
            if r["hp"] or r["mp"]:
                self.facts.append((t, "vitals", {"hp": r["hp"], "mp": r["mp"]}))
        except Exception as e:
            log(f"HP/MP read failed: {type(e).__name__}: {e}")

    def _party(self, t, img, origin):
        """The party box (party.py) -> a 'party' fact: rows top to bottom (empty = no box).
        Names are matched later (live.py), where your own and your partners' names are known."""
        self.party_t = t
        try:
            with perf.timed("party"):
                rows = self.party.read(img, origin, t)
            self.facts.append((t, "party", rows))
        except Exception as e:
            log(f"party box read failed: {type(e).__name__}: {e}")

    def _teach_vitals(self, stat, hud_img):
        """The Stat window shows the same HP / MP as the status bar: teach its digits."""
        if not hud_img:
            return
        img, origin = hud_img
        for k in ("hp", "mp"):
            if stat.get(k) and "?" in (vitals.read_text(img, k, tuple(self.res or (1920, 1080)),
                                                         origin) or ""):
                new = vitals.try_learn(img, k, stat[k], tuple(self.res or (1920, 1080)), origin)
                if new:
                    log(f"learned status-bar digit(s) {sorted(new)} from the Stat window")

    def _save_icons(self, skills):
        """Cut each skill's icon from the player's own Skill window, once (local only:
        templates/skill_icons/, never shipped). The skill bar shows them."""
        from paths import DATA
        d = DATA / "templates" / "skill_icons"
        for row in skills.get("skills", []):
            icon = row.pop("_icon", None)
            if icon is None or not row.get("id"):
                continue
            p = d / f"{row['id']}.png"
            if not p.exists():
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(icon.copy()).save(p)
                    log(f"saved skill icon: {row.get('name')}")
                except Exception as e:
                    log(f"skill icon save failed: {e}")

    def _panels(self, t, frame=None, small=None, hud=None, spot_imgs=None, hud_img=None):
        """Look for the Character Stat / Skill / Change Channel windows. With the overview
        (`small`): read the ones that are at their remembered spot (`spot_imgs`, same frame);
        others get a whole-frame grab next look. With a full frame: read them all.
        `hud` = (cur, pct, level) from this same frame teaches unknown digits."""
        try:
            with perf.timed("panels_scan"):
                if small is not None:
                    cands = panels.scan(small)
                elif frame is not None:
                    cands = panels.scan(frame.resize(
                        (frame.width // panels.SMALL, frame.height // panels.SMALL), Image.NEAREST))
                else:
                    return
            if not cands:
                self.panels_due = False
                self.panels_seen = frozenset()
                return
            with perf.timed("panels_read"):
                if frame is None:
                    res = {}
                    for kind in cands:
                        if kind in (spot_imgs or {}):
                            img, origin = spot_imgs[kind]
                            r = panels.read_at(kind, img, origin)
                            if r:
                                res[kind] = r
                    # moved / never seen: grab the whole frame next look
                    self.panels_due = any(k not in res for k in cands)
                else:
                    import numpy as np
                    a = np.asarray(frame.convert("RGB"))
                    res = panels.read(a, cands)
                    if hud and "stat" in res and self._teach(a, res["stat"], hud):
                        res["stat"] = panels.read_stat(a, cands["stat"]) or res["stat"]
                    self.panels_due = False
            self._remember_spots(res)
            if "stat" in res:
                self._teach_vitals(res["stat"], hud_img)
            if "skills" in res:
                self._save_icons(res["skills"])
            kinds = frozenset(res)
            if kinds != self.panels_seen:
                if kinds:
                    log("window(s) seen: " + ", ".join(sorted(kinds)))
                self.panels_seen = kinds
            for kind, v in res.items():
                v = {k: x for k, x in v.items() if not k.startswith("_")}
                if kind == "stat":
                    # what the HUD says, to check the window against (character.py scoring)
                    lv, lt = self.last_level
                    v["hud"] = {"level": hud[2] if hud else (lv if t - lt < 120 else None),
                                "exp": hud[0] if hud else None}
                self.facts.append((t, kind, v))
        except Exception as e:
            self.panels_due = False
            log(f"window read failed: {type(e).__name__}: {e}")

    @staticmethod
    def _teach(a, stat, hud):
        """Unknown digits in the stat window: the HUD shows the same EXP and level right now."""
        cur, pct, level = hud
        crops, raw, learned = stat.get("_crops") or {}, stat.get("raw") or {}, False
        if level and "?" in raw.get("level", ""):
            learned |= panels.try_learn(crops["level"], str(level))
        if cur is not None and pct is not None and "?" in raw.get("exp", ""):
            for p in {int(pct), int(round(pct))}:           # whole % in the window
                if panels.try_learn(crops["exp"], f"{cur} ({p}%)"):
                    learned = True
                    break
        if learned:
            log("learned window digit(s) from the HUD's EXP/level")
        return learned

    def take_facts(self):
        """Facts gathered since the last call (for character.Profile.update)."""
        f, self.facts = self.facts, []
        return f

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

    def _take_due_shots(self, t, frame=None):
        due = [p for p in self.pending_shots if p[0] <= t]
        if due:
            self.pending_shots = [p for p in self.pending_shots if p[0] > t]
            for _, out_dir, stem in due:
                self._save_shot(frame if frame is not None else self.frame, out_dir, stem)

    def end_session(self):
        self.session_dir = None
        self.recent.clear()

    def close(self):
        self.frames.close()
