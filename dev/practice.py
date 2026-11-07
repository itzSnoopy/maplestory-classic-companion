"""practice.py - dev only: start a solo practice duel in the running tracker (the board counts your own white EXP lines).

    python dev/practice.py [MINUTES] [--data DIR]

Writes challenge_cmd.json into the data folder (default: the installed app's %LOCALAPPDATA%/MapleClassicCompanion), the
same channel the party panel uses; the tracker picks it up within a second, shows the board after a 5 s countdown
and journals "Practice duel: N own kills, M shared". Nothing goes to the relay. Needs a build that has it.
"""
import json
import os
import sys
import time
from pathlib import Path


def main(argv):
    data = Path(os.environ.get("LOCALAPPDATA", ".")) / "MapleClassicCompanion"
    minutes = 5
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--data" and args:
            data = Path(args.pop(0))
        elif a.isdigit():
            minutes = int(a)
    if not data.is_dir():
        print(f"no data folder: {data}")
        return 1
    (data / "challenge_cmd.json").write_text(json.dumps({"t": round(time.time(), 2), "action": "practice",
                                                         "minutes": minutes}), encoding="utf-8")
    print(f"practice duel ({minutes} min) requested in {data}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
