"""diary_html.py - local HTML viewer for the diary (diary.html next to diary.md).

Built from history/timeline.jsonl, history/levels.csv, history/map_stats.json, digest.json and
maps_classic.json. One self-contained file (no internet needed); pictures are linked by relative
path from screenshots/diary/, so open it straight from disk:

  python diary.py html          rebuild and open in the browser

diary.render() rebuilds it automatically, so it stays current while the tracker runs.
It shows Nexon game imagery: local only, never publish or upload it.
"""
import csv
import json
from datetime import datetime
from pathlib import Path

import diary

CODE = Path(__file__).parent


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _regions():
    """map name -> {region, street} from maps_classic.json."""
    out = {}
    for m in _read_json(CODE / "maps_classic.json", {}).get("maps", []):
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
    items = sorted(diary.entries(), key=lambda e: e["time"])
    for e in items:      # only pictures that exist; split close-ups from full frames
        shots = [s for s in e.get("shots", []) if (root / s).exists()]
        e["chars"] = [s for s in shots if s.endswith("_char.png")]
        e["fulls"] = [s for s in shots if not s.endswith("_char.png")]
    maps = _read_json(root / "history" / "map_stats.json", {})
    digest = _read_json(root / "digest.json", {})
    return {"name": diary.char_name(), "generated": datetime.now().isoformat(timespec="seconds"),
            "entries": items, "levels": _levels(root), "maps": maps,
            "regions": {k: v for k, v in _regions().items()
                        if k in maps or any(e.get("map") == k for e in items)}, "now": digest.get("now", {}), "totals": digest.get("totals", {})}


def build(root=None):
    root = Path(root or diary.ROOT)
    d = data(root)
    blob = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    html = (PAGE.replace("__TITLE__", _esc(f"{d['name']}'s diary"))
            .replace("__DATA__", blob))
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
  --levelup: #3f8f4a; --death: #b8433a; --job: #b8862b; --first_visit: #3a78b8; --note: #7a6a9a;
  --shadow: 0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #181614; --panel: #221f1c; --ink: #ece6de; --muted: #9c9389; --line: #36312c;
    --accent: #f0a050; --accent-soft: #3a2b1c;
    --levelup: #6cc077; --death: #e0756b; --job: #e2b558; --first_visit: #6fa8e0; --note: #b3a3d3;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.25);
  }
}
:root[data-theme="dark"] {
  --bg: #181614; --panel: #221f1c; --ink: #ece6de; --muted: #9c9389; --line: #36312c;
  --accent: #f0a050; --accent-soft: #3a2b1c;
  --levelup: #6cc077; --death: #e0756b; --job: #e2b558; --first_visit: #6fa8e0; --note: #b3a3d3;
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
.foot { margin-top: 30px; color: var(--muted); font-size: 12px; }
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
    <button class="theme" id="theme" title="Switch light / dark">Theme</button>
  </header>
  <div class="stats" id="stats"></div>
  <nav class="tabs" id="tabs">
    <button data-tab="timeline">Timeline</button>
    <button data-tab="levels">Levels</button>
    <button data-tab="places">Places</button>
  </nav>
  <section id="timeline">
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
  <div class="cap"></div>
  <button class="next" title="Next (→)">›</button>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const KINDS = {levelup: ["⬆️", "Level up"], death: ["💀", "Death"], job: ["⭐", "Job advancement"],
               first_visit: ["🗺️", "First visit"], note: ["📝", "Note"]};
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

// tabs
function showTab(t) {
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === t);
  for (const id of ["timeline", "levels", "places"]) $("#" + id).hidden = id !== t;
  store.set("tab", t);
}
for (const b of document.querySelectorAll("#tabs button")) b.onclick = () => showTab(b.dataset.tab);
showTab(store.get("tab", "timeline"));

// timeline
let filter = store.get("filter", "all"), newestFirst = store.get("order", "new") === "new";
let gallery = [];   // [{src, cap}] for the lightbox, in display order
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
    if (dn !== day) { day = dn; out.push(`<div class="day">${esc(dn)}</div>`); }
    const [icon, label] = KINDS[e.kind] || ["•", e.kind];
    const meta = [];
    if (e.level) meta.push(`Lv ${e.level}`);
    if (e.job) meta.push(esc(e.job));
    if (e.map) { const r = D.regions?.[e.map]; meta.push(esc(e.map) + (r ? ` · ${esc(r.street)}, ${esc(r.region)}` : "")); }
    const cap = `${hm(d)} · ${e.text.replace(/\*/g, "")}`;
    const pics = [];
    for (const s of e.chars || []) { pics.push(`<img class="char" loading="lazy" data-i="${gallery.length}" src="${src(s)}" alt="close-up">`); gallery.push({src: s, cap}); }
    (e.fulls || []).forEach((s, i) => { pics.push(`<img class="full" loading="lazy" data-i="${gallery.length}" src="${src(s)}" alt="screenshot">`);
      gallery.push({src: s, cap: cap + (i ? " (a few seconds later)" : "")}); });
    out.push(`<article class="entry" style="--k:var(--${esc(e.kind)},var(--accent))">
      <div class="time">${hm(d)}</div>
      <div><div class="kind">${icon} ${esc(label)}</div>
        <div class="text">${md(e.text)}</div>
        <div class="meta">${meta.map(m => `<span>${m}</span>`).join("")}</div>
        ${pics.length ? `<div class="pics">${pics.join("")}</div>` : ""}</div></article>`);
  }
  $("#list").innerHTML = out.join("");
  for (const img of document.querySelectorAll("#list img")) {
    img.onclick = () => openLb(+img.dataset.i);
    img.onerror = () => img.remove();
  }
}
renderFilters(); renderList();

// lightbox
let lbi = 0;
function openLb(i) { lbi = (i + gallery.length) % gallery.length; const g = gallery[lbi];
  $("#lb img").src = src(g.src); $("#lb .cap").textContent = g.cap; $("#lb").classList.add("on"); }
const closeLb = () => $("#lb").classList.remove("on");
$("#lb .x").onclick = closeLb;
$("#lb .prev").onclick = e => { e.stopPropagation(); openLb(lbi - 1); };
$("#lb .next").onclick = e => { e.stopPropagation(); openLb(lbi + 1); };
$("#lb").onclick = e => { if (e.target.id === "lb") closeLb(); };
document.addEventListener("keydown", e => { if (!$("#lb").classList.contains("on")) return;
  if (e.key === "Escape") closeLb(); else if (e.key === "ArrowLeft") openLb(lbi - 1); else if (e.key === "ArrowRight") openLb(lbi + 1); });

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
