"""album_html.py - the monster album page (album.html; also served by diary_server at /album.html).

A grid of every Classic mob, bosses first, then by region, by level:
  ?       not seen yet
  grey    seen (or a boss / a mob near your level): click for where it lives and what it drops
  caught  full colour: click for the photo of your first kill (and where / drops)

Pictures by relative path from the data folder: assets/mob_icons/<id>.png (mobicons.py, MeowDB),
screenshots/diary/... (kill photos), assets/item_icons/<key>.png (drop icons, when already
downloaded for the training panel). Local only: Nexon imagery never leaves this PC.
"""
import json
from datetime import datetime
from pathlib import Path

from components.album import album as A


def data(root=None, album=None):
    root = Path(root or A.ROOT)
    a = album or A.Album(root)
    level = a.char().get("level")
    mobs = []
    want = []
    for m in A.catalog():
        st = a.state(m, level)
        icon = root / "assets" / "mob_icons" / f"{m['id']}.png"
        if st != "unknown" and not icon.exists():
            want.append(m["id"])
        row = {"id": m["id"], "state": st, "level": m.get("level"), "boss": m.get("boss"),
               "region": m.get("region")}
        if st != "unknown":
            k = a.char()["killed"].get(m["id"])
            row.update({
                "name": m["name"], "exp": m.get("exp"), "hp": m.get("hp"),
                "icon": f"assets/mob_icons/{m['id']}.png" if icon.exists() else None,
                "maps": [{"name": x["name"], "region": x["region"], "count": x.get("count"),
                          "respawn": x.get("respawn")} for x in m.get("maps") or []],
                "drops": [{"name": d["name"], "rate": d.get("rate"),
                           "icon": (f"assets/item_icons/{d['icon']}.png"
                                    if d.get("icon") and (root / "assets" / "item_icons" / f"{d['icon']}.png").exists()
                                    else None)}
                          for d in (m.get("drops") or [])],
                "seen": (a.char()["seen"].get(m["id"]) or {}).get("map"),
            })
            if k:
                shots = [s for s in k.get("shots") or [] if (root / s).exists()]
                row["kill"] = {"time": k.get("time"), "map": k.get("map"), "level": k.get("level"),
                               "how": k.get("how"), "shots": shots}
        mobs.append(row)
    if want:
        try:
            from components.album import mobicons
            mobicons.want(want)          # downloads in the background; the next build shows them
        except Exception:
            pass
    return {"mobs": mobs, "who": a.char().get("name"), "level": level, "summary": a.summary(),
            "grey_levels": A.GREY_LEVELS, "generated": datetime.now().isoformat(timespec="seconds")}


def page(root=None, album=None, live=False):
    d = data(root, album)
    d["live"] = live
    blob = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    return HTML.replace("__DATA__", blob)


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monster Album</title>
<style>
:root {
  --bg: #f6f3ee; --panel: #fffdf9; --ink: #2a2622; --muted: #7a7168; --line: #e6dfd5;
  --accent: #d9822b; --accent-soft: #fbead6; --boss: #b8433a; --tile: #efe9e0; --q: #c9bfb2;
  --shadow: 0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.05);
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #181614; --panel: #221f1c; --ink: #ece6de; --muted: #9c9389; --line: #36312c;
  --accent: #f0a050; --accent-soft: #3a2b1c; --boss: #e0756b; --tile: #2b2723; --q: #5a524a;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.25); } }
:root[data-theme="dark"] {
  --bg: #181614; --panel: #221f1c; --ink: #ece6de; --muted: #9c9389; --line: #36312c;
  --accent: #f0a050; --accent-soft: #3a2b1c; --boss: #e0756b; --tile: #2b2723; --q: #5a524a;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.25); }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.45 "Segoe UI", system-ui, sans-serif; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 24px 16px 60px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; margin-bottom: 6px; }
h1 { margin: 0; font-size: 28px; letter-spacing: -.01em; }
h1 span { color: var(--accent); }
.count { font-variant-numeric: tabular-nums; color: var(--muted); }
.count b { color: var(--ink); font-size: 18px; }
.bar { height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; margin: 8px 0 4px; display: flex; }
.bar i { display: block; height: 100%; }
.bar .c { background: var(--accent); } .bar .s { background: var(--q); }
.hint { color: var(--muted); font-size: 13px; margin: 0 0 18px; }
.top { display: flex; gap: 10px; align-items: center; margin-left: auto; font-size: 13px; }
.top a { color: var(--muted); }
h2 { font-size: 15px; margin: 26px 0 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; display: flex; gap: 8px; align-items: baseline; }
h2 small { font-weight: 400; text-transform: none; letter-spacing: 0; font-variant-numeric: tabular-nums; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 10px; }
.tile { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 8px 8px 7px;
  text-align: center; cursor: pointer; box-shadow: var(--shadow); font: inherit; color: inherit; display: flex; flex-direction: column; align-items: center; }
.tile:hover { border-color: var(--accent); }
.tile:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.pic { width: 92px; height: 82px; display: grid; place-items: center; background: var(--tile); border-radius: 8px; overflow: hidden; }
.pic img { max-width: 88px; max-height: 78px; image-rendering: pixelated; object-fit: contain; }
.pic .q { font-size: 40px; font-weight: 800; color: var(--q); }
.pic .ph { font-size: 22px; font-weight: 700; color: var(--muted); }
.tile.seen .pic img, .tile.grey .pic img { filter: grayscale(1) brightness(.45) contrast(.9); opacity: .8; }
.tile.unknown { cursor: default; box-shadow: none; }
.tile.unknown:hover { border-color: var(--line); }
.name { font-size: 13px; font-weight: 600; margin-top: 6px; line-height: 1.2; min-height: 2.4em; display: flex; align-items: center; }
.tile.seen .name, .tile.grey .name { color: var(--muted); }
.lv { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tile.boss { border-color: color-mix(in srgb, var(--boss) 45%, var(--line)); }
.crown { position: absolute; top: 5px; right: 7px; font-size: 13px; }
.caughtmark { position: absolute; top: 5px; left: 7px; font-size: 12px; }
dialog { border: 1px solid var(--line); border-radius: 14px; padding: 0; background: var(--panel); color: var(--ink);
  width: min(760px, calc(100vw - 24px)); max-height: calc(100vh - 40px); box-shadow: 0 10px 40px rgba(0,0,0,.35); }
dialog::backdrop { background: rgba(0,0,0,.55); }
.dhead { display: flex; gap: 14px; align-items: center; padding: 16px 18px; border-bottom: 1px solid var(--line); }
.dhead .pic { width: 84px; height: 76px; flex: none; }
.dhead h3 { margin: 0; font-size: 20px; }
.dhead .sub { color: var(--muted); font-size: 13px; }
.close { margin-left: auto; align-self: flex-start; border: 1px solid var(--line); background: var(--panel); color: var(--ink);
  border-radius: 8px; font: inherit; padding: 4px 10px; cursor: pointer; }
.dbody { padding: 14px 18px 18px; overflow: auto; }
.photo { margin: 0 0 14px; }
.photo img { width: 100%; max-height: 62vh; object-fit: contain; border-radius: 10px; display: block; background: #000; }
.thumbs { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.thumbs button { border: 2px solid transparent; padding: 0; border-radius: 6px; overflow: hidden; cursor: pointer; background: none; }
.thumbs button.on { border-color: var(--accent); }
.thumbs img { height: 54px; display: block; }
.facts { color: var(--muted); font-size: 13px; margin-bottom: 12px; }
h4 { margin: 14px 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }
ul.maps { margin: 0; padding-left: 18px; }
ul.maps li { margin: 2px 0; }
table.drops { width: 100%; border-collapse: collapse; font-size: 13px; }
table.drops td { padding: 3px 4px; border-top: 1px solid var(--line); vertical-align: middle; }
table.drops td.r { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
table.drops img { width: 24px; height: 24px; object-fit: contain; vertical-align: middle; margin-right: 6px; }
.note { font-size: 12px; color: var(--muted); margin-top: 6px; }
.empty { color: var(--muted); }
footer { margin-top: 30px; color: var(--muted); font-size: 12px; }
@media (max-width: 520px) { .grid { grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); } .pic { width: 78px; height: 70px; } .pic img { max-width: 74px; max-height: 66px; } }
</style></head><body>
<div class="wrap">
  <header><h1>Monster <span>Album</span></h1><span class="count" id="count"></span>
    <span class="top" id="top"></span></header>
  <div class="bar" id="bar"></div>
  <p class="hint" id="hint"></p>
  <div id="sections"></div>
  <footer id="foot"></footer>
</div>
<dialog id="dlg" aria-labelledby="dtitle"></dialog>
<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const src = p => p.split("/").map(encodeURIComponent).join("/");
const dt = s => s ? new Date(s).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : "";
const REGION_ORDER = ["Maple Island", "Victoria Island", "Ossyria", "Hidden Street", "Event", "Unknown"];

const S = D.summary;
$("#count").innerHTML = `<b>${S.caught}</b> / ${S.total} caught · ${S.seen} seen`;
$("#bar").innerHTML = `<i class="c" style="width:${100 * S.caught / S.total}%"></i><i class="s" style="width:${100 * S.seen / S.total}%"></i>`;
$("#hint").textContent = (D.who ? D.who + ". " : "") + `Grey: seen on a map, a boss, or at most ${D.grey_levels} levels above you` +
  (D.level ? ` (Lv ${D.level})` : "") + ". Click a monster for where it lives, what it drops, and your first-kill photo.";
$("#top").innerHTML = D.live ? `<a href="/">Diary</a>` : `<a href="diary.html">Diary</a>`;
$("#foot").textContent = `Rebuilt ${dt(D.generated)} · refresh to see new catches · pictures stay on this PC · monster facts from NiaMeowDB`;

function pic(m, big) {
  if (m.state === "unknown") return `<div class="pic"><span class="q">?</span></div>`;
  if (m.icon) return `<div class="pic"><img src="${src(m.icon)}" alt="" loading="lazy"></div>`;
  const ini = (m.name || "?").split(/\s+/).map(w => w[0]).join("").slice(0, 3);
  return `<div class="pic"><span class="ph">${esc(ini)}</span></div>`;
}
function tile(m) {
  const label = m.state === "unknown" ? "Unknown monster" : `${m.name}, level ${m.level}${m.state === "caught" ? ", caught" : ", not caught"}`;
  return `<button type="button" class="tile ${m.state}${m.boss ? " boss" : ""}" data-id="${esc(m.id)}" aria-label="${esc(label)}"${m.state === "unknown" ? " disabled" : ""}>
    ${m.boss && m.state !== "unknown" ? `<span class="crown" title="Boss">👑</span>` : ""}${m.state === "caught" ? `<span class="caughtmark" title="Caught">⚔️</span>` : ""}
    ${pic(m)}<div class="name">${m.state === "unknown" ? "???" : esc(m.name)}</div>
    <div class="lv">${m.state === "unknown" ? "Lv ?" : "Lv " + m.level}</div></button>`;
}
function section(title, list) {
  if (!list.length) return "";
  const c = list.filter(m => m.state === "caught").length;
  return `<h2>${esc(title)} <small>${c} / ${list.length}</small></h2><div class="grid">${list.map(tile).join("")}</div>`;
}
const bosses = D.mobs.filter(m => m.boss);
const rest = D.mobs.filter(m => !m.boss);
const regions = [...new Set(rest.map(m => m.region))].sort((a, b) => {
  const ia = REGION_ORDER.indexOf(a), ib = REGION_ORDER.indexOf(b);
  return (ia < 0 ? 50 : ia) - (ib < 0 ? 50 : ib) || String(a).localeCompare(b); });
$("#sections").innerHTML = section("Bosses", bosses) + regions.map(r => section(r, rest.filter(m => m.region === r))).join("");

const dlg = $("#dlg");
function open(id) {
  const m = D.mobs.find(x => x.id === id); if (!m || m.state === "unknown") return;
  const k = m.kill;
  let body = "";
  if (k) {
    const shots = k.shots || [];
    body += shots.length ? `<figure class="photo"><img id="big" src="${src(shots[0])}" alt="First kill of ${esc(m.name)}">
      ${shots.length > 1 ? `<div class="thumbs">${shots.map((s, i) => `<button type="button" data-s="${esc(s)}" class="${i ? "" : "on"}"><img src="${src(s)}" alt="Picture ${i + 1}"></button>`).join("")}</div>` : ""}</figure>`
      : `<p class="empty">${k.how === "manual" ? "Marked as caught by hand, so there's no photo." : "No photo of this kill."}</p>`;
    body += `<div class="facts">First ${m.boss ? "boss kill" : "kill"}: ${esc(dt(k.time))}${k.map ? " · " + esc(k.map) : ""}${k.level ? " · you were Lv " + k.level : ""}</div>`;
  } else {
    body += `<div class="facts">Not caught yet${m.seen ? ` · seen at ${esc(m.seen)}` : (m.boss ? " · a boss" : " · near your level")}.</div>`;
  }
  const maps = (m.maps || []).map(x => `<li>${esc(x.name)} <span class="lv">· ${esc(x.region)}${x.count ? ` · ${x.count} spawn${x.count > 1 ? "s" : ""}` : ""}${x.respawn >= 600 ? ` · every ${x.respawn >= 3600 ? Math.round(x.respawn / 360) / 10 + " h" : Math.round(x.respawn / 60) + " min"}` : ""}</span></li>`).join("");
  body += `<h4>Where it lives</h4>${maps ? `<ul class="maps">${maps}</ul>` : `<p class="empty">No spawn list.</p>`}`;
  const drops = (m.drops || []).map(d => `<tr><td>${d.icon ? `<img src="${src(d.icon)}" alt="">` : ""}${esc(d.name)}</td><td class="r">${esc(d.rate || "")}</td></tr>`).join("");
  body += `<h4>Drops</h4>${drops ? `<table class="drops">${drops}</table><p class="note">Rates are OSMLib reference numbers shown by NiaMeowDB; Classic's own rates may differ.</p>` : `<p class="empty">No drop list.</p>`}`;
  dlg.innerHTML = `<div class="dhead">${pic(m)}<div><h3 id="dtitle">${m.boss ? "👑 " : ""}${esc(m.name)}</h3>
    <div class="sub">Lv ${m.level}${m.hp ? ` · ${m.hp.toLocaleString()} HP` : ""}${m.exp ? ` · ${m.exp.toLocaleString()} EXP` : ""}</div></div>
    <button type="button" class="close" data-close>Close</button></div><div class="dbody">${body}</div>`;
  dlg.querySelector(".dhead .pic").classList.add(m.state);
  if (m.state !== "caught") dlg.querySelector(".dhead .pic img")?.style.setProperty("filter", "grayscale(1) brightness(.45) contrast(.9)");
  dlg.showModal();
}
document.addEventListener("click", e => {
  const t = e.target.closest(".tile"); if (t) return open(t.dataset.id);
  if (e.target.closest("[data-close]") || e.target === dlg) return dlg.close();
  const b = e.target.closest(".thumbs button");
  if (b) { $("#big").src = src(b.dataset.s); dlg.querySelectorAll(".thumbs button").forEach(x => x.classList.toggle("on", x === b)); }
});
</script></body></html>
"""


def build(root=None, album=None):
    root = Path(root or A.ROOT)
    (root / "album.html").write_text(page(root, album), encoding="utf-8")
    return root / "album.html"
