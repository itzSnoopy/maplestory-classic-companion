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
          h("button", { type: "button", class: "danger", onclick: function () {
            if (!window.confirm("Forget this room on this device? The token is lost unless you saved it.")) return;
            saveMine(loadMine().filter(function (x) { return x.id !== r.id; }));
            renderMine();
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
      var list = loadMine().filter(function (x) { return x.id !== r.id; });
      list.unshift({ id: r.id, name: r.room.name, token: r.token });
      saveMine(list);
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
  function icon(path) {
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(SVG, "path");
    p.setAttribute("d", path);
    svg.appendChild(p);
    return svg;
  }
  var CUP = "M2 4h9v5a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V4zm9 1h1.5a1.5 1.5 0 0 1 0 3H11V5zM3 14h8v1H3z";
  var DIAMOND = "M8 1l6 7-6 7-6-7z";
  var ACCESS_TEXT = { open: "Open", approval: "Approval", private: "PIN (private)", unlisted: "PIN (private)" };
  function roomCard(r) {
    var party = r.kind === "party";
    var count = party
      ? (r.max_members ? r.members + "/" + r.max_members : r.members + " in")
      : (r.online != null ? r.online : 0) + " online";
    var meta = r.activity ? count + " \u00b7 " + r.activity : count;
    return h("div", { class: "roomcard " + (party ? "party" : "hangout") },
      h("div", { class: "top" },
        h("span", { class: "badge kind" }, icon(party ? DIAMOND : CUP), party ? "Party" : "Hangout"),
        h("span", { class: "badge access" }, ACCESS_TEXT[r.access] || r.access)),
      h("div", { class: "rname" }, r.name),
      h("div", { class: "meta" }, meta),
      h("div", { class: "meta" }, h("code", {}, r.id)));
  }

  // ---- lobby
  function loadLobby() {
    var box = $("lobby");
    api("GET", "/v2/lobby").then(function (d) {
      clear(box);
      if (!d.enabled) { box.textContent = "Lobby is off on this server"; return; }
      if (!d.rooms.length) { box.textContent = "No rooms listed right now."; return; }
      var grid = h("div", { class: "grid" });
      d.rooms.forEach(function (r) { grid.appendChild(roomCard(r)); });
      box.appendChild(grid);
    }, function (e) { box.textContent = e.error; });
  }
  $("reload").addEventListener("click", loadLobby);

  // ---- is creating open? (/health says; a host key is the way in when it is not)
  function checkOpen() {
    fetch("/health").then(function (r) { return r.json(); }).then(function (d) {
      $("closed").hidden = d.create_open !== false;
    }).catch(function () {});
  }

  renderMine();
  checkOpen();
  loadLobby();
})();
