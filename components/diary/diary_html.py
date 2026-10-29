"""diary_html.py - local HTML viewer for the diary (diary.html next to diary.md).

Built from history/timeline.jsonl, history/levels.csv, history/map_stats.json, digest.json and
maps_classic.json. One self-contained file (no internet needed); pictures are linked by relative
path from screenshots/diary/, so open it straight from disk:

  python -m components.diary.diary html          rebuild and open in the browser

diary.render() rebuilds it automatically, so it stays current while the tracker runs.
It shows Nexon game imagery: local only, never publish or upload it.
"""
import csv
import json
from datetime import datetime
from pathlib import Path

from components.diary import diary

from base import paths


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _regions():
    """map name -> {region, street} from maps_classic.json."""
    out = {}
    for m in _read_json(paths.asset("gamedata/maps_classic.json"), {}).get("maps", []):
        out.setdefault(m["name"], {"region": m.get("region"), "street": m.get("street")})
    return out


def _levels(root):
    try:
        rows = list(csv.DictReader(open(root / "history" / "levels.csv", encoding="utf-8")))
    except OSError:
        return []
    out = []
    for r in rows:
        def num(k):
            try:
                return int(float(r.get(k) or ""))
            except ValueError:
                return None
        out.append({"level": num("level_reached"), "time": r.get("time"), "map": r.get("map"),
                    "active_s": num("active_seconds_in_prev_level"),
                    "wall_s": num("wall_seconds_in_prev_level"),
                    "exp_needed": num("exp_needed_prev_level")})
    return out


def data(root=None):
    root = Path(root or diary.ROOT)
    items = [e for e in diary.with_context(diary.entries()) if not e.get("hidden")]
    custom = diary.custom_captions()
    for e in items:      # only pictures that exist; split close-ups from full frames
        shots = [s for s in e.get("shots", []) if (root / s).exists()]
        # close-ups: the character, and a first kill's middle-of-screen square (album.py)
        e["chars"] = [s for s in shots if s.endswith("_char.png") or s.endswith("_kill.jpg")]
        e["fulls"] = [s for s in shots if s not in e["chars"]]
        e["id"] = diary.entry_id(e)
        e["story"], e["story_source"] = diary.caption_info(e, custom)
        e["custom"] = e["id"] in custom
        e["clock"] = diary.clock(datetime.fromisoformat(e["time"]))
    maps = _read_json(root / "history" / "map_stats.json", {})
    digest = _read_json(root / "digest.json", {})
    return {"name": diary.char_name(), "generated": datetime.now().isoformat(timespec="seconds"),
            "entries": items, "levels": _levels(root), "maps": maps,
            "regions": {k: v for k, v in _regions().items()
                        if k in maps or any(e.get("map") == k for e in items)}, "now": digest.get("now", {}), "totals": digest.get("totals", {})}


def page(root=None, live=False):
    """The page as text. live=True: served by diary_server, so Edit / Generate can save."""
    d = data(root)
    d["live"] = live
    d["sources"] = diary.all_sources()
    d["sources_off"] = sorted(diary.sources_off())
    try:
        from gamedata import game_refs
        d["icons"] = game_refs.icons()
    except Exception:
        d["icons"] = {}
    blob = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__TITLE__", _esc(f"{d['name']}'s diary")).replace("__DATA__", blob)


def build(root=None):
    root = Path(root or diary.ROOT)
    html = page(root)
    out = root / "diary.html"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(out)
    return out


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #f6f3ee; --panel: #fffdf9; --ink: #2a2622; --muted: #7a7168; --line: #e6dfd5;
  --accent: #d9822b; --accent-soft: #fbead6;
  --levelup: #3f8f4a; --death: #b8433a; --job: #b8862b; --first_visit: #3a78b8; --note: #7a6a9a; --moment: #c0679a; --first_kill: #8a5a2b;
  --shadow: 0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #181614; --panel: #221f1c; --ink: #ece6de; --muted: #9c9389; --line: #36312c;
    --accent: #f0a050; --accent-soft: #3a2b1c;
    --levelup: #6cc077; --death: #e0756b; --job: #e2b558; --first_visit: #6fa8e0; --note: #b3a3d3; --moment: #e595c2; --first_kill: #d49a5e;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.25);
  }
}
:root[data-theme="dark"] {
  --bg: #181614; --panel: #221f1c; --ink: #ece6de; --muted: #9c9389; --line: #36312c;
  --accent: #f0a050; --accent-soft: #3a2b1c;
  --levelup: #6cc077; --death: #e0756b; --job: #e2b558; --first_visit: #6fa8e0; --note: #b3a3d3; --moment: #e595c2; --first_kill: #d49a5e;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.25);
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }
.wrap { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
header { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px 24px; margin-bottom: 18px; }
h1 { font-size: 28px; margin: 0; letter-spacing: -.01em; }
.sub { color: var(--muted); font-size: 13px; }
.stats { display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 18px; }
.stat { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 14px; min-width: 110px; box-shadow: var(--shadow); }
.stat b { display: block; font-size: 20px; font-variant-numeric: tabular-nums; }
.stat span { color: var(--muted); font-size: 12px; }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; margin-top: 4px; }
.bar i { display: block; height: 100%; background: var(--accent); }
nav.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin-bottom: 16px; }
nav.tabs button { background: none; border: 0; padding: 8px 14px; font: inherit; color: var(--muted);
  cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; }
nav.tabs button.on { color: var(--ink); border-color: var(--accent); font-weight: 600; }
.controls { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 14px; }
.chip { border: 1px solid var(--line); background: var(--panel); color: var(--ink); border-radius: 999px;
  padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
.chip.on { background: var(--accent-soft); border-color: var(--accent); }
.chip .n { color: var(--muted); margin-left: 4px; font-variant-numeric: tabular-nums; }
.spacer { flex: 1; }
.day { margin: 22px 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); font-weight: 600; }
.entry { display: grid; grid-template-columns: 56px 1fr; gap: 0 14px; background: var(--panel);
  border: 1px solid var(--line); border-left: 4px solid var(--k); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 10px; box-shadow: var(--shadow); }
.entry .time { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 13px; padding-top: 2px; }
.entry .kind { font-size: 12px; font-weight: 600; color: var(--k); text-transform: uppercase; letter-spacing: .05em; }
.entry .text { margin: 2px 0 6px; }
.entry .meta { display: flex; flex-wrap: wrap; gap: 6px; font-size: 12px; color: var(--muted); }
.entry .meta span { background: var(--bg); border-radius: 6px; padding: 1px 8px; }
.pics { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.pics img { display: block; border-radius: 8px; border: 1px solid var(--line); cursor: zoom-in; background: #000; }
.pics img.char { width: 240px; max-width: 100%; height: auto; }
.pics img.full { width: 200px; max-width: 100%; height: auto; opacity: .92; }
.pics img:hover { opacity: 1; outline: 2px solid var(--accent); }
.empty { color: var(--muted); padding: 30px 0; text-align: center; }
table { width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 10px;
  overflow: hidden; box-shadow: var(--shadow); font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); font-size: 14px; }
th { font-size: 12px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
td.num, th.num { text-align: right; }
td .tbar { height: 8px; border-radius: 4px; background: var(--accent); opacity: .75; min-width: 2px; }
tr:last-child td { border-bottom: 0; }
.tablewrap { overflow-x: auto; }
.lb { position: fixed; inset: 0; background: rgba(0,0,0,.88); display: none; flex-direction: column;
  align-items: center; justify-content: center; z-index: 10; padding: 16px; }
.lb.on { display: flex; }
.lb img { max-width: 100%; max-height: calc(100vh - 90px); border-radius: 6px; }
.lb .cap { color: #eee; margin-top: 10px; font-size: 14px; text-align: center; max-width: 900px; }
.lb button { position: absolute; background: rgba(255,255,255,.12); color: #fff; border: 0;
  border-radius: 999px; width: 44px; height: 44px; font-size: 22px; cursor: pointer; }
.lb .x { top: 14px; right: 14px; } .lb .prev { left: 14px; top: 50%; } .lb .next { right: 14px; top: 50%; }
/* Story view: one line down the middle, beats alternating left / right */
.story { position: relative; padding: 6px 0 40px; }
.story::before { content: ""; position: absolute; left: 50%; top: 0; bottom: 0; width: 4px;
  margin-left: -2px; background: var(--ink); border-radius: 2px; }
.story .end { position: relative; text-align: center; color: var(--muted); font-size: 13px; padding-top: 18px; }
.story .end span, .sday span { position: relative; background: var(--bg); padding: 3px 14px; }
.sday { position: relative; text-align: center; margin: 22px 0 14px; }
.sday span { border: 2px solid var(--ink); border-radius: 999px; font-weight: 600; font-size: 13px; }
.beat { position: relative; width: 50%; padding: 8px 40px 26px; }
.beat.l { text-align: right; }
.beat.r { margin-left: 50%; }
.beat .dot { position: absolute; top: 6px; width: 34px; height: 34px; border-radius: 50%;
  background: var(--panel); border: 3px solid var(--k); display: grid; place-items: center; font-size: 15px; }
.beat.l .dot { right: -17px; } .beat.r .dot { left: -17px; }
.beat .cap { font-family: "Segoe Print", "Bradley Hand", "Comic Sans MS", cursive; font-size: 19px;
  line-height: 1.4; padding-top: 4px; }
.beat.death .cap { color: var(--death); }
.beat.job .cap { color: var(--job); }
.polaroid { display: inline-block; position: relative; margin: 12px 0 0; background: #fbfaf7;
  padding: 8px 8px 0; border-radius: 3px; box-shadow: 0 2px 4px rgba(0,0,0,.18), 0 8px 22px rgba(0,0,0,.14);
  cursor: zoom-in; transform: rotate(var(--tilt)); transition: transform .15s; max-width: 100%; }
.polaroid:hover { transform: rotate(0deg) scale(1.03); }
.beat.l .polaroid { --tilt: 2deg; } .beat.r .polaroid { --tilt: -2deg; }
.polaroid img { display: block; width: 230px; max-width: 100%; height: auto; background: #000; }
.polaroid.wide img { width: 280px; aspect-ratio: 16 / 10; object-fit: cover; }
.polaroid .stamp { position: absolute; right: 14px; top: 14px; font: 700 14px/1 Consolas, "Courier New", monospace;
  color: #ff9a2e; text-shadow: 0 0 2px #ff5a00, 0 0 6px rgba(255,90,0,.6); letter-spacing: .06em; }
.polaroid figcaption { color: #4a4540; font: 13px/1.2 "Segoe Print", "Comic Sans MS", cursive; padding: 7px 4px 10px;
  text-align: center; max-width: 230px; margin: 0 auto; }
@media (max-width: 640px) {
  .story::before { left: 18px; }
  .beat, .beat.l, .beat.r { width: auto; margin-left: 0; padding: 6px 0 24px 50px; text-align: left; }
  .beat.l .dot, .beat.r .dot { left: 1px; right: auto; }
  .beat.l .tools { justify-content: flex-start; }
  .beat.l .polaroid { --tilt: -2deg; }
  .story .end, .sday { text-align: left; padding-left: 0; }
}
.beat .tools { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; align-items: center; }
.beat.l .tools { justify-content: flex-end; }
.beat .tools button { border: 1px solid var(--line); background: var(--panel); color: var(--ink); border-radius: 999px;
  padding: 2px 10px; font: inherit; font-size: 12px; cursor: pointer; opacity: .55; transition: opacity .15s; }
.beat:hover .tools button, .beat .tools button:focus-visible, .editor .tools button { opacity: 1; }
.beat .tools button:hover { border-color: var(--accent); }
.beat .tools button:disabled { opacity: .4; cursor: wait; }
.beat .src { font-size: 11px; color: var(--muted); font-style: italic; margin-top: 2px; }
.gicon { height: 24px; width: auto; aspect-ratio: 184 / 69; border-radius: 3px; vertical-align: middle; margin-right: 6px; }
.refs .gicon { height: 20px; vertical-align: middle; margin-right: 0; }
.beat .tools .mine { font-size: 11px; color: var(--muted); }
.editor textarea { width: 100%; max-width: 420px; font: 17px/1.4 "Segoe Print", "Comic Sans MS", cursive; color: var(--ink);
  background: var(--panel); border: 2px solid var(--accent); border-radius: 8px; padding: 6px 10px; resize: vertical; }
.refs { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 14px; margin: 0 0 16px; z-index: 1; }
.refs summary { cursor: pointer; font-weight: 600; }
.refs .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.refs label { display: inline-flex; gap: 6px; align-items: center; border: 1px solid var(--line); border-radius: 999px;
  padding: 3px 10px; font-size: 13px; cursor: pointer; }
.refs label:has(input:not(:checked)) { opacity: .55; text-decoration: line-through; }
.refs .n { color: var(--muted); font-size: 12px; }
.refs .row2 { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; font-size: 12px; color: var(--muted); }
.refs button { border: 1px solid var(--line); background: var(--bg); color: var(--ink); border-radius: 999px;
  padding: 3px 12px; font: inherit; font-size: 12px; cursor: pointer; }
.refs button.save { background: var(--accent); border-color: var(--accent); color: var(--panel); font-weight: 600; }
.hint { position: relative; background: var(--bg); color: var(--muted); font-size: 13px; text-align: center; padding: 0 0 12px; }
.hint code { background: var(--panel); border: 1px solid var(--line); border-radius: 5px; padding: 1px 6px; }
#toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%) translateY(20px); opacity: 0; pointer-events: none;
  background: var(--ink); color: var(--bg); padding: 8px 16px; border-radius: 8px; font-size: 14px; transition: .2s; z-index: 20; max-width: calc(100% - 32px); }
#toast.on { opacity: 1; transform: translateX(-50%); }
@media (hover: none) { .beat .tools button { opacity: 1; } }
.foot { margin-top: 30px; color: var(--muted); font-size: 12px; }
/* day navigation (Story + Details) */
.daynav { position: sticky; top: 0; z-index: 5; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  background: var(--bg); padding: 6px 0 10px; margin-bottom: 6px; }
.daynav[hidden] { display: none; }
.daynav button, .daynav select { border: 1px solid var(--line); background: var(--panel); color: var(--ink); border-radius: 999px;
  padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
.daynav button:disabled { opacity: .4; cursor: default; }
.daynav button:not(:disabled):hover, .daynav select:hover { border-color: var(--accent); }
.daynav .today { font-weight: 600; }
.daynav .viewsw { margin-left: auto; display: inline-flex; }
.daynav .viewsw button { border-radius: 0; }
.daynav .viewsw button:first-child { border-radius: 999px 0 0 999px; }
.daynav .viewsw button:last-child { border-radius: 0 999px 999px 0; margin-left: -1px; }
.daynav .viewsw button.on { background: var(--accent-soft); border-color: var(--accent); }
.entry .tools { margin-top: 8px; }
.entry .tools button { border: 1px solid var(--line); background: var(--panel); color: var(--muted); border-radius: 999px;
  padding: 2px 10px; font: inherit; font-size: 12px; cursor: pointer; }
.entry .tools button:hover { color: var(--death); border-color: var(--death); }
.beat .tools button.del:hover { border-color: var(--death); color: var(--death); }
/* lightbox: Close-up | Full for the same entry */
.lb .lbtabs { display: flex; gap: 6px; margin-top: 10px; }
.lb .lbtabs:empty { display: none; }
.lb .lbtabs button { position: static; width: auto; height: auto; font-size: 13px; padding: 5px 14px; border-radius: 999px; }
.lb .lbtabs button.on { background: var(--accent); color: #1b1309; font-weight: 600; }
/* horizontal Story: the line runs left to right, scroll sideways */
.story.h { display: flex; align-items: flex-start; overflow-x: auto; overflow-y: hidden; padding: 0 0 18px;
  scroll-behavior: smooth; overscroll-behavior-x: contain; }
.story.h::before { display: none; }
.story.h > * { flex: 0 0 auto; position: relative; }
.story.h > *::after { content: ""; position: absolute; left: 0; right: 0; top: 22px; height: 4px; background: var(--ink); z-index: 0; }
.story.h .beat, .story.h .beat.l, .story.h .beat.r { width: 330px; margin: 0; padding: 60px 40px 10px; text-align: center; }
.story.h .beat .dot, .story.h .beat.l .dot, .story.h .beat.r .dot { left: 50%; right: auto; top: 6px; transform: translateX(-50%); z-index: 1; }
.story.h .beat .tools { justify-content: center; }
.story.h .sday { margin: 0; padding: 12px 22px 0; align-self: stretch; }
/* a thin line down the strip where a new day starts */
.story.h .sday:not(:first-child)::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 0;
  border-left: 2px dashed var(--line); }
.story.h .sday span { z-index: 1; white-space: nowrap; }
.story.h .end { padding: 12px 24px 0; }
.story.h .end span { z-index: 1; white-space: nowrap; }
.story.h { cursor: grab; user-select: none; scroll-behavior: auto; }
.story.h.dragging { cursor: grabbing; }
.story.h.dragging * { pointer-events: none; }
.story.h img { -webkit-user-drag: none; }
.storybox { position: relative; }
.storybox .harrow { display: none; position: absolute; top: 0; bottom: 18px; width: 64px; z-index: 3; border: 0;
  font: 300 56px/1 "Segoe UI", system-ui, sans-serif; color: var(--ink); cursor: pointer; opacity: .55; transition: opacity .15s; }
.storybox.h .harrow { display: block; }
.storybox .harrow:hover { opacity: 1; }
.storybox .harrow[disabled] { opacity: 0; pointer-events: none; }
.storybox .harrow.left { left: 0; background: linear-gradient(to right, var(--bg) 25%, transparent); text-align: left; padding-left: 10px; }
.storybox .harrow.right { right: 0; background: linear-gradient(to left, var(--bg) 25%, transparent); text-align: right; padding-right: 10px; }
/* vertical Story: a small separator above each new day */
.story:not(.h) .sday:not(:first-child) { margin-top: 34px; padding-top: 18px; }
.story:not(.h) .sday:not(:first-child)::before { content: ""; position: absolute; left: 50%; top: 0; width: 120px; margin-left: -60px;
  border-top: 2px dashed var(--line); }
.theme { background: none; border: 1px solid var(--line); color: var(--muted); border-radius: 8px;
  padding: 4px 10px; font: inherit; font-size: 12px; cursor: pointer; }
@media (max-width: 600px) {
  .entry { grid-template-columns: 1fr; }
  .entry .time { padding: 0 0 2px; }
  h1 { font-size: 22px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1 id="title"></h1>
      <div class="sub">MapleStory Classic World · written automatically by the tracker · local only</div>
    </div>
    <div class="spacer"></div>
    <a class="theme" href="album.html" title="Monster album: every mob, your first kills" style="text-decoration:none">👾 Album</a>
    <button class="theme" id="theme" title="Switch light / dark">Theme</button>
  </header>
  <div class="stats" id="stats"></div>
  <nav class="tabs" id="tabs">
    <button data-tab="story">Story</button>
    <button data-tab="timeline">Details</button>
    <button data-tab="levels">Levels</button>
    <button data-tab="places">Places</button>
  </nav>
  <div class="daynav" id="daynav"></div>
  <section id="story"><div id="storytop"></div>
    <div class="storybox" id="storybox">
      <button class="harrow left" id="hleft" title="Back (←)" aria-label="Scroll back">‹</button>
      <div class="story" id="storyline"></div>
      <button class="harrow right" id="hright" title="Forward (→)" aria-label="Scroll forward">›</button>
    </div></section>
  <section id="timeline" hidden>
    <div class="controls" id="filters"></div>
    <div id="list"></div>
  </section>
  <section id="levels" hidden><div class="tablewrap" id="levtable"></div></section>
  <section id="places" hidden><div class="tablewrap" id="maptable"></div></section>
  <div class="foot" id="foot"></div>
</div>
<div class="lb" id="lb">
  <button class="x" title="Close (Esc)">×</button>
  <button class="prev" title="Previous (←)">‹</button>
  <img alt="">
  <div class="lbtabs"></div>
  <div class="cap"></div>
  <button class="next" title="Next (→)">›</button>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const KINDS = {levelup: ["⬆️", "Level up"], death: ["💀", "Death"], job: ["⭐", "Job advancement"],
               first_visit: ["🗺️", "First visit"], first_kill: ["⚔️", "First kill"], note: ["📝", "Note"],
               moment: ["📸", "Moment"]};
const $ = s => document.querySelector(s);
const store = {
  get(k, d) { try { const v = localStorage.getItem("diary." + k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("diary." + k, v); } catch (e) {} }
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const md = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\*(.+?)\*/g, "<i>$1</i>");
const src = p => p.split("/").map(encodeURIComponent).join("/");
const dur = s => { if (s == null) return "–"; s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : m ? `${m}m` : `${s}s`; };
const dt = iso => new Date(iso);
const dayName = d => d.toLocaleDateString(undefined, {weekday: "long", month: "long", day: "numeric", year: "numeric"});
const hm = d => d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", hour12: false});
const shortDay = d => d.toLocaleDateString(undefined, {month: "short", day: "numeric"});
const num = n => n == null ? "–" : Number(n).toLocaleString();

document.title = D.name + "'s diary";
$("#title").textContent = D.name + "'s diary";

// theme toggle: auto -> light -> dark
const themes = ["auto", "light", "dark"];
function applyTheme(t) { if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t); $("#theme").textContent = "Theme: " + t; }
let theme = store.get("theme", "auto"); applyTheme(theme);
$("#theme").onclick = () => { theme = themes[(themes.indexOf(theme) + 1) % 3]; store.set("theme", theme); applyTheme(theme); };

// stats strip
(function stats() {
  const n = D.now || {}, E = D.entries;
  const lastJob = [...E].reverse().find(e => e.job)?.job;
  const level = n.level ?? (Math.max(0, ...E.map(e => e.level || 0)) || "–");
  const parts = [];
  parts.push(`<div class="stat"><b>Lv ${esc(level)}</b><span>${esc(n.job || lastJob || "")}</span>` +
    (n.pct != null ? `<div class="bar"><i style="width:${Math.min(100, n.pct)}%"></i></div><span>${n.pct.toFixed(1)}% to Lv ${level + 1}</span>` : "") + `</div>`);
  if (D.totals?.played) parts.push(`<div class="stat"><b>${esc(D.totals.played)}</b><span>played</span></div>`);
  const c = k => E.filter(e => e.kind === k).length;
  parts.push(`<div class="stat"><b>${E.length}</b><span>diary entries</span></div>`);
  parts.push(`<div class="stat"><b>${Object.keys(D.maps || {}).length}</b><span>maps explored</span></div>`);
  parts.push(`<div class="stat"><b>${c("death")}</b><span>deaths</span></div>`);
  $("#stats").innerHTML = parts.join("");
})();

// days: Day 1 = the first day with an entry
const dayKey = d => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
let DAYS = [];          // [{key, n, label, long}] oldest first
function computeDays() {
  const seen = new Map();
  for (const e of D.entries) { const d = dt(e.time), k = dayKey(d);
    if (!seen.has(k)) seen.set(k, {key: k, n: seen.size + 1, label: shortDay(d), long: dayName(d)}); }
  DAYS = [...seen.values()];
}
const dayOf = e => DAYS.find(x => x.key === dayKey(dt(e.time)));
let curDay = 0;         // index into DAYS shown in the nav
let horizontal = store.get("view", "v") === "h";

function renderDayNav() {
  const nav = $("#daynav"), tab = store.get("tab", "story");
  nav.hidden = !(tab === "story" || tab === "timeline") || !DAYS.length;
  if (nav.hidden) return;
  curDay = Math.max(0, Math.min(curDay, DAYS.length - 1));
  nav.innerHTML = `<button data-nav="prev" title="Previous day"${curDay ? "" : " disabled"}>‹</button>
    <select title="Jump to a day">${DAYS.map((x, i) => `<option value="${i}"${i === curDay ? " selected" : ""}>Day ${x.n} · ${esc(x.label)}</option>`).join("")}</select>
    <button data-nav="next" title="Next day"${curDay < DAYS.length - 1 ? "" : " disabled"}>›</button>
    <button data-nav="today" class="today" title="Jump to today">Today</button>` +
    (tab === "story" ? `<span class="viewsw"><button data-view="v" class="${horizontal ? "" : "on"}" title="Top to bottom">Vertical</button><button data-view="h" class="${horizontal ? "on" : ""}" title="Left to right, scroll sideways">Horizontal</button></span>` : "");
  nav.querySelector("select").onchange = ev => jumpDay(+ev.target.value);
  for (const b of nav.querySelectorAll("[data-nav]")) b.onclick = () => {
    if (b.dataset.nav === "prev") jumpDay(curDay - 1);
    else if (b.dataset.nav === "next") jumpDay(curDay + 1);
    else { const i = DAYS.findIndex(x => x.key === dayKey(new Date()));
      if (i < 0) toast("No entries today yet. Showing your last day.");
      jumpDay(i < 0 ? DAYS.length - 1 : i); }
  };
  for (const b of nav.querySelectorAll("[data-view]")) b.onclick = () => {
    horizontal = b.dataset.view === "h"; store.set("view", horizontal ? "h" : "v");
    const keep = curDay; renderStory(); renderDayNav(); jumpDay(keep, false);
  };
}
function dayHeader(i) {
  const tab = store.get("tab", "story");
  return document.querySelector(`${tab === "story" ? "#storyline" : "#list"} [data-day="${i}"]`);
}
function jumpDay(i, smooth = true) {
  if (!DAYS.length) return;
  curDay = Math.max(0, Math.min(i, DAYS.length - 1));
  history.replaceState(null, "", "#day-" + DAYS[curDay].n);
  renderDayNav();
  const h = dayHeader(curDay); if (!h) return;
  if (horizontal && store.get("tab", "story") === "story") {
    const box = $("#storyline");
    hscrollTo(h.offsetLeft - box.offsetLeft - 8, smooth);
    const y = box.getBoundingClientRect().top + window.scrollY - $("#daynav").offsetHeight - 8;
    if (box.getBoundingClientRect().top < 0 || box.getBoundingClientRect().top > innerHeight / 2) window.scrollTo({top: y});
  } else {
    const y = h.getBoundingClientRect().top + window.scrollY - $("#daynav").offsetHeight - 8;
    window.scrollTo({top: y, behavior: smooth ? "smooth" : "auto"});
  }
}
// horizontal Story: big arrows (most of a screen per click), drag to move, arrow keys
function arrows() {
  const box = $("#storyline");
  $("#hleft").disabled = box.scrollLeft <= 2;
  $("#hright").disabled = box.scrollLeft + box.clientWidth >= box.scrollWidth - 2;
}
// smooth when the browser animates it; a page in the background doesn't, so jump if nothing moved
function hscrollTo(left, smooth = true) {
  const box = $("#storyline"), from = box.scrollLeft;
  left = Math.max(0, Math.min(left, box.scrollWidth - box.clientWidth));
  box.scrollTo({left, behavior: smooth ? "smooth" : "auto"});
  if (smooth) setTimeout(() => { if (box.scrollLeft === from && from !== left) box.scrollLeft = left; arrows(); }, 400);
}
function hpage(dir) { const box = $("#storyline"); hscrollTo(box.scrollLeft + dir * box.clientWidth * 0.8); }
$("#hleft").onclick = () => hpage(-1);
$("#hright").onclick = () => hpage(1);
document.addEventListener("keydown", e => {
  if (!horizontal || store.get("tab", "story") !== "story" || $("#lb").classList.contains("on")) return;
  if (e.target.closest && e.target.closest("textarea, input, select")) return;
  if (e.key === "ArrowLeft") { hpage(-1); e.preventDefault(); } else if (e.key === "ArrowRight") { hpage(1); e.preventDefault(); }
});
(function drag() {
  const box = $("#storyline");
  let x0 = 0, s0 = 0, down = false, moved = false;
  box.addEventListener("pointerdown", e => {
    if (!horizontal || e.button !== 0 || e.target.closest("button, textarea, input, select, a")) return;
    down = true; moved = false; x0 = e.clientX; s0 = box.scrollLeft;
  });
  window.addEventListener("pointermove", e => {
    if (!down) return;
    const dx = e.clientX - x0;
    if (!moved && Math.abs(dx) > 6) { moved = true; box.classList.add("dragging"); }
    if (moved) box.scrollLeft = s0 - dx;
  });
  window.addEventListener("pointerup", () => {
    if (!down) return; down = false;
    if (moved) { box.classList.remove("dragging");
      // a drag that ends on a photo isn't a click on it
      const eat = ev => { ev.stopPropagation(); ev.preventDefault(); };
      box.addEventListener("click", eat, {capture: true, once: true});
      setTimeout(() => { box.removeEventListener("click", eat, {capture: true}); moved = false; }, 0); }
  });
})();

// keep the nav on the day you're looking at while you scroll
function syncDay() {
  const tab = store.get("tab", "story"); if (!(tab === "story" || tab === "timeline")) return;
  const hs = [...document.querySelectorAll(`${tab === "story" ? "#storyline" : "#list"} [data-day]`)];
  if (!hs.length) return;
  let pick = hs[0];
  if (horizontal && tab === "story") { const box = $("#storyline"), x = box.scrollLeft + 40;
    for (const h of hs) if (h.offsetLeft - box.offsetLeft <= x) pick = h; }
  else { const top = $("#daynav").offsetHeight + 20; for (const h of hs) if (h.getBoundingClientRect().top <= top) pick = h; }
  const i = +pick.dataset.day; if (i !== curDay) { curDay = i; const sel = $("#daynav select"); if (sel) {
    sel.value = i; $("#daynav [data-nav=prev]").disabled = !i; $("#daynav [data-nav=next]").disabled = i >= DAYS.length - 1; } }
}
let syncT = 0; const syncSoon = () => { clearTimeout(syncT); syncT = setTimeout(syncDay, 80); };
window.addEventListener("scroll", syncSoon, {passive: true});

// tabs
function showTab(t) {
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === t);
  for (const id of ["story", "timeline", "levels", "places"]) $("#" + id).hidden = id !== t;
  store.set("tab", t);
  if (DAYS.length) { renderDayNav(); setTimeout(syncDay, 0); }
}
for (const b of document.querySelectorAll("#tabs button")) b.onclick = () => showTab(b.dataset.tab);
showTab(store.get("tab", "story"));

// timeline
let filter = store.get("filter", "all"), newestFirst = store.get("order", "new") === "new";
let gallery = [];   // [{cap, pics: [{src, kind, label}]}] one group per entry, in display order
function renderFilters() {
  const counts = {}; for (const e of D.entries) counts[e.kind] = (counts[e.kind] || 0) + 1;
  const chips = [["all", "All", D.entries.length]];
  for (const k of Object.keys(KINDS)) if (counts[k]) chips.push([k, KINDS[k][0] + " " + KINDS[k][1], counts[k]]);
  $("#filters").innerHTML = chips.map(([k, l, n]) =>
    `<button class="chip${filter === k ? " on" : ""}" data-k="${k}">${esc(l)}<span class="n">${n}</span></button>`).join("") +
    `<span class="spacer"></span><button class="chip" id="order">${newestFirst ? "Newest first" : "Oldest first"}</button>`;
  for (const b of document.querySelectorAll("#filters [data-k]"))
    b.onclick = () => { filter = b.dataset.k; store.set("filter", filter); renderFilters(); renderList(); };
  $("#order").onclick = () => { newestFirst = !newestFirst; store.set("order", newestFirst ? "new" : "old"); renderFilters(); renderList(); };
}
function renderList() {
  let items = D.entries.filter(e => filter === "all" || e.kind === filter);
  if (newestFirst) items = [...items].reverse();
  gallery = [];
  if (!items.length) { $("#list").innerHTML = `<div class="empty">Nothing here yet.</div>`; return; }
  let day = null; const out = [];
  for (const e of items) {
    const d = dt(e.time), dn = dayName(d);
    if (dn !== day) { day = dn; const x = dayOf(e);
      out.push(`<div class="day" data-day="${x.n - 1}">Day ${x.n} · ${esc(dn)}</div>`); }
    const [icon, label] = KINDS[e.kind] || ["•", e.kind];
    const meta = [];
    if (e.level) meta.push(`Lv ${e.level}`);
    if (e.job) meta.push(esc(e.job));
    if (e.map) { const r = D.regions?.[e.map]; meta.push(esc(e.map) + (r ? ` · ${esc(r.street)}, ${esc(r.region)}` : "")); }
    const cap = `${hm(d)} · ${e.text.replace(/\*/g, "")}`;
    const pics = [], g = picGroup(e, cap), gi = gallery.length;
    if (g.pics.length) gallery.push(g);
    g.pics.forEach((p, j) => pics.push(`<img class="${p.kind}" loading="lazy" data-g="${gi}" data-p="${j}" src="${src(p.src)}" alt="${esc(p.label)}">`));
    out.push(`<article class="entry" style="--k:var(--${esc(e.kind)},var(--accent))">
      <div class="time">${hm(d)}</div>
      <div><div class="kind">${icon} ${esc(label)}</div>
        <div class="text">${md(e.text)}</div>
        <div class="meta">${meta.map(m => `<span>${m}</span>`).join("")}</div>
        ${pics.length ? `<div class="pics">${pics.join("")}</div>` : ""}
        ${D.live ? `<div class="tools"><button data-del="${esc(e.id)}" title="Delete this entry for good">🗑 Delete</button></div>` : ""}</div></article>`);
  }
  $("#list").innerHTML = out.join("");
  for (const img of document.querySelectorAll("#list img")) {
    img.onclick = () => openLb(+img.dataset.g, gallery, +img.dataset.p);
    img.onerror = () => img.remove();
  }
  for (const b of document.querySelectorAll("#list [data-del]"))
    b.onclick = () => deleteEntry(D.entries.find(x => x.id === b.dataset.del), b);
}
// the pictures of one entry: close-up first, then the full screenshot(s)
function picGroup(e, cap) {
  const pics = (e.chars || []).map(s => ({src: s, kind: "char", label: "Close-up"}));
  (e.fulls || []).forEach((s, i) => pics.push({src: s, kind: "full", label: i ? "Full, a few seconds later" : "Full screenshot"}));
  return {cap, pics};
}
// delete for good (served page only): warn first, there's no undo
async function deleteEntry(e, btn) {
  if (!e) return;
  const d = dt(e.time), what = (e.story || e.text || "").replace(/\*/g, "");
  const n = (e.chars || []).length + (e.fulls || []).length;
  const msg = `Delete this diary entry for good?\n\n"${what}"\n${dayName(d)}, ${hm(d)}` +
    (n ? `\n\nIts ${n === 1 ? "picture is" : n + " pictures are"} deleted from your PC too.` : "") +
    (e.kind === "levelup" ? "\nThe level itself stays in your Levels history." : "") +
    `\n\nThis can't be undone.`;
  if (!confirm(msg)) return;
  if (btn) btn.disabled = true;
  try {
    await api("/api/delete", {id: e.id});
    D.entries = D.entries.filter(x => x.id !== e.id);
    computeDays(); renderFilters(); renderList(); renderStory(); renderDayNav(); toast("Entry deleted.");
  } catch (err) {
    if (btn) btn.disabled = false;
    toast(/fetch/i.test(err.message) ? "Can't reach the diary server. Is it still running?" : err.message);
  }
}
computeDays(); renderFilters(); renderList();

// lightbox
// one entry at a time; its Close-up / Full screenshot are tabs (F switches), arrows move between entries
let lbi = 0, lbp = 0, lbList = [], lbKind = "char";
function openLb(i, list, p) { if (list) lbList = list; if (!lbList.length) return;
  lbi = (i + lbList.length) % lbList.length; const g = lbList[lbi];
  if (p == null) { p = g.pics.findIndex(x => x.kind === lbKind); if (p < 0) p = 0; }   // keep the kind you were viewing
  lbp = Math.max(0, Math.min(p, g.pics.length - 1)); lbKind = g.pics[lbp].kind;
  $("#lb img").src = src(g.pics[lbp].src); $("#lb .cap").textContent = g.cap;
  $("#lb .lbtabs").innerHTML = g.pics.length > 1 ? g.pics.map((x, j) =>
    `<button data-p="${j}" class="${j === lbp ? "on" : ""}" title="Switch picture (F)">${esc(x.label)}</button>`).join("") : "";
  for (const b of $("#lb .lbtabs").querySelectorAll("button")) b.onclick = ev => { ev.stopPropagation(); openLb(lbi, null, +b.dataset.p); };
  $("#lb").classList.add("on"); }
const closeLb = () => $("#lb").classList.remove("on");
$("#lb .x").onclick = closeLb;
$("#lb .prev").onclick = e => { e.stopPropagation(); openLb(lbi - 1); };
$("#lb .next").onclick = e => { e.stopPropagation(); openLb(lbi + 1); };
$("#lb").onclick = e => { if (e.target.id === "lb") closeLb(); };
document.addEventListener("keydown", e => { if (!$("#lb").classList.contains("on")) return;
  if (e.key === "Escape") closeLb(); else if (e.key === "ArrowLeft") openLb(lbi - 1); else if (e.key === "ArrowRight") openLb(lbi + 1);
  else if (e.key === "f" || e.key === "F") { const g = lbList[lbi]; if (g.pics.length > 1) openLb(lbi, null, (lbp + 1) % g.pics.length); } });

// story: oldest at the top, beats alternate sides, one photo print per beat.
// Served by `python -m components.diary.diary serve` (D.live), each line gets Edit / Generate buttons that save.
async function api(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Diary": "1"},
                               body: JSON.stringify(body)});
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
let editing = null;     // entry id being edited
let refsOpen = false;
const gicon = s => D.icons?.[s] ? `<img class="gicon" src="${src(D.icons[s])}" alt="" width="184" height="69" loading="lazy" onerror="this.remove()">` : "";
function renderStory() {
  const E = D.entries, pics = [], out = [], top = [];
  if (!D.live) top.push(`<div class="hint">Want to edit or reroll the captions? Run <code>python -m components.diary.diary serve</code>.</div>`);
  else if (Object.keys(D.sources || {}).length) {
    const off = new Set(D.sources_off || []);
    top.push(`<details class="refs" id="refs"${refsOpen ? " open" : ""}><summary>🎮 References used for random captions (${Object.keys(D.sources).length - off.size} of ${Object.keys(D.sources).length} on)</summary>
      <div class="chips">${Object.entries(D.sources).map(([s, n]) => `<label><input type="checkbox" value="${esc(s)}"${off.has(s) ? "" : " checked"}> ${gicon(s)}${esc(s)} <span class="n">${n}</span></label>`).join("")}</div>
      <div class="row2"><button type="button" class="save" data-refs="save">Save</button><button type="button" data-refs="all">All on</button>
        <span>Plain lines are always on. Your own captions stay as they are.</span></div></details>`);
  }
  $("#storytop").innerHTML = top.join("");
  $("#storyline").classList.toggle("h", horizontal);
  $("#storybox").classList.toggle("h", horizontal);
  if (!E.length) { $("#storyline").innerHTML = `<div class="empty">The story starts with your first level-up.</div>`; return; }
  let day = null, n = 0;
  E.forEach((e, idx) => {
    const d = dt(e.time), dn = dayName(d);
    if (dn !== day) { day = dn; const x = dayOf(e);
      out.push(`<div class="sday" data-day="${x.n - 1}"><span>Day ${x.n} · ${esc(horizontal ? x.label : dn)}</span></div>`); }
    const side = n++ % 2 ? "r" : "l", icon = (KINDS[e.kind] || ["•"])[0];
    const shots = [...(e.chars || []), ...(e.fulls || [])];
    let photo = "";
    if (shots.length) {
      const i0 = pics.length, cap = `${e.clock} · ${e.story}`;
      pics.push(picGroup(e, cap));
      const yy = String(d.getFullYear()).slice(2), mm = String(d.getMonth() + 1).padStart(2, "0"), dd = String(d.getDate()).padStart(2, "0");
      photo = `<figure class="polaroid${e.chars?.length ? "" : " wide"}" data-i="${i0}" title="Click for the screenshot">
        <img loading="lazy" src="${src(shots[0])}" alt=""${e.chars?.length ? ' width="320" height="230"' : ""}>
        <span class="stamp">'${yy} ${mm} ${dd} ${esc(e.clock)}</span>
        <figcaption>${esc(e.map || "")}</figcaption></figure>`;
    }
    let cap;
    if (D.live && editing === e.id) {
      cap = `<div class="editor"><textarea maxlength="300" rows="2">${esc(e.story)}</textarea>
        <div class="tools"><button data-act="save">Save</button><button data-act="cancel">Cancel</button>
        ${e.custom && e.kind !== "note" ? `<button data-act="auto" title="Back to the automatic line">Automatic</button>` : ""}</div></div>`;
    } else {
      cap = `<div class="cap">${esc(e.story || e.text)}</div>` + (e.story_source || (!shots.length && e.map) ? `<div class="src">${e.story_source ? gicon(e.story_source) + "— " + esc(e.story_source) : ""}${e.story_source && !shots.length && e.map ? " · " : ""}${!shots.length && e.map ? "📍 " + esc(e.map) : ""}</div>` : "") + (D.live ? `<div class="tools">
        <button data-act="edit" title="Write your own caption">✏️ Edit</button>
        ${e.kind !== "note" ? `<button data-act="gen" title="Another random line from captions.json">🎲 Generate</button>` : ""}
        <button data-act="del" class="del" title="Delete this entry for good">🗑 Delete</button>
        ${e.custom ? `<span class="mine">edited</span>` : ""}</div>` : "");
    }
    out.push(`<div class="beat ${side} ${esc(e.kind)}" data-idx="${idx}" style="--k:var(--${esc(e.kind)},var(--accent))">
      <span class="dot">${icon}</span>${cap}${photo}</div>`);
  });
  out.push(`<div class="end"><span>…to be continued</span></div>`);
  $("#storyline").innerHTML = out.join("");
  for (const f of document.querySelectorAll("#storyline .polaroid")) {
    f.onclick = () => openLb(+f.dataset.i, pics);
    f.querySelector("img").onerror = () => f.remove();
  }
  const box = $("#storyline");
  box.onwheel = horizontal ? ev => { if (Math.abs(ev.deltaY) > Math.abs(ev.deltaX) && !ev.shiftKey) {
    box.scrollBy({left: ev.deltaY, behavior: "auto"}); ev.preventDefault(); } } : null;
  box.onscroll = horizontal ? () => { syncSoon(); arrows(); } : null;
  if (horizontal) setTimeout(arrows, 0);
  const refs = $("#refs");
  if (refs) {
    refs.ontoggle = () => { refsOpen = refs.open; };
    refs.querySelector("[data-refs=all]").onclick = () => { for (const c of refs.querySelectorAll("input")) c.checked = true; };
    refs.querySelector("[data-refs=save]").onclick = async () => {
      const off = [...refs.querySelectorAll("input")].filter(c => !c.checked).map(c => c.value);
      try { await api("/api/sources", {off}); location.reload(); }
      catch (err) { toast(/fetch/i.test(err.message) ? "Can't reach the diary server. Is `python -m components.diary.diary serve` still running?" : err.message); }
    };
  }
  const ta = document.querySelector("#storyline textarea");
  if (ta) { ta.focus(); ta.select();
    ta.onkeydown = ev => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); act(ta.closest(".beat"), "save"); }
                           else if (ev.key === "Escape") act(ta.closest(".beat"), "cancel"); }; }
  for (const b of document.querySelectorAll("#storyline .tools button"))
    b.onclick = () => act(b.closest(".beat"), b.dataset.act, b);
}
async function act(beat, what, btn) {
  const e = D.entries[+beat.dataset.idx];
  if (what === "edit") { editing = e.id; return renderStory(); }
  if (what === "del") return deleteEntry(e, btn);
  if (what === "cancel") { editing = null; return renderStory(); }
  for (const b of beat.querySelectorAll("button")) b.disabled = true;
  try {
    let r;
    if (what === "gen") r = await api("/api/generate", {id: e.id});
    else r = await api("/api/caption", {id: e.id, text: what === "auto" ? "" : beat.querySelector("textarea").value});
    e.story = r.story; e.story_source = r.source; e.custom = r.custom; editing = null; renderStory();
  } catch (err) {
    for (const b of beat.querySelectorAll("button")) b.disabled = false;
    toast(/fetch/i.test(err.message) ? "Can't reach the diary server. Is `python -m components.diary.diary serve` still running?" : err.message);
  }
}
function toast(msg) {
  let t = $("#toast"); if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("on"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("on"), 4000);
}
renderStory();
renderDayNav();
{ const m = /^#day-(\d+)$/.exec(location.hash); if (m) setTimeout(() => jumpDay(+m[1] - 1, false), 50); }

// levels table: one row per level reached
(function levels() {
  const L = D.levels.filter(l => l.level);
  if (!L.length) { $("#levtable").innerHTML = `<div class="empty">No level-ups recorded yet.</div>`; return; }
  const max = Math.max(1, ...L.map(l => l.active_s || 0));
  const rows = L.map(l => { const d = dt(l.time);
    const rate = l.active_s && l.exp_needed ? Math.round(l.exp_needed / (l.active_s / 3600)) : null;
    return `<tr><td><b>Lv ${l.level}</b></td><td>${esc(shortDay(d))} ${hm(d)}</td>
      <td class="num">${dur(l.active_s)}</td>
      <td style="width:20%">${l.active_s ? `<div class="tbar" style="width:${100 * l.active_s / max}%"></div>` : ""}</td>
      <td class="num">${num(l.exp_needed)}</td><td class="num">${num(rate)}</td><td>${esc(l.map || "")}</td></tr>`; });
  const n = D.now || {};
  if (n.level && n.pct != null) rows.push(`<tr><td><b>Lv ${n.level + 1}</b></td><td class="sub">next</td><td class="num sub">${esc(n.time_in_level_active || "")} so far</td>
    <td><div class="bar"><i style="width:${Math.min(100, n.pct)}%"></i></div></td><td class="num">${num(n.exp_needed)}</td><td></td><td>${esc(n.map || "")}</td></tr>`);
  $("#levtable").innerHTML = `<p class="sub">Took = play time (AFK excluded) spent on the level before. EXP = what that level needed.</p><table><thead><tr><th>Level</th><th>Reached</th><th class="num">Took</th><th></th>
    <th class="num">EXP</th><th class="num">EXP / hr</th><th>Map</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
})();

// places table
(function places() {
  const M = Object.entries(D.maps || {}).map(([name, m]) => ({name, ...m}))
    .sort((a, b) => (b.active_s || 0) - (a.active_s || 0));
  if (!M.length) { $("#maptable").innerHTML = `<div class="empty">No maps yet.</div>`; return; }
  const max = Math.max(1, ...M.map(m => m.active_s || 0));
  const first = {}; for (const e of D.entries) if (e.map && !first[e.map]) first[e.map] = e.time;
  const rows = M.map(m => { const r = D.regions?.[m.name];
    const rate = m.active_s > 60 ? Math.round(m.exp / (m.active_s / 3600)) : null;
    const lv = m.level_min === m.level_max ? m.level_min : `${m.level_min}–${m.level_max}`;
    return `<tr><td><b>${esc(m.name)}</b>${r ? `<div class="sub">${esc(r.street)}, ${esc(r.region)}</div>` : ""}</td>
      <td class="num">${dur(m.active_s)}</td><td style="width:18%"><div class="tbar" style="width:${100 * (m.active_s || 0) / max}%"></div></td>
      <td class="num">${num(m.exp)}</td><td class="num">${num(rate)}</td><td class="num">${esc(lv ?? "")}</td><td class="num">${num(m.visits)}</td></tr>`; });
  $("#maptable").innerHTML = `<table><thead><tr><th>Map</th><th class="num">Time</th><th></th><th class="num">EXP</th>
    <th class="num">EXP / hr</th><th class="num">Levels</th><th class="num">Visits</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
})();

$("#foot").textContent = `Rebuilt ${dt(D.generated).toLocaleString()} · refresh the page to see new entries · screenshots stay on this PC`;
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print(build())
