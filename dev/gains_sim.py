"""gains_sim.py - the gains ledger on a burst fixture: replays the message corner and the EXP bar (vitals.csv) through
components/gains and prints the burst checks. Usage: python dev/gains_sim.py FIXTURE_DIR [OUT_DIR]"""
import csv
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_COMPANION_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image  # noqa: E402

from components.gains.component import GainsComponent  # noqa: E402
from engine.msgcorner import MsgCorner  # noqa: E402
from hub.bus import Hub  # noqa: E402


def main(folder, out=None):
    d = Path(folder)
    out = Path(out or tempfile.mkdtemp(prefix="gains_sim_"))
    hub = Hub(out, log=print)
    comp = GainsComponent()
    comp.attach(hub)
    rows = list(csv.DictReader(open(d / "vitals.csv", newline=""))) if (d / "vitals.csv").exists() else []
    t0 = float(rows[0]["t"]) if rows else 0.0
    bar = [(float(r["t"]) - t0, int(r["exp"])) for r in rows]
    mc = MsgCorner(str(out / "glyphs_msg.json"))
    frames = sorted(p for p in d.glob("*.jpg") if re.match(r"\d{4}\.\d\d_", p.name))
    bi, now = 0, 0.0
    for p in frames:
        sec = float(p.name[:7])
        facts = [(t, "exp", {"cur": c, "pct": 0.0}) for t, c in bar[bi:] if t <= sec]
        bi += len(facts)
        facts += mc.feed(sec, Image.open(p).convert("RGB"))
        hub.feed_facts(facts, None, now=sec)
        hub.emit("poll", now=sec)
        now = sec
    hub.emit("poll", now=now + 10)
    for name in ("gains.csv", "gain_check.csv"):
        print(f"--- {name}")
        f = out / "history" / name
        print(f.read_text(encoding="utf-8") if f.exists() else "(none)")


if __name__ == "__main__":
    main(*sys.argv[1:3])
