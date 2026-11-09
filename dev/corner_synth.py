"""corner_synth.py - synthetic message-corner test for engine/msgcorner.py.

Draws the gain lines (`You received EXP (+N)`, `You have gained mesos (+N)`, `Bonus EXP for PARTY (+N)`, item pickups)
with Arial 12 (system arial.ttf, fontmode "1", like engine/seed.py) over any background picture and feeds them to MsgCorner
look by look, then scores what it read against the exact script. No game pixels live in this file: the backgrounds are
paths you pass in (screenshots of the player's own game, kept local).

What is measured from real recordings (NOTES 2026-11-08 round 3):
- ink is white (255) or pale yellow (about 255,242,140 for teammates' EXP and Bonus lines), the outline is a one pixel DROP SHADOW
  (down and right of every ink pixel, black), the right ink column is crop col 312 and the glyph rows are the slot's 13 rows
  (text origin y = -1) - checked against real lines: a rendered line matches the picture with 1 wrong pixel of ~370;
- a line is alpha-blended against the picture: ink -> bg*(1-a)+255*a, shadow -> bg*(1-a); a = 1 - 0.17 * age (age in s),
  the line is gone after ~4.4 s (Perion 20261108_153208 line 49.42: ink G 250 -> 105 in 4.3 s over a G 55 background);
- new lines appear at the bottom slot, older ones move up one pitch (14 px), at most 6 are shown.
The camera: a 320x142 window (the corner box) cut from the picture at the corner's own rows (y 790..932, + a vertical bob while
"jumping"), moved sideways 4-8 px per look while "walking", standing still in between, bouncing inside [x0, x1] (stay clear of
the quick-slot box, the chat box and the real corner at x >= 1560).

Usage:
  python dev/corner_synth.py one BG.jpg [--x0 0 --x1 1200] [--seeds 1,2] [--secs 150] [--jpeg] [--burst] [--out DIR]
      (--burst: only pickup bursts, 2-4 lines within 1-3 looks, meso+meso+item, identical values; --fast: 5 looks/s; --dropout: 4% of the looks show no line at all; --fadein: lines start at 60% brightness, full after 0.5 s)
  python dev/corner_synth.py batch MAPS.json [--seg "Map name=lo:hi" ...] [--seeds 1,2] [--out DIR]
      MAPS.json = {name: [bg paths]} or {name: {"paths": [...], "seg": [x0, x1]}}
  python dev/corner_synth.py sidebyside SYNTH_BG.jpg REAL_FRAME.jpg OUT.png   (look-alike check, local pictures only)
Set MAPLE_COMPANION_OFFLINE=1. Output goes to --out (default: next to the temp dir), never into the repo.
"""
import io
import json
import os
import random
import statistics
import sys
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from engine import msgcorner as M       # noqa: E402
from engine import seed                  # noqa: E402

W, HBOX = 320, 142                       # corner box size (BOX 1600,790,1920,932)
BOX_Y = 790
RIGHT_INK = 312                          # crop column of the last ink column
LIFE_S = 4.4                             # a line is drawn for this long
FADE_PER_S = 0.17                        # alpha lost per second
FADE_IN_S, FADE_IN_START = 0.0, 0.6          # --fadein sets 0.5 s: a line starts at 60% and is full after 0.5 s
WHITE, YELLOW = (255, 255, 255), (255, 242, 140)
ITEMS = ["Tree Branch x1 earned. (Etc)", "Red Potion x1 earned. (Use)", "Leaf x1 earned. (Etc)",
         "Arrows for Bows x17 earned. (Use)", "Snail Shell x1 earned. (Etc)", "Evil Eye Tail x1 earned. (Etc)",
         "Orange Mushroom Cap x2 earned. (Etc)"]
KIND_TEXT = {"exp": "You received EXP (+%d)", "bonus": "Bonus EXP for PARTY (+%d)", "meso": "You have gained mesos (+%d)"}

_font = None


def font():
    global _font
    if _font is None:
        _font = seed.arial(12)
        if _font is None:
            raise SystemExit("arial.ttf not found (the generator needs the system Arial)")
    return _font


_ink_cache = {}


def ink_of(text):
    """13 x 320 bool: the line's ink, right ink column at RIGHT_INK."""
    if text in _ink_cache:
        return _ink_cache[text]
    im = Image.new("1", (500, 13), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    d.text((40, -1), text, font=font(), fill=1)
    a = np.array(im, bool)
    cols = np.where(a.any(0))[0]
    shift = RIGHT_INK - int(cols.max())
    out = np.zeros((13, W), bool)
    for x in cols:
        if 0 <= x + shift < W:
            out[:, x + shift] = a[:, x]
    _ink_cache[text] = out
    return out


# ------------------------------------------------------------------ the picture
class Camera:
    """Window positions per look: stand, walk (bouncing in [x0, x1-W]), jump (vertical bob)."""

    def __init__(self, rng, x0, x1, ymax):
        self.rng, self.lo, self.hi = rng, x0, max(x0, x1 - W)
        self.x = rng.uniform(self.lo, self.hi)
        self.dir = rng.choice((-1, 1))
        self.mode, self.left = "stand", rng.randint(5, 25)
        self.jump = 0
        self.ymax = ymax

    def step(self):
        r = self.rng
        self.left -= 1
        if self.left <= 0:
            if self.mode == "stand":
                self.mode, self.left = "walk", r.randint(20, 70)
                self.dir = r.choice((-1, 1))
                self.speed = r.uniform(4, 8)
            else:
                self.mode, self.left = "stand", r.randint(8, 40)
        if self.mode == "walk":
            self.x += self.dir * self.speed
            if self.x < self.lo or self.x > self.hi:
                self.x = min(max(self.x, self.lo), self.hi)
                self.dir = -self.dir
        if self.jump <= 0 and r.random() < 0.02:
            self.jump = 8
        dy = 0
        if self.jump > 0:
            dy = int(round(10 * np.sin(np.pi * (8 - self.jump) / 8)))
            self.jump -= 1
        return int(round(self.x)), dy


def window(img, x, dy):
    y = BOX_Y + dy
    return img[y:y + HBOX, x:x + W]


def draw_lines(bg, lines, t):
    """bg: HBOX x W x 3 float; lines: [(slot, text, colour, born)] -> composited copy."""
    out = bg.copy()
    for slot, text, colour, born in lines:
        a = max(0.0, 1.0 - FADE_PER_S * (t - born))
        if FADE_IN_S:                                  # optional: the line is drawn dim at first and reaches full brightness
            a *= min(1.0, FADE_IN_START + (1.0 - FADE_IN_START) * (t - born) / FADE_IN_S)
        if a <= 0:
            continue
        y1 = M.SLOT0 - M.PITCH * slot
        if y1 < 0:
            continue
        ink = ink_of(text)
        sh = np.zeros_like(ink)
        sh[1:, 1:] = ink[:-1, :-1]
        sh &= ~ink
        region = out[y1:y1 + 13]
        cov = ink[..., None]
        shd = sh[..., None]
        region[:] = np.where(cov, region * (1 - a) + np.array(colour, float) * a, region)
        region[:] = np.where(shd, region * (1 - a), region)
    return out


# ------------------------------------------------------------------ the script
def make_script(rng, secs):
    """[(t, [line dicts])]: each line = {kind, value, bonus, colour, text}. Lines of one event arrive in the same look."""
    exp_vals = [rng.randint(1, 9), rng.randint(10, 99), rng.randint(100, 999)]
    meso_vals = [rng.randint(1, 9), rng.randint(10, 99), rng.randint(10, 99), rng.randint(100, 999)]
    ev = []

    def exp_line(v, colour=WHITE):
        return {"kind": "exp", "value": v, "colour": colour, "text": KIND_TEXT["exp"] % v, "own": colour == WHITE}

    def meso_line(v):
        return {"kind": "meso", "value": v, "colour": WHITE, "text": KIND_TEXT["meso"] % v}

    def item_line():
        return {"kind": "item", "value": None, "colour": WHITE, "text": rng.choice(ITEMS)}
    t = 3.0
    party = (secs * 0.45, secs * 0.45 + 22)
    repeats = sorted(rng.uniform(10, secs - 15) for _ in range(3))
    bursts = sorted(rng.uniform(10, secs - 15) for _ in range(3))
    while t < secs - 4:
        t += rng.uniform(1.4, 5.5)
        if repeats and t >= repeats[0]:             # identical meso values one after another (1.0-1.8 s apart)
            repeats.pop(0)
            v = rng.choice(meso_vals)
            tt = t
            for _ in range(3):
                ev.append((tt, [meso_line(v)]))
                tt += rng.uniform(1.0, 1.8)
            t = tt
            continue
        if bursts and t >= bursts[0]:               # 2-4 lines in the same look
            bursts.pop(0)
            lines = [exp_line(rng.choice(exp_vals)), meso_line(rng.choice(meso_vals))]
            if rng.random() < 0.7:
                lines.append(item_line())
            if rng.random() < 0.4:
                lines.append(meso_line(rng.choice(meso_vals)))
            ev.append((t, lines))
            continue
        if party[0] <= t <= party[1]:
            if rng.random() < 0.5:                   # a teammate's kill: yellow EXP only
                ev.append((t, [exp_line(rng.choice(exp_vals), YELLOW)]))
            else:                                    # my kill in a party: white EXP + Bonus line right below it
                v, b = rng.choice(exp_vals), rng.randint(1, 60)
                ev.append((t, [exp_line(v), {"kind": "bonus", "value": b, "colour": YELLOW, "text": KIND_TEXT["bonus"] % b}]))
            continue
        ev.append((t, [exp_line(rng.choice(exp_vals))]))
        if rng.random() < 0.6:
            ev.append((t + rng.uniform(0.15, 0.9), [meso_line(rng.choice(meso_vals) if rng.random() < 0.7 else rng.randint(1, 999))]))
        if rng.random() < 0.35:
            ev.append((t + rng.uniform(0.2, 1.2), [item_line()]))
    ev.sort(key=lambda e: e[0])
    return ev


def make_burst_script(rng, secs):
    """Pickup bursts only (round 5): every 7-11 s two to four lines land within 1-3 looks (0.05-0.35 s apart): meso + meso + item,
    identical meso values in a row, EXP + meso + item, so the stack shifts by 1, then 2 or 3 between looks."""
    ev = []
    t = 4.0
    while t < secs - 6:
        v = rng.choice([rng.randint(1, 9), rng.randint(10, 99), rng.randint(100, 999)])
        w = v if rng.random() < 0.4 else rng.randint(10, 99)
        kind = rng.choice(("mmi", "mmi", "emi", "mmmi", "mim"))
        mk = lambda x: {"kind": "meso", "value": x, "colour": WHITE, "text": KIND_TEXT["meso"] % x}
        it = lambda: {"kind": "item", "value": None, "colour": WHITE, "text": rng.choice(ITEMS)}
        ex = lambda x: {"kind": "exp", "value": x, "colour": WHITE, "text": KIND_TEXT["exp"] % x, "own": True}
        seq = {"mmi": [mk(v), mk(w), it()], "emi": [ex(rng.randint(1, 99)), mk(v), it()],
               "mmmi": [mk(v), mk(v), mk(w), it()], "mim": [mk(v), it(), mk(w)]}[kind]
        tt = t
        for ln in seq:
            ev.append((tt, [ln]))
            tt += rng.uniform(0.05, 0.35)
        t += rng.uniform(7, 11)
    return ev


# ------------------------------------------------------------------ one run
def run_one(bg_img, seed_no, x0, x1, secs=150.0, jpeg=False, store=None, save_dir=None, tag="run", dump=None, keep=None, burst=False, dt=(0.2, 0.34), dropout=0.0):
    """bg_img: HxWx3 uint8 full frame. -> result dict."""
    rng = random.Random(seed_no)
    script = make_burst_script(rng, secs) if burst else make_script(rng, secs)
    cam = Camera(random.Random(seed_no * 7 + 1), x0, x1, bg_img.shape[0])
    store = store or os.path.join(tempfile.gettempdir(), f"corner_synth_{os.getpid()}_{seed_no}.json")
    if os.path.exists(store):
        os.remove(store)
    seed.seed_msg(store)
    mc = M.MsgCorner(store)

    # look times
    looks, t = [], 0.0
    lrng = random.Random(seed_no * 13 + 5)
    rng_drop = random.Random(seed_no * 31 + 7)
    while t < secs:
        t += lrng.uniform(*dt)
        looks.append(t)
    stack = []                  # newest first: dict(line, born, first_seen)
    truth = []                  # every line: dict(kind,value,born,first_seen,bonus,own)
    ei = 0
    facts, ms = [], []
    saved = 0
    for t in looks:
        newcount = 0
        while ei < len(script) and script[ei][0] <= t:
            te, lines = script[ei]
            ei += 1
            for ln in lines:
                rec = {"kind": ln["kind"], "value": ln["value"], "born": te, "first_seen": None, "own": ln.get("own"),
                       "text": ln["text"], "colour": ln["colour"], "look_new": None}
                truth.append(rec)
                stack.insert(0, rec)
                newcount += 1
            del stack[M.MAX_LINES:]
        stack[:] = [r for r in stack if t - r["born"] <= LIFE_S]
        x, dy = cam.step()
        win = window(bg_img, x, dy).astype(float)
        draw = []
        blank = bool(dropout and rng_drop.random() < dropout)    # a look where the picture shows no line at all (capture glitch / flash)
        for slot, r in enumerate(stack):
            if r["first_seen"] is None and not blank:
                r["first_seen"] = t
                r["look_new"] = newcount
            draw.append((slot, r["text"], r["colour"], r["born"]))
        if blank:
            draw = []
        img = draw_lines(win, draw, t)
        arr = np.clip(np.rint(img), 0, 255).astype(np.uint8)
        im = Image.fromarray(arr)
        if jpeg:
            b = io.BytesIO()
            im.save(b, "JPEG", quality=90)
            im = Image.open(io.BytesIO(b.getvalue())).convert("RGB")
        if save_dir and draw and saved < 3 and len(stack) >= 3 and t > 30:
            im.save(os.path.join(save_dir, f"{tag}_look{int(t*100):05d}.png"))
            saved += 1
        if keep is not None:
            keep[t] = im
        if dump and dump[0] <= t <= dump[1]:
            im.save(os.path.join(dump[2], f"look_{t:07.2f}.png"))
        w0 = time.perf_counter()
        facts += mc.feed(t, im, cropped=True)
        ms.append(1000 * (time.perf_counter() - w0))
    res = score(truth, facts)
    res["tess_calls"] = mc.stats["tess_calls"]
    res["ms_median"] = round(statistics.median(ms), 2)
    res["ms_p95"] = round(sorted(ms)[int(0.95 * (len(ms) - 1))], 2)
    res["stats"] = {k: v for k, v in mc.stats.items() if k in ("new_lines", "resync", "unread", "unknown", "ghost", "tess_calls")}
    res["seed"] = seed_no
    res["looks"] = len(looks)
    res["_truth"], res["_facts"] = truth, facts
    return res


def score(truth, facts):
    """Per kind: shown / correct / wrong / missed / extra; overflow losses apart."""
    out = {}
    for kind in ("meso", "exp"):
        fk = [(t, v) for t, k, v in facts if k == kind + ("_gain")]
        tl = [r for r in truth if r["kind"] == kind]
        shown = [r for r in tl if r["first_seen"] is not None]
        unseen = len(tl) - len(shown)
        # bonus lines belong to the EXP line born in the same event (same born time)
        bonus_of = {}
        if kind == "exp":
            for r in truth:
                if r["kind"] == "bonus" and r["first_seen"] is not None:
                    bonus_of[round(r["born"], 6)] = r["value"]
        groups = {}
        for r in shown:
            groups.setdefault(round(r["first_seen"], 4), [None, []])
            groups[round(r["first_seen"], 4)][0] = groups[round(r["first_seen"], 4)][0] or []
        tg, fg = {}, {}
        for r in shown:
            tg.setdefault(round(r["first_seen"], 4), []).append(r)
        for t, v in fk:
            fg.setdefault(round(t, 4), []).append(v)
        correct = wrong = missed = extra = ovl_missed = 0
        own_bad = 0
        true_sum = sum(r["value"] for r in shown) if kind == "meso" else 0
        read_sum = sum(v["mesos"] for _, v in fk) if kind == "meso" else 0
        for key in set(tg) | set(fg):
            tr, rd = list(tg.get(key, [])), list(fg.get(key, []))
            for r in list(tr):
                if kind == "meso":
                    want = r["value"]
                    hit = next((v for v in rd if v["mesos"] == want), None)
                else:
                    want = (r["value"], bonus_of.get(round(r["born"], 6), 0))
                    hit = next((v for v in rd if (v["base"], v["party_bonus"]) == want), None)
                if hit is not None:
                    rd.remove(hit)
                    tr.remove(r)
                    correct += 1
                    if kind == "exp" and bool(hit.get("own")) != bool(r["own"]):
                        own_bad += 1
            n = min(len(tr), len(rd))
            wrong += n
            for r in tr[n:]:
                if (r["look_new"] or 0) >= M.MAX_LINES - 1:
                    ovl_missed += 1
                else:
                    missed += 1
            extra += len(rd) - n
        out[kind] = {"shown": len(shown), "unseen": unseen, "correct": correct, "wrong": wrong, "missed": missed,
                     "overflow_missed": ovl_missed, "extra": extra, "own_wrong": own_bad}
        if kind == "meso":
            out[kind]["sum_true"], out[kind]["sum_read"] = true_sum, read_sum
    return out


def fmt(name, res):
    m, e = res["meso"], res["exp"]
    return (f"{name:34s} s{res['seed']} meso {m['correct']}/{m['shown']} w{m['wrong']} x{m['extra']} miss{m['missed']}(+{m['overflow_missed']}ovf) "
            f"sum {m['sum_true']}/{m['sum_read']} | exp {e['correct']}/{e['shown']} w{e['wrong']} x{e['extra']} miss{e['missed']}(+{e['overflow_missed']}ovf) "
            f"own!{e['own_wrong']} | unseen {m['unseen'] + e['unseen']} tess {res['tess_calls']} ms {res['ms_median']}/{res['ms_p95']}")


def load_bg(path):
    return np.asarray(Image.open(path).convert("RGB"))


def sidebyside(synth_bg, real_frame, out_png, x=0, t=40.0, label="synthetic | real"):
    """A synthetic corner (fresh meso line, EXP at age 2.5 s, yellow Bonus) above the real corner of a frame (x3, NEAREST).
    Both pictures stay local: the output goes where you say, never into the repo."""
    bg = load_bg(synth_bg)
    win = window(bg, x, 0).astype(float)
    lines = [(0, "You have gained mesos (+26)", WHITE, t - 0.1), (1, "You received EXP (+10)", WHITE, t - 2.5),
             (2, "Bonus EXP for PARTY (+12)", YELLOW, t - 0.5)]
    syn = Image.fromarray(np.clip(np.rint(draw_lines(win, lines, t)), 0, 255).astype(np.uint8))
    real = Image.open(real_frame).convert("RGB").crop((1600, 790, 1920, 932))
    box = (100, 60, W, HBOX)
    c1 = syn.crop(box).resize((220 * 3, 82 * 3), Image.NEAREST)
    c2 = real.crop(box).resize((220 * 3, 82 * 3), Image.NEAREST)
    canvas = Image.new("RGB", (220 * 3, 82 * 3 * 2 + 6), (255, 0, 255))
    canvas.paste(c1, (0, 0))
    canvas.paste(c2, (0, 82 * 3 + 6))
    canvas.save(out_png)


def main(argv):
    os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")
    if not argv or argv[0] not in ("one", "batch", "sidebyside"):
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    global FADE_IN_S
    if '--fadein' in args:
        FADE_IN_S = 0.5
    opts = {"--x0": "0", "--x1": "1200", "--seeds": "1,2", "--secs": "150", "--out": os.path.join(tempfile.gettempdir(), "corner_synth")}
    segs, pos, flags, i = {}, [], set(), 0
    while i < len(args):
        a = args[i]
        if a in opts:
            opts[a] = args[i + 1]
            i += 2
        elif a == "--seg":
            k, v = args[i + 1].rsplit("=", 1)
            segs[k] = tuple(int(n) for n in v.split(":"))
            i += 2
        elif a.startswith("--"):
            flags.add(a)
            i += 1
        else:
            pos.append(a)
            i += 1
    out = opts["--out"]
    os.makedirs(out, exist_ok=True)
    seeds = [int(s) for s in opts["--seeds"].split(",")]
    secs = float(opts["--secs"])
    if cmd == "sidebyside":
        sidebyside(pos[0], pos[1], pos[2])
        return 0
    jobs = []
    if cmd == "one":
        jobs.append((os.path.basename(pos[0]), [pos[0]], (int(opts["--x0"]), int(opts["--x1"]))))
    else:
        data = json.load(open(pos[0]))
        for name, v in data.items():
            paths = v["paths"] if isinstance(v, dict) else v
            seg = tuple(v["seg"]) if isinstance(v, dict) and "seg" in v else segs.get(name, (0, 1200))
            jobs.append((name, paths, seg))
    rows = []
    for name, paths, seg in jobs:
        for k, s in enumerate(seeds):
            bg = load_bg(paths[k % len(paths)])
            res = run_one(bg, s, seg[0], seg[1], secs, "--jpeg" in flags, burst="--burst" in flags, dt=((0.19, 0.21) if "--fast" in flags else (0.2, 0.34)), dropout=(0.04 if "--dropout" in flags else 0.0), save_dir=out, tag=name.replace(" ", "_").replace("<", "").replace(">", "")[:20] + f"_s{s}")
            print(fmt(name, res), flush=True)
            res2 = {k2: v for k2, v in res.items() if not k2.startswith("_")}
            res2["name"] = name
            rows.append(res2)
    json.dump(rows, open(os.path.join(out, "results.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
