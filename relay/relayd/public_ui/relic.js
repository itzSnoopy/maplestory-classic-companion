"use strict";
// Relic hunt table. Plain JS; server values go in only through textContent / DOM APIs / CSSOM.
// Auth: a member token. The app opens this page with "#t=<token>" (a fragment is never sent to the
// server); it is moved into sessionStorage and removed from the address bar. An owner token kept by the
// /rooms page (localStorage "relayRoomsMine") works too. The token is never put in a URL or a log.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var m = /^\/rooms\/([a-z0-9]{4}-[a-z0-9]{4})\/relic\/?$/.exec(location.pathname);
  var rid = m ? m[1] : "";
  var SKEY = "relicToken:" + rid;
  var token = "";
  var state = null, tiles = {}, slots = [], scene = null, sceneKey = "", dragging = null;
  var pollTimer = null, busy = false, popped = {};

  function say(text) { var e = $("msg"); e.textContent = text || ""; e.hidden = !text; }
  function tryStore(fn) { try { return fn(); } catch (e) { return null; } }

  function findToken() {
    var t = "";
    var hm = /(?:^#|&)t=([^&]+)/.exec(location.hash || "");
    if (hm) {
      try { t = decodeURIComponent(hm[1]); } catch (e) { t = ""; }
      if (t) tryStore(function () { sessionStorage.setItem(SKEY, t); });
      try { history.replaceState(null, "", location.pathname); } catch (e) { /* ignore */ }
      return t;
    }
    t = tryStore(function () { return sessionStorage.getItem(SKEY); }) || "";
    if (t) return t;
    var list = tryStore(function () { return JSON.parse(localStorage.getItem("relayRoomsMine") || "[]"); }) || [];
    for (var i = 0; Array.isArray(list) && i < list.length; i++) {
      if (list[i] && list[i].id === rid && typeof list[i].token === "string") return list[i].token;
    }
    return "";
  }

  function api(method, path, body) {
    var opts = { method: method, headers: { Authorization: "Bearer " + token } };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch("/v2/rooms/" + rid + path, opts).then(function (r) {
      return r.json().then(function (d) {
        if (r.ok) return d;
        d.status = r.status;
        return Promise.reject(d);
      }, function () { return Promise.reject({ error: "unexpected reply", status: r.status }); });
    }, function () { return Promise.reject({ error: "cannot reach the server", status: 0 }); });
  }

  function hue(name) {
    var h = 0;
    for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
    return "hsl(" + h + ", 70%, 62%)";
  }

  // ---- placeholder art: a code-drawn landscape (sky, sun, hills, a temple, trees)
  function drawScene(cols, rows) {
    var S = 96, W = cols * S, H = rows * S;
    var c = document.createElement("canvas");
    c.width = W; c.height = H;
    var g = c.getContext("2d");
    var sky = g.createLinearGradient(0, 0, 0, H);
    sky.addColorStop(0, "#5b86c9"); sky.addColorStop(0.7, "#f0c98a"); sky.addColorStop(1, "#f6e0b0");
    g.fillStyle = sky; g.fillRect(0, 0, W, H);
    g.fillStyle = "#fff3b8"; g.beginPath(); g.arc(W * 0.78, H * 0.22, H * 0.09, 0, 6.3); g.fill();
    g.fillStyle = "rgba(255,255,255,.7)";
    [[0.2, 0.18], [0.45, 0.12], [0.6, 0.27]].forEach(function (p) {
      g.beginPath(); g.ellipse(W * p[0], H * p[1], H * 0.1, H * 0.035, 0, 0, 6.3); g.fill();
    });
    function hills(color, base, amp, f, ph) {
      g.fillStyle = color; g.beginPath(); g.moveTo(0, H);
      for (var x = 0; x <= W; x += 6) g.lineTo(x, H * base - Math.sin(x / W * f + ph) * H * amp);
      g.lineTo(W, H); g.closePath(); g.fill();
    }
    hills("#7a8fb5", 0.55, 0.09, 9, 0.5);
    hills("#5f8a4e", 0.7, 0.07, 7, 2);
    var tx = W * 0.5, ty = H * 0.66, u = H * 0.05;
    g.fillStyle = "#d8cdb5"; g.fillRect(tx - 5 * u, ty, 10 * u, 2.4 * u);
    g.fillStyle = "#c2b697"; g.fillRect(tx - 4 * u, ty - 3 * u, 8 * u, 3 * u);
    g.fillStyle = "#8a4b32";
    g.beginPath(); g.moveTo(tx - 5.2 * u, ty - 3 * u); g.lineTo(tx, ty - 6.2 * u); g.lineTo(tx + 5.2 * u, ty - 3 * u); g.closePath(); g.fill();
    g.fillStyle = "#4b3a2a"; g.fillRect(tx - u * 0.8, ty - 1.6 * u, u * 1.6, 1.6 * u);
    hills("#3f7040", 0.86, 0.05, 11, 1);
    for (var i = 0; i < 9; i++) {
      var x = W * (0.06 + i * 0.115), y = H * (0.8 + (i % 3) * 0.05);
      g.fillStyle = "#5a3b24"; g.fillRect(x - 3, y, 6, H * 0.06);
      g.fillStyle = "#2f6a35"; g.beginPath(); g.arc(x, y, H * 0.045, 0, 6.3); g.fill();
    }
    return c;
  }

  function tileCanvas(pid, st) {
    var c = document.createElement("canvas");
    c.width = c.height = 96;
    var g = c.getContext("2d");
    g.drawImage(scene, (pid % st.cols) * 96, Math.floor(pid / st.cols) * 96, 96, 96, 0, 0, 96, 96);
    g.font = "bold 15px sans-serif"; g.textAlign = "right"; g.textBaseline = "bottom";
    g.lineWidth = 3; g.strokeStyle = "rgba(0,0,0,.65)"; g.fillStyle = "#fff";
    g.strokeText(String(pid + 1), 92, 92); g.fillText(String(pid + 1), 92, 92);
    return c;
  }

  // ---- layout (normalized table coordinates: x over the width, y over the height)
  var table = $("table"), boardBox = null;
  function place(el, x, y, st) {          // x,y = tile centre
    var w = table.clientWidth, h = table.clientHeight, s = st.tile * h;
    el.style.width = el.style.height = s + "px";
    el.style.left = (x * w - s / 2) + "px";
    el.style.top = (y * h - s / 2) + "px";
  }

  function build(st) {
    var key = st.cols + "x" + st.rows + ":" + st.difficulty;
    if (key === sceneKey) return;
    sceneKey = key;
    while (table.firstChild) table.removeChild(table.firstChild);
    tiles = {}; slots = []; popped = {};
    scene = drawScene(st.cols, st.rows);
    boardBox = document.createElement("div");
    boardBox.className = "boardbox";
    table.appendChild(boardBox);
    for (var i = 0; i < st.pieces_total; i++) {
      var s = document.createElement("div");
      s.className = "slot";
      table.appendChild(s);
      slots.push(s);
    }
  }

  function layout(st) {
    var b = st.board, w = table.clientWidth, h = table.clientHeight;
    boardBox.style.left = (b.x * w - 2) + "px";
    boardBox.style.top = (b.y * h - 2) + "px";
    boardBox.style.width = (b.w * w + 4) + "px";
    boardBox.style.height = (b.h * h + 4) + "px";
    var cw = b.w * w / st.cols, ch = b.h * h / st.rows;
    slots.forEach(function (s, i) {
      s.style.left = (b.x * w + (i % st.cols) * cw) + "px";
      s.style.top = (b.y * h + Math.floor(i / st.cols) * ch) + "px";
      s.style.width = cw + "px";
      s.style.height = ch + "px";
    });
  }

  function ensureTile(p, st) {
    var t = tiles[p.id];
    if (t) return t;
    t = document.createElement("div");
    t.className = "tile";
    t.appendChild(tileCanvas(p.id, st));
    var dot = document.createElement("span");
    dot.className = "dot";
    t.appendChild(dot);
    t.addEventListener("pointerdown", function (ev) { startDrag(ev, p.id, t); });
    table.appendChild(t);
    tiles[p.id] = t;
    return t;
  }

  function render(st) {
    build(st);
    layout(st);
    st.pieces.forEach(function (p) {
      if (p.state === "hidden") return;
      var t = ensureTile(p, st);
      t.lastChild.style.setProperty("--dotc", hue(p.by || ""));
      t.title = "Found by " + (p.by || "?");
      var wasPlaced = t.classList.contains("placed");
      if (p.state === "placed") {
        t.classList.add("placed");
        if (!wasPlaced && !popped[p.id]) { t.classList.add("pop"); popped[p.id] = 1; }
      }
      if (dragging && dragging.id === p.id) return;
      place(t, p.x, p.y, st);
    });
    var total = st.pieces_total, placed = st.placed_count, found = st.found_count;
    $("count").textContent = placed + " / " + total + " pieces";
    $("detail").textContent = "found " + found + ", on the table " + (found - placed) + ", still to find " + (total - found);
    $("barFound").style.width = (100 * found / total) + "%";
    $("barPlaced").style.width = (100 * placed / total) + "%";
    $("chance").textContent = st.next_chance_pct == null ? "-" : st.next_chance_pct.toFixed(2) + " %";
    var tg = st.together || { n: 1, bonus_pct: 0 };
    $("together").textContent = tg.n + " here, +" + tg.bonus_pct + " %";
    $("sub").textContent = st.label + " · " + total + " pieces";
    var done = $("done");
    done.hidden = !st.finished;
    if (st.finished) {
      done.textContent = "Complete! Most pieces found: " + st.most_found + ". Last piece placed by " + st.last_placed_by + ".";
    }
    var f = $("finders");
    while (f.firstChild) f.removeChild(f.firstChild);
    Object.keys(st.finders || {}).sort(function (a, b2) { return st.finders[b2] - st.finders[a]; }).forEach(function (n) {
      var span = document.createElement("span");
      var i = document.createElement("i");
      i.style.setProperty("--dotc", hue(n));
      span.appendChild(i);
      span.appendChild(document.createTextNode(n + " " + st.finders[n]));
      f.appendChild(span);
    });
  }

  // ---- dragging (mouse and touch through pointer events)
  function startDrag(ev, id, el) {
    if (el.classList.contains("placed") || dragging || !state) return;
    ev.preventDefault();
    var r = table.getBoundingClientRect();
    var er = el.getBoundingClientRect();
    dragging = { id: id, el: el, dx: ev.clientX - (er.left + er.width / 2), dy: ev.clientY - (er.top + er.height / 2) };
    el.classList.add("drag");
    try { el.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
    function pos(e) {
      var x = Math.min(1, Math.max(0, (e.clientX - dragging.dx - r.left) / r.width));
      var y = Math.min(1, Math.max(0, (e.clientY - dragging.dy - r.top) / r.height));
      dragging.x = x; dragging.y = y;
      place(el, x, y, state);
    }
    function up() {
      el.removeEventListener("pointermove", pos);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      el.classList.remove("drag");
      var d = dragging;
      dragging = null;
      if (d && d.x != null) drop(d);
    }
    el.addEventListener("pointermove", pos);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  }

  function drop(d) {
    busy = true;
    api("POST", "/relic/move", { piece: d.id, x: d.x, y: d.y }).then(function (res) {
      busy = false;
      if (res.placed) { d.el.classList.add("placed", "pop"); popped[d.id] = 1; }
      place(d.el, res.x, res.y, state);
      refresh();
    }, function (e) {
      busy = false;
      if (e.x != null && state) place(d.el, e.x, e.y, state);       // someone else moved it first: snap back
      else if (e.status === 401) needToken("That token does not work here.");
      refresh();
    });
  }

  // ---- polling
  function needToken(text) {
    token = "";
    tryStore(function () { sessionStorage.removeItem(SKEY); });
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    $("game").hidden = true;
    $("tokenForm").hidden = false;
    say(text || "");
  }

  function refresh() {
    if (!token || document.hidden || dragging) return;
    api("GET", "/relic/state").then(function (st) {
      if (dragging) return;
      say("");
      if (!st.pieces_total) {
        $("game").hidden = true;
        say("No relic hunt is running. The owner can start one from the rooms page.");
        return;
      }
      state = st;
      $("game").hidden = false;
      render(st);
    }, function (e) {
      if (e.status === 401) needToken("That token does not work in this room.");
      else if (e.status === 404) { $("game").hidden = true; say("This room has no relic hunt."); }
      else say(e.error || "Could not load the table.");
    });
  }

  $("tokenForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    token = $("tokenInput").value.trim();
    $("tokenInput").value = "";
    if (!token) return;
    tryStore(function () { sessionStorage.setItem(SKEY, token); });
    $("tokenForm").hidden = true;
    start();
  });
  window.addEventListener("resize", function () { if (state) render(state); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });

  function start() {
    say("Loading...");
    refresh();
    if (!pollTimer) pollTimer = setInterval(function () { if (!busy) refresh(); }, 1500);
  }

  if (!rid) { say("This is not a relic table address."); return; }
  token = findToken();
  if (token) start(); else needToken("");
})();
