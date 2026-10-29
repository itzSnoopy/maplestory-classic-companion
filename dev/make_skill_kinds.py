"""Generate gamedata/skill_kinds_classic.json from skills_classic.json.

    python dev/make_skill_kinds.py          write the file (identical bytes on every run)
    python dev/make_skill_kinds.py --check  exit 1 if the file on disk differs from what would be generated

A text rule gives a first guess; OVERRIDES (hand-checked) win. Never hand-edit the json: fix OVERRIDES here.
Kinds: attack | buff | passive | other. See .claude/handoff/10-skills-plan.md.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "gamedata" / "skills_classic.json"
OUT = ROOT / "gamedata" / "skill_kinds_classic.json"
KINDS = ("attack", "buff", "passive", "other")

OV = {}   # id -> (kind, sub)


def _ov(ids, kind, sub=None):
    for i in ids.split():
        OV[i] = (kind, sub)


_ov("1101000 1101001 1201000 1201001 1301000 1301001 3101000 3201000", "attack", "proc")      # Final Attack: fires on its own
_ov("1111001 1111003 1311002 2111002 2201003 2211001 3101004 3211001 4211001 4201002 4111002 4201003 "
    "4211002 4211004 4111003 1101005 1201005 1301005", "attack")                              # Rush stays attack
_ov("1111004 1201004 1211004 1311003 2101002 2201002 2111004 2211004 2311004 4001000", "other", "debuff")
_ov("2311005 3111004 3211004 3111000 3211000 4111001", "other", "summon")
_ov("2301001 0001001", "other", "heal")
_ov("2101001 2201001 2301000 3111005 3211005 4111005 4211005", "other", "mobility")
_ov("2311000 2311001", "other", "utility")
_ov("1111000 1111005 1211005 1311004 4211000 4211003 4111000 4001001 2311002 2001000 2301002", "buff")
_ov("2111000 2211000", "passive")
_ov("1101002 1101003 1201002 1201003 1301002 1301003 2111005 2211005 3101001 3201001 4101000 4201000", "buff", "booster")
_ov("1211001 1211002 1211003", "buff", "charge")

UNTIL_DEATH = {"2001000"}                       # Magic Guard (player, 2026-10-29)
DURATION_FIXED = {"0001001": 30}                # Recovery: heals over 30 s

ATTACK = re.compile(r"Basic Attack \d|Mastery|\bDamage \d|\d\s*% damage", re.I)
DUR = re.compile(r"\bfor (\d+)\s*(sec|seconds|min)\b", re.I)


def rule(s, sid):
    if sid[3] == "0" and not s.get("mp"):
        return "passive"
    text = " ".join(s.get("levels") or [])
    if ATTACK.search(text):
        return "attack"
    if s.get("time") or DUR.search(text):
        return "buff"
    return "other"


def durations(s):
    n = s.get("max") or 0
    t = s.get("time")
    if t and len(t) == n:
        return list(t)
    out = []
    for lv in s.get("levels") or []:
        m = DUR.search(lv)
        if not m:
            return None
        out.append(int(m.group(1)) * (60 if m.group(2).lower() == "min" else 1))
    return out if n and len(out) == n else None


def build():
    src = json.loads(SRC.read_text(encoding="utf-8"))
    kinds = {}
    for sid in sorted(src["skills"]):
        s = src["skills"][sid]
        kind, sub = OV.get(sid, (rule(s, sid), None))
        text = " ".join(s.get("levels") or [])
        dur = None if kind == "passive" else durations(s)
        if sid in DURATION_FIXED:
            dur = [DURATION_FIXED[sid]] * s["max"]
        until = None
        if sid in UNTIL_DEATH:
            until, dur = "death", None
        if kind == "attack" or (kind == "other" and sub == "debuff"):
            target = "mobs"
        elif kind == "buff":
            target = "party" if re.search("party", text, re.I) else "self"
        else:
            target = None
        kinds[sid] = {"kind": kind, "sub": sub, "target": target, "duration_s": dur, "until": until, "name": s["name"]}
    return {"made": "2026-10-29", "from": f"skills_classic.json fetched {src.get('fetched')}", "kinds": kinds}


def render():
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


def main():
    text = render()
    if "--check" in sys.argv:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("skill_kinds_classic.json matches the generator" if ok else "skill_kinds_classic.json differs from the generator")
        sys.exit(0 if ok else 1)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    from collections import Counter
    k = build()["kinds"]
    print(len(k), dict(Counter(v["kind"] for v in k.values())), dict(Counter((v["kind"], v["sub"]) for v in k.values() if v["sub"])))


if __name__ == "__main__":
    main()
