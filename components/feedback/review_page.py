"""review_page.py - builds the skill review page (served at /skill-review.html) at render time.

Skills come from gamedata/skills_classic.json + gamedata/skill_kinds_classic.json (shipped); the page
shell is components/feedback/review.html. Class tabs are navigation only: answers are one store.
"""
import json
import re

from base import paths

# Top class -> its job lines (each line = [1st/2nd job class, 3rd job class]). Any class present in the
# data but missing here gets a tab of its own, so nothing is ever hidden.
GROUPS = [
    ("Beginner", [["Beginner"]]),
    ("Warrior", [["Warrior"], ["Fighter", "Crusader"], ["Page", "White Knight"], ["Spearman", "Dragon Knight"]]),
    ("Magician", [["Magician"], ["Cleric", "Priest"], ["F/P Wizard", "F/P Mage"], ["I/L Wizard", "I/L Mage"]]),
    ("Bowman", [["Bowman"], ["Hunter", "Ranger"], ["Crossbowman", "Sniper"]]),
    ("Thief", [["Thief"], ["Assassin", "Hermit"], ["Bandit", "Chief Bandit"]]),
]
ITEM = re.compile(r"((?:Red |Blue )?Snail Shell|Magic Rock|Summoning Rock|Magic Stone|Thrown Stars) -(\d+)")


def _load(rel):
    return json.loads(paths.asset(rel).read_text(encoding="utf-8"))


def skill_rows():
    """(fetched, rows): one row per skill with its levels [lv, mp, hp, items, duration_s, cooldown_s, text]."""
    S = _load("gamedata/skills_classic.json")
    K = _load("gamedata/skill_kinds_classic.json")["kinds"]
    rows = []
    for sid, v in S["skills"].items():
        k = K.get(sid) or {"kind": "other"}
        mp = v.get("mp") or []
        lv = []
        for i, t in enumerate(v["levels"]):
            hp = re.search(r"HP -(\d+)", t)
            items = [f"{m.group(1)} {m.group(2)}" for m in ITEM.finditer(t)]
            a = re.search(r"Uses (\d+) Arrows", t)
            if a:
                items.append(f"Arrows {a.group(1)}")
            m = re.search(r"Throws (\d+) Mesos", t)
            if m:
                items.append(f"{m.group(1)} mesos")
            if "times level in Mesos" in t:
                items.append("mesos 10 x level")
            cd = v["cd"][i] if v.get("cd") and i < len(v["cd"]) else None
            if cd is None:
                c = re.search(r"Cooldown:? (\d+) (sec|min)", t)
                if c:
                    cd = int(c.group(1)) * (60 if c.group(2) == "min" else 1)
            dur = k["duration_s"][i] if k.get("duration_s") and i < len(k["duration_s"]) else None
            lv.append([i + 1, mp[i] if i < len(mp) else None, int(hp.group(1)) if hp else None,
                       items, dur, cd, t])
        rows.append({"id": sid, "name": v["name"], "cls": v["class"], "job": v["job"], "max": v["max"],
                     "kind": k["kind"], "sub": k.get("sub"), "target": k.get("target"),
                     "until": k.get("until"), "lv": lv})
    return S.get("fetched"), rows


def branches(rows):
    """Class tabs: GROUPS filtered to the classes present, then one tab per class not in GROUPS."""
    present = {r["cls"] for r in rows}
    out, seen = [], set()
    for top, lines in GROUPS:
        ls = [[c for c in line if c in present] for line in lines]
        ls = [line for line in ls if line]
        if ls:
            out.append([top, ls])
            seen.update(c for line in ls for c in line)
    for c in sorted(present - seen):
        out.append([c, [[c]]])
    return out


def page(reviewer=""):
    fetched, rows = skill_rows()
    data = json.dumps({"fetched": fetched, "skills": rows}, ensure_ascii=False,
                      separators=(",", ":")).replace("</", "<\\/")
    cfg = json.dumps({"branches": branches(rows), "reviewer": str(reviewer or "")[:24]},
                     ensure_ascii=False).replace("</", "<\\/")
    html = paths.asset("components/feedback/review.html").read_text(encoding="utf-8")
    return html.replace("__DATA__", data).replace("__CFG__", cfg)
