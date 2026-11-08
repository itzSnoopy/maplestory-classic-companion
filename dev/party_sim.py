"""party_sim.py - the sticky party roster (components/party/roster.py).

    python dev/party_sim.py [OUT_DIR]

1. Synthetic cases (always run): a short blip keeps the roster, a real change after a stable hour needs the
   longer grace, a raw OCR read never replaces a snapped name, a relay name fills the unnamed row, relay
   numbers are used for relay members and screen numbers for the rest, on_map needs 2 looks.
2. Replay (skips cleanly without the local, git-ignored burst frames in dev/fixtures/burst/): the real
   engine.party reader + Names over each recording that has a party box, through the OLD component logic
   (publish every look, party_reset on any count change) and the NEW roster; prints per recording how many
   times a published name / the published row count changed. No names are printed (placeholders only).
Exit 1 when a synthetic case fails or the new roster flickers more than the old logic.
"""
import glob
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from components.party import roster as R      # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def mk(names, on_map=True):
    """Fake engine rows: the 'mask' is the text itself; name_of returns it (a stand-in for OCR + snap)."""
    return [{"mask": n, "on_map": on_map, "hp": 50.0 if on_map else None, "leader": i == 0}
            for i, n in enumerate(names)]


def fake_roster():
    return R.Roster(lambda i, mask, known, t: mask or None, lambda: None)


def synthetic():
    K = ["Aa", "Bb", "Cc"]
    # 1. a 2-s blip keeps the roster (and the names, and no reset)
    r = fake_roster()
    t = 0.0
    for _ in range(10):
        r.update(t, mk(["Aa", "Bb"]), K)
        t += 1
    resets = r.resets
    out = None
    for _ in range(2):
        out = r.update(t, [], K)
        t += 1
    check("2 s blip: rows kept, held", [x["name"] for x in out] == ["Aa", "Bb"] and all(x["held"] for x in out))
    out = r.update(t, mk(["Aa", "Bb"]), K)
    check("blip over: back to live, no reset", not out[0]["held"] and r.resets == resets)
    # 2. a young party (< 1 min): a 5 s blip (measured map change) is still held, ~7 s is not
    r = fake_roster()
    t = 0.0
    for _ in range(10):
        r.update(t, mk(["Aa", "Bb"]), K)
        t += 1
    for _ in range(5):
        out = r.update(t, [], K)
        t += 1
    check("young party: 5 s blip held", len(out) == 2)
    for _ in range(3):
        out = r.update(t, [], K)
        t += 1
    check("young party: gone after ~6 s", out == [])
    # 3. after a stable hour a new count (3rd member joins) needs the long grace
    r = fake_roster()
    t = 0.0
    for _ in range(3600):
        r.update(t, mk(["Aa", "Bb"]), K)
        t += 1
    since = t
    n_at = None
    for k in range(30):
        out = r.update(t, mk(["Aa", "Bb", "Cc"]), K)
        if len(out) == 3 and n_at is None:
            n_at = t - since
        t += 1
    check("stable hour: new member needs ~20 s", n_at is not None and 18 <= n_at <= 22, f"{n_at}")
    check("stable hour: old members keep first_seen", out[0]["since"] == 0.0 and out[2]["since"] > 3000)
    check("grace numbers", abs(R.grace(0) - 6) < 1e-9 and abs(R.grace(600) - 20) < 1e-9 and 6 < R.grace(300) < 20)
    # 4. a raw (unsnapped) OCR read never replaces a snapped name, a snapped one needs several looks
    r = fake_roster()
    t = 0.0
    for _ in range(60):
        r.update(t, mk(["Aa", "Bb"]), K)
        t += 1
    for _ in range(40):
        out = r.update(t, mk(["Aa", "Zq9"]), K)
        t += 1
    check("raw OCR never replaces a snapped name", out[1]["name"] == "Bb")
    out = r.update(t, mk(["Aa", "Cc"]), K)
    t += 1
    check("one snapped look is not enough after a stable minute", out[1]["name"] == "Bb")
    for _ in range(10):
        out = r.update(t, mk(["Aa", "Cc"]), K)
        t += 1
    check("a snapped name repeating takes over", out[1]["name"] == "Cc")
    # 5. one unnamed row + one relay partner on no row: that partner (exact name from the relay)
    r = fake_roster()
    out = r.update(0, mk(["Aa", ""]), ["Aa"], {"Dd": {"hp": None}})
    check("relay name fills the unnamed row", out[1]["name"] == "Dd")
    # 6. relay numbers when the member is on the relay, screen numbers when not
    r = fake_roster()
    mates = {"Bb": {"hp": [100, 400], "mp": [5, 50], "level": 30, "map": "X"}}
    out = r.update(0, mk(["Aa", "Bb", "Cc"]), K, mates, "Aa")
    check("relay member: src relay, exact HP %", out[1]["src"] == "relay" and out[1]["hp"] == 25.0)
    check("not on the relay: src screen, box HP %", out[2]["src"] == "screen" and out[2]["hp"] == 50.0)
    check("me flag", out[0]["me"] and not out[1]["me"])
    # 7. on_map changes need 2 looks
    r = fake_roster()
    r.update(0, mk(["Aa", "Bb"]), K)
    out = r.update(1, mk(["Aa"]) + mk(["Bb"], on_map=False), K)
    check("on_map: one dim look does not flip", out[1]["on_map"] is True)
    out = r.update(2, mk(["Aa"]) + mk(["Bb"], on_map=False), K)
    check("on_map: two looks do", out[1]["on_map"] is False and out[1]["hp"] is None)


# ---------------------------------------------------------------- replay
def old_logic(names_obj, count_prev, rows, known, t):
    """The component before 2026-11-07: any count change resets the names; publish the look as it is."""
    if len(rows) != count_prev:
        names_obj.reset()
    return [{"name": names_obj.name(i, r["mask"], known, t), "on_map": r["on_map"]} for i, r in enumerate(rows)]


def changes(pubs):
    """(name changes between consecutive publishes on the same row index, row-count changes)."""
    nc = cc = 0
    prev = None
    for p in pubs:
        if prev is not None:
            if len(p) != len(prev):
                cc += 1
            nc += sum(1 for a, b in zip(prev, p) if a["name"] != b["name"])
        prev = p
    return nc, cc


def replay(out_dir):
    from PIL import Image
    from engine import party, ocr
    from base.paths import load_config
    ocr.setup(load_config().get("tesseract"))
    if not ocr.READY:
        print("SKIP  replay (Tesseract not set up)")
        return
    dirs = sorted(glob.glob(str(ROOT / "dev" / "fixtures" / "burst" / "*")))
    if not dirs:
        print("SKIP  replay (no dev/fixtures/burst frames)")
        return
    rep = []
    for d in dirs:
        frames = sorted(glob.glob(d + "/*.jpg")) + sorted(glob.glob(d + "/*.png"))
        stream = []
        fin = party.Finder()
        for k, p in enumerate(frames):
            try:
                t = float(os.path.basename(p).split("_")[0])
            except ValueError:
                t = k * 0.5
            im = Image.open(p)
            b = party.search_box(im.size)
            stream.append((t, fin.read(im.crop(b), b[:2], now=t)))
        if not any(rows for _, rows in stream):
            continue
        # known names: what a tracker would have (you + one relay partner): the two top rows' first readings
        probe = party.Names()
        first = next(rows for _, rows in stream if rows)
        known = [x for x in (probe.name(i, r["mask"], [], 0.0) for i, r in enumerate(first[:2])) if x]
        res = {}
        for label, kn in (("no known names", []), ("known: you + 1 mate", known)):
            old_n, prev, olds = party.Names(), 0, []
            for t, rows in stream:
                olds.append(old_logic(old_n, prev, rows, kn, t))
                prev = len(rows)
            nn = party.Names()
            ro = R.Roster(nn.name, nn.reset)
            news = [ro.update(t, rows, kn, {}, kn[0] if kn else None) for t, rows in stream]
            res[label] = (changes(olds), changes(news), ro.resets)
        rep.append((Path(d).name, len(stream), sum(1 for _, r in stream if r), res))
    if not rep:
        print("SKIP  replay (no recording with a party box)")
        return
    worse = False
    lines = []
    for name, n, withbox, res in rep:
        lines.append(f"{name}: {n} frames, {withbox} with a party box")
        for lbl, ((on, oc), (nn_, nc), rs) in res.items():
            lines.append(f"   {lbl:22} name changes {on:3} -> {nn_:3}   row-count changes {oc:3} -> {nc:3}   resets {rs}")
            worse |= nn_ > on or nc > oc
    txt = "\n".join(lines)
    print(txt)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "party_sim.txt").write_text(txt + "\n", encoding="utf-8")
    check("replay: roster flickers no more than the old logic", not worse)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="party_sim_")
    synthetic()
    replay(out)
    print("FAIL" if FAILS else "OK")
    sys.exit(1 if FAILS else 0)
