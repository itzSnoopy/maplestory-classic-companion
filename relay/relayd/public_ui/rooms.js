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
      loadLobby();
    }, function (e) {
      showError(e.error);
      checkOpen();
    });
  });

  // ---- lobby
  function loadLobby() {
    var box = $("lobby");
    api("GET", "/v2/lobby").then(function (d) {
      clear(box);
      if (!d.enabled) { box.textContent = "Lobby is off on this server"; return; }
      if (!d.rooms.length) { box.textContent = "No rooms listed right now."; return; }
      d.rooms.forEach(function (r) {
        var line = r.kind + ", " + r.access + ", " + r.members + (r.members === 1 ? " member" : " members") +
          (r.online != null ? ", " + r.online + " online" : "");
        box.appendChild(h("div", { class: "item" }, h("b", {}, r.name), " ", h("code", {}, r.id), h("div", { class: "muted" }, line)));
      });
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
