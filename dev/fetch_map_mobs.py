"""dev/fetch_map_mobs.py - build map_mobs.json (which mobs live on each Classic map) from NiaMeowDB.

One page per map in maps_classic.json, fetched slowly (1.5 s apart). Each MeowDB map page embeds its
spawn list: mob name, level, EXP, spawn count, and respawn timers (timed spawns = bosses / rare mobs
like Mushmom). Used by killers.py to guess who killed the player.

  python dev/fetch_map_mobs.py            fetch maps not in map_mobs.json yet (resumable)
  python dev/fetch_map_mobs.py --all      refetch everything
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "map_mobs.json"
UA = {"User-Agent": "MapleClassicCompanion map-mobs fetch (personal use, one-off)"}
URL = "https://meowdb.com/msclassic/maps/{}"


def _array_after(text, key):
    """JSON array that follows `"key":[` in the page's (unescaped) payload, or None."""
    i = text.find(f'"{key}":[')
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, i + len(key) + 3)[0]
    except ValueError:
        return None


def _payload(html):
    """The page's Next.js data stream: string chunks pushed by self.__next_f.push([1,"..."]),
    joined (a chunk boundary can fall in the middle of an array)."""
    parts = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S):
        try:
            parts.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return "".join(parts)


def parse(html):
    t = _payload(html)
    spawns = _array_after(t, "monsters") or []
    # the first "monsters" array may be the short {"id","name"} list; prefer the one with counts
    for m in re.finditer(r'"monsters":\[\{"id":"', t):
        arr = _array_after(t[m.start():], "monsters")
        if arr and "count" in arr[0]:
            spawns = arr
            break
    per = {str(p["id"]): p for p in (_array_after(t, "perMob") or [])}
    mobs = []
    for s in spawns:
        if "count" not in s:
            continue
        p = per.get(str(s["id"]), {})
        mobs.append({
            "id": str(s["id"]), "name": s["name"], "level": p.get("level"), "exp": p.get("exp"),
            "count": s["count"],
            # respawn timer in seconds (0 = normal wave spawn); bosses / rare mobs have one
            "respawn": s.get("respawnMin") or 0,
        })
    return mobs


def main():
    maps = json.loads((ROOT / "maps_classic.json").read_text(encoding="utf-8"))["maps"]
    old = {}
    if OUT.exists() and "--all" not in sys.argv:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("maps", {})
    out = dict(old)
    todo = [m for m in maps if m["id"] not in old]
    print(f"{len(todo)} maps to fetch")
    for n, m in enumerate(todo, 1):
        try:
            req = urllib.request.Request(URL.format(m["id"]), headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
            mobs = parse(html)
            out[m["id"]] = {"name": m["name"], "street": m["street"], "region": m["region"],
                            "mobs": mobs}
            print(f"[{n}/{len(todo)}] {m['name']}: {', '.join(x['name'] for x in mobs) or '-'}")
        except Exception as ex:
            print(f"[{n}/{len(todo)}] {m['name']} ({m['id']}): FAILED {ex}")
        if n % 25 == 0 or n == len(todo):
            save(out)
        time.sleep(1.5)
    save(out)


def save(maps):
    OUT.write_text(json.dumps({
        "_source": "NiaMeowDB map pages, https://meowdb.com/msclassic/maps/<id> (spawn lists)",
        "_fetched": date.today().isoformat(),
        "_note": "Built by dev/fetch_map_mobs.py. respawn = seconds between timed spawns (0 = normal).",
        "maps": maps}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
