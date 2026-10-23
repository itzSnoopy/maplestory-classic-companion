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
  python diary.py render                          rebuild diary.md (and diary.html)
  python diary.py html                            rebuild diary.html and open it in the browser
  python diary.py backfill                        add entries for level-ups in history/levels.csv
                                                  that predate the diary (uses screenshots/levelups)

Screenshots are Nexon game imagery: local only, never publish or upload.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

CODE = Path(__file__).parent
ICON = {"levelup": "⬆️", "death": "💀", "job": "⭐", "first_visit": "🗺️", "note": "📝"}


def set_root(root):
    global ROOT, TIMELINE, DIARY, SHOTS, PLACES, REQUESTS
    ROOT = Path(root)
    TIMELINE = ROOT / "history" / "timeline.jsonl"
    DIARY = ROOT / "diary.md"
    SHOTS = ROOT / "screenshots" / "diary"
    PLACES = ROOT / "history" / "places.json"          # maps already in the diary
    REQUESTS = ROOT / "diary_requests.jsonl"           # notes waiting for the tracker


set_root(CODE)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def char_name():
    return _read_json(CODE / "helper_settings.json", {}).get("character", {}).get("name") or "My character"


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
    when = when or datetime.now()
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
    return f"First visit to *{map}*."


# ------------------------------------------------------------------ places
def seen_places():
    return set(_read_json(PLACES, []))


def mark_place(name):
    s = seen_places()
    if name in s:
        return False
    s.add(name)
    PLACES.parent.mkdir(parents=True, exist_ok=True)
    PLACES.write_text(json.dumps(sorted(s), indent=1), encoding="utf-8")
    return True


def seed_places(names):
    """Maps visited before the diary existed: don't announce them as first visits."""
    if PLACES.exists():
        return
    PLACES.parent.mkdir(parents=True, exist_ok=True)
    PLACES.write_text(json.dumps(sorted(set(n for n in names if n)), indent=1), encoding="utf-8")


# ------------------------------------------------------------------ rendering
def render():
    items = sorted(entries(), key=lambda e: e["time"])
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
    try:                                   # diary.html viewer; never let it break the diary
        import diary_html
        diary_html.build(ROOT)
    except Exception as ex:
        print(f"diary.html not rebuilt: {ex}", file=sys.stderr)


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
