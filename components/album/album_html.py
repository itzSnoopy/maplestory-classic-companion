"""album_html.py - the monster album page (album.html; also served by diary_server at /album.html).

An open book (two pages of 3x3 slots, themeable: Henesys parchment, Ellinia tome, plain) of the
Classic mobs that are out, bosses first, then by region, by level. Click a slot: the entry opens in
the same book (hash #mob=<id>). Slots:
  ?       not seen yet
  grey    seen on its map (or a boss at most BOSS_GREY_LEVELS above you): click for where it lives and what it drops
  caught  full colour: click for the photo of your first kill (and where / drops)

Pictures by relative path from the data folder: assets/mob_icons/<id>.png (mobicons.py, MeowDB),
screenshots/diary/... (kill photos), assets/item_icons/<key>.png (drop icons, when already
downloaded for the training panel). Local only: Nexon imagery never leaves this PC.
"""
import json
from datetime import datetime
from pathlib import Path

from components.album import album as A

# Only these regions are out in Classic right now (Hidden Street = King Slime, Kerning PQ): the rest
# stays in the data but is not shown. Add a region here when it opens.
SHOWN_REGIONS = ("Victoria Island", "Hidden Street")
# True = one chapter per region, once other continents are in the game. False = one chapter "Monsters" by level.
SPLIT_REGIONS = False


def data(root=None, album=None):
    root = Path(root or A.ROOT)
    a = album or A.Album(root)
    level = a.char().get("level")
    mobs = []
    want = []
    for m in A.catalog():
        st = a.state(m, level)
        icon = root / "assets" / "mob_icons" / f"{m['id']}.png"
        shown = m.get("region") in SHOWN_REGIONS
        if shown and st != "unknown" and not icon.exists():
            want.append(m["id"])
        row = {"id": m["id"], "state": st, "level": m.get("level"), "boss": m.get("boss"),
               "region": m.get("region"), "shown": shown}
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
            "boss_grey_levels": A.BOSS_GREY_LEVELS, "shown_regions": list(SHOWN_REGIONS), "split_regions": SPLIT_REGIONS, "generated": datetime.now().isoformat(timespec="seconds")}


def page(root=None, album=None, live=False):
    d = data(root, album)
    d["live"] = live
    blob = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    return HTML.replace("__DATA__", blob)


HTML = r"""<!doctype html>
<html lang="en" data-book="henesys"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monster Album</title>
<link rel="icon" type="image/png" href="/favicon.png">
<style>
/* ==========================================================================================
   1. HOW THEMES WORK
   ------------------------------------------------------------------------------------------
   The page is an open book. Section 4 (layout) only reads CSS variables, it never names a
   colour. A theme is ONE block  [data-book="<name>"] { --var: ...; }  in section 3; the
   picker sets data-book on <html> (remembered in localStorage). To add a town book: copy a
   block, rename it, change the values, add one <option> to THEMES in the script. Nothing else.
   Optional per-theme extras (a pseudo-element, a texture) go in a marked sub-block right
   after that theme's variables. No web fonts; the ornaments are original inline SVG data-URIs
   held in variables (below), everything else is gradients and Unicode.

   Variables a theme must set:
     desk:    --desk-bg  --desk-ink  --desk-muted  --desk-line
     type:    --font-body  --font-head
     cover:   --cover  --cover-edge  --shadow-book
     pages:   --page-bg-l  --page-bg-r  (full `background` values, gradients allowed)
              --page-edge  --spine
     ink:     --ink  --ink-soft  --line
     accents: --accent  --accent-soft  --boss  --seal  --seal-ink  --glow  --hover-glow
     slots:   --slot-bg  --slot-border  --slot-empty  --pic-bg  --q
     arrows:  --arrow  --arrow-hover  --arrow-glow
     ribbons: --ribbon  --ribbon-active  --ribbon-ink
     bar:     --bar-track  --bar-caught  --bar-seen
     chrome:  --field-bg  (picker / small buttons)
     art:     --art-nav-next  --art-nav-prev  (+ --art-nav-prev-flip: -1 mirrors next, 1 = own file)
              --art-corner (quill / pen, bottom-left of the left page)  --art-bullet (list marker)
              --paper-texture (fibres / mottling)  --paper-stains (stains, specks; spans both pages)
              --floaters (4x2 sprite sheet of drifting runes)   `none` switches an art slot off.
     art tuning (defaults in section 2): --nav-art-display --arrow-glyph-display --nav-w --nav-h
              --nav-glow --corner-display --corner-w --corner-opacity --corner-rot --list-style
              --bullet-display --bullet-w --bullet-h --bullet-top --floaters-display --floaters-opacity
   art slots: the player's artist can replace any of these with a file, e.g. url('art/album/henesys/nav_next.png')
   ========================================================================================== */

/* ==========================================================================================
   2. BASE TOKENS (no theme changes these)
   ========================================================================================== */
:root {
  --book-max: 1020px;      /* widest the open book gets */
  --cover-pad: 14px;       /* leather visible around the pages */
  --book-radius: 14px;
  --page-radius: 8px;
  --page-h: clamp(440px, calc(100vh - 200px), 640px); /* page height on wide screens: fits the window */
  --page-pad: 16px;
  --slot-radius: 12px;
  --slot-gap: 8px;
  --pic-radius: 9px;
  --turn-ms: 240ms;        /* page turn */
  --hover-ms: 140ms;
  --ribbon-h: 30px;
  /* art defaults = plain (no ornaments); themes override */
  --nav-k: 1;  --corner-k: 1;  --art-nav-prev-flip: -1;
  --nav-art-display: none;  --arrow-glyph-display: inline-flex;  --nav-w: 110px;  --nav-h: 28px;  --nav-glow: none;
  --corner-display: none;  --corner-w: 150px;  --corner-opacity: 1;  --corner-rot: 0deg;
  --list-style: disc;  --bullet-display: none;  --bullet-w: 14px;  --bullet-h: 8px;  --bullet-top: .6em;
  --floaters-display: none;  --floaters-opacity: .07;
}

/* ==========================================================================================
   3. THEMES
   ========================================================================================== */

/* ---- henesys: old parchment book in brown leather (default) ---- */
[data-book="henesys"] {
  --desk-bg: radial-gradient(ellipse at 50% 0%, #6a4a2c 0%, #3e2a18 60%, #2a1b0f 100%);
  --desk-ink: #f1e3c8;  --desk-muted: #c4ad88;  --desk-line: rgba(241,227,200,.22);
  --font-body: "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif;
  --font-head: "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif;
  --cover: linear-gradient(135deg, #7a4a28 0%, #5c3519 55%, #4a2a13 100%);
  --cover-edge: #2e1a0a;
  --shadow-book: 0 18px 40px rgba(0,0,0,.55), inset 0 0 0 2px rgba(255,214,150,.12), inset 0 0 24px rgba(0,0,0,.4);
  --page-bg-l: linear-gradient(to left, rgba(92,55,20,.30), rgba(92,55,20,0) 14%),
               radial-gradient(ellipse at 45% 45%, #f5e8c6 0%, #ebd6a6 62%, #d6bb86 100%);
  --page-bg-r: linear-gradient(to right, rgba(92,55,20,.30), rgba(92,55,20,0) 14%),
               radial-gradient(ellipse at 55% 45%, #f5e8c6 0%, #ebd6a6 62%, #d6bb86 100%);
  --page-edge: rgba(110,70,30,.35);
  --spine: linear-gradient(to right, rgba(40,22,8,0), rgba(40,22,8,.55) 48%, rgba(40,22,8,.55) 52%, rgba(40,22,8,0));
  --ink: #3a2815;  --ink-soft: #7d6446;  --line: rgba(110,70,30,.28);
  --accent: #9b5a1f;  --accent-soft: rgba(155,90,31,.14);  --boss: #9c2f26;
  --seal: radial-gradient(circle at 35% 30%, #d9594b, #9c2f26 70%);  --seal-ink: #ffe9d0;
  --glow: 0 0 0 1px rgba(155,90,31,.35), 0 2px 8px rgba(120,70,20,.25);
  --hover-glow: 0 0 0 2px rgba(155,90,31,.55), 0 6px 14px rgba(90,50,15,.3);
  --slot-bg: rgba(255,249,230,.88);  --slot-border: rgba(110,70,30,.40);
  --slot-empty: rgba(110,70,30,.16);
  --pic-bg: rgba(120,80,35,.12);  --q: rgba(110,70,30,.35);
  --arrow: #e6c58a;  --arrow-hover: #fff0cf;  --arrow-glow: 0 1px 0 rgba(0,0,0,.5);
  --ribbon: #8f3a2f;  --ribbon-active: #b4493b;  --ribbon-ink: #fbe9d0;
  --bar-track: rgba(241,227,200,.18);  --bar-caught: #d9a45a;  --bar-seen: rgba(241,227,200,.45);
  --field-bg: rgba(241,227,200,.12);
  /* art slots (inline SVG; the artist can replace any with a file, e.g. url('art/album/henesys/nav_next.png')) */
  --art-nav-next: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='110' height='28' viewBox='0 0 110 28'%3E%3Cdefs%3E%3ClinearGradient id='g' gradientUnits='userSpaceOnUse' x1='0' y1='0' x2='0' y2='28'%3E%3Cstop offset='0' stop-color='%23fff4c2'/%3E%3Cstop offset='.5' stop-color='%23e2b44a'/%3E%3Cstop offset='1' stop-color='%2394640f'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cg fill='url(%23g)' stroke='%234a2f08' stroke-width='.9' stroke-linejoin='round' stroke-linecap='round'%3E%3Cpath d='M8 14 L3 5 L17 5 L24 13.2 Z'/%3E%3Cpath d='M8 14 L3 23 L17 23 L24 14.8 Z'/%3E%3Cpath d='M2 14 L10 11.6 L24 12.4 L24 15.6 L10 16.4 Z'/%3E%3Crect x='8' y='12.6' width='78' height='2.8' rx='1.4'/%3E%3Crect x='30' y='11.8' width='2.6' height='4.4' rx='1'/%3E%3Crect x='35' y='11.8' width='2.6' height='4.4' rx='1'/%3E%3Crect x='82' y='11.6' width='6' height='4.8' rx='1.2'/%3E%3Cpath d='M90 14 L84.5 5.5 L97 11.4 L109 14 L97 16.6 L84.5 22.5 Z'/%3E%3C/g%3E%3Cg stroke='%234a2f08' stroke-width='.5' fill='none' stroke-linecap='round'%3E%3Cpath d='M12 12.5 L9 5.8 M16 12.5 L13 5.8 M20 12.5 L17 5.8'/%3E%3Cpath d='M12 15.5 L9 22.2 M16 15.5 L13 22.2 M20 15.5 L17 22.2'/%3E%3Cpath d='M92 14 H106'/%3E%3C/g%3E%3C/svg%3E");
  --art-nav-prev: var(--art-nav-next);  --art-nav-prev-flip: -1;   /* prev = next mirrored; set the flip to 1 for a real prev file */
  --art-corner: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='q' x1='0' y1='0' x2='1' y2='0'%3E%3Cstop offset='0' stop-color='%23ffffff'/%3E%3Cstop offset='1' stop-color='%23e6dfd2'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cg transform='rotate(45 50 50)' stroke-linejoin='round' stroke-linecap='round'%3E%3Cpath d='M50 4 C63 20 63 48 51 74 C53 50 53 28 50 4 Z' fill='url(%23q)' stroke='%23a39580' stroke-width='.9'/%3E%3Cpath d='M50 4 C39 22 38 48 49 72 C47 50 47 28 50 4 Z' fill='%23fbf8f2' stroke='%23a39580' stroke-width='.9'/%3E%3Cg stroke='%23b8ab96' stroke-width='.55' fill='none'%3E%3Cpath d='M52 62 L59 55 M52 52 L61 44 M52 42 L60 33 M52 32 L58 23'/%3E%3Cpath d='M48 62 L42 55 M48 52 L40 44 M48 42 L41 33 M48 32 L43 23'/%3E%3C/g%3E%3Cpath d='M50 4 L50 86' stroke='%239c8a70' stroke-width='1.3' fill='none'/%3E%3Cpath d='M48.8 80 L51.2 80 L50 95 Z' fill='%233a2a18' stroke='%233a2a18' stroke-width='.5'/%3E%3C/g%3E%3C/svg%3E");   /* quill in the bottom-left corner of the left page */
  --art-bullet: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='28'%20height='16'%20viewBox='0%200%2028%2016'%3E%3Cg%20fill='%23d4a032'%20stroke='%235a3a0a'%20stroke-width='.8'%20stroke-linejoin='round'%3E%3Cpath%20d='M5%208%20L2%203%20L8%203%20L10%208%20Z'/%3E%3Cpath%20d='M5%208%20L2%2013%20L8%2013%20L10%208%20Z'/%3E%3Crect%20x='4'%20y='7.2'%20width='16'%20height='1.6'%20rx='.8'/%3E%3Cpath%20d='M18%208%20C21%203.5%2025%204.5%2027.5%208%20C25%2011.5%2021%2012.5%2018%208%20Z'/%3E%3C/g%3E%3C/svg%3E");
  --paper-texture: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='240'%20height='240'%3E%3Cfilter%20id='h'%20x='0'%20y='0'%20width='100%25'%20height='100%25'%3E%3CfeTurbulence%20type='fractalNoise'%20baseFrequency='.012%20.85'%20numOctaves='2'%20seed='3'%20stitchTiles='stitch'/%3E%3CfeColorMatrix%20values='0%200%200%200%20.45%20%200%200%200%200%20.30%20%200%200%200%200%20.12%20%201.1%200%200%200%20-.38'/%3E%3C/filter%3E%3Cfilter%20id='v'%20x='0'%20y='0'%20width='100%25'%20height='100%25'%3E%3CfeTurbulence%20type='fractalNoise'%20baseFrequency='.85%20.015'%20numOctaves='2'%20seed='8'%20stitchTiles='stitch'/%3E%3CfeColorMatrix%20values='0%200%200%200%20.45%20%200%200%200%200%20.30%20%200%200%200%200%20.12%20%201.1%200%200%200%20-.40'/%3E%3C/filter%3E%3Crect%20width='240'%20height='240'%20filter='url(%23h)'%20opacity='.30'/%3E%3Crect%20width='240'%20height='240'%20filter='url(%23v)'%20opacity='.22'/%3E%3C/svg%3E");   /* papyrus fibres */
  --paper-stains: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='800'%20height='500'%20viewBox='0%200%20800%20500'%20preserveAspectRatio='none'%3E%3Cdefs%3E%3CradialGradient%20id='b'%3E%3Cstop%20offset='0'%20stop-color='%23a06a24'%20stop-opacity='.05'/%3E%3Cstop%20offset='.78'%20stop-color='%23a06a24'%20stop-opacity='.09'/%3E%3Cstop%20offset='.92'%20stop-color='%238b5a22'%20stop-opacity='.20'/%3E%3Cstop%20offset='1'%20stop-color='%238b5a22'%20stop-opacity='0'/%3E%3C/radialGradient%3E%3CradialGradient%20id='s'%3E%3Cstop%20offset='0'%20stop-color='%23a06a24'%20stop-opacity='.16'/%3E%3Cstop%20offset='1'%20stop-color='%23a06a24'%20stop-opacity='0'/%3E%3C/radialGradient%3E%3Cfilter%20id='f'%3E%3CfeGaussianBlur%20stdDeviation='1.3'/%3E%3C/filter%3E%3C/defs%3E%3Cg%20filter='url(%23f)'%3E%3Ccircle%20cx='130'%20cy='400'%20r='62'%20fill='url(%23b)'/%3E%3Ccircle%20cx='300'%20cy='95'%20r='38'%20fill='none'%20stroke='%238b5a22'%20stroke-opacity='.14'%20stroke-width='4'/%3E%3Ccircle%20cx='600'%20cy='360'%20r='78'%20fill='url(%23b)'/%3E%3Ccircle%20cx='640'%20cy='150'%20r='44'%20fill='none'%20stroke='%238b5a22'%20stroke-opacity='.13'%20stroke-width='5'/%3E%3Ccircle%20cx='652'%20cy='156'%20r='30'%20fill='none'%20stroke='%238b5a22'%20stroke-opacity='.08'%20stroke-width='3'/%3E%3C/g%3E%3Ccircle%20cx='430'%20cy='260'%20r='26'%20fill='url(%23s)'/%3E%3Ccircle%20cx='60'%20cy='110'%20r='20'%20fill='url(%23s)'/%3E%3Ccircle%20cx='740'%20cy='440'%20r='24'%20fill='url(%23s)'/%3E%3Ccircle%20cx='215'%20cy='250'%20r='14'%20fill='url(%23s)'/%3E%3Ccircle%20cx='14'%20cy='30'%20r='1.4'%20fill='%235a3a12'%20opacity='0.4'/%3E%3Ccircle%20cx='22'%20cy='44'%20r='0.9'%20fill='%235a3a12'%20opacity='0.35'/%3E%3Ccircle%20cx='9'%20cy='300'%20r='1.2'%20fill='%235a3a12'%20opacity='0.35'/%3E%3Ccircle%20cx='30'%20cy='470'%20r='1.5'%20fill='%235a3a12'%20opacity='0.4'/%3E%3Ccircle%20cx='180'%20cy='12'%20r='1'%20fill='%235a3a12'%20opacity='0.3'/%3E%3Ccircle%20cx='250'%20cy='486'%20r='1.2'%20fill='%235a3a12'%20opacity='0.35'/%3E%3Ccircle%20cx='380'%20cy='492'%20r='1.5'%20fill='%235a3a12'%20opacity='0.4'/%3E%3Ccircle%20cx='392'%20cy='10'%20r='1.1'%20fill='%235a3a12'%20opacity='0.3'/%3E%3Ccircle%20cx='410'%20cy='18'%20r='1.3'%20fill='%235a3a12'%20opacity='0.35'/%3E%3Ccircle%20cx='430'%20cy='490'%20r='1'%20fill='%235a3a12'%20opacity='0.35'/%3E%3Ccircle%20cx='560'%20cy='8'%20r='1.2'%20fill='%235a3a12'%20opacity='0.3'/%3E%3Ccircle%20cx='790'%20cy='30'%20r='1.5'%20fill='%235a3a12'%20opacity='0.4'/%3E%3Ccircle%20cx='782'%20cy='46'%20r='0.9'%20fill='%235a3a12'%20opacity='0.35'/%3E%3Ccircle%20cx='776'%20cy='300'%20r='1.2'%20fill='%235a3a12'%20opacity='0.35'/%3E%3Ccircle%20cx='795'%20cy='470'%20r='1.4'%20fill='%235a3a12'%20opacity='0.4'/%3E%3Ccircle%20cx='640'%20cy='492'%20r='1'%20fill='%235a3a12'%20opacity='0.3'/%3E%3Ccircle%20cx='20'%20cy='200'%20r='0.9'%20fill='%235a3a12'%20opacity='0.3'/%3E%3Ccircle%20cx='398'%20cy='250'%20r='1'%20fill='%235a3a12'%20opacity='0.3'/%3E%3C/svg%3E");   /* tea stains and crumbs; one image across both pages */
  --floaters: none;
  /* art tuning */
  --nav-art-display: block;  --arrow-glyph-display: none;  --nav-w: 110px;  --nav-h: 28px;
  --nav-glow: drop-shadow(0 0 5px rgba(255,214,110,.85));
  --corner-display: block;  --corner-w: 150px;  --corner-opacity: 1;  --corner-rot: 202deg;
  --list-style: none;  --bullet-display: block;  --bullet-w: 14px;  --bullet-h: 8px;  --bullet-top: .62em;
}

/* ---- ellinia: a magic tome, forest-green / indigo leather, moonlit pages, gold leaf ---- */
[data-book="ellinia"] {
  --desk-bg: radial-gradient(ellipse at 50% 0%, #1d2a4a 0%, #0f1630 55%, #080b1b 100%);
  --desk-ink: #e4e6f7;  --desk-muted: #98a0c8;  --desk-line: rgba(160,170,230,.25);
  --font-body: "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif;
  --font-head: "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif;
  --cover: linear-gradient(135deg, #1f4a3f 0%, #1c3558 50%, #231f4f 100%);
  --cover-edge: #d1ad55;
  --shadow-book: 0 18px 44px rgba(0,0,0,.65), 0 0 40px rgba(110,90,220,.25), inset 0 0 0 2px rgba(214,178,92,.55), inset 0 0 26px rgba(0,0,0,.5);
  --page-bg-l: linear-gradient(to left, rgba(60,50,120,.26), rgba(60,50,120,0) 14%),
               radial-gradient(ellipse at 45% 40%, #e9f0f7 0%, #d8e4ee 60%, #c0d0e2 100%);
  --page-bg-r: linear-gradient(to right, rgba(60,50,120,.26), rgba(60,50,120,0) 14%),
               radial-gradient(ellipse at 55% 40%, #e6eff7 0%, #d5e2ee 60%, #bccee2 100%);
  --page-edge: rgba(150,125,60,.5);
  --spine: linear-gradient(to right, rgba(10,8,40,0), rgba(10,8,40,.6) 48%, rgba(10,8,40,.6) 52%, rgba(10,8,40,0));
  --ink: #26234a;  --ink-soft: #6c6a96;  --line: rgba(80,70,150,.25);
  --accent: #a8801f;  --accent-soft: rgba(168,128,31,.15);  --boss: #7a2f6e;
  --seal: radial-gradient(circle at 35% 30%, #f3d98a, #b88a22 70%);  --seal-ink: #3b2a05;
  --glow: 0 0 0 1px rgba(168,128,31,.5), 0 0 12px rgba(120,100,240,.45), 0 0 4px rgba(80,200,190,.4);
  --hover-glow: 0 0 0 2px rgba(168,128,31,.8), 0 0 18px rgba(130,110,255,.6);
  --slot-bg: rgba(250,253,255,.86);  --slot-border: rgba(110,95,180,.38);
  --slot-empty: rgba(80,70,150,.14);
  --pic-bg: rgba(90,80,170,.10);  --q: rgba(90,80,170,.35);
  --arrow: #e8c870;  --arrow-hover: #fff3c4;  --arrow-glow: 0 0 10px rgba(150,130,255,.9), 0 0 3px rgba(240,200,100,.8);
  --ribbon: #2d5d68;  --ribbon-active: #3f8791;  --ribbon-ink: #f3e3b0;
  --bar-track: rgba(160,170,230,.2);  --bar-caught: #e4c26a;  --bar-seen: rgba(130,200,200,.55);
  --field-bg: rgba(160,170,230,.14);
  /* art slots (inline SVG; the artist can replace any with a file, e.g. url('art/album/ellinia/nav_next.png')) */
  --art-nav-next: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='110'%20height='30'%20viewBox='0%200%20110%2030'%3E%3Cdefs%3E%3ClinearGradient%20id='w'%20x1='0'%20y1='0'%20x2='0'%20y2='1'%3E%3Cstop%20offset='0'%20stop-color='%23d79a58'/%3E%3Cstop%20offset='1'%20stop-color='%23744420'/%3E%3C/linearGradient%3E%3CradialGradient%20id='m'%20cx='.35'%20cy='.3'%20r='.8'%3E%3Cstop%20offset='0'%20stop-color='%23eafcff'/%3E%3Cstop%20offset='.5'%20stop-color='%2362c0e8'/%3E%3Cstop%20offset='1'%20stop-color='%23283f9a'/%3E%3C/radialGradient%3E%3C/defs%3E%3Cg%20stroke-linejoin='round'%20stroke-linecap='round'%3E%3Crect%20x='5'%20y='11.2'%20width='80'%20height='7.6'%20rx='3.8'%20fill='url(%23w)'%20stroke='%23442410'%20stroke-width='1.3'/%3E%3Crect%20x='28'%20y='10.4'%20width='4.4'%20height='9.2'%20rx='1.6'%20fill='%23f0c75a'%20stroke='%23775012'%20stroke-width='.9'/%3E%3Crect%20x='54'%20y='10.4'%20width='4.4'%20height='9.2'%20rx='1.6'%20fill='%23f0c75a'%20stroke='%23775012'%20stroke-width='.9'/%3E%3Ccircle%20cx='6'%20cy='15'%20r='4.3'%20fill='%23f0c75a'%20stroke='%23775012'%20stroke-width='1.1'/%3E%3Cpath%20d='M80%2012%20Q84%203.5%2094%204%20M80%2018%20Q84%2026.5%2094%2026'%20fill='none'%20stroke='%23775012'%20stroke-width='5'/%3E%3Cpath%20d='M80%2012%20Q84%203.5%2094%204%20M80%2018%20Q84%2026.5%2094%2026'%20fill='none'%20stroke='%23f0c75a'%20stroke-width='2.8'/%3E%3Ccircle%20cx='96'%20cy='15'%20r='10'%20fill='url(%23m)'%20stroke='%231c2d6e'%20stroke-width='1.4'/%3E%3Cellipse%20cx='92.5'%20cy='11.5'%20rx='3.2'%20ry='2'%20fill='%23ffffff'%20opacity='.85'%20transform='rotate(-35%2092.5%2011.5)'/%3E%3C/g%3E%3Cpath%20d='M106%203%20L107.2%206.2%20L110%207.4%20L107.2%208.6%20L106%2012%20L104.8%208.6%20L102%207.4%20L104.8%206.2%20Z'%20fill='%23fff6c9'%20stroke='%23d9a73a'%20stroke-width='.6'/%3E%3C/svg%3E");
  --art-nav-prev: var(--art-nav-next);  --art-nav-prev-flip: -1;   /* prev = next mirrored; set the flip to 1 for a real prev file */
  --art-corner: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='n' x1='0' y1='0' x2='1' y2='0'%3E%3Cstop offset='0' stop-color='%23c9d0da'/%3E%3Cstop offset='.5' stop-color='%23f3f6fa'/%3E%3Cstop offset='1' stop-color='%238c96a6'/%3E%3C/linearGradient%3E%3ClinearGradient id='d' x1='0' y1='0' x2='1' y2='0'%3E%3Cstop offset='0' stop-color='%23704626'/%3E%3Cstop offset='.4' stop-color='%23a06a3a'/%3E%3Cstop offset='1' stop-color='%234a2a14'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cg transform='rotate(45 50 50)' stroke-linejoin='round' stroke-linecap='round'%3E%3Cpath d='M46.5 8 Q50 3 53.5 8 L52.4 68 L47.6 68 Z' fill='url(%23d)' stroke='%232e180a' stroke-width='1'/%3E%3Cpath d='M49 12 L49.4 64' stroke='%23d9a878' stroke-width='.8' opacity='.7'/%3E%3Crect x='46.8' y='66' width='6.4' height='8' rx='1' fill='url(%23n)' stroke='%23566070' stroke-width='.9'/%3E%3Cpath d='M47.6 74 L52.4 74 L51.6 88 L50 95 L48.4 88 Z' fill='url(%23n)' stroke='%23566070' stroke-width='.9'/%3E%3Cpath d='M50 80 L50 94' stroke='%23566070' stroke-width='.6'/%3E%3C/g%3E%3C/svg%3E");   /* old dip pen in the bottom-left corner of the left page */
  --art-bullet: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='24'%20height='24'%20viewBox='0%200%2024%2024'%20fill='none'%3E%3Ccircle%20cx='12'%20cy='12'%20r='10.5'%20stroke='%232b8c92'%20stroke-width='1.6'/%3E%3Ccircle%20cx='12'%20cy='12'%20r='6'%20stroke='%23c79a2c'%20stroke-width='1.3'/%3E%3Cpath%20d='M12%201.5%20V5%20M12%2019%20V22.5%20M1.5%2012%20H5%20M19%2012%20H22.5'%20stroke='%232b8c92'%20stroke-width='1.2'/%3E%3Ccircle%20cx='12'%20cy='12'%20r='1.6'%20fill='%23c79a2c'/%3E%3Ccircle%20cx='12'%20cy='7'%20r='.9'%20fill='%232b8c92'/%3E%3Ccircle%20cx='12'%20cy='17'%20r='.9'%20fill='%232b8c92'/%3E%3Ccircle%20cx='7'%20cy='12'%20r='.9'%20fill='%232b8c92'/%3E%3Ccircle%20cx='17'%20cy='12'%20r='.9'%20fill='%232b8c92'/%3E%3C/svg%3E");
  --paper-texture: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='300'%20height='300'%3E%3Cfilter%20id='a'%20x='0'%20y='0'%20width='100%25'%20height='100%25'%3E%3CfeTurbulence%20type='fractalNoise'%20baseFrequency='.011'%20numOctaves='3'%20seed='5'%20stitchTiles='stitch'/%3E%3CfeColorMatrix%20values='0%200%200%200%20.30%20%200%200%200%200%20.42%20%200%200%200%200%20.66%20%201.3%200%200%200%20-.50'/%3E%3C/filter%3E%3Cfilter%20id='c'%20x='0'%20y='0'%20width='100%25'%20height='100%25'%3E%3CfeTurbulence%20type='fractalNoise'%20baseFrequency='.7'%20numOctaves='2'%20seed='11'%20stitchTiles='stitch'/%3E%3CfeColorMatrix%20values='0%200%200%200%20.25%20%200%200%200%200%20.35%20%200%200%200%200%20.55%20%201.2%200%200%200%20-.52'/%3E%3C/filter%3E%3Crect%20width='300'%20height='300'%20filter='url(%23a)'%20opacity='.42'/%3E%3Crect%20width='300'%20height='300'%20filter='url(%23c)'%20opacity='.20'/%3E%3C/svg%3E");   /* moonlit vellum mottling */
  --paper-stains: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='800'%20height='500'%20viewBox='0%200%20800%20500'%20preserveAspectRatio='none'%3E%3Cdefs%3E%3CradialGradient%20id='t'%3E%3Cstop%20offset='0'%20stop-color='%232b8c92'%20stop-opacity='.12'/%3E%3Cstop%20offset='1'%20stop-color='%232b8c92'%20stop-opacity='0'/%3E%3C/radialGradient%3E%3CradialGradient%20id='u'%3E%3Cstop%20offset='0'%20stop-color='%234a3fa0'%20stop-opacity='.12'/%3E%3Cstop%20offset='1'%20stop-color='%234a3fa0'%20stop-opacity='0'/%3E%3C/radialGradient%3E%3CradialGradient%20id='k'%3E%3Cstop%20offset='0'%20stop-color='%23d9b44a'%20stop-opacity='.10'/%3E%3Cstop%20offset='1'%20stop-color='%23d9b44a'%20stop-opacity='0'/%3E%3C/radialGradient%3E%3C/defs%3E%3Ccircle%20cx='110'%20cy='120'%20r='90'%20fill='url(%23t)'/%3E%3Ccircle%20cx='320'%20cy='380'%20r='110'%20fill='url(%23u)'/%3E%3Ccircle%20cx='520'%20cy='90'%20r='100'%20fill='url(%23u)'/%3E%3Ccircle%20cx='690'%20cy='330'%20r='120'%20fill='url(%23t)'/%3E%3Ccircle%20cx='420'%20cy='230'%20r='70'%20fill='url(%23k)'/%3E%3Ccircle%20cx='230'%20cy='250'%20r='55'%20fill='url(%23k)'/%3E%3Ccircle%20cx='740'%20cy='60'%20r='60'%20fill='url(%23k)'/%3E%3Ccircle%20cx='18'%20cy='40'%20r='1.2'%20fill='%23c79a2c'%20opacity='0.5'/%3E%3Ccircle%20cx='30'%20cy='60'%20r='0.8'%20fill='%23ffffff'%20opacity='0.7'/%3E%3Ccircle%20cx='10'%20cy='280'%20r='1.1'%20fill='%23c79a2c'%20opacity='0.45'/%3E%3Ccircle%20cx='60'%20cy='480'%20r='1.3'%20fill='%2339529a'%20opacity='0.3'/%3E%3Ccircle%20cx='240'%20cy='10'%20r='1'%20fill='%23ffffff'%20opacity='0.7'/%3E%3Ccircle%20cx='396'%20cy='30'%20r='1.2'%20fill='%23c79a2c'%20opacity='0.45'/%3E%3Ccircle%20cx='410'%20cy='22'%20r='0.9'%20fill='%23ffffff'%20opacity='0.7'/%3E%3Ccircle%20cx='390'%20cy='480'%20r='1.2'%20fill='%2339529a'%20opacity='0.3'/%3E%3Ccircle%20cx='500'%20cy='490'%20r='1'%20fill='%23c79a2c'%20opacity='0.45'/%3E%3Ccircle%20cx='780'%20cy='20'%20r='1.3'%20fill='%23c79a2c'%20opacity='0.5'/%3E%3Ccircle%20cx='790'%20cy='260'%20r='0.9'%20fill='%23ffffff'%20opacity='0.7'/%3E%3Ccircle%20cx='770'%20cy='480'%20r='1.2'%20fill='%2339529a'%20opacity='0.3'/%3E%3Ccircle%20cx='40'%20cy='250'%20r='0.9'%20fill='%23ffffff'%20opacity='0.6'/%3E%3Ccircle%20cx='600'%20cy='12'%20r='1'%20fill='%23ffffff'%20opacity='0.7'/%3E%3C/svg%3E");   /* soft blotches and specks; one image across both pages */
  --floaters: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='400'%20height='200'%20viewBox='0%200%20400%20200'%3E%3Cg%20fill='none'%20stroke='%232d2b78'%20stroke-width='3'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Cg%20transform='translate(0%200)'%3E%3Ccircle%20cx='50'%20cy='50'%20r='40'/%3E%3Ccircle%20cx='50'%20cy='50'%20r='28'/%3E%3Cpath%20d='M50%2010%20V22%20M50%2078%20V90%20M10%2050%20H22%20M78%2050%20H90'/%3E%3Cpath%20d='M50%2024%20L72%2062%20H28%20Z'/%3E%3C/g%3E%3Cg%20transform='translate(100%200)'%3E%3Ccircle%20cx='50'%20cy='50'%20r='40'/%3E%3Cpath%20d='M50%2014%20L71.2%2079.1%20L15.8%2038.9%20L84.2%2038.9%20L28.8%2079.1%20Z'/%3E%3C/g%3E%3Cg%20transform='translate(200%200)'%3E%3Cpath%20d='M50%2012%20L86%2074%20H14%20Z'/%3E%3Cpath%20d='M50%2088%20L86%2026%20H14%20Z'/%3E%3Ccircle%20cx='50'%20cy='50'%20r='6'/%3E%3C/g%3E%3Cg%20transform='translate(300%200)'%3E%3Cpath%20d='M62%2014%20A38%2038%200%201%200%2062%2086%20A30%2030%200%201%201%2062%2014%20Z'/%3E%3Ccircle%20cx='78'%20cy='30'%20r='3'/%3E%3Ccircle%20cx='84'%20cy='56'%20r='2'/%3E%3Ccircle%20cx='70'%20cy='78'%20r='2.5'/%3E%3C/g%3E%3Cg%20transform='translate(0%20100)'%3E%3Cpath%20d='M8%2050%20Q50%2014%2092%2050%20Q50%2086%208%2050%20Z'/%3E%3Ccircle%20cx='50'%20cy='50'%20r='13'/%3E%3Ccircle%20cx='50'%20cy='50'%20r='4'/%3E%3Cpath%20d='M50%208%20V20%20M24%2018%20L30%2027%20M76%2018%20L70%2027'/%3E%3C/g%3E%3Cg%20transform='translate(100%20100)'%3E%3Ccircle%20cx='50'%20cy='50'%20r='12'/%3E%3Ccircle%20cx='50'%20cy='16'%20r='7'/%3E%3Ccircle%20cx='50'%20cy='84'%20r='7'/%3E%3Ccircle%20cx='16'%20cy='50'%20r='7'/%3E%3Ccircle%20cx='84'%20cy='50'%20r='7'/%3E%3Cpath%20d='M50%2023%20V38%20M50%2062%20V77%20M23%2050%20H38%20M62%2050%20H77'/%3E%3C/g%3E%3Cg%20transform='translate(200%20100)'%3E%3Cpath%20d='M50%208%20L92%2050%20L50%2092%20L8%2050%20Z'/%3E%3Ccircle%20cx='50'%20cy='50'%20r='24'/%3E%3Cpath%20d='M50%2026%20V74%20M26%2050%20H74'/%3E%3Ccircle%20cx='50'%20cy='50'%20r='4'/%3E%3C/g%3E%3Cg%20transform='translate(300%20100)'%3E%3Ccircle%20cx='50'%20cy='50'%20r='40'/%3E%3Cpath%20d='M50%2022%20V80%20M50%2050%20L34%2030%20M50%2050%20L66%2030%20M50%2066%20L38%2054%20M50%2066%20L62%2054'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");   /* 4x2 sprite sheet of faint runes that drift over the pages */
  /* art tuning */
  --nav-art-display: block;  --arrow-glyph-display: none;  --nav-w: 110px;  --nav-h: 30px;
  --nav-glow: drop-shadow(0 0 7px rgba(120,225,255,.95));
  --corner-display: block;  --corner-w: 150px;  --corner-opacity: 1;  --corner-rot: 202deg;
  --list-style: none;  --bullet-display: block;  --bullet-w: 12px;  --bullet-h: 12px;  --bullet-top: .42em;
  --floaters-display: block;  --floaters-opacity: .045;
}
/* ellinia extras: a faint gold inner frame on each page */
[data-book="ellinia"] .page { box-shadow: inset 0 0 0 1px rgba(168,128,31,.35); }

/* ---- plain: the clean look; follows light / dark ---- */
[data-book="plain"] {
  --desk-bg: #f6f3ee;  --desk-ink: #2a2622;  --desk-muted: #7a7168;  --desk-line: #e6dfd5;
  --font-body: "Segoe UI", system-ui, sans-serif;
  --font-head: "Segoe UI", system-ui, sans-serif;
  --cover: #e6dfd5;  --cover-edge: #d6cdc0;
  --shadow-book: 0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.05);
  --page-bg-l: #fffdf9;  --page-bg-r: #fffdf9;
  --page-edge: #e6dfd5;
  --spine: linear-gradient(to right, rgba(0,0,0,0), rgba(0,0,0,.08) 50%, rgba(0,0,0,0));
  --ink: #2a2622;  --ink-soft: #7a7168;  --line: #e6dfd5;
  --accent: #d9822b;  --accent-soft: #fbead6;  --boss: #b8433a;
  --seal: #d9822b;  --seal-ink: #fff;
  --glow: 0 1px 2px rgba(0,0,0,.06);
  --hover-glow: 0 0 0 2px #d9822b;
  --slot-bg: #fffdf9;  --slot-border: #e6dfd5;  --slot-empty: #f0eae1;
  --pic-bg: #efe9e0;  --q: #c9bfb2;
  --arrow: #7a7168;  --arrow-hover: #d9822b;  --arrow-glow: none;
  --ribbon: #e6dfd5;  --ribbon-active: #fbead6;  --ribbon-ink: #2a2622;
  --bar-track: #e6dfd5;  --bar-caught: #d9822b;  --bar-seen: #c9bfb2;
  --field-bg: #fffdf9;
  /* art slots: all off in plain (the layout falls back to the arrow glyphs and normal bullets) */
  --art-nav-next: none;  --art-nav-prev: none;  --art-corner: none;  --art-bullet: none;
  --paper-texture: none;  --paper-stains: none;  --floaters: none;
}
@media (prefers-color-scheme: dark) {
  [data-book="plain"] {
    --desk-bg: #181614;  --desk-ink: #ece6de;  --desk-muted: #9c9389;  --desk-line: #36312c;
    --cover: #2b2723;  --cover-edge: #36312c;
    --shadow-book: 0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.25);
    --page-bg-l: #221f1c;  --page-bg-r: #221f1c;  --page-edge: #36312c;
    --spine: linear-gradient(to right, rgba(0,0,0,0), rgba(0,0,0,.35) 50%, rgba(0,0,0,0));
    --ink: #ece6de;  --ink-soft: #9c9389;  --line: #36312c;
    --accent: #f0a050;  --accent-soft: #3a2b1c;  --boss: #e0756b;
    --seal: #f0a050;  --seal-ink: #181614;
    --glow: 0 1px 2px rgba(0,0,0,.3);  --hover-glow: 0 0 0 2px #f0a050;
    --slot-bg: #221f1c;  --slot-border: #36312c;  --slot-empty: #2b2723;
    --pic-bg: #2b2723;  --q: #5a524a;
    --arrow: #9c9389;  --arrow-hover: #f0a050;
    --ribbon: #36312c;  --ribbon-active: #3a2b1c;  --ribbon-ink: #ece6de;
    --bar-track: #36312c;  --bar-caught: #f0a050;  --bar-seen: #5a524a;
    --field-bg: #221f1c;
  }
}

/* ==========================================================================================
   4. LAYOUT AND COMPONENTS (variables only, no theme colours below this line)
   ========================================================================================== */

/* 4.1 page chrome (the desk) */
* { box-sizing: border-box; }
body { overflow-x: clip;  margin: 0; background: var(--desk-bg); background-attachment: fixed; color: var(--desk-ink);
  font: 15px/1.45 var(--font-body); min-height: 100vh; }
.wrap { max-width: var(--book-max); margin: 0 auto; padding: 22px 16px 50px; }
.title { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; margin-bottom: 6px; }
h1 { margin: 0; font: 700 30px/1.1 var(--font-head); letter-spacing: .01em; }
h1 span { color: var(--bar-caught); }
.count { font-variant-numeric: tabular-nums; color: var(--desk-muted); }
.count b { color: var(--desk-ink); font-size: 18px; }
.top { display: flex; gap: 12px; align-items: center; margin-left: auto; font-size: 13px; }
.top a { color: var(--desk-muted); }
.top select { font: inherit; font-size: 13px; color: var(--desk-ink); background: var(--field-bg);
  border: 1px solid var(--desk-line); border-radius: 8px; padding: 3px 6px; }
.top select option { color: #222; background: #fff; }
.bar { height: 7px; background: var(--bar-track); border-radius: 4px; overflow: hidden; margin: 6px 0 4px; display: flex; }
.bar i { display: block; height: 100%; }
.bar .c { background: var(--bar-caught); } .bar .s { background: var(--bar-seen); }
.hint { color: var(--desk-muted); font-size: 13px; margin: 0 0 6px; }
.foot { margin-top: 24px; color: var(--desk-muted); font-size: 12px; }

/* 4.2 arrows and chapter ribbons (above the book) */
.tools { display: flex; align-items: flex-end; justify-content: space-between; gap: 8px; min-height: 52px; }
.ribbons { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px; margin-bottom: -2px; position: relative; z-index: 2; }
.ribbon { background: var(--ribbon); color: var(--ribbon-ink); border: 0; font: 600 12px/1 var(--font-head);
  letter-spacing: .04em; padding: 8px 14px 14px; cursor: pointer;
  clip-path: polygon(0 0, 100% 0, 100% 100%, 50% calc(100% - 7px), 0 100%);
  transition: transform var(--hover-ms), background var(--hover-ms); }
.ribbon:hover, .ribbon:focus-visible { transform: translateY(2px); background: var(--ribbon-active); outline: none; }
.ribbon.on { background: var(--ribbon-active); padding-top: 12px; }
/* art slot: --art-nav-next / --art-nav-prev (section 3) draw .arrow-art; .arrow-glyph is the plain fallback */
.arrow { background: none; border: 0; color: var(--arrow); text-shadow: var(--arrow-glow); cursor: pointer;
  font: 400 30px/1 var(--font-head); padding: 4px 6px; display: inline-flex; align-items: center; gap: 4px;
  transition: color var(--hover-ms), transform var(--hover-ms); }
.arrow:hover:not(:disabled), .arrow:focus-visible { color: var(--arrow-hover); outline: none; }
.arrow-left:hover:not(:disabled) { transform: translateX(-3px); }
.arrow-right:hover:not(:disabled) { transform: translateX(3px); }
.arrow:disabled { opacity: .28; cursor: default; text-shadow: none; }
.arrow-art { display: var(--nav-art-display); width: calc(var(--nav-w) * var(--nav-k)); height: calc(var(--nav-h) * var(--nav-k));
  background: var(--art-nav-next) no-repeat center / contain; transition: filter var(--hover-ms); }
.arrow-left .arrow-art { background-image: var(--art-nav-prev); transform: scaleX(var(--art-nav-prev-flip)); }
.arrow:hover:not(:disabled) .arrow-art, .arrow:focus-visible .arrow-art { filter: var(--nav-glow); }
.arrow:disabled .arrow-art { filter: grayscale(.6); }
.arrow-glyph { display: var(--arrow-glyph-display); align-items: center; gap: 4px; }
.arrow .fl { font-size: 24px; } .arrow .sh { font-size: 34px; letter-spacing: -.1em; }

/* 4.3 the book: leather cover, two pages, spine */
.book { position: relative; background: var(--cover); border: 2px solid var(--cover-edge); border-radius: var(--book-radius);
  padding: var(--cover-pad); box-shadow: var(--shadow-book); }
.pages { display: grid; grid-template-columns: 1fr 1fr; height: var(--page-h); position: relative; }
.pages.turn-n { animation: turn-n var(--turn-ms) ease-out; }
.pages.turn-p { animation: turn-p var(--turn-ms) ease-out; }
@keyframes turn-n { from { opacity: 0; transform: translateX(26px); } to { opacity: 1; transform: none; } }
@keyframes turn-p { from { opacity: 0; transform: translateX(-26px); } to { opacity: 1; transform: none; } }
.spine { position: absolute; top: var(--cover-pad); bottom: var(--cover-pad); left: 50%; width: 30px; margin-left: -15px;
  background: var(--spine); pointer-events: none; z-index: 1; }
.page { position: relative; display: flex; flex-direction: column; min-height: 0; padding: var(--page-pad);
  border: 1px solid var(--page-edge); color: var(--ink); }
.page.left { background: var(--page-bg-l); border-radius: var(--page-radius) 0 0 var(--page-radius); border-right: 0; }
.page.right { background: var(--page-bg-r); border-radius: 0 var(--page-radius) var(--page-radius) 0; border-left: 0; }
.page.blank { opacity: .55; }
/* page decoration: stains + texture under everything, corner piece behind the slots; never catches clicks */
.page::before { content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none; z-index: 0;
  background-image: var(--paper-stains), var(--paper-texture); background-repeat: no-repeat, repeat; background-size: 200% 100%, auto;
  background-position: 0 0, 0 0; }
.page.right::before { background-position: 100% 0, 0 0; }
/* corner piece (quill / pen): lies on top of the book across its bottom-left corner, crossing the
   cover's left and bottom edges, with a drop shadow as if put down there */
.book::after { content: ""; position: absolute; z-index: 5; pointer-events: none;
  left: calc(-14px * var(--corner-k)); bottom: calc(-52px * var(--corner-k));
  display: var(--corner-display); width: calc(var(--corner-w) * var(--corner-k)); height: calc(var(--corner-w) * var(--corner-k));
  background: var(--art-corner) no-repeat center / contain; opacity: var(--corner-opacity);
  transform: rotate(var(--corner-rot)); filter: drop-shadow(3px 5px 4px rgba(0,0,0,.45)); }
.page > * { position: relative; z-index: 1; }
.phead { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; min-height: 22px;
  font: 600 13px/1 var(--font-head); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-soft);
  border-bottom: 1px solid var(--line); padding-bottom: 6px; margin-bottom: 8px; }
.phead .chapcount { font-weight: 400; letter-spacing: 0; text-transform: none; font-variant-numeric: tabular-nums; }
.pnum { margin-top: 6px; font: 400 12px var(--font-head); color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.page.left .pnum { text-align: left; } .page.right .pnum { text-align: right; }

/* 4.4 the 3x3 grid and the slots */
.grid { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(3, 1fr); gap: var(--slot-gap); }
.slot { position: relative; min-height: 0; min-width: 0; display: flex; flex-direction: column; align-items: center;
  padding: 6px 4px 5px; background: var(--slot-bg); border: 1px solid var(--slot-border); border-radius: var(--slot-radius);
  color: inherit; font: inherit; text-align: center; cursor: pointer;
  transition: transform var(--hover-ms), box-shadow var(--hover-ms); }
.slot:hover:not(:disabled), .slot:focus-visible { transform: translateY(-2px); box-shadow: var(--hover-glow); outline: none; }
.slot.empty { background: none; border: 1px dashed var(--slot-empty); cursor: default; }
.slot.unknown { cursor: default; }
.slot.caught { box-shadow: var(--glow); }
.slot.boss { border-color: var(--boss); }
.pic { flex: 1; min-height: 0; width: 100%; display: grid; place-items: center; background: var(--pic-bg);
  border-radius: var(--pic-radius); overflow: hidden; }
.pic img { max-width: 90%; max-height: 90%; image-rendering: pixelated; object-fit: contain; }
.pic .q { font: 700 38px var(--font-head); color: var(--q); }
:is(.slot, .frame).seen .pic img, :is(.slot, .frame).grey .pic img { filter: grayscale(1) brightness(.45) contrast(.9); opacity: .8; }
.name { font-size: 12.5px; font-weight: 600; margin-top: 4px; line-height: 1.15; min-height: 2.3em; display: flex; align-items: center;
  max-width: 100%; overflow-wrap: anywhere; }
.slot.seen .name, .slot.grey .name { color: var(--ink-soft); }
.lv { font-size: 11.5px; color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.crown { position: absolute; top: 3px; right: 6px; font-size: 14px; color: var(--boss); }
.seal { position: absolute; top: 4px; left: 5px; width: 17px; height: 17px; border-radius: 50%; background: var(--seal);
  color: var(--seal-ink); display: grid; place-items: center; font-size: 10px; line-height: 1; box-shadow: 0 1px 2px rgba(0,0,0,.35); }
.seal::after { content: "\2726"; }

/* 4.5 detail view (an entry in the book) */
.page-body { flex: 1; min-height: 0; overflow: auto; padding-right: 4px; }
.back { background: var(--ribbon); color: var(--ribbon-ink); border: 0; border-radius: 6px; font: 600 12px var(--font-head);
  padding: 5px 12px; cursor: pointer; margin-bottom: 10px; }
.back:hover, .back:focus-visible { background: var(--ribbon-active); outline: none; }
.frame { width: 100%; aspect-ratio: 1.25; max-height: 240px; display: flex; }
.frame .pic { border: 1px solid var(--slot-border); border-radius: calc(var(--slot-radius) + 4px); background: var(--pic-bg); }
.frame .pic img { max-width: 80%; max-height: 80%; }
.photo { margin: 12px 0 0; }
.photo img { width: 100%; max-height: 180px; object-fit: contain; border-radius: var(--pic-radius); display: block; background: #000; }
.thumbs { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.thumbs button { border: 2px solid transparent; padding: 0; border-radius: 6px; overflow: hidden; cursor: pointer; background: none; }
.thumbs button.on { border-color: var(--accent); }
.thumbs img { height: 44px; display: block; }
.field { margin-bottom: 12px; }
.field h4 { margin: 0 0 3px; font: 600 11px var(--font-head); letter-spacing: .12em; text-transform: uppercase; color: var(--ink-soft); }
.field .big { margin: 0; font: 700 26px/1.15 var(--font-head); }
.field .big .crown { position: static; font-size: 22px; margin-right: 4px; }
.field p, .field ul { margin: 0; }
.field ul { padding-left: 18px; list-style: var(--list-style); }
.field li { margin: 2px 0; position: relative; }
.field li::before { content: ""; display: var(--bullet-display); position: absolute; top: var(--bullet-top);
  left: calc(-4px - var(--bullet-w)); width: var(--bullet-w); height: var(--bullet-h);
  background: var(--art-bullet) no-repeat center / contain; }
.sub { color: var(--ink-soft); font-size: 13px; }
.empty { color: var(--ink-soft); }
table.drops { width: 100%; border-collapse: collapse; font-size: 13px; }
table.drops td { padding: 3px 4px; border-top: 1px solid var(--line); vertical-align: middle; }
table.drops td.r { text-align: right; color: var(--ink-soft); font-variant-numeric: tabular-nums; white-space: nowrap; }
table.drops img { width: 24px; height: 24px; object-fit: contain; vertical-align: middle; margin-right: 6px; image-rendering: pixelated; }
.note { font-size: 12px; color: var(--ink-soft); margin-top: 6px; }

/* 4.5b floating runes (themes that set --floaters): 8 cells of a 4x2 sprite, drifting slowly, never clickable */
.floaters { position: absolute; inset: 0; overflow: hidden; border-radius: inherit; pointer-events: none; z-index: 3;
  display: var(--floaters-display); opacity: var(--floaters-opacity); }
.floaters i { position: absolute; width: 76px; height: 76px; background: var(--floaters) no-repeat; background-size: 400% 200%;
  animation: float-a 28s ease-in-out infinite alternate; }
.floaters i:nth-child(1) { left: 5%;  top: 9%;  background-position: 0 0;          animation-duration: 31s; }
.floaters i:nth-child(2) { left: 30%; top: 60%; background-position: 33.333% 0;     animation-name: float-b; animation-duration: 37s; animation-delay: -9s; }
.floaters i:nth-child(3) { left: 44%; top: 10%; background-position: 66.667% 0;     animation-duration: 26s; animation-delay: -14s; }
.floaters i:nth-child(4) { left: 68%; top: 64%; background-position: 100% 0;       animation-name: float-b; animation-duration: 34s; animation-delay: -5s; }
.floaters i:nth-child(5) { left: 84%; top: 12%; background-position: 0 100%;        animation-duration: 40s; animation-delay: -21s; }
.floaters i:nth-child(6) { left: 14%; top: 72%; background-position: 33.333% 100%;  animation-name: float-b; animation-duration: 29s; animation-delay: -17s; }
.floaters i:nth-child(7) { left: 56%; top: 38%; background-position: 66.667% 100%;  animation-duration: 36s; animation-delay: -2s; }
.floaters i:nth-child(8) { left: 24%; top: 30%; background-position: 100% 100%;     animation-name: float-b; animation-duration: 33s; animation-delay: -11s; }
@keyframes float-a { from { transform: translate(0, 0) rotate(0deg); } to { transform: translate(26px, -34px) rotate(42deg); } }
@keyframes float-b { from { transform: translate(0, 0) rotate(0deg); } to { transform: translate(-30px, 24px) rotate(-36deg); } }

/* 4.6 responsive: under 760 px one page at a time */
@media (max-width: 759px) {
  :root { --cover-pad: 9px; --page-pad: 10px; --slot-gap: 5px; --nav-k: .68; --corner-k: .62; }
  .floaters i { width: 52px; height: 52px; }
  .pages { grid-template-columns: 1fr; height: auto; }
  .spine { display: none; }
  .page.left, .page.right { border: 1px solid var(--page-edge); border-radius: var(--page-radius); }
  .grid { grid-template-rows: none; grid-auto-rows: auto; }
  .slot { padding: 5px 3px 4px; }
  .pic { flex: none; aspect-ratio: 1.1; }
  .pic .q { font-size: 30px; }
  .name { font-size: 11px; }
  .lv { font-size: 10.5px; }
  .page-body { overflow: visible; }
  .page.left .pnum, .page.right .pnum { text-align: center; }
  h1 { font-size: 25px; }
  .top { margin-left: 0; }
  .arrow .fl { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .pages.turn-n, .pages.turn-p { animation: none; }
  .slot, .ribbon, .arrow, .arrow-art { transition: none; }
  .floaters i { animation: none; }
}
</style></head><body>
<div class="wrap">
  <div class="title"><h1>Monster <span>Album</span></h1><span class="count" id="count"></span>
    <span class="top" id="top"></span></div>
  <div class="bar" id="bar"></div>
  <p class="hint" id="hint"></p>
  <div class="tools"><span id="arL"></span><nav class="ribbons" id="ribbons" aria-label="Chapters"></nav><span id="arR"></span></div>
  <div class="book"><div class="pages" id="pages"></div><div class="spine"></div><div class="floaters" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div>
  <div class="foot" id="foot"></div>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const src = p => p.split("/").map(encodeURIComponent).join("/");
const dt = s => s ? new Date(s).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : "";
const REGION_ORDER = ["Maple Island", "Victoria Island", "Ossyria", "Hidden Street", "Event", "Unknown"];
const THEMES = [["henesys", "Henesys parchment"], ["ellinia", "Ellinia tome"], ["plain", "Plain"]];
const PER = 9;

/* ---- theme ---- */
function getTheme() {
  try { const t = localStorage.getItem("album-book"); if (THEMES.some(x => x[0] === t)) return t; } catch (e) {}
  return "henesys";
}
function setTheme(t) {
  document.documentElement.dataset.book = t;
  try { localStorage.setItem("album-book", t); } catch (e) {}
}
setTheme(getTheme());

/* ---- what is shown: only the regions that are out in Classic; the rest stays in the data ---- */
const shown = D.mobs.filter(m => m.shown);
const S = {total: shown.length, caught: shown.filter(m => m.state === "caught").length,
           seen: shown.filter(m => m.state === "seen" || m.state === "grey").length};
$("#count").innerHTML = `<b>${S.caught}</b> / ${S.total} caught · ${S.seen} seen`;
const pct = n => S.total ? 100 * n / S.total : 0;
$("#bar").innerHTML = `<i class="c" style="width:${pct(S.caught)}%"></i><i class="s" style="width:${pct(S.seen)}%"></i>`;
$("#hint").textContent = (D.who ? D.who + ". " : "") + `Grey: seen on its map, or a boss at most ${D.boss_grey_levels} levels above you` +
  (D.level ? ` (Lv ${D.level})` : "") + ". Click a monster for where it lives, what it drops, and your first-kill photo.";
$("#top").innerHTML = `<label>Book <select id="theme" aria-label="Book style">${THEMES.map(t => `<option value="${t[0]}">${esc(t[1])}</option>`).join("")}</select></label>` +
  (D.live ? `<a href="/">Diary</a>` : `<a href="diary.html">Diary</a>`);
$("#theme").value = getTheme();
$("#theme").addEventListener("change", e => setTheme(e.target.value));
$("#foot").textContent = `Rebuilt ${dt(D.generated)} · refresh to see new catches · pictures stay on this PC · monster facts from NiaMeowDB`;

/* ---- chapters and pages: Bosses first, then Monsters (or each shown region); a chapter starts on a fresh page ---- */
const chapters = [];
const bosses = shown.filter(m => m.boss);
if (bosses.length) chapters.push({name: "Bosses", list: bosses});
const rest = shown.filter(m => !m.boss);
if (D.split_regions) {            // one chapter per region (SPLIT_REGIONS in album_html.py)
  [...new Set(rest.map(m => m.region))].sort((a, b) => {
    const ia = REGION_ORDER.indexOf(a), ib = REGION_ORDER.indexOf(b);
    return (ia < 0 ? 50 : ia) - (ib < 0 ? 50 : ib) || String(a).localeCompare(b); })
    .forEach(r => chapters.push({name: r, list: rest.filter(m => m.region === r)}));
} else if (rest.length) {         // one chapter, by level (stable: ties keep the catalog order)
  chapters.push({name: "Monsters", list: rest.slice().sort((a, b) => (a.level ?? 999) - (b.level ?? 999))});
}
const pages = [];
chapters.forEach((c, ci) => {
  c.caught = c.list.filter(m => m.state === "caught").length;
  c.page = pages.length;
  for (let i = 0; i < c.list.length; i += PER) {
    const slots = c.list.slice(i, i + PER);
    while (slots.length < PER) slots.push(null);
    pages.push({ci, slots, first: i === 0});
  }
});
if (!pages.length) pages.push({ci: -1, slots: Array(PER).fill(null), first: true});
const order = chapters.flatMap(c => c.list).filter(m => m.state !== "unknown");   // detail view: prev / next
const pageOf = id => pages.findIndex(p => p.slots.some(m => m && m.id === id));

/* ---- state ---- */
const mq = window.matchMedia("(max-width: 759px)");
let cur = 0;            // first visible page index
let detailId = null;    // open entry, or null for the spread

/* ---- pieces ---- */
function pic(m) {
  if (m.state === "unknown") return `<div class="pic"><span class="q">?</span></div>`;
  if (m.icon) return `<div class="pic"><img src="${src(m.icon)}" alt="" loading="lazy"></div>`;
  // No picture downloaded yet (Settings > Internet > "Monster pictures"): a "?" like unknown mobs.
  return `<div class="pic nopic" title="Picture not downloaded yet"><span class="q">?</span></div>`;
}
/* Small sprites (snails are ~37 px) get a whole-number zoom so they stay crisp and fill the frame
   (max-width / max-height still cap them). */
document.addEventListener("load", e => {
  const img = e.target;
  if (!(img instanceof HTMLImageElement) || !img.closest(".pic")) return;
  const z = Math.max(1, Math.min(3, Math.floor(80 / Math.max(img.naturalWidth, img.naturalHeight, 1))));
  if (z > 1) img.style.width = img.naturalWidth * z + "px";
}, true);
function slot(m) {
  if (!m) return `<div class="slot empty" aria-hidden="true"></div>`;
  const label = m.state === "unknown" ? "Unknown monster" : `${m.name}, level ${m.level}${m.state === "caught" ? ", caught" : ", not caught"}`;
  return `<button type="button" class="slot ${m.state}${m.boss ? " boss" : ""}" data-id="${esc(m.id)}" aria-label="${esc(label)}"${m.state === "unknown" ? " disabled" : ""}>
    ${m.boss && m.state !== "unknown" ? `<span class="crown" title="Boss">♛</span>` : ""}${m.state === "caught" ? `<span class="seal" title="Caught"></span>` : ""}
    ${pic(m)}<div class="name">${m.state === "unknown" ? "???" : esc(m.name)}</div>
    <div class="lv">${m.state === "unknown" ? "Lv ?" : "Lv " + m.level}</div></button>`;
}
/* art: --art-nav-next / --art-nav-prev in the theme block draw .arrow-art; .arrow-glyph shows when a theme has none */
function arrow(dir, label, disabled) {
  const glyph = dir < 0 ? `<span class="fl">☙</span><span class="sh">⟵</span>` : `<span class="sh">⟶</span><span class="fl">❧</span>`;
  return `<button type="button" class="arrow arrow-${dir < 0 ? "left" : "right"}" data-dir="${dir}" aria-label="${esc(label)}"${disabled ? " disabled" : ""}><span class="arrow-art" aria-hidden="true"></span><span class="arrow-glyph">${glyph}</span></button>`;
}
function pageHTML(idx, side) {
  const p = pages[idx];
  if (!p) return `<section class="page ${side} blank"></section>`;
  const c = chapters[p.ci];
  return `<section class="page ${side}"><div class="phead"><span class="chap">${esc(c ? c.name : "")}</span>` +
    (p.first && c ? `<span class="chapcount">caught ${c.caught} / ${c.list.length}</span>` : "") + `</div>` +
    `<div class="grid">${p.slots.map(slot).join("")}</div><div class="pnum">${idx + 1}</div></section>`;
}
function ribbons() {
  const curCh = detailId ? (chapters.findIndex(c => c.list.some(m => m.id === detailId))) : (pages[cur] || {}).ci;
  $("#ribbons").innerHTML = chapters.length > 1 || chapters.length === 1 ? chapters.map((c, i) =>
    `<button type="button" class="ribbon${i === curCh ? " on" : ""}" data-ch="${i}" title="${esc(c.name)}">${esc(c.name)}</button>`).join("") : "";
}

/* ---- the spread ---- */
function renderSpread(dir) {
  const wide = !mq.matches;
  if (wide) cur -= cur % 2;
  cur = Math.max(0, Math.min(cur, pages.length - 1));
  const el = $("#pages");
  el.className = "pages" + (dir ? (dir > 0 ? " turn-n" : " turn-p") : "");
  el.innerHTML = wide ? pageHTML(cur, "left") + pageHTML(cur + 1, "right") : pageHTML(cur, "left");
  const step = wide ? 2 : 1;
  $("#arL").innerHTML = arrow(-1, wide ? "Previous pages" : "Previous page", cur <= 0);
  $("#arR").innerHTML = arrow(1, wide ? "Next pages" : "Next page", cur + step >= pages.length);
}

/* ---- the entry ---- */
function maps(m) {
  return (m.maps || []).map(x => `<li>${esc(x.name)} <span class="sub">· ${esc(x.region)}${x.count ? ` · ${x.count} spawn${x.count > 1 ? "s" : ""}` : ""}${x.respawn >= 600 ? ` · every ${x.respawn >= 3600 ? Math.round(x.respawn / 360) / 10 + " h" : Math.round(x.respawn / 60) + " min"}` : ""}</span></li>`).join("");
}
function renderDetail(m, dir) {
  const k = m.kill;
  let left = `<button type="button" class="back" data-back>⟵ Back to the album</button><div class="frame ${m.state}">${pic(m)}</div>`;
  if (k) {
    const shots = k.shots || [];
    left += shots.length ? `<figure class="photo"><img id="big" src="${src(shots[0])}" alt="First kill of ${esc(m.name)}">
      ${shots.length > 1 ? `<div class="thumbs">${shots.map((s, i) => `<button type="button" data-s="${esc(s)}" class="${i ? "" : "on"}"><img src="${src(s)}" alt="Picture ${i + 1}"></button>`).join("")}</div>` : ""}</figure>`
      : `<p class="empty">${k.how === "manual" ? "Marked as caught by hand, so there's no photo." : "No photo of this kill."}</p>`;
  } else {
    left += `<p class="empty">Not caught yet${m.seen ? ` · seen at ${esc(m.seen)}` : (m.boss ? " · a boss near your level" : "")}.</p>`;
  }
  let right = `<div class="field"><h4>Name</h4><p class="big" id="dtitle">${m.boss ? `<span class="crown" title="Boss">♛</span>` : ""}${esc(m.name)}</p>
    <p class="sub">Lv ${m.level}${m.hp ? ` · ${m.hp.toLocaleString()} HP` : ""}${m.exp ? ` · ${m.exp.toLocaleString()} EXP` : ""}</p></div>`;
  if (k) right += `<div class="field"><h4>First ${m.boss ? "boss kill" : "kill"}</h4><p>${esc(dt(k.time))}${k.map ? " · " + esc(k.map) : ""}${k.level ? " · you were Lv " + k.level : ""}${k.how === "party_boss" ? " · party kill" : ""}</p></div>`;
  const ml = maps(m);
  right += `<div class="field"><h4>Environment</h4>${ml ? `<ul>${ml}</ul>` : `<p class="empty">No spawn list.</p>`}</div>`;
  const drops = (m.drops || []).map(d => `<tr><td>${d.icon ? `<img src="${src(d.icon)}" alt="">` : ""}${esc(d.name)}</td><td class="r">${esc(d.rate || "")}</td></tr>`).join("");
  right += `<div class="field"><h4>Drops</h4>${drops ? `<table class="drops">${drops}</table><p class="note">Rates are OSMLib reference numbers shown by NiaMeowDB; Classic's own rates may differ.</p>` : `<p class="empty">No drop list.</p>`}</div>`;
  const el = $("#pages");
  el.className = "pages" + (dir ? (dir > 0 ? " turn-n" : " turn-p") : "");
  el.innerHTML = `<section class="page left" aria-labelledby="dtitle"><div class="page-body">${left}</div></section>
    <section class="page right"><div class="page-body">${right}</div></section>`;
  const i = order.findIndex(x => x.id === m.id);
  $("#arL").innerHTML = arrow(-1, "Previous monster", i <= 0);
  $("#arR").innerHTML = arrow(1, "Next monster", i < 0 || i >= order.length - 1);
}

function render(dir) {
  const m = detailId && order.find(x => x.id === detailId);
  if (m) renderDetail(m, dir); else { detailId = null; renderSpread(dir); }
  ribbons();
}

/* ---- navigation (the open entry lives in the URL hash: #mob=<id>) ---- */
function route() {
  const h = /^#mob=(.+)$/.exec(location.hash);
  const id = h ? decodeURIComponent(h[1]) : null;
  const ok = id && order.some(x => x.id === id) ? id : null;
  if (!ok && detailId) { const p = pageOf(detailId); if (p >= 0) cur = p; }
  detailId = ok;
  render();
}
function clearHash() { history.pushState(null, "", location.pathname + location.search); }
function closeEntry() { if (!detailId) return; clearHash(); route(); }
function step(dir) {
  if (detailId) {
    const i = order.findIndex(x => x.id === detailId), n = order[i + dir];
    if (!n) return;
    detailId = n.id;
    history.replaceState(null, "", "#mob=" + encodeURIComponent(n.id));
  } else {
    cur += dir * (mq.matches ? 1 : 2);
    if (cur < 0 || cur >= pages.length) { cur = Math.max(0, Math.min(cur, pages.length - 1)); }
  }
  render(dir);
}
function goChapter(ci) {
  const c = chapters[ci]; if (!c) return;
  const was = detailId;
  detailId = null;
  if (was) clearHash();
  const to = c.page;
  const dir = to >= cur ? 1 : -1;
  cur = to;
  render(was ? 0 : dir);
}

document.addEventListener("click", e => {
  const t = e.target.closest(".slot[data-id]"); if (t) { location.hash = "mob=" + encodeURIComponent(t.dataset.id); return; }
  const a = e.target.closest(".arrow"); if (a && !a.disabled) return step(+a.dataset.dir);
  const r = e.target.closest(".ribbon"); if (r) return goChapter(+r.dataset.ch);
  if (e.target.closest("[data-back]")) return closeEntry();
  const b = e.target.closest(".thumbs button");
  if (b) { $("#big").src = src(b.dataset.s); document.querySelectorAll(".thumbs button").forEach(x => x.classList.toggle("on", x === b)); }
});
document.addEventListener("keydown", e => {
  if (/^(SELECT|INPUT|TEXTAREA)$/.test(e.target.tagName) || e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "Escape") closeEntry();
});
window.addEventListener("hashchange", route);
(mq.addEventListener ? mq.addEventListener("change", () => render()) : mq.addListener(() => render()));
route();
</script></body></html>
"""


def build(root=None, album=None):
    root = Path(root or A.ROOT)
    (root / "album.html").write_text(page(root, album), encoding="utf-8")
    return root / "album.html"
