"""icon_sources_report.py - where the skill pictures came from: the fallback log (history/icon_sources.jsonl) and
assets/skill_icons/index.json "from" counts. Read-only. Usage: python dev/icon_sources_report.py"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from base.paths import DATA            # noqa: E402
from gamedata import gamefiles         # noqa: E402


def main():
    print("data folder:", DATA)
    print("fallback log summary:")
    s = gamefiles.summary()
    if not s:
        print("  (empty)")
    for used, v in sorted(s.items()):
        print("  %-8s lines %d, ids %d, days %s" % (used, v["lines"], len(v["ids"]), ", ".join(v["days"])))
    try:
        idx = json.loads((DATA / "assets" / "skill_icons" / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("index.json: none")
        return
    counts = {}
    for src in (idx.get("from") or {}).values():
        counts[src] = counts.get(src, 0) + 1
    print("index.json from:", counts or "{}", "| game_stamp:", "set" if idx.get("game_stamp") else "none")


if __name__ == "__main__":
    main()
