"""training.py - what training on a map costs you: MP, potions, mesos, per kill and to level up.

Core side (no pixels), fed by component.py (bus facts) with the same facts the skill bar gets:
  'map'  {name}            a new map visit starts (the last one is added to history/training.json)
  'exp'  {cur, pct}        the EXP bar on every quick look: each rise is kills. A rise is split
                           into the map's mobs by their EXP (map_mobs.json, MeowDB), fewest kills
                           first; a rise no mix of mobs explains is "other EXP" (quests, party share)
  mpuse.Usage totals       casts per skill, potions by name, MP/HP from regen, hits (diffed against
                           the totals when the visit started)
  progress                 level / EXP / EXP needed from the tracker's readings (to-level numbers)

Prices: NPC shop prices (potions_classic.json "shop", MeowDB /msclassic/npc-shops). Items that
aren't sold in shops (Elixirs, event items) count, but cost nothing here. Meso income isn't read
yet (user, 2026-10-24: costs only for now).

Per visit (live.json "training") and per map all time (history/training.json):
  kills {mob: n}, exp, casts {skill: {n, mp}}, potions {name: {n, mp, hp, mesos}}, mp from
  casts / regen / potions, hp lost / from regen / from potions, mesos spent
Derived: MP and mesos per kill and per hour, what one cast costs in mesos (MP x the meso price of
an MP point with the MP potion you use most), and how many potions you'd need to level up at
this map's rate: with your regen, and with no regen at all.
"""
import json
import math
import time
from collections import Counter
from itertools import combinations_with_replacement

from hub import exprate
from hub import history as H
from base.paths import asset

MAX_KILLS = 4           # kills that can share one EXP rise (looks are 0.5-1 s apart)
MIN_KILLS_FOR_RATES = 3 # per-kill numbers only after this many kills
LUMP_KILLS = MAX_KILLS  # a rise no mob mix explains and bigger than this many of the map's
                        # biggest mob is a quest turn-in or the like: not in EXP/hr (exprate.py)
DEFAULT_MP_ITEM = "Blue Potion"
DEFAULT_HP_ITEM = "Red Potion"
SAVE_EVERY_S = 60


def _load(name, key):
    try:
        return json.loads(asset(name).read_text(encoding="utf-8"))[key]
    except (OSError, ValueError, KeyError):
        return {}


_prefs = {"key": None, "val": {}}


def prefs(path=None):
    """helper_settings.json "trainingpanel": mp_item / hp_item (which potion prices your MP /
    HP; None = the one you use most) and prices {item name: mesos} (your own prices: market,
    rocks). Cached by file time; the tracker follows changes live."""
    import os
    if path is None:
        from base.paths import DATA
        path = DATA / "helper_settings.json"
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        return {}
    if key != _prefs["key"]:
        try:
            v = json.loads(open(path, encoding="utf-8").read()).get("trainingpanel") or {}
        except (OSError, ValueError, AttributeError):
            v = {}
        _prefs.update(key=key, val=v if isinstance(v, dict) else {})
    return _prefs["val"]


def _diff(now, then):
    """usage totals now - then: {kind: {name: {n, amount}}}."""
    out = {}
    for kind, rows in (now or {}).items():
        if not isinstance(rows, dict):
            continue
        for name, v in rows.items():
            o = ((then or {}).get(kind) or {}).get(name) or {"n": 0, "amount": 0}
            n, a = v["n"] - o["n"], v["amount"] - o["amount"]
            if n:
                out.setdefault(kind, {})[name] = {"n": n, "amount": a}
    return out


class Visit:
    def __init__(self, t, name, base_totals):
        self.t0, self.name = t, name
        self.base = json.loads(json.dumps(base_totals or {}))
        self.kills = Counter()
        self.exp = 0                 # EXP from kills
        self.other_exp = 0           # rises no mix of the map's mobs explains (party shares:
                                     # the user was partied for most of the 2026-10-25 recording)
        self.party = False           # in a party at some point of this visit (component.py, 'party' fact)
        self.quest_exp = 0           # lumps: quest turn-ins, EXP in town (not in the rates)
        self.rate = exprate.ExpRate()   # EXP/hr: active clock, party share apart
        self.last_t = t


class Training:
    def __init__(self, usage, mobs=None, items=None, path=None):
        self.usage = usage                       # mpuse.Usage
        self.items = items if items is not None else _load("gamedata/potions_classic.json", "items")
        maps = mobs if mobs is not None else _load("gamedata/map_mobs.json", "maps")
        # town maps (maps_classic.json): EXP there is a quest / NPC turn-in, not training
        self.towns = {m["name"] for m in _load("gamedata/maps_classic.json", "maps") or [] if m.get("town")}
        self.mobs_by_map = {}
        for m in maps.values():
            ms = [(x["name"], x["exp"], x.get("count") or 1) for x in m.get("mobs") or []
                  if x.get("exp")]
            if ms:
                self.mobs_by_map[m["name"]] = ms
        self.path = path
        self.visit = None
        self.exp_prev = None                     # (cur, pct) of the last quick look
        self.progress = {}                       # level, exp_cur, exp_needed (tracker readings)
        self.last_save = 0.0
        self._sums_cache = {}
        self.on_rise = None          # callback(t, map, d, party) for every EXP rise (component.py emits exp_rise; the album listens)
        self.exp_prev_t = None       # time of the last quick look with an EXP reading
        self.exp_gap = None          # seconds between the last two such looks

    def _store(self):
        return self.path or (H.ROOT / "history" / "training.json")

    # -- facts -----------------------------------------------------------------------
    def on_map(self, t, name):
        if self.visit and self.visit.name == name:
            return
        self.close(t)
        self.visit = Visit(t, name, self.usage.totals)
        self.exp_prev = None

    def set_progress(self, level=None, exp_cur=None, exp_needed=None):
        self.progress = {"level": level, "exp_cur": exp_cur, "exp_needed": exp_needed}

    def on_exp(self, t, cur, pct):
        p, self.exp_prev = self.exp_prev, (cur, pct)
        self.exp_gap = (t - self.exp_prev_t) if self.exp_prev_t is not None else None
        self.exp_prev_t = t
        self.progress["exp_cur"] = cur
        if p is None or self.visit is None:
            return
        d = cur - p[0]
        if d < 0:
            if pct < p[1] - 50:                  # level-up: the bar starts over
                need = self.progress.get("exp_needed")
                d = (need - p[0] + cur) if need and need > p[0] else cur
                self.progress["exp_needed"] = None   # the tracker's next reading says the new one
            else:
                return                           # death penalty or a misread: not kills
        if d <= 0:
            return
        self.visit.last_t = t
        if self.on_rise is not None:
            try:
                self.on_rise(t, self.visit.name, d, self.visit.party)
            except Exception:
                pass
        kills = self._split(d)
        if kills is None:
            if self._lump(d):
                self.visit.quest_exp += d
                self.visit.rate.add(t, d, "lump")
                return
            self.visit.other_exp += d
            self.visit.rate.add(t, d, "share", party=self.visit.party)
            return
        for name in kills:
            self.visit.kills[name] += 1
        self.visit.exp += d
        self.visit.rate.add(t, d, "kill")

    def _lump(self, d):
        """A rise that isn't training: in town, or more than LUMP_KILLS of the biggest mob here."""
        name = self.visit.name
        if name in self.towns:
            return True
        mobs = self.mobs_by_map.get(name)
        return bool(mobs) and d > LUMP_KILLS * max(e for _, e, _ in mobs)

    def _split(self, d):
        """The kills behind an EXP rise d on this map: [mob names], or None."""
        mobs = self.mobs_by_map.get(self.visit.name) if self.visit else None
        if not mobs:
            return None
        key = self.visit.name
        if key not in self._sums_cache:
            best = {}
            total_count = sum(c for _, _, c in mobs)
            for n in range(1, MAX_KILLS + 1):
                for combo in combinations_with_replacement(mobs, n):
                    s = sum(e for _, e, _ in combo)
                    # fewer kills first, then the mobs there are more of
                    score = (n, -sum(c for _, _, c in combo) / total_count)
                    if s not in best or score < best[s][0]:
                        best[s] = (score, [m for m, _, _ in combo])
            self._sums_cache[key] = {s: v[1] for s, v in best.items()}
        return self._sums_cache[key].get(d)

    # -- results ---------------------------------------------------------------------
    def _price(self, name):
        """Mesos for one: the player's own price (Settings: market prices, rocks), else the
        NPC shop price, else None (not sold: drops / events)."""
        own = (prefs().get("prices") or {}).get(name)
        if isinstance(own, (int, float)) and own >= 0:
            return own
        for it in self.items.values():
            if it["name"] == name:
                return it.get("shop")
        return None

    def _key(self, name):
        return next((k for k, it in self.items.items() if it["name"] == name), None)

    def _item(self, name):
        return next((it for it in self.items.values() if it["name"] == name), None)

    def _per_point(self, k, potions, mx):
        """(item name, mesos per HP/MP point, points per item) for the item you use most for
        meter k on this visit (else the default), with the recovery skill's item bonus."""
        used = [(v["n"], n) for n, v in potions.items() if v.get(k)]
        pick = prefs().get(k + "_item")                 # the player's choice (panel right-click)
        name = (pick if pick and self._item(pick) else
                max(used)[1] if used else (DEFAULT_MP_ITEM if k == "mp" else DEFAULT_HP_ITEM))
        it = self._item(name) or {}
        b = self.usage.item_bonus(k)
        pts = it.get(k) and int(it[k] * (100 + b) / 100 + 1e-6)
        if not pts and it.get(k + "_pct") and mx:
            pts = int(mx * it[k + "_pct"] / 100 * (100 + b) / 100 + 1e-6)
        price = self._price(name)
        return name, (price / pts if price and pts else None), pts

    def mp_price(self):
        """(potion name, mesos per MP point, MP per potion) right now: what one MP costs you."""
        v = self.visit
        potions = {}
        if v is not None:
            u = _diff(self.usage.totals.get("mp"), v.base.get("mp"))
            for n, r in (u.get("potion") or {}).items():
                potions[n.replace(" (to full)", "")] = {"n": r["n"], "mp": r["amount"]}
        mx = (self.usage.prev and self.usage.prev[2] and self.usage.prev[2][1]) or None
        return self._per_point("mp", potions, mx)

    def item_names(self, k):
        """Potions / food that restore meter k (for the panel's "Price with" menu)."""
        return sorted({it["name"] for it in self.items.values()
                       if (it.get(k) or it.get(k + "_pct")) and "(Alt)" not in it["name"]
                       and (it.get("shop") or it.get(k + "_pct"))})

    def summary(self, now=None):
        """live.json "training": this map visit, with costs per kill / hour / to level."""
        v = self.visit
        if v is None:
            return None
        now = now or time.time()
        u = _diff(self.usage.totals.get("mp"), v.base.get("mp"))
        uh = _diff(self.usage.totals.get("hp"), v.base.get("hp"))
        casts = {n: {"n": r["n"], "mp": -r["amount"]} for n, r in (u.get("cast") or {}).items()}
        potions = {}
        for k, rows in (("mp", u.get("potion") or {}), ("hp", uh.get("potion") or {})):
            for n, r in rows.items():
                name = n.replace(" (to full)", "")
                p = potions.setdefault(name, {"n": 0, "mp": 0, "hp": 0, "mesos": 0})
                p[k] += r["amount"]
                if k == "mp" or not p["mp"]:     # one item, both meters (Elixir): count once
                    p["n"] = max(p["n"], r["n"])
        for name, p in potions.items():
            price = self._price(name)
            p["mesos"] = price * p["n"] if price else 0
        mp_casts = sum(c["mp"] for c in casts.values())
        mp_regen = sum(r["amount"] for r in (u.get("regen") or {}).values())
        hp_lost = -sum(r["amount"] for r in (uh.get("hit") or {}).values())
        hp_regen = sum(r["amount"] for r in (uh.get("regen") or {}).values())
        mesos = sum(p["mesos"] for p in potions.values())
        kills_seen = sum(v.kills.values())
        exp_all = v.exp + v.other_exp
        # crowded maps split a mob's EXP by damage (2026-10-25, Caution Falling Down: only 145 of
        # 1,219 EXP came as whole kills), so "kills" = mobs' worth of EXP: EXP / the map's average
        # mob EXP (weighted by how many spawn). Whole kills seen are kept apart.
        mobs = self.mobs_by_map.get(v.name) or []
        avg = (sum(e * c for _, e, c in mobs) / sum(c for _, _, c in mobs)) if mobs else None
        kills = round(exp_all / avg) if avg else kills_seen
        mins = max(1e-6, (now - v.t0) / 60)
        rate = v.rate.summary(now)               # EXP/hr: active time, lumps out (exprate.py)
        mx_mp = (self.usage.prev and self.usage.prev[2] and self.usage.prev[2][1]) or None
        mx_hp = (self.usage.prev and self.usage.prev[1] and self.usage.prev[1][1]) or None
        mp_item, mp_meso_pt, mp_pts = self._per_point("mp", potions, mx_mp)
        hp_item, hp_meso_pt, hp_pts = self._per_point("hp", potions, mx_hp)
        out = {
            "map": v.name, "since": round(v.t0, 1), "minutes": round(mins, 1),
            "kills": dict(v.kills), "kills_seen": kills_seen, "kills_total": kills,
            "party": v.party,
            "mob_exp_avg": round(avg, 1) if avg else None,
            "exp": exp_all, "exp_whole_kills": v.exp, "exp_shared": v.other_exp,
            "exp_quests": v.quest_exp, "rate": rate,
            "casts": casts, "potions": potions,
            "mp": {"casts": mp_casts, "regen": mp_regen,
                   "potions": sum(p["mp"] for p in potions.values())},
            "hp": {"lost": hp_lost, "regen": hp_regen,
                   "potions": sum(p["hp"] for p in potions.values())},
            "mesos": mesos,
            "per_hour": {"mesos": round(mesos * 60 / mins), "kills": round(kills * 60 / mins),
                         "exp": rate["exp_hr"]},
            "mp_item": {"name": mp_item, "points": mp_pts, "key": self._key(mp_item),
                        "picked": bool(prefs().get("mp_item")),
                        "mesos_per_mp": round(mp_meso_pt, 3) if mp_meso_pt else None},
            "hp_item": {"name": hp_item, "points": hp_pts, "key": self._key(hp_item),
                        "picked": bool(prefs().get("hp_item")),
                        "mesos_per_hp": round(hp_meso_pt, 3) if hp_meso_pt else None},
        }
        # one cast in mesos: its MP at what an MP point costs you
        out["cast_cost"] = {n: {"mp": round(c["mp"] / c["n"]),
                                "mesos": round(c["mp"] / c["n"] * mp_meso_pt, 1)
                                if mp_meso_pt else None}
                            for n, c in casts.items() if c["n"]}
        if kills >= MIN_KILLS_FOR_RATES:
            out["per_kill"] = {"mp": round(mp_casts / kills, 1),
                               "casts": round(sum(c["n"] for c in casts.values()) / kills, 2),
                               "hp": round(hp_lost / kills, 1),
                               "mesos": round(mesos / kills, 1),
                               "exp": round(exp_all / kills, 1)}
        if exp_all > 0:
            out["per_1k_exp"] = {"mp": round(mp_casts * 1000 / exp_all),
                                 "casts": round(sum(c["n"] for c in casts.values()) * 1000 / exp_all, 1),
                                 "mesos": round(mesos * 1000 / exp_all)}
        need, cur = self.progress.get("exp_needed"), self.progress.get("exp_cur")
        if exp_all > 0 and need and cur is not None and need > cur:
            left = need - cur
            share = left / exp_all                         # "this visit, again, `share` times"
            mp_no = mp_casts * share
            mp_net = max(0.0, (mp_casts - mp_regen) * share)
            hp_no = hp_lost * share
            hp_net = max(0.0, (hp_lost - hp_regen) * share)

            def pots(x, pts):
                return math.ceil(x / pts) if pts else None
            to = {"exp_left": left,
                  "kills": round(kills * share) if kills else None,
                  # grinding time at the active rate; and at your pace here with breaks
                  "minutes": round(left / rate["exp_hr"] * 60) if rate["exp_hr"] else None,
                  "minutes_with_breaks": round(left / rate["with_breaks_hr"] * 60)
                  if rate["with_breaks_hr"] else None,
                  "mp_pots": {"item": mp_item, "no_regen": pots(mp_no, mp_pts),
                              "with_regen": pots(mp_net, mp_pts)},
                  "hp_pots": {"item": hp_item, "no_regen": pots(hp_no, hp_pts),
                              "with_regen": pots(hp_net, hp_pts)}}
            price_mp, price_hp = self._price(mp_item), self._price(hp_item)
            if to["mp_pots"]["with_regen"] is not None and price_mp:
                to["mesos"] = (to["mp_pots"]["with_regen"] * price_mp
                               + (to["hp_pots"]["with_regen"] or 0) * (price_hp or 0))
            out["to_level"] = to
        if now - self.last_save > SAVE_EVERY_S:
            self.last_save = now
            self._save(out, final=False)
        return out

    # -- all-time per map ---------------------------------------------------------------
    def close(self, t):
        """End the current visit: add it to history/training.json."""
        if self.visit is None:
            return
        s = self.summary(t)
        self._save(s, final=True)
        self.visit = None

    def _save(self, s, final):
        """history/training.json {map: {visits, minutes, kills{}, exp, casts{}, potions{},
        mp{}, hp{}, mesos}} plus "_open": the visit under way (so a crash loses little)."""
        if not s or s["minutes"] < 0.5:
            return
        p = self._store()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d.pop("_open", None)
        if final:
            m = d.setdefault(s["map"], {"visits": 0, "minutes": 0, "kills": {}, "exp": 0,
                                         "casts": {}, "potions": {}, "mp": {}, "hp": {},
                                         "mesos": 0})
            m["visits"] += 1
            m["minutes"] = round(m["minutes"] + s["minutes"], 1)
            m["exp"] += s["exp"]
            m["mesos"] += s["mesos"]
            for name, n in s["kills"].items():
                m["kills"][name] = m["kills"].get(name, 0) + n
            for name, c in s["casts"].items():
                o = m["casts"].setdefault(name, {"n": 0, "mp": 0})
                o["n"] += c["n"]
                o["mp"] += c["mp"]
            for name, c in s["potions"].items():
                o = m["potions"].setdefault(name, {"n": 0, "mp": 0, "hp": 0, "mesos": 0})
                for k in o:
                    o[k] += c.get(k, 0)
            for k in ("mp", "hp"):
                for kk, val in s[k].items():
                    m[k][kk] = m[k].get(kk, 0) + val
        else:
            d["_open"] = s
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            H.write_json(p, d)
        except OSError:
            pass
