"""dev/fetch_potions.py - build potions_classic.json: every Classic Use item that restores HP or MP.

From NiaMeowDB's item API (the one its item list page loads), all Use items, fetched slowly
(1.5 s apart). Kept: items whose stats restore HP / MP, flat or % of max.

  python dev/fetch_potions.py          # writes potions_classic.json
  python dev/fetch_potions.py --keys   # also prints every stats key seen (to spot new ones)

potions_classic.json: {"source", "fetched", "items": {"<iconKey>": {name, subtype, hp, mp,
hp_pct, mp_pct, price, shop, cooldown}}}. `price` is MeowDB's item price (the NPC *sell* value);
`shop` = the cheapest NPC shop price from /msclassic/npc-shops (None = not sold in shops: drops,
events). Used by mpuse.py (which item a rise was) and training.py (what training costs).
"""
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "potions_classic.json"
UA = {"User-Agent": "MapleClassicCompanion potion fetch (personal use, one-off)"}
API = "https://meowdb.com/msclassic/api/items?type=Use&ossyria={o}&page={p}&limit=100&locale=en"
# stats keys -> our fields (MeowDB's names; *Pct = % of max). Plain "hp" / "mp" are scroll
# stat boosts (max HP/MP on gear), not restores.
KEYS = {"hpRecover": "hp", "mpRecover": "mp", "hpRecoverPct": "hp_pct", "mpRecoverPct": "mp_pct"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


SHOPS = "https://meowdb.com/msclassic/npc-shops"


def shop_prices():
    """{iconKey: cheapest NPC shop price} from the shop list page (server-rendered table rows)."""
    import re
    req = urllib.request.Request(SHOPS, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    out = {}
    for m in re.finditer(r'icons/(\d+)"[^<]*</div></td><td[^>]*><a[^>]*>[^<]+</a></td>'
                         r'<td[^>]*>([\d,]+)<span[^>]*>mesos', html):
        key, price = m.group(1), int(m.group(2).replace(",", ""))
        out[key] = min(price, out.get(key, price))
    return out


def fetch_all():
    items = {}
    for ossyria in (0, 1):          # MeowDB splits Victoria / Ossyria content
        p = 1
        while True:
            d = get(API.format(o=ossyria, p=p))
            for it in d.get("items", []):
                items[it["iconKey"] or str(it["id"])] = it
            if not d.get("hasMore"):
                break
            p += 1
            time.sleep(1.5)
        time.sleep(1.5)
    return items


def main():
    raw = fetch_all()
    time.sleep(1.5)
    shops = shop_prices()
    keys, out = set(), {}
    for key, it in raw.items():
        try:
            st = json.loads(it.get("stats") or "{}")
        except ValueError:
            continue
        keys.update(st)
        row = {f: st[k] for k, f in KEYS.items() if isinstance(st.get(k), (int, float)) and st[k]}
        if not row:
            continue
        out[key] = {"name": it.get("displayName") or it.get("name"),
                    "subtype": it.get("displaySubtype") or it.get("subtype"),
                    **row, "price": st.get("price"), "shop": shops.get(key),
                    "cooldown": st.get("useCooldown")}
    if "--keys" in sys.argv:
        print("stats keys:", sorted(keys))
    OUT.write_text(json.dumps({"source": "meowdb.com/msclassic (item API)",
                               "fetched": date.today().isoformat(),
                               "items": dict(sorted(out.items()))}, indent=1), encoding="utf-8")
    print(f"{len(raw)} Use items, {len(out)} restore HP/MP -> {OUT.name}")


if __name__ == "__main__":
    main()
