"use strict";
// Public rooms page. Plain JS; server values only go in through textContent / DOM APIs.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var STORE = "relayRoomsMine";

  function h(tag, props) {
    var el = document.createElement(tag);
    for (var k in (props || {})) {
      var v = props[k];
      if (k === "class") el.className = v;
      else if (k.indexOf("on") === 0) el.addEventListener(k.slice(2), v);
      else if (v != null && v !== false) el.setAttribute(k, v);
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (Array.isArray(c)) c.forEach(function (x) { add(el, x); });
    else if (c != null && c !== false) el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function showError(msg) { var e = $("err"); e.textContent = msg || ""; e.hidden = !msg; }

  function api(method, path, body, headers) {
    var opts = { method: method, headers: headers || {} };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) {
        if (r.ok) return d;
        return Promise.reject({ error: d.error || ("error " + r.status), status: r.status });
      }, function () { return Promise.reject({ error: "unexpected reply" }); });
    }, function () { return Promise.reject({ error: "cannot reach the server" }); });
  }

  // ---- rooms kept in this browser (only if storage works)
  function loadMine() {
    try { var l = JSON.parse(localStorage.getItem(STORE) || "[]"); return Array.isArray(l) ? l : []; }
    catch (e) { return []; }
  }
  function saveMine(list) {
    try { localStorage.setItem(STORE, JSON.stringify(list.slice(0, 10))); } catch (e) { /* storage blocked */ }
  }
  function copy(text) {
    if (navigator.clipboard) navigator.clipboard.writeText(text).catch(function () {});
  }
  function copyBtn(text) { return h("button", { type: "button", onclick: function () { copy(text); } }, "Copy"); }

  function findMine(id) {
    var l = loadMine().filter(function (x) { return x.id === id; });
    return l.length ? l[0] : null;
  }
  function rememberRoom(rec) {
    var list = loadMine().filter(function (x) { return x.id !== rec.id; });
    list.unshift({ id: rec.id, name: rec.name, token: rec.token });
    saveMine(list);
  }
  function forgetRoom(id) {
    saveMine(loadMine().filter(function (x) { return x.id !== id; }));
    if (managed && managed.id === id) closeManage();
    renderMine();
    renderLobby();
  }

  function renderMine() {
    var list = loadMine(), box = $("mineList");
    clear(box);
    $("mine").hidden = !list.length;
    list.forEach(function (r) {
      box.appendChild(h("div", { class: "item" },
        h("b", {}, r.name), " ", h("code", {}, r.id),
        h("div", { class: "muted" }, "Owner token:"),
        h("code", { class: "keybox" }, r.token),
        h("div", { class: "row" }, copyBtn(r.token),
          h("button", { type: "button", class: "primary", onclick: function () { openManage(r); } }, "Manage"),
          h("button", { type: "button", class: "danger", onclick: function () {
            if (!window.confirm("Forget this room on this device? The token is lost unless you saved it.")) return;
            forgetRoom(r.id);
          } }, "Forget"))));
    });
  }

  // ---- create form: collapsed into one bar
  function setOpen(open) {
    $("createForm").hidden = !open;
    $("createToggle").setAttribute("aria-expanded", open ? "true" : "false");
    $("createToggle").hidden = open;
    if (open) $("name").focus();
  }
  $("createToggle").addEventListener("click", function () { setOpen(true); });
  $("createCancel").addEventListener("click", function () { showError(""); setOpen(false); });
  function syncKind() {            // a party room holds 1-6 people; a hangout has no limit
    var m = $("max");
    if ($("kind").value === "party") {
      m.setAttribute("min", "1"); m.setAttribute("max", "6"); m.setAttribute("placeholder", "6");
      $("maxHelp").textContent = "Party: 1 to 6 people (empty = 6).";
    } else {
      m.setAttribute("min", "0"); m.removeAttribute("max"); m.setAttribute("placeholder", "no limit");
      $("maxHelp").textContent = "Hangout: leave empty or 0 for no limit.";
    }
  }
  $("kind").addEventListener("change", syncKind);
  syncKind();

  // ---- create
  function accessText(access) {
    if (access === "approval") return "You accept each person who asks to join.";
    if (access === "unlisted") return "The room is hidden from the lobby, so only people you tell can find it.";
    if (access === "private") return "It is listed in the lobby, but joining needs the PIN or password.";
    return "Anyone who has the room id can join.";
  }
  $("createForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    showError("");
    var b = { name: $("name").value, owner: $("owner").value, kind: $("kind").value, access: $("access").value };
    var secret = $("secret").value;
    if (secret) b.secret = secret;
    if ($("max").value !== "") b.max_members = parseInt($("max").value, 10);
    if (b.kind === "party" && b.max_members !== undefined && (b.max_members < 1 || b.max_members > 6)) {
      showError("a party room holds up to 6 people");
      return;
    }
    var headers = {}, key = $("hostkey").value.trim();
    if (key) headers["X-Host-Key"] = key;
    api("POST", "/v2/rooms", b, headers).then(function (r) {
      var res = $("result");
      clear(res);
      res.appendChild(h("div", { class: "result" },
        h("b", {}, "Room created"),
        h("p", {}, r.room.name + " (" + r.room.kind + ", " + r.room.access + ")"),
        h("div", {}, "Room id: ", h("code", {}, r.id), " ", copyBtn(r.id)),
        h("p", {}, "Friends join with the room id" + (secret ? " and the PIN or password you set (" + secret + "). " : ". ") +
          accessText(r.room.access)),
        h("b", {}, "Owner token: save this, it is shown only once"),
        h("code", { class: "keybox" }, r.token),
        h("div", { class: "row" }, copyBtn(r.token)),
        h("p", { class: "muted" }, "The owner token is your key to manage the room from the app. This page also keeps it in this browser until you press Forget.")));
      rememberRoom({ id: r.id, name: r.room.name, token: r.token });
      renderMine();
      $("name").value = ""; $("secret").value = ""; $("max").value = "";
      setOpen(false);
      loadLobby();
    }, function (e) {
      showError(e.error);
      checkOpen();
    });
  });

  // ---- lobby cards
  var SVG = "http://www.w3.org/2000/svg";
  function icon(paths) {
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    paths.forEach(function (d) {
      var p = document.createElementNS(SVG, "path");
      p.setAttribute("d", d);
      svg.appendChild(p);
    });
    return svg;
  }
  var ICONS = {
    cup: ["M2.5 5h8v3.5a4 4 0 0 1-8 0z", "M10.5 6h1.5a1.5 1.5 0 0 1 0 3h-1.5", "M3 14h7"],
    sword: ["M13.5 2.5L6 10", "M10 2.5h3.5V6", "M4.5 8.5l3 3", "M5.5 10.5L3 13"],
    lockOpen: ["M3.5 7.5h9v6h-9z", "M5.5 7.5v-2a2.5 2.5 0 0 1 4.9-.7"],
    lock: ["M3.5 7.5h9v6h-9z", "M5.5 7.5v-2a2.5 2.5 0 0 1 5 0v2"],
    userCheck: ["M6.5 7a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z", "M1.5 14c0-2.5 2.2-4 5-4s5 1.5 5 4", "M11.5 6.5l1.5 1.5 2.5-3"]
  };
  var ACCESS_VIEW = {
    open: ["lockOpen", "Open"], approval: ["userCheck", "Approval"],
    private: ["lock", "PIN"], unlisted: ["lock", "PIN"]
  };
  function roomCard(r) {
    var party = r.kind === "party";
    var count = party
      ? (r.max_members ? r.members + "/" + r.max_members : r.members + " in")
      : (r.online != null ? r.online : 0) + " online";
    var meta = r.activity ? count + " · " + r.activity : count;
    var av = ACCESS_VIEW[r.access] || ACCESS_VIEW.open;
    var mine = findMine(r.id);
    return h("div", { class: "roomcard " + (party ? "party" : "hangout") },
      h("div", { class: "top" },
        h("div", { class: "rname" }, r.name),
        h("span", { class: "badge access" }, icon(ICONS[av[0]]), av[1])),
      h("span", { class: "badge kind" }, icon(party ? ICONS.sword : ICONS.cup), party ? "Party" : "Hangout"),
      h("div", { class: "meta" }, meta),
      h("div", { class: "idline" }, h("code", {}, r.id)),
      mine
        ? h("button", { type: "button", class: "full primary", onclick: function () { openManage(mine); } }, "Manage")
        : h("button", { type: "button", class: "full", title: "Joining happens in the app",
            onclick: function () { copy(r.id); } }, "Copy id"));
  }

  // ---- lobby
  var lobbyData = null;
  function renderLobby() {
    var box = $("lobby");
    if (!lobbyData) return;
    clear(box);
    if (!lobbyData.enabled) { box.textContent = "Lobby is off on this server"; return; }
    if (!lobbyData.rooms.length) { box.textContent = "No rooms listed right now."; return; }
    var grid = h("div", { class: "grid" });
    lobbyData.rooms.forEach(function (r) { grid.appendChild(roomCard(r)); });
    box.appendChild(grid);
  }
  function loadLobby() {
    api("GET", "/v2/lobby").then(function (d) { lobbyData = d; renderLobby(); },
      function (e) { lobbyData = null; $("lobby").textContent = e.error; });
  }
  $("reload").addEventListener("click", loadLobby);

  // ---- is creating open? (/health says; a host key is the way in when it is not)
  function checkOpen() {
    fetch("/health").then(function (r) { return r.json(); }).then(function (d) {
      $("closed").hidden = d.create_open !== false;
    }).catch(function () {});
  }


  // ---- manage a room (owner token kept in this browser)
  var managed = null, pollTimer = null, mFetchedAt = 0, mBusy = false;
  function mError(msg) { var e = $("mErr"); e.textContent = msg || ""; e.hidden = !msg; }
  function authed(method, path, body) {
    return api(method, "/v2/rooms/" + managed.id + path, body, { Authorization: "Bearer " + managed.token });
  }
  function openManage(rec) {
    closeManage();
    managed = { id: rec.id, name: rec.name, token: rec.token };
    $("manage").hidden = false;
    $("mBad").hidden = true;
    $("mBody").hidden = false;
    mError("");
    $("mTitle").textContent = "Manage: " + rec.name;
    ["mRequests", "mMembers", "mActivity", "mInfo"].forEach(function (id) { clear($(id)); });
    $("mInfo").appendChild(h("span", { class: "muted" }, "Room id "));
    $("mInfo").appendChild(h("code", {}, rec.id));
    $("manage").scrollIntoView();
    refreshManage();
    pollTimer = setInterval(refreshManage, 4000);
  }
  function closeManage() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    managed = null;
    $("manage").hidden = true;
  }
  function refreshManage() {
    if (!managed || mBusy) return;
    var id = managed.id;
    authed("GET", "").then(function (d) {
      if (!managed || managed.id !== id) return;
      mFetchedAt = Date.now();
      managed.name = d.name;
      $("mTitle").textContent = "Manage: " + d.name;
      var rec = findMine(id);
      if (rec && rec.name !== d.name) rememberRoom({ id: id, name: d.name, token: rec.token });
      mError("");
      renderManage(d);
    }, function (e) {
      if (!managed || managed.id !== id) return;
      if (e.status === 401) {
        $("mBody").hidden = true;
        $("mBad").hidden = false;
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = null;
      } else {
        mError(e.error);
      }
    });
  }
  function act(path, body) {
    mError("");
    mBusy = true;
    authed("POST", path, body).then(function () { mBusy = false; refreshManage(); },
      function (e) { mBusy = false; mError(e.error); refreshManage(); });
  }
  function memberBtn(name, action, label, cls, confirmText) {
    return h("button", { type: "button", class: cls || "", onclick: function () {
      if (confirmText && !window.confirm(confirmText)) return;
      act("/members/" + encodeURIComponent(name) + "/" + action, {});
    } }, label);
  }
  function mmss(sec) {
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ":" + (sec % 60 < 10 ? "0" : "") + (sec % 60);
  }
  function renderManage(d) {
    var req = $("mRequests"), mem = $("mMembers");
    clear(req); clear(mem);
    var requests = d.requests || [];
    if (!requests.length) req.appendChild(h("p", { class: "muted" }, "Nobody is waiting."));
    requests.forEach(function (n) {
      req.appendChild(h("div", { class: "mrow" }, h("span", { class: "grow" }, n),
        memberBtn(n, "approve", "Approve", "primary"), memberBtn(n, "deny", "Deny", "danger")));
    });
    (d.members || []).forEach(function (m) {
      var status = m.online ? (m.state || "online") : "offline";
      mem.appendChild(h("div", { class: "mrow" },
        h("span", { class: "grow" }, h("span", { class: "dot" + (m.online ? " on" : "") }), m.name,
          m.role !== "member" ? " (" + m.role + ")" : "", " ", h("span", { class: "muted" }, status)),
        m.role === "member"
          ? memberBtn(m.name, "kick", "Kick", "danger", "Remove " + m.name + " from the room?") : null));
    });
    renderActivity(d);
  }
  function renderActivity(d) {
    var race = (d.activities || {}).race, a = d.activity;
    var sel = $("mMinutes");
    if (race && sel.options.length !== race.minutes.length) {
      clear(sel);
      race.minutes.forEach(function (m) { sel.appendChild(h("option", { value: m }, m + " min")); });
    }
    var open = !!a && (a.state === "open" || a.state === "running");
    $("mControls").hidden = !race;
    $("mControls").querySelector("label").hidden = open;
    $("mStart").hidden = open;
    $("mStop").hidden = !open;
    $("mHint").hidden = open || !race;
    var box = $("mActivity");
    clear(box);
    if (!a) { box.appendChild(h("p", { class: "muted" }, "Nothing running.")); return; }
    var now = (a.server_t || 0) + (Date.now() - mFetchedAt) / 1000;
    var accepted = a.players.filter(function (p) { return p.accepted; });
    var head, sub = null;
    if (a.state === "open") {
      head = "Kill race starting: " + accepted.length + " in, " + a.invited.length + " still to answer";
      sub = "Starts when everyone has answered, or at the latest in " + mmss((a.decide_t || 0) - now) +
        " (needs 2 players).";
    } else if (a.state === "running") {
      head = now < a.start_t ? "Kill race: starting in " + Math.ceil(a.start_t - now) + " s"
        : "Kill race: " + mmss(a.end_t - now) + " left";
    } else if (a.state === "done") {
      var r = a.result || {};
      head = "Kill race finished: " + (r.tie ? "a tie" : "winner " + r.winner);
    } else {
      head = "Kill race " + a.state + ".";
    }
    var live = h("div", { class: "live" }, h("b", {}, head));
    if (sub) live.appendChild(h("div", { class: "muted" }, sub));
    a.players.slice().sort(function (x, y) { return y.score - x.score; }).forEach(function (p) {
      live.appendChild(h("div", { class: "score" }, h("span", {}, p.name), h("span", {}, String(p.score))));
    });
    a.invited.forEach(function (n) {
      live.appendChild(h("div", { class: "score muted" }, h("span", {}, n), h("span", {}, "waiting")));
    });
    box.appendChild(live);
  }
  $("mStart").addEventListener("click", function () {
    if (managed) act("/challenge/start", { kind: "race", minutes: parseInt($("mMinutes").value, 10) });
  });
  $("mStop").addEventListener("click", function () {
    if (managed && window.confirm("Stop the race now?")) act("/challenge/stop", {});
  });
  $("mClose").addEventListener("click", closeManage);
  $("mForget").addEventListener("click", function () { if (managed) forgetRoom(managed.id); });

  // ---- "Manage a room": paste a room id + owner token (e.g. a room made on the admin page)
  function setAddOpen(open) {
    $("addForm").hidden = !open;
    $("addToggle").setAttribute("aria-expanded", open ? "true" : "false");
    $("addToggle").hidden = open;
    $("addErr").hidden = true;
    if (open) $("addId").focus();
  }
  $("addToggle").addEventListener("click", function () { setAddOpen(true); });
  $("addCancel").addEventListener("click", function () { setAddOpen(false); });
  $("addForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var id = $("addId").value.trim().toLowerCase(), token = $("addToken").value.trim();
    var fail = function (msg) { $("addErr").textContent = msg; $("addErr").hidden = false; };
    if (!/^[a-z0-9]{4}-[a-z0-9]{4}$/.test(id)) return fail("A room id looks like abcd-2345.");
    api("GET", "/v2/rooms/" + id, null, { Authorization: "Bearer " + token }).then(function (d) {
      if (!d.requests) return fail("That token is not an owner or mod token.");
      rememberRoom({ id: id, name: d.name, token: token });
      $("addToken").value = ""; $("addId").value = "";
      setAddOpen(false);
      renderMine();
      renderLobby();
      openManage(findMine(id));
    }, function (e) { fail(e.status === 401 ? "That room id and token do not match." : e.error); });
  });

  renderMine();
  checkOpen();
  loadLobby();
})();
