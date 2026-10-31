"""album.py - the monster album: every Classic mob, as "?", greyed out (seen) or caught (killed).

  ?        never on a map that spawns it
  grey     seen: you were on a map that spawns it. Bosses (MeowDB's flag) at most
           BOSS_GREY_LEVELS above your level are grey too (player, 2026-10-31; before: every boss
           and any mob up to 3 levels above you)
  caught   first kill confirmed: full-colour picture, and a diary beat ("first_kill") with a photo

Which mob died: nothing on screen names it. training.py splits every EXP rise on a quick look into
the map's mobs; the album counts a first kill only when the rise is exactly one mob's EXP, no other
mob there gives the same EXP, and no mix of 2-4 kills adds up to it. Party EXP is skipped (shares
make the numbers lie), with one exception: a boss that is always fought in a party (King Slime) is
caught from a party rise bigger than anything a regular mob of its map could give (party_boss, how
"party_boss"). Missing a first kill costs nothing: the next one counts.

Photo: while the map has a mob you haven't caught, each quick look also grabs a square around the
screen middle (sources/screen.py); on a first kill the square from the look before the EXP rose
(the mob still there) is saved, plus a full screenshot and close-up on the next look.

State per character (the fingerprint character.py uses): history/album.json.
Mob facts: mobs_classic.json (dev/fetch_mobs.py, MeowDB). Pictures: mobicons.py (downloaded, local).

  python -m components.album.album                 open the album (diary server, http://127.0.0.1:8737/album.html)
  python -m components.album.album html            rebuild album.html and open it from disk
  python -m components.album.album status          counts per state for the current character
  python -m components.album.album kill "Mob"      mark a mob as caught by hand (no photo)
  python -m components.album.album unkill "Mob"    undo a catch (wrong mob)
  python -m components.album.album icons           download every mob picture now (Settings > Internet allows it)

Screenshots are Nexon game imagery: local only, never publish or upload.
"""
from base import lean  # noqa: F401,I001
import json
import sys
from collections import Counter
from datetime import datetime
from itertools import combinations_with_replacement
from pathlib import Path

from base import paths
from base.paths import DATA

BOSS_GREY_LEVELS = 15    # bosses up to this many levels above you are grey before you see them
MAX_KILLS = 2            # kills that may share one EXP rise between two looks ~1 s apart. 4 (as
                         # in training.py) made Octopus uncatchable on Kerning City Construction
                         # Site (23 = 4+4+15); single-target attacks rarely kill 3 within a second
MAX_GAP_S = 2.5          # a rise over a longer gap between looks may hide several kills: skipped
PARTY_MAX_FACTOR = 1.6   # a full share plus a 6-member bonus (~10% of base each): the most a party rise
                         # can be over one mob's EXP (NOTES.md, "Boss kills in a party")
PQ_BOSS_IDS = (800000, 899999)   # party-quest mob ids: the only bosses party_boss() catches
SKIP_REGIONS = {"Hidden Street", "Event"}
HIDDEN_IDS = {"1"}       # tutorial-only mobs (Tutorial Jr. Sentinel): never part of the album


def set_root(root):
    global ROOT, STATE, PAGE
    ROOT = Path(root)
    STATE = ROOT / "history" / "album.json"
    PAGE = ROOT / "album.html"


set_root(DATA)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ the mob list
_cat = {"key": None, "val": None}


def _hidden(mid, m):
    """Not album mobs: party-quest / job-test copies ('(alt)', MeowDB ids 800000+ except bosses
    like King Slime), Lv 200 event / test dummies, and HIDDEN_IDS (tutorial-only mobs)."""
    return (str(mid) in HIDDEN_IDS or "(alt)" in m["name"] or (m.get("level") or 0) >= 200
            or (str(mid).isdigit() and 800000 <= int(mid) < 900000 and not m.get("boss")))


def catalog():
    """[mob dicts] sorted by level: id, name, level, exp, hp, boss, region, maps [{name, region,
    count, respawn}], drops [{name, icon, rate}] (OSMLib reference rates)."""
    path = paths.asset("gamedata/mobs_classic.json")
    try:
        key = path.stat().st_mtime_ns
    except OSError:
        key = None
    if _cat["val"] is not None and _cat["key"] == key:
        return _cat["val"]
    mobs = _read_json(path, {}).get("mobs")
    if not mobs:                                  # not built yet: what map_mobs.json knows
        mobs = {}
        for mid, mp in _read_json(paths.asset("gamedata/map_mobs.json"), {}).get("maps", {}).items():
            for x in mp["mobs"]:
                e = mobs.setdefault(x["id"], {"name": x["name"], "level": x["level"], "exp": x["exp"],
                                              "maps": [], "drops": None})
                e["maps"].append({"id": mid, "name": mp["name"], "region": mp["region"],
                                  "count": x["count"], "respawn": x["respawn"]})
    out = []
    _raw.clear()
    for mid, m in mobs.items():
        _raw[str(mid)] = m
        if _hidden(mid, m):
            continue
        regions = Counter(x["region"] for x in m.get("maps") or [])
        main = [r for r, _ in regions.most_common() if r not in SKIP_REGIONS]
        out.append({**m, "id": str(mid), "boss": bool(m.get("boss")),
                    "region": (main or [r for r, _ in regions.most_common()] or ["Unknown"])[0]})
    out.sort(key=lambda m: (m.get("level") or 0, m["name"]))
    _cat.update(key=key, val=out)
    return out


def by_id():
    return {m["id"]: m for m in catalog()}


def find(name):
    """A mob by (case-insensitive) name or id."""
    name = str(name).strip().lower()
    for m in catalog():
        if m["id"] == name or m["name"].lower() == name:
            return m
    return None


_on = {"cat": None, "maps": {}}


_raw = {}


def _all_on(map_name):
    """Every mob of the catalog source that spawns on a map, including the ones _hidden hides."""
    catalog()
    return [m for m in _raw.values() if any(x.get("name") == map_name for x in m.get("maps") or [])]


def mobs_on(map_name):
    """Album mobs that spawn on a map (by name; several maps can share one)."""
    cat = catalog()
    if _on["cat"] is not cat:
        _on.update(cat=cat, maps={})
        for m in cat:
            for x in m.get("maps") or []:
                lst = _on["maps"].setdefault(x["name"], [])
                if m not in lst:
                    lst.append(m)
    return _on["maps"].get(map_name, [])


# ------------------------------------------------------------------ which kill is a first kill
_sums = {}


def _multi_sums(map_name):
    """EXP totals that 2..MAX_KILLS kills on this map can add up to (any mobs of the map's spawn
    list, album or not), using each mob at most as often as it spawns there (Stumpy's 276 EXP is
    4 Jr. Boogies on paper, but only 2 Boogies spawn on Parched Land)."""
    if map_name not in _sums:
        cap = Counter()                           # exp -> how many such mobs can be on the map
        for mp in _read_json(paths.asset("gamedata/map_mobs.json"), {}).get("maps", {}).values():
            if mp["name"] == map_name:
                for x in mp["mobs"]:
                    if x.get("exp"):
                        cap[x["exp"]] = max(cap[x["exp"]], 0) + (x.get("count") or 1)
        s = set()
        for n in range(2, MAX_KILLS + 1):
            for combo in combinations_with_replacement(sorted(cap), n):
                if all(combo.count(e) <= cap[e] for e in set(combo)):
                    s.add(sum(combo))
        _sums[map_name] = (set(cap), s)
    return _sums[map_name]


def identify(map_name, d):
    """The one album mob an EXP rise of d on this map can only be, or None."""
    if not map_name or not d or d <= 0:
        return None
    _, multi = _multi_sums(map_name)
    if d in multi:
        return None
    hits = [m for m in mobs_on(map_name) if m.get("exp") == d]
    names = {m["name"] for m in hits}
    return hits[0] if len(names) == 1 else None


def party_boss(map_name, d):
    """The boss a PARTY EXP rise of d can only be, or None. Assumption from NOTES.md ("Boss kills in
    a party"): in a party the rise is your share plus ~10% of the base per active member, so a
    boss that is only ever fought in a party (King Slime) can't be matched by exact EXP. Rule: the
    map has exactly one boss with EXP (and it is a catalog mob), d is bigger than anything a regular
    mob of the map could give in a party (its max EXP x PARTY_MAX_FACTOR; hidden '(alt)' mobs count,
    no regular mobs = 0) and at most the boss's EXP x PARTY_MAX_FACTOR.
    Party-quest bosses only (MeowDB ids 800000-899999): open-world bosses (Mano, Stumpy, Mushmom...)
    have low ceilings that a few mixed kills can pass, and crowds hit them; they need their own rule."""
    if not map_name or not d or d <= 0:
        return None
    allm = _all_on(map_name)
    bosses = [m for m in allm if m.get("boss") and (m.get("exp") or 0) > 0]
    if len(bosses) != 1:
        return None
    boss = bosses[0]
    bid = next((k for k, v in _raw.items() if v is boss), "")
    if not (bid.isdigit() and PQ_BOSS_IDS[0] <= int(bid) <= PQ_BOSS_IDS[1]):
        return None
    mine = [m for m in mobs_on(map_name) if m["name"] == boss["name"]]
    if not mine:
        return None                               # the boss itself must be an album mob
    ceiling = max([m.get("exp") or 0 for m in allm if not m.get("boss")] or [0]) * PARTY_MAX_FACTOR
    if ceiling < d <= boss["exp"] * PARTY_MAX_FACTOR:
        return mine[0]
    return None


# ------------------------------------------------------------------ the player's album
class Album:
    def __init__(self, root=None):
        if root is not None:
            set_root(root)
        self.data = _read_json(STATE, {})
        self.data.setdefault("chars", {})
        self.who = self.data.get("last_who") or "_me"
        self.last_how = "exp"

    # -- who / level
    def set_who(self, who, name=None):
        who = who or "_me"
        if who != self.who:
            # the first character seen inherits what was collected before we knew who played
            if self.who == "_me" and who not in self.data["chars"] and "_me" in self.data["chars"]:
                self.data["chars"][who] = self.data["chars"].pop("_me")
            self.who = who
            self.data["last_who"] = who
            self._save()
        if name and self.char().get("name") != name:
            self.char()["name"] = name
            self._save()

    def char(self, who=None):
        c = self.data["chars"].setdefault(who or self.who, {})
        c.setdefault("seen", {})
        c.setdefault("killed", {})
        return c

    def set_level(self, level):
        if level and self.char().get("level") != level:
            self.char()["level"] = level
            self._save()
            return True
        return False

    def _save(self):
        _write_json(STATE, self.data)

    # -- seen
    def seed(self, map_names):
        """Maps visited before the album existed (diary places, map stats): their mobs are seen.
        Only once per character."""
        c = self.char()
        if c.get("seeded"):
            return 0
        n = 0
        for name in map_names:
            n += len(self.on_map(name, save=False))
        c["seeded"] = datetime.now().isoformat(timespec="seconds")
        self._save()
        return n

    def on_map(self, map_name, save=True):
        """You're on this map: its mobs are seen. Returns the newly seen mobs."""
        if not map_name:
            return []
        c = self.char()
        new = []
        for m in mobs_on(map_name):
            if m["id"] not in c["seen"]:
                c["seen"][m["id"]] = {"time": datetime.now().isoformat(timespec="seconds"),
                                      "map": map_name}
                new.append(m)
        if new and save:
            self._save()
        return new

    # -- caught
    def caught(self, mob_id):
        return str(mob_id) in self.char()["killed"]

    def count(self):
        return len(self.char()["killed"])

    def wants_photo(self, map_name):
        """A first kill is possible here (a mob of this map isn't caught yet). Asked every look:
        cached per (character, map, catches)."""
        if not map_name:
            return False
        key = (self.who, map_name, self.count())
        if getattr(self, "_wp", (None,))[0] != key:
            self._wp = (key, any(not self.caught(m["id"]) and m.get("exp") for m in mobs_on(map_name)))
        return self._wp[1]

    def first_kill(self, map_name, d, party=False, gap=None):
        """The mob an EXP rise d just caught for the first time, or None (not sure / not new).
        gap: seconds since the previous look with an EXP reading (None = unknown).
        self.last_how says how it was told: "exp" (exact EXP) or "party_boss" (party_boss())."""
        self.last_how = "exp"
        if gap is not None and gap > MAX_GAP_S:
            return None
        if party:
            m = party_boss(map_name, d)
            self.last_how = "party_boss"
        else:
            m = identify(map_name, d)
        if m is None or self.caught(m["id"]):
            return None
        return m

    def record(self, mob, map_name=None, level=None, shots=None, how="exp", when=None):
        c = self.char()
        c["killed"][mob["id"]] = {
            "time": (when or datetime.now()).isoformat(timespec="seconds"), "map": map_name,
            "level": level, "shots": shots or [], "how": how}
        c["seen"].setdefault(mob["id"], {"time": c["killed"][mob["id"]]["time"], "map": map_name})
        self._save()

    def unrecord(self, mob):
        gone = self.char()["killed"].pop(mob["id"], None)
        if gone is not None:
            self._save()
        return gone

    # -- what the page shows
    def state(self, mob, level=None):
        """'caught' | 'seen' | 'grey' (a boss at most BOSS_GREY_LEVELS above you) | 'unknown'."""
        c = self.char()
        level = level if level is not None else c.get("level")
        if mob["id"] in c["killed"]:
            return "caught"
        if mob["id"] in c["seen"]:
            return "seen"
        if mob.get("boss") and level and (mob.get("level") or 999) <= level + BOSS_GREY_LEVELS:
            return "grey"
        return "unknown"

    def summary(self):
        n = Counter(self.state(m) for m in catalog())
        return {"total": len(catalog()), "caught": n["caught"], "seen": n["seen"] + n["grey"],
                "unknown": n["unknown"]}


def first_kill_text(mob, map_name):
    what = "boss" if mob.get("boss") else "kill"
    return f"First {what}: **{mob['name']}**" + (f" at *{map_name}*" if map_name else "") + "."


# ------------------------------------------------------------------ page
def render(album=None):
    """Rebuild album.html (static, pictures by relative path)."""
    from components.album import album_html
    try:
        PAGE.write_text(album_html.page(ROOT, album=album), encoding="utf-8")
    except OSError:
        pass


def seen_places():
    """Maps the diary has marked as visited (history/places.json: a data file, written by the diary)."""
    try:
        return set(json.loads((ROOT / "history" / "places.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _cli(argv):
    a = Album()
    cmd = argv[0] if argv else "open"
    if cmd == "status":
        print(f"{a.char().get('name') or a.who}: {a.summary()}")
    elif cmd in ("kill", "unkill") and len(argv) > 1:
        m = find(" ".join(argv[1:]))
        if m is None:
            print("no such mob in the album")
            return 1
        if cmd == "kill":
            a.record(m, how="manual")
            print(f"caught: {m['name']}")
        else:
            print(f"removed: {m['name']}" if a.unrecord(m) else f"{m['name']} wasn't caught")
        render(a)
    elif cmd == "icons":
        import time
        from components.album import mobicons
        from base import internet
        if not internet.allowed("mobs"):
            print('Settings > Internet > "Monster pictures" is off')
            return 1
        mobicons.want([m["id"] for m in catalog()])
        while mobicons.pending():
            print(f"\r{mobicons.pending()} left ", end="", flush=True)
            time.sleep(1)
        print("\rdone      ")
        render(a)
    elif cmd == "html":
        import webbrowser
        a.seed(seen_places())
        render(a)
        webbrowser.open(PAGE.resolve().as_uri())
    elif cmd == "serve":                  # background server started by open_album()
        from hub import pages      # the page server, with the album registered
        pages.serve(open_browser=True, open_path="/album.html",
                    idle_exit_s=30 if "--idle-exit" in argv else None)
    else:
        a.seed(seen_places())
        render(a)
        print(open_album())
    return 0


def open_album(in_process=False):
    """Open the album in the browser, through the diary's local server like the diary itself (the
    server hosts the page this component registered: hub/pages.py): reuse a running one, else serve
    from this process (in_process / packaged) or start a background one. Falls back to album.html
    from disk."""
    import os
    from hub import pages
    url = pages.open_page("/album.html", in_process=in_process)
    if url:
        return url
    render()
    os.startfile(str(PAGE))
    return str(PAGE)


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
