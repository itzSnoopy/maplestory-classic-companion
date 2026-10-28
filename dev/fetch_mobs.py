"""dev/fetch_mobs.py - build mobs_classic.json (one entry per Classic mob) from NiaMeowDB monster pages.

For the monster album (album.py). One page per mob in map_mobs.json, fetched slowly (1.5 s apart):
name, level, HP, EXP, boss flag, drops. Where each mob lives comes from map_mobs.json (spawn lists).
Names and numbers only: the mob pictures are Nexon art and are fetched by each player's app
(mobicons.py), never shipped.

Drops: MeowDB shows OSMLib reference rates and says they may not be Classic's rates; the album
shows them as "may drop", rates as a hint.

  python dev/fetch_mobs.py            fetch mobs not in mobs_classic.json yet (resumable)
  python dev/fetch_mobs.py --all      refetch everything
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "gamedata" / "mobs_classic.json"
UA = {"User-Agent": "MapleClassicCompanion mob fetch (personal use, one-off)"}
URL = "https://meowdb.com/msclassic/monsters/{}"


def _payload(html):
    """The page's Next.js data stream (string chunks pushed by self.__next_f.push), joined."""
    parts = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S):
        try:
            parts.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return "".join(parts)


def _num(s):
    return int(s.replace(",", "")) if s else None


def parse(html):
    t = _payload(html)
    out = {}
    m = re.search(r'"monsterId":(\d+),"level":(\d+),"isBoss":(true|false)', t)
    if m:
        out["level"] = int(m.group(2))
        out["boss"] = m.group(3) == "true"
    m = re.search(r'og\?title=([^&]+)&(?:amp;)?subtitle=HP\+([\d%C2]+?)(?:\+%C2%B7\+EXP\+([\d%C2]+))?&', t)
    if m:
        from urllib.parse import unquote_plus
        out["name"] = unquote_plus(m.group(1))
        out["hp"] = _num(unquote_plus(m.group(2)))
        out["exp"] = _num(unquote_plus(m.group(3) or ""))
    drops = []
    i = t.find("Reference Drop Rates")
    if i >= 0:
        j = t.find('"tbody"', i)
        k = t.find('"tbody"', j + 1) if j >= 0 else -1      # next table, if any, ends the scan
        body = t[j:k if k > 0 else len(t)] if j >= 0 else ""
        for r in re.finditer(r'"href":"/msclassic/item-db/(\d+)".*?"src":"/msclassic/api/assets/icons/(\d+)"'
                             r'.*?"children":"([^"]+)"\}\]\]\}\]\}\].*?"children":"([\d.]+%)"', body, re.S):
            drops.append({"item": r.group(1), "icon": r.group(2), "name": r.group(3), "rate": r.group(4)})
    out["drops"] = drops
    return out


def mob_maps():
    """{mob id: {"name", "maps": [{"id", "name", "region", "count", "respawn"}]}} from map_mobs.json."""
    data = json.loads((ROOT / "gamedata" / "map_mobs.json").read_text(encoding="utf-8"))["maps"]
    mobs = {}
    for mid, mp in data.items():
        for x in mp["mobs"]:
            e = mobs.setdefault(x["id"], {"name": x["name"], "level": x["level"], "exp": x["exp"], "maps": []})
            e["maps"].append({"id": mid, "name": mp["name"], "region": mp["region"],
                              "count": x["count"], "respawn": x["respawn"]})
    return mobs


def main():
    base = mob_maps()
    old = {}
    if OUT.exists() and "--all" not in sys.argv:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("mobs", {})
    out = {k: v for k, v in old.items() if k in base}
    todo = [k for k in base if k not in out]
    print(f"{len(todo)} mobs to fetch")
    for n, mid in enumerate(todo, 1):
        e = dict(base[mid])
        try:
            req = urllib.request.Request(URL.format(mid), headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
            p = parse(html)
            e.update({k: v for k, v in p.items() if v not in (None, "")})
            print(f"[{n}/{len(todo)}] {e['name']} Lv {e['level']}{' BOSS' if e.get('boss') else ''}: "
                  f"{len(e.get('drops', []))} drops")
        except Exception as ex:
            print(f"[{n}/{len(todo)}] {e['name']} ({mid}): FAILED {ex}")
            e["drops"] = None
        out[mid] = e
        if n % 25 == 0 or n == len(todo):
            save(out)
        time.sleep(1.5)
    save(out)


def save(mobs):
    OUT.write_text(json.dumps({
        "_source": "NiaMeowDB monster pages, https://meowdb.com/msclassic/monsters/<id>; maps from map_mobs.json",
        "_fetched": date.today().isoformat(),
        "_note": "Built by dev/fetch_mobs.py. drops = OSMLib reference rates shown by MeowDB (may not be "
                 "Classic's). boss = MeowDB's flag. Pictures are not in here (mobicons.py).",
        "mobs": dict(sorted(mobs.items(), key=lambda kv: (kv[1].get("level") or 0, kv[1]["name"])))},
        indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
