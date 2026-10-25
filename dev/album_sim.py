"""Offline test of the monster album (album.py) end to end. All outputs go to OUT_DIR, never the
real history/, diary or screenshots.

  python dev/album_sim.py OUT_DIR

1. kill matching: every spawn entry in map_mobs.json, which mobs can be caught from one EXP rise
2. EXP facts -> live.Live -> training.Training -> rise hook -> album -> Session.first_kill
   (a normal mob, a boss, a repeat kill, an ambiguous rise, party EXP), with a stub source
3. screen.py's middle-square keeper + album_shot on a saved frame (dev/fixtures, if present)
4. album.html / diary.html build; captions for first_kill and first_kill_boss
Local only: the fixture frames are Nexon imagery.
"""
import json
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "album_sim_out").resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ["MAPLE_COMPANION_DATA"] = str(OUT)        # helper_settings etc. read from here
import lean  # noqa: F401,E402,I001
import album  # noqa: E402
import diary  # noqa: E402
import live  # noqa: E402
import tracker as T  # noqa: E402

T.set_root(OUT)
fails = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


# 1 ------------------------------------------------------------------------------------------
maps = json.loads((CODE / "map_mobs.json").read_text(encoding="utf-8"))["maps"]
can = set()
for mp in maps.values():
    for x in mp["mobs"]:
        m = album.identify(mp["name"], x["exp"])
        if m:
            can.add(m["id"])
never = [m["name"] for m in album.catalog() if m["id"] not in can]
print(f"{len(album.catalog())} album mobs; never caught from one rise: {never}")
check(album.identify("Thicket Around the Beach III", 148)["name"] == "Mano", "Mano from 148 EXP")
check(album.identify("Parched Land", 276)["name"] == "Stumpy", "Stumpy from 276 (only 2 Boogies spawn)")
check(album.identify("Wolf Territory I", 350) is None, "Werewolf 350 = 2 Hectors: not sure")
check(album.identify("Kerning City Construction Site", 23)["name"] == "Octopus",
      "Octopus on Kerning City Construction Site (23 = 3 kills only)")

# 2 ------------------------------------------------------------------------------------------
class StubSource:
    name = "stub"

    def __init__(self):
        self.album_watch = False

    def album_shot(self, out_dir, stem, t=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        for s in (f"{stem}_kill.jpg", f"{stem}.jpg"):
            Image.new("RGB", (64, 48), (90, 120, 60)).save(out_dir / s)
        Image.new("RGB", (32, 32), (200, 160, 90)).save(out_dir / f"{stem}_char.png")
        return [out_dir / f"{stem}_kill.jpg", out_dir / f"{stem}_char.png", out_dir / f"{stem}.jpg"]


class StubJournal:
    lines = []

    def write(self, s):
        self.lines.append(s)


class Sess:                     # just what Session.first_kill uses
    first_kill = T.Session.first_kill

    def __init__(self, src):
        self.source, self.journal = src, StubJournal()
        self.tr = type("Tr", (), {"level": 22, "job": "Magician", "map": None})()
        self.render_at = self.album_render_at = 0.0


alb = album.Album(OUT)
alb.set_who("fp_test", "Snoopy")
alb.set_level(22)
src = StubSource()
sess = Sess(src)
feed = live.Live(path=OUT / "live.json")
caught = []


def rise_hook(t, map_name, d, party, gap=None):
    m = alb.first_kill(map_name, d, party, gap)
    if m:
        caught.append(m["name"])
        sess.first_kill(m, map_name, alb, t)


feed.rise_hook = rise_hook
t = time.time()


def facts(*fs):
    feed.update(list(fs), None, now=t)


facts((t, "map", "Thicket Around the Beach III"))
new = alb.on_map("Thicket Around the Beach III")
check(len(new) >= 2, f"seen on Thicket III: {[m['name'] for m in new]}")
cur = 1000
facts((t + 1, "exp", {"cur": cur, "pct": 10.0}))
facts((t + 2, "exp", {"cur": cur + 148, "pct": 11.0}))            # Mano
facts((t + 3, "exp", {"cur": cur + 296, "pct": 12.0}))            # Mano again: not new
check(caught == ["Mano"], f"Mano caught once ({caught})")
snail = next(x for x in maps.values() if x["name"] == "Thicket Around the Beach III")["mobs"]
print("  Thicket III spawns:", [(x["name"], x["exp"]) for x in snail])
other = next((x for x in snail if x["name"] != "Mano"
              and album.identify("Thicket Around the Beach III", x["exp"])), None)
if other:
    facts((t + 4, "exp", {"cur": cur + 296 + other["exp"], "pct": 12.5}))
    check(caught[-1] == other["name"], f"{other['name']} caught from {other['exp']} EXP")
n = len(caught)
base = cur + 296 + (other["exp"] if other else 0)
# partied earlier on this visit (sticky visit.party), nobody else on the map now: still counts
feed.training.visit.party = True
feed.party = {"t": time.time(), "rows": [{"on_map": True}, {"on_map": False}]}
orange = album.find("Orange Mushroom")
facts((t + 5, "exp", {"cur": base + orange["exp"], "pct": 13.0}))
check(caught[-1:] == ["Orange Mushroom"], "partied this visit, partner elsewhere: Orange Mushroom caught")
n = len(caught)
# a partner on your map: EXP is shared, nothing counts (Stump 10 EXP would match otherwise)
feed.party = {"t": time.time(), "rows": [{"on_map": True}, {"on_map": True}]}
facts((t + 6, "exp", {"cur": base + orange["exp"] + 10, "pct": 13.2}))
check(len(caught) == n, "partner on the map: nothing caught")
feed.party = {"t": time.time(), "rows": []}
b2 = base + orange["exp"] + 10
facts((t + 30, "map", "Kerning City Construction Site"))
facts((t + 31, "exp", {"cur": b2, "pct": 14.0}))
facts((t + 45, "exp", {"cur": b2 + 23, "pct": 14.2}))                     # 14 s since last look
check(len(caught) == n, "rise over a long gap between looks: nothing caught")
facts((t + 46, "exp", {"cur": b2 + 46, "pct": 14.4}))                     # 1 s: one Octopus
check(caught[-1:] == ["Octopus"], "Octopus caught on Kerning City Construction Site")
check(alb.count() == len(caught), f"album has {alb.count()} caught")
check(alb.state(album.find("Mano")) == "caught", "Mano tile is caught")
lvl_near = next(m for m in album.catalog() if m["id"] not in alb.char()["seen"] and m["level"] <= 25
                and not m["boss"])
check(alb.state(lvl_near) == "grey", f"{lvl_near['name']} (Lv {lvl_near['level']}) grey: near Lv 22")
far = next(m for m in album.catalog() if m["level"] > 40 and not m["boss"] and m["id"] not in alb.char()["seen"])
check(alb.state(far) == "unknown", f"{far['name']} (Lv {far['level']}) is ?")
check(alb.state(album.find("Jr. Balrog")) == "grey", "Jr. Balrog (boss, Lv 80) grey")

ents = [e for e in diary.entries() if e["kind"] == "first_kill"]
check(len(ents) == len(caught), f"{len(ents)} first_kill diary beats")
boss = next(e for e in ents if e["mob"] == "Mano")
check(boss["boss"] is True and boss["caught"] == 1, "Mano beat: boss, caught 1")
cap_boss, src_boss = diary.caption_info(boss)
boss_lines = [x if isinstance(x, str) else x["text"]
              for x in json.loads((CODE / "captions.json").read_text(encoding="utf-8"))["first_kill_boss"]]
check(any(cap_boss == ln.format(mob="Mano", map=boss["map"], name=diary.nickname(),
                                time=diary.clock(datetime.fromisoformat(boss["time"])), caught=1)
          for ln in boss_lines), f"boss caption from first_kill_boss: {cap_boss!r} ({src_boss})")
if len(ents) > 1:
    print("  normal caption:", diary.caption_info(ents[1]))
check(sess.journal.lines and "First boss kill: Mano" in sess.journal.lines[0], "journal line")

# 3 ------------------------------------------------------------------------------------------
fx = sorted((CODE / "dev" / "fixtures" / "levelup_lv5").glob("*.png"))
if len(fx) >= 2:
    from PIL import Image
    import levelup_shot
    from sources.screen import ScreenSource
    s = ScreenSource.__new__(ScreenSource)
    s.album_watch, s.album_prev, s.pending_shots = True, deque(maxlen=2), []
    s.lu, s.frame, s._nametag = levelup_shot.Timer(), None, None
    a, b = Image.open(fx[0]).convert("RGB"), Image.open(fx[1]).convert("RGB")
    t0 = time.time()
    s._album_keep(t0 - 1.0, a)
    s._album_keep(t0, b)
    paths = s.album_shot(OUT / "shots", "sim_firstkill", t0)
    kill = OUT / "shots" / "sim_firstkill_kill.jpg"
    check(kill.exists() and Image.open(kill).size == (720, 720), f"kill square saved {Image.open(kill).size if kill.exists() else ''}")
    def diff(x, y):
        x, y = x.convert("L").resize((32, 32)), y.convert("L").resize((32, 32))
        return sum(abs(p - q) for p, q in zip(x.getdata(), y.getdata())) / 1024
    box = (a.width // 2 - 360, a.height // 2 - 360, a.width // 2 + 360, a.height // 2 + 360)
    got = Image.open(kill)
    da, db = diff(got, a.crop(box)), diff(got, b.crop(box))
    check(da <= db and da < 3, f"kill square is the look before the rise (diff {da:.2f} vs {db:.2f})")
    s._take_due_shots(time.time() + 0.2, b)
    check((OUT / "shots" / "sim_firstkill.jpg").exists(), "full screenshot on the next look")
    print("  files:", [p.name for p in paths], "char exists:", (OUT / "shots" / "sim_firstkill_char.png").exists())
else:
    print("skip 3: no fixture frames")

# 4 ------------------------------------------------------------------------------------------
album.render(alb)
html = (OUT / "album.html").read_text(encoding="utf-8")
check("Monster <span>Album</span>" in html and '"state": "caught"' in html, "album.html built")
import diary_html  # noqa: E402
diary_html.build(OUT)
dh = (OUT / "diary.html").read_text(encoding="utf-8")
check('"kind": "first_kill"' in dh and "_kill.jpg" in dh, "diary.html has the first kill and its photo")
print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
