"""diary.py - the character's diary: a timeline of milestones with screenshots.

  history/timeline.jsonl   one JSON object per entry (the permanent record; never rewritten
                           except by `annotate`)
  diary.md                 readable diary rendered from the timeline, grouped by day, with
                           character close-ups inline (open in VS Code / Obsidian / any md viewer)
  screenshots/diary/       <stamp>_<kind>.jpg (full frame) + <stamp>_<kind>_char.png (close-up)

Entry kinds: levelup, death, job, first_visit, note.
The tracker adds entries automatically. By hand:

  python diary.py note "Finally got my Metus! Party with Ana"   (screenshot if the tracker runs)
  python diary.py killed-by "Zombie Mushroom"     annotate the latest death
  python diary.py caption "I bit the dust"        own words for the latest entry in the Story view
  python diary.py caption --kind death "..."      ... for the latest death ("" = automatic again)
  python diary.py caption --random                a random line from captions.json instead
  python diary.py icons [--force]                 game icons from Steam for the caption tags
  python diary.py refs [on|off "Game" ...]         which games' lines random captions may use
  python diary.py nickname Snoopy                 what {name} says in captions ("" = character name)
  python diary.py open                            the editable diary (starts the server if needed)
  python diary.py serve                           diary.html with Edit / Generate buttons (localhost)
  python diary.py render                          rebuild diary.md (and diary.html)
  python diary.py html                            rebuild diary.html and open it in the browser
  python diary.py backfill                        add entries for level-ups in history/levels.csv
                                                  that predate the diary (uses screenshots/levelups)

Screenshots are Nexon game imagery: local only, never publish or upload.
"""
import lean  # noqa: F401,I001  (first: caps math threads, stubs OpenCV; keeps RAM down)
import json
import sys
from datetime import datetime
from pathlib import Path

from paths import APP as CODE, DATA
ICON = {"levelup": "⬆️", "death": "💀", "job": "⭐", "first_visit": "🗺️", "note": "📝", "moment": "📸"}
SKIP_REGIONS = {"Event", "Hidden Street", None}     # never "arrived in a new region"
MOMENT_TEXT = "A random moment."


def set_root(root):
    global ROOT, TIMELINE, DIARY, SHOTS, PLACES, REQUESTS, CUSTOM
    ROOT = Path(root)
    TIMELINE = ROOT / "history" / "timeline.jsonl"
    DIARY = ROOT / "diary.md"
    SHOTS = ROOT / "screenshots" / "diary"
    PLACES = ROOT / "history" / "places.json"          # maps already in the diary
    REQUESTS = ROOT / "diary_requests.jsonl"           # notes waiting for the tracker
    CUSTOM = ROOT / "history" / "captions_custom.json"  # Story captions the player wrote/rolled


set_root(DATA)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def char_name():
    return _read_json(DATA / "helper_settings.json", {}).get("character", {}).get("name") or "My character"


def entries():
    if not TIMELINE.exists():
        return []
    out = []
    for line in TIMELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _write_all(items):
    TIMELINE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TIMELINE.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in items), encoding="utf-8")
    tmp.replace(TIMELINE)


def add(kind, text, when=None, level=None, map=None, job=None, shots=None, **details):
    """Append one entry and re-render diary.md. `shots` = paths relative to the project root."""
    global _pending_place
    when = when or datetime.now()
    if kind == "first_visit" and _pending_place.get("map") == map:
        for k in ("why", "new_region", "region"):
            details.setdefault(k, _pending_place.get(k))
        _pending_place = {}
    e = {"time": when.isoformat(timespec="seconds"), "kind": kind, "text": text,
         "level": level, "map": map, "job": job, "shots": shots or [], **details}
    TIMELINE.parent.mkdir(parents=True, exist_ok=True)
    with open(TIMELINE, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    render()
    return e


# ------------------------------------------------------------------ texts for each event
def fmt_dur(s):
    if not s:
        return None
    s = int(float(s))
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m" if m else f"{s}s")


def levelup_text(level, map, took_s=None):
    t = f"Reached **Lv {level}**" + (f" at *{map}*" if map else "")
    d = fmt_dur(took_s)
    return t + (f". Lv {level - 1} took {d} of play." if d else ".")


def death_text(level, map, lost, pct=None, killed_by=None):
    t = "Died" + (f" to **{killed_by}**" if killed_by else "") + (f" at *{map}*" if map else "")
    t += f" (Lv {level})" if level else ""
    if lost:
        t += f", lost {lost:,} EXP" + (f" ({pct:.1f}%)" if pct is not None else "")
    return t + "."


def job_text(old, new, level, map):
    return (f"**Job advancement: {old} → {new}**" + (f" at Lv {level}" if level else "")
            + (f", *{map}*" if map else "") + ".")


def first_visit_text(map):
    info = _pending_place if _pending_place.get("map") == map else place_info(map)
    if info.get("new_region"):
        return f"First steps on **{info['new_region']}**: *{map}*."
    return f"First visit to *{map}*" + (f", {info['why']}." if info.get("why") else ".")


# ------------------------------------------------------------------ story captions (diary.html)
# One-liners for the Story view, from the dictionary in captions.json (the player's copy in DATA
# wins over the shipped one). Each entry gets a fixed line (crc32 of its time+kind), so rebuilds
# never reshuffle. Custom or generated captions live in history/captions_custom.json, keyed by
# entry_id(), so the timeline itself is never rewritten while the tracker appends to it.
def entry_id(e):
    return f"{e['time']}|{e['kind']}"


def caption_lines():
    """kind -> [(line template, source or None)] from captions.json."""
    d = None
    for path in (DATA / "captions.json", CODE / "captions.json"):
        d = _read_json(path, None)
        if isinstance(d, dict):
            break
    out = {}
    for k, v in (d or {}).items():
        if k.startswith("_") or not isinstance(v, list):
            continue
        lines = [(x.get("text"), x.get("source")) if isinstance(x, dict) else (x, None) for x in v]
        out[k] = [(t, src) for t, src in lines if isinstance(t, str) and t.strip()]
    return out


def clock(when):
    """3:30pm style."""
    return f"{when.hour % 12 or 12}:{when.minute:02d}{'am' if when.hour < 12 else 'pm'}"


def nickname():
    """{name} in captions: the nickname if set (`python diary.py nickname Snoopy`), else the
    character name."""
    return custom_captions().get("_nickname") or char_name()


def sources_off():
    """References (games) the player switched off for automatic and generated captions.
    Lines without a source are always allowed."""
    return set(custom_captions().get("_sources_off", []))


def set_sources_off(names):
    custom = custom_captions()
    names = sorted(set(n for n in names if n))
    if names:
        custom["_sources_off"] = names
    else:
        custom.pop("_sources_off", None)
    _save_custom(custom)


def all_sources():
    """{source: number of lines} over the whole dictionary."""
    out = {}
    for lines in caption_lines().values():
        for _, src in lines:
            if src:
                out[src] = out.get(src, 0) + 1
    return dict(sorted(out.items()))


def _fill(lines, vals):
    import string
    off = sources_off()
    out = []
    for line, src in lines:
        if src and src in off:
            continue
        try:
            names = {f for _, f, _, _ in string.Formatter().parse(line) if f}
            if all(vals.get(n) not in (None, "") for n in names):
                out.append((line.format(**vals), src))
        except (ValueError, KeyError, IndexError):
            continue                               # broken line in captions.json: skip it
    return out


def _filled_lines(e):
    """(kind lines, any lines) for this entry, values filled in, as (text, source). Lines needing
    a value the entry doesn't have are left out."""
    when = datetime.fromisoformat(e["time"])
    lv = e.get("level")
    vals = {"map": e.get("map"), "time": clock(when), "lv": lv,
            "prev": lv - 1 if lv else None, "job": e.get("job_to") or e.get("job"),
            "old": e.get("job_from"), "took": fmt_dur(e.get("took_s")),
            "lost": f"{e['lost']:,}" if e.get("lost") else None, "killer": e.get("killed_by"),
            "name": nickname(), "why": e.get("why"), "region": e.get("region")}
    lines = caption_lines()
    key = e["kind"]
    if key == "death" and vals["killer"] and lines.get("death_killer"):
        key = "death_killer"
    if key == "first_visit" and e.get("new_region") and lines.get("new_region"):
        key = "new_region"
        vals["region"] = e["new_region"]
    if key == "moment":
        key = "any"
    return _fill(lines.get(key, []), vals), _fill(lines.get("any", []), vals)


def custom_captions():
    return _read_json(CUSTOM, {})


def caption_info(e, custom=None):
    """(one-liner, source or None) for the Story view."""
    own = (custom_captions() if custom is None else custom).get(entry_id(e)) or e.get("caption")
    if e["kind"] == "note" and not own:
        return e["text"], None
    lines, anys = _filled_lines(e)                   # events keep lines about what happened;
    if own:                                          # 'any' lines are for random moments
        return own, next((src for t, src in lines + anys if t == own), None)
    if not lines:
        return e["text"].replace("**", "").replace("*", ""), None
    import zlib
    return lines[zlib.crc32((e["time"] + e["kind"]).encode()) % len(lines)]


def caption(e, custom=None):
    return caption_info(e, custom)[0]


def set_nickname(name):
    custom = custom_captions()
    if name:
        custom["_nickname"] = name
    else:
        custom.pop("_nickname", None)
    _save_custom(custom)


def _save_custom(custom):
    CUSTOM.parent.mkdir(parents=True, exist_ok=True)
    tmp = CUSTOM.with_suffix(".tmp")
    tmp.write_text(json.dumps(custom, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CUSTOM)
    _rebuild_html()


def _find(eid=None, kind=None):
    for e in reversed(with_context(entries())):
        if (eid and entry_id(e) == eid) or (not eid and (kind is None or e["kind"] == kind)):
            return e
    return None


def set_caption(text, kind=None, eid=None):
    """Own words for an entry (by id, else the latest one of `kind`); "" = automatic again."""
    e = _find(eid, kind)
    if not e:
        return None
    custom = custom_captions()
    if text and text.strip():
        custom[entry_id(e)] = text.strip()
    else:
        custom.pop(entry_id(e), None)
    _save_custom(custom)
    return e


def generate_caption(eid=None, kind=None):
    """Pick a random different dictionary line for the entry and keep it. Returns the line."""
    import random
    e = _find(eid, kind)
    if not e:
        return None
    lines, _ = _filled_lines(e)                      # same kind only (moments: the 'any' lines)
    cur = caption(e)
    group = [t for t, _ in lines if t != cur] or [t for t, _ in lines]
    if not group:
        return None
    pick = random.choice(group)
    set_caption(pick, eid=entry_id(e))
    return pick


def open_via():
    """"localhost" (default: http://127.0.0.1, the address bar shows no folder paths, and the page
    has the Edit / Generate buttons) or "file" (diary.html straight from disk).
    Settings > Diary in the helper (helper_settings.json -> diary.open_via)."""
    v = _read_json(DATA / "helper_settings.json", {}).get("diary", {}).get("open_via", "localhost")
    return v if v in ("localhost", "file") else "localhost"


def open_diary(via=None, in_process=False):
    """Open the diary in the browser. For the tray's "Open diary", Settings > Diary, the pop-up and
    any later caller (the tracker, a hotkey). Returns what was opened.

    localhost: reuse a running diary server, else start one. in_process=True (the helper: a
    long-running process) serves from a thread there, which also works in the packaged build;
    otherwise a background `diary.py serve --idle-exit=30` (from source only). Falls back to the
    file when no server can run."""
    import os
    import webbrowser
    import paths
    via = via or open_via()
    if via == "localhost":
        try:
            import diary_server
            url = diary_server.url()
            if diary_server.running():
                webbrowser.open(url)
                return url
            if in_process or paths.FROZEN:
                url = diary_server.start_thread()
                webbrowser.open(url)
                return url
            import subprocess
            exe = Path(sys.executable)
            exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
            subprocess.Popen([str(exe), str(CODE / "diary.py"), "serve", "--idle-exit=30"],
                             cwd=str(ROOT), creationflags=0x00000008 | 0x00000200, close_fds=True)
            return url + " (starting; it opens the browser itself)"
        except Exception as ex:
            print(f"diary server not started, opening the file: {ex}", file=sys.stderr)
    _rebuild_html()
    page = ROOT / "diary.html"
    os.startfile(str(page))
    return str(page)


def _rebuild_html():
    try:
        import diary_html
        diary_html.build(ROOT)
    except Exception as ex:
        print(f"diary.html not rebuilt: {ex}", file=sys.stderr)


# ------------------------------------------------------------------ places
def seen_places():
    return set(_read_json(PLACES, []))


_pending_place = {}      # context of the place mark_place() just announced, for add()


def map_db():
    """map name -> {region, street} from maps_classic.json."""
    out = {}
    for m in _read_json(CODE / "maps_classic.json", {}).get("maps", []):
        out.setdefault(m["name"], {"region": m.get("region"), "street": m.get("street")})
    return out


def important_maps():
    """map name -> why it matters, from important_maps.json (player's copy in DATA wins)."""
    for path in (DATA / "important_maps.json", CODE / "important_maps.json"):
        d = _read_json(path, None)
        if isinstance(d, dict):
            return {m["map"]: m.get("why") or "" for group in ("towns", "landmarks")
                    for m in d.get(group, []) if isinstance(m, dict) and m.get("map")}
    return {}


def place_info(name, seen=None, db=None):
    """{map, region, why, important, new_region} for a map; new_region is the region's name when
    no map in `seen` is in that region yet."""
    db = map_db() if db is None else db
    region = (db.get(name) or {}).get("region")
    why = important_maps().get(name)
    info = {"map": name, "region": region, "why": why, "important": why is not None,
            "new_region": None}
    if seen is not None and region not in SKIP_REGIONS and \
            not any((db.get(m) or {}).get("region") == region for m in seen if m != name):
        info["new_region"] = region
    return info


def mark_place(name):
    """Remember the map; True when it deserves a diary entry: an important map (important_maps.json)
    or the first map of a new region. Other maps are remembered quietly."""
    global _pending_place
    s = seen_places()
    if name in s:
        return False
    info = place_info(name, seen=s)
    s.add(name)
    PLACES.parent.mkdir(parents=True, exist_ok=True)
    PLACES.write_text(json.dumps(sorted(s), indent=1), encoding="utf-8")
    _pending_place = info
    return bool(info["important"] or info["new_region"])


def with_context(items):
    """Entries in time order with place context (why / new_region / region), worked out for old
    entries that predate it, plus `hidden` for first visits that aren't worth showing."""
    db, imp, seen_regions = map_db(), important_maps(), set()
    out = []
    for e in sorted(items, key=lambda e: e["time"]):
        e = dict(e)
        region = (db.get(e.get("map")) or {}).get("region")
        if e["kind"] == "first_visit":
            if "new_region" not in e:            # entries from before this existed
                e["new_region"] = region if (region not in SKIP_REGIONS
                                             and region not in seen_regions) else None
            if e.get("why") is None:
                e["why"] = imp.get(e.get("map"))
            e["hidden"] = not (e.get("new_region") or e.get("map") in imp)
        if e.get("region") is None:
            e["region"] = region
        if region not in SKIP_REGIONS:
            seen_regions.add(region)
        out.append(e)
    return out


# ------------------------------------------------------------------ random moments
_moment = {"next": None, "last": 0.0}


def moment_settings():
    """(min minutes, max minutes, on) from helper_settings.json -> diary (Settings > Diary)."""
    m = _read_json(DATA / "helper_settings.json", {}).get("diary", {})
    try:
        lo, hi = float(m.get("moment_min", 45)), float(m.get("moment_max", 120))
    except (TypeError, ValueError):
        lo, hi = 45.0, 120.0
    lo = max(5.0, lo)
    return min(lo, hi), max(lo, hi), bool(m.get("moments", True))


def moment_due(active_seconds):
    """Called by the tracker on each reading while playing. True now and then (every 45-120 min
    of active play by default; Settings > Diary in the helper) when nothing made the diary lately."""
    import random
    lo, hi, on = moment_settings()
    if not on:
        return False
    if _moment["next"] is None or active_seconds < _moment["last"]:      # new session
        _moment["next"] = active_seconds + random.uniform(lo, hi) * 60
    _moment["last"] = active_seconds
    if active_seconds < _moment["next"]:
        return False
    _moment["next"] = active_seconds + random.uniform(lo, hi) * 60
    items = entries()
    if items:                                   # don't crowd a real event
        last = datetime.fromisoformat(items[-1]["time"])
        if (datetime.now() - last).total_seconds() < 10 * 60:
            return False
    return True


def seed_places(names):
    """Maps visited before the diary existed: don't announce them as first visits."""
    if PLACES.exists():
        return
    PLACES.parent.mkdir(parents=True, exist_ok=True)
    PLACES.write_text(json.dumps(sorted(set(n for n in names if n)), indent=1), encoding="utf-8")


# ------------------------------------------------------------------ rendering
def render():
    items = [e for e in with_context(entries()) if not e.get("hidden")]
    out = [f"# {char_name()}'s diary\n",
           "*MapleStory Classic World. Written automatically by the tracker; screenshots are "
           "local only.*\n"]
    day = None
    for e in items:
        when = datetime.fromisoformat(e["time"])
        d = when.strftime("%A, %B %d, %Y").replace(" 0", " ")
        if d != day:
            day = d
            out.append(f"\n## {d}\n")
        out.append(f"\n**{when:%H:%M}** {ICON.get(e['kind'], '•')} {e['text']}\n")
        shots = [s for s in e.get("shots", []) if (ROOT / s).exists()]
        chars = [s for s in shots if s.endswith("_char.png")]
        fulls = [s for s in shots if not s.endswith("_char.png")]
        for s in chars:
            out.append(f"\n![{e['kind']}]({s.replace(' ', '%20')})\n")
        if fulls:
            links = " · ".join(f"[{'screenshot' if i == 0 else 'later'}]({s.replace(' ', '%20')})"
                               for i, s in enumerate(fulls))
            out.append(f"\n<sub>{links}</sub>\n")
    DIARY.write_text("".join(out), encoding="utf-8")
    _rebuild_html()                        # diary.html viewer; never breaks the diary


# ------------------------------------------------------------------ manual entries
def tracker_running():
    import msvcrt
    lock = ROOT / "history" / "tracker.lock"
    if not lock.exists():
        return False
    try:
        with open(lock, "a+") as f:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return True


def request_note(text):
    """Let the running tracker add the note with a screenshot of the current moment."""
    with open(REQUESTS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "note", "text": text,
                            "time": datetime.now().isoformat(timespec="seconds")}) + "\n")


def take_requests():
    """Called by the tracker: return and clear pending manual notes."""
    if not REQUESTS.exists():
        return []
    try:
        lines = REQUESTS.read_text(encoding="utf-8").splitlines()
        REQUESTS.unlink()
    except OSError:
        return []
    return [json.loads(x) for x in lines if x.strip()]


def annotate_last_death(killed_by):
    items = entries()
    for e in reversed(items):
        if e["kind"] == "death":
            e["killed_by"] = killed_by
            e["text"] = death_text(e.get("level"), e.get("map"), e.get("lost"), e.get("pct_lost"),
                                   killed_by)
            _write_all(items)
            render()
            return e
    return None


def backfill():
    """Diary entries for level-ups recorded before the diary existed."""
    import csv
    from PIL import Image
    from portrait import NameTag
    have = {(e["kind"], e.get("level")) for e in entries()}
    rows = list(csv.DictReader(open(ROOT / "history" / "levels.csv", encoding="utf-8")))
    tag, n = NameTag(), 0
    for r in rows:
        lv = int(r["level_reached"])
        if ("levelup", lv) in have:
            continue
        when = datetime.fromisoformat(r["time"])
        shots = []
        src = sorted((ROOT / "screenshots" / "levelups").glob(f"Lv{lv}_{when:%Y-%m-%d}_*.png"))
        if src:
            frame = Image.open(src[0]).convert("RGB")
            SHOTS.mkdir(parents=True, exist_ok=True)
            stem = f"{when:%Y-%m-%d_%H%M%S}_levelup"
            frame.save(SHOTS / f"{stem}.jpg", quality=88)
            shots.append(f"screenshots/diary/{stem}.jpg")
            p = tag.portrait(frame)
            if p:
                p.save(SHOTS / f"{stem}_char.png")
                shots.insert(0, f"screenshots/diary/{stem}_char.png")
        took = r.get("active_seconds_in_prev_level") or None
        add("levelup", levelup_text(lv, r.get("map"), took), when=when, level=lv, map=r.get("map"),
            shots=shots, took_s=int(took) if took else None)
        n += 1
    print(f"backfilled {n} level-up(s)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "render"
    if cmd == "note" and len(sys.argv) > 2:
        text = " ".join(sys.argv[2:])
        if tracker_running():
            request_note(text)
            print("Note queued: the tracker adds it with a screenshot within a few seconds.")
        else:
            add("note", text)
            print("Note added (tracker not running, so no screenshot).")
    elif cmd == "caption" and len(sys.argv) > 2:
        args, kind = sys.argv[2:], None
        if args[0] == "--kind" and len(args) > 1:
            kind, args = args[1], args[2:]
        if args == ["--random"]:
            line = generate_caption(kind=kind)
            print(f"New caption: {line}" if line else "No dictionary line fits that entry.")
        else:
            e = set_caption(" ".join(args), kind)
            print(f"Caption for {e['time']} {e['kind']}: {caption(e)}" if e else "No such entry.")
    elif cmd == "icons":
        import game_refs
        n = game_refs.fetch_icons(force="--force" in sys.argv)
        _rebuild_html()
        print(f"{n} icon(s) downloaded to {game_refs.ICONS}")
    elif cmd == "refs":
        args = sys.argv[2:]
        off = sources_off()
        if args[:1] in (["off"], ["on"]):
            names = set(args[1:])
            off = off | names if args[0] == "off" else off - names
            set_sources_off(off)
        for name, n in all_sources().items():
            print(f"  [{' ' if name in off else 'x'}] {name} ({n})")
        print("Switch with: python diary.py refs off \"Game\" / refs on \"Game\"")
    elif cmd == "nickname":
        set_nickname(" ".join(sys.argv[2:]).strip())
        print(f"Captions now call you {nickname()}.")
    elif cmd == "serve":
        import diary_server
        idle = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--idle-exit=")), None)
        diary_server.serve(open_browser="--no-open" not in sys.argv,
                           idle_exit_s=float(idle) * 60 if idle else None)
    elif cmd == "open":
        print(open_diary(via="file" if "--file" in sys.argv else None))
    elif cmd == "killed-by" and len(sys.argv) > 2:
        e = annotate_last_death(" ".join(sys.argv[2:]))
        print(f"Updated: {e['text']}" if e else "No death in the diary yet.")
    elif cmd == "backfill":
        backfill()
    elif cmd == "render":
        render()
        print(f"Rendered {DIARY} and diary.html")
    elif cmd == "html":
        import os
        import diary_html
        out = diary_html.build(ROOT)
        print(f"Built {out}")
        if "--no-open" not in sys.argv:
            os.startfile(out)
    else:
        print(__doc__)
