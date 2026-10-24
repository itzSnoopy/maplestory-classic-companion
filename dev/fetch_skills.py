"""dev/fetch_skills.py - build skills_classic.json (every Classic skill: name, job, max level) from NiaMeowDB.

The skills page links one page per class; each class page embeds its skills as JSON objects
({"id", "name", "class_name", "job", "max_level", ...}). Fetched slowly (1.5 s apart).
Used by panels.py to snap OCR'd skill names from the Skill window to real names.

  python dev/fetch_skills.py
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "skills_classic.json"
UA = {"User-Agent": "MapleClassicCompanion skills fetch (personal use, one-off)"}
BASE = "https://meowdb.com/msclassic"


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def payload(html):
    parts = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S):
        try:
            parts.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return "".join(parts)


def skills_in(text):
    """Every {"id":"<digits>","name":...,"max_level":n} object in the page payload."""
    dec, out = json.JSONDecoder(), {}
    for m in re.finditer(r'\{"id":"(\d{4,8})","name":"', text):
        try:
            obj = dec.raw_decode(text, m.start())[0]
        except ValueError:
            continue
        if "max_level" in obj:
            out[obj["id"]] = {"name": obj["name"], "class": obj.get("class_name"),
                              "job": obj.get("job"), "max": obj.get("max_level")}
    return out


def main():
    t = payload(get(f"{BASE}/skills"))
    slugs = sorted(set(re.findall(r'/msclassic/classes/([a-z0-9-]+)', t)))
    print(f"{len(slugs)} classes: {' '.join(slugs)}", flush=True)
    skills = {}
    for s in slugs:
        time.sleep(1.5)
        try:
            found = skills_in(payload(get(f"{BASE}/classes/{s}")))
        except Exception as e:
            print(f"  {s}: {e}", file=sys.stderr)
            continue
        skills.update(found)
        print(f"  {s}: {len(found)}", flush=True)
    OUT.write_text(json.dumps({"source": f"{BASE}/skills", "fetched": str(date.today()),
                               "skills": dict(sorted(skills.items()))}, indent=1),
                   encoding="utf-8")
    print(f"{len(skills)} skills -> {OUT}")


if __name__ == "__main__":
    main()
