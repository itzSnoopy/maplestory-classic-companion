"use strict";
// Relay admin page. Plain JS; server values only go in through textContent / DOM APIs.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var viewEl = $("view");
  var current = "overview";

  // ---- tiny DOM helper: h("div", {class: "x", onclick: fn}, child, "text")
  function h(tag, props) {
    var el = document.createElement(tag);
    for (var k in (props || {})) {
      var v = props[k];
      if (k === "class") el.className = v;
      else if (k.indexOf("on") === 0) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else if (v !== false && v != null) el.setAttribute(k, v);
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (Array.isArray(c)) c.forEach(function (x) { add(el, x); });
    else if (c != null && c !== false) el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function btn(label, fn, cls) { return h("button", { type: "button", class: cls || "", onclick: fn }, label); }
  function when(t) { return t ? new Date(t * 1000).toLocaleString() : "-"; }
  function show(el, on) { el.hidden = !on; }

  function showError(msg) {
    var e = $("err");
    e.textContent = msg || "";
    show(e, !!msg);
  }

  // ---- API
  function api(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (method !== "GET") {
      opts.headers["X-Relay-Admin"] = "1";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body || {});
    }
    return fetch("/v2/admin/" + path, opts).then(function (r) {
      var ct = r.headers.get("Content-Type") || "";
      if (ct.indexOf("application/json") < 0) return r.ok ? r : Promise.reject({ error: "unexpected reply" });
      return r.json().then(function (d) {
        if (r.ok) return d;
        if (r.status === 401 && path !== "login") showLogin();
        return Promise.reject({ error: d.error || ("error " + r.status), status: r.status });
      });
    }, function () { return Promise.reject({ error: "cannot reach the server" }); });
  }
  // run a call, refresh the current view on success, show the error otherwise
  function act(method, path, body, after) {
    showError("");
    return api(method, path, body).then(function (d) {
      if (after) after(d); else render();
    }, function (e) { showError(e.error); });
  }
  function id(x) { return encodeURIComponent(x); }

  // ---- login / shell
  function showLogin() {
    show($("login"), true); show($("app"), false); show($("bar"), false);
  }
  function showApp(via) {
    show($("login"), false); show($("app"), true); show($("bar"), true);
    $("via").textContent = via === "proxy" ? "via https proxy" : "via LAN";
    render();
  }
  $("loginForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    showError("");
    api("POST", "login", { pass: $("pass").value }).then(function () {
      $("pass").value = "";
      return api("GET", "me");
    }).then(function (me) { showApp(me.via); }, function (e) { showError(e.error); });
  });
  $("logout").addEventListener("click", function () {
    api("POST", "logout").then(function () { showError(""); showLogin(); }, function (e) { showError(e.error); });
  });
  Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) {
    b.addEventListener("click", function () { current = b.getAttribute("data-tab"); showError(""); render(); });
  });

  function render() {
    Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) {
      b.className = b.getAttribute("data-tab") === current ? "on" : "";
    });
    var fn = { overview: pageOverview, rooms: pageRooms, hostkeys: pageHostKeys,
      accounts: pageAccounts, feedback: pageFeedback, audit: pageAudit }[current];
    clear(viewEl);
    fn();
  }

  function table(heads, rows, onclick) {
    var t = h("table", {}, h("thead", {}, h("tr", {}, heads.map(function (x) { return h("th", {}, x); }))));
    var tb = h("tbody");
    rows.forEach(function (r) {
      var tr = h("tr", onclick ? { class: "click", onclick: function () { onclick(r); } } : {},
        r.cells.map(function (c) { return h("td", {}, c); }));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    return h("div", { class: "scroll" }, t);
  }
  function confirmThen(msg, fn) { if (window.confirm(msg)) fn(); }

  // ---- Overview
  function pageOverview() {
    api("GET", "status").then(function (s) {
      var c = s.counts;
      var cards = [["Rooms", c.rooms], ["Members", c.members], ["Online now", c.online],
        ["Accounts", c.accounts], ["Host keys active", c.host_keys_active], ["Feedback items", c.feedback_items]];
      var up = Math.floor(s.uptime_s / 60);
      clear(viewEl);
      add(viewEl, h("div", { class: "cards" }, cards.map(function (x) {
        return h("div", { class: "card" }, h("span", { class: "muted" }, x[0]), h("b", {}, x[1]));
      })));
      add(viewEl, h("p", { class: "muted" }, "Version " + s.version + ", up " + Math.floor(up / 60) + " h " + (up % 60) + " min. " +
        "Old /v1 party endpoints: " + (s.legacy_v1 ? "on" : "off") + ". Feedback inbox: " + (s.feedback_enabled ? "on" : "off") + "."));
      var p = h("div", { class: "panel" }, h("h2", {}, "Switches"));
      [["lobby", "Public lobby list"], ["allow_anyone_create", "Anyone can create rooms (no host key)"],
       ["secure_tokens", "Long random tokens for new owner tokens and host keys (turn on if short ones are abused)"]].forEach(function (k) {
        var st = s.settings[k[0]];
        var cb = h("input", { type: "checkbox", disabled: st.locked_by_env });
        cb.checked = st.value;
        cb.addEventListener("change", function () {
          var b = {}; b[k[0]] = cb.checked; act("PATCH", "settings", b);
        });
        p.appendChild(h("div", { class: "row" }, h("label", { class: "inline" }, cb, k[1]),
          st.locked_by_env ? h("span", { class: "muted" }, "set by env") : null));
      });
      viewEl.appendChild(p);
      viewEl.appendChild(h("div", { class: "panel" }, h("h2", {}, "Backup"),
        h("p", { class: "muted" }, "A consistent copy of the whole database."),
        btn("Download backup", downloadBackup, "primary")));
    }, function (e) { showError(e.error); });
  }
  function downloadBackup() {
    showError("");
    fetch("/v2/admin/backup", { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) return r.json().then(function (d) { throw { error: d.error || "backup failed" }; });
      var cd = r.headers.get("Content-Disposition") || "";
      var m = /filename="([^"]+)"/.exec(cd);
      return r.blob().then(function (b) { return [b, m ? m[1] : "relay.db"]; });
    }).then(function (x) {
      var a = h("a", { href: URL.createObjectURL(x[0]), download: x[1] });
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    }, function (e) { showError(e.error || "backup failed"); });
  }

  // ---- Rooms
  function pageRooms() {
    var listBox = h("div");
    var shown = h("div");
    function loadList() {                 // refresh only the list, so a just-shown owner token stays visible
      api("GET", "rooms").then(function (d) {
        clear(listBox);
        add(listBox, h("p", { class: "muted" }, d.rooms.length + " rooms (unlisted ones included). Click one to manage it."));
        add(listBox, table(["Id", "Name", "Kind", "Access", "Members", "Online", "Owner"],
          d.rooms.map(function (r) {
            return { id: r.id, cells: [r.id, r.name, r.kind, r.access + (r.has_secret ? " (secret)" : ""),
              r.members + (r.pending ? " +" + r.pending + " asking" : ""), r.online, r.owner_name || "-"] };
          }), function (r) { pageRoom(r.id); }));
      }, function (e) { showError(e.error); });
    }
    clear(viewEl);
    var name = h("input", { type: "text", maxlength: "40" });
    var owner = h("input", { type: "text", maxlength: "16", autocomplete: "off" });
    var kind = h("select", {}, ["hangout", "party"].map(function (a) { return h("option", { value: a }, a); }));
    var access = h("select", {}, ["open", "approval", "private", "unlisted"].map(function (a) { return h("option", { value: a }, a); }));
    var secret = h("input", { type: "text", autocomplete: "off", placeholder: "needed for private and unlisted" });
    var max = h("input", { type: "number", min: "0", placeholder: "default (party 6, hangout none)" });
    kind.addEventListener("change", function () {       // a party room holds 1-6 people
      if (kind.value === "party") { max.setAttribute("max", "6"); max.setAttribute("min", "1"); }
      else { max.removeAttribute("max"); max.setAttribute("min", "0"); }
    });
    // collapsed into one bar, like /rooms: the form opens on "Create room"
    var form = h("div", { hidden: true });
    var openBtn = btn("Create room", function () { setOpen(true); }, "primary");
    function setOpen(open) {
      form.hidden = !open; openBtn.hidden = open;
      if (open) name.focus(); else showError("");
    }
    add(viewEl, h("div", { class: "panel" },
      h("div", { class: "bar" }, h("h2", {}, "Create room"), openBtn), form, shown));
    add(form, [
      h("label", {}, "Room name", name),
      h("label", {}, "Owner name (the owner's name in the room, letters and digits, up to 16)", owner),
      h("label", {}, "Kind", kind), h("label", {}, "Access", access),
      h("label", {}, "PIN (4-12 digits) or password (8+ characters)", secret),
      h("label", {}, "Max members (hangout: 0 = no limit; party: 1-6)", max),
      btn("Create", function () {
        var b = { name: name.value, owner: owner.value, kind: kind.value, access: access.value };
        if (secret.value) b.secret = secret.value;
        if (max.value !== "") b.max_members = parseInt(max.value, 10);
        showError("");
        api("POST", "rooms", b).then(function (r) {
          clear(shown);
          var code = h("code", { class: "keybox" }, r.token);
          shown.appendChild(h("div", { class: "panel" },
            h("b", {}, "Room created: " + r.id),
            h("p", {}, r.room.name + ", " + r.room.kind + ", " + r.room.access +
              (secret.value ? ", secret: " + secret.value : ", no secret") +
              ", max " + (r.room.max_members || "no limit") + ". Friends join with the room id" +
              (secret.value ? " and the secret." : ".")),
            h("b", {}, "Owner token (it won't be shown again)"), code,
            h("p", { class: "muted" }, "The owner token is the owner's key to manage this room from the app later. Save it now."),
            btn("Copy", function () {
              if (navigator.clipboard) navigator.clipboard.writeText(r.token).catch(function () {});
              var rng = document.createRange(); rng.selectNodeContents(code);
              var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(rng);
            })));
          name.value = ""; owner.value = ""; secret.value = ""; max.value = "";
          setOpen(false);
          loadList();
        }, function (e) { showError(e.error); });
      }, "primary"), " ", btn("Close", function () { setOpen(false); })]);
    add(viewEl, listBox);
    loadList();
  }

  var MODULES = ["presence", "challenge", "relic"];
  function pageRoom(rid) {
    showError("");
    api("GET", "rooms/" + id(rid)).then(function (r) {
      clear(viewEl);
      add(viewEl, btn("< All rooms", function () { render(); }));
      add(viewEl, h("h2", {}, r.name + " (" + r.id + ")"));
      add(viewEl, h("p", { class: "muted" }, r.kind + ", " + r.access + (r.has_secret ? ", has secret" : "") +
        ", max " + (r.max_members || "no limit") + ", modules: " + (r.modules.join(", ") || "none") +
        ". Created " + when(r.created_t) + ", last active " + when(r.last_active_t) +
        ". Failed joins: " + r.failed_joins + ". Owner account: " + (r.owner_name || "-") + " (#" + r.owner_account + ")."));
      add(viewEl, h("pre", {}, JSON.stringify(r.module_views, null, 2)));

      var act1 = function (name, action) {
        return function () { act("POST", "rooms/" + id(rid) + "/members/" + id(name) + "/" + action, {}, function () { pageRoom(rid); }); };
      };
      var rows = r.members_list.map(function (m) {
        var b = [];
        if (m.status === "pending") b.push(btn("Approve", act1(m.name, "approve")), btn("Deny", act1(m.name, "deny")));
        if (m.role !== "owner") {
          if (m.status === "active") b.push(btn("Make owner", function () {
            confirmThen("Make " + m.name + " the owner? The current owner becomes a mod.", act1(m.name, "make_owner"));
          }));
          if (m.role === "member" && m.status === "active") b.push(btn("Promote", act1(m.name, "promote")));
          if (m.role === "mod") b.push(btn("Demote", act1(m.name, "demote")));
          b.push(btn("Kick", act1(m.name, "kick")), btn("Ban", act1(m.name, "ban"), "danger"));
        }
        return { cells: [m.name, m.role, m.status, when(m.joined_t), when(m.last_seen_t), h("div", { class: "row" }, b)] };
      });
      add(viewEl, h("h2", {}, "Members"));
      add(viewEl, table(["Name", "Role", "Status", "Joined", "Last seen", "Actions"], rows));
      var unban = h("input", { type: "text", placeholder: "name", maxlength: "16" });
      add(viewEl, h("div", { class: "row" }, unban, btn("Unban name", function () {
        if (unban.value) act("POST", "rooms/" + id(rid) + "/members/" + id(unban.value) + "/unban", {}, function () { pageRoom(rid); });
      })));

      // edit form
      var name = h("input", { type: "text", maxlength: "40", value: r.name });
      var access = h("select", {}, ["open", "approval", "private", "unlisted"].map(function (a) { return h("option", { value: a }, a); }));
      access.value = r.access;
      var kind = h("select", {}, ["party", "hangout"].map(function (a) { return h("option", { value: a }, a); }));
      kind.value = r.kind;
      var secret = h("input", { type: "text", placeholder: "leave empty to keep", autocomplete: "off" });
      var clearSecret = h("input", { type: "checkbox" });
      var max = h("input", { type: "number", min: "0", value: r.max_members });
      var mods = MODULES.map(function (m) {
        var cb = h("input", { type: "checkbox" });
        cb.checked = r.modules.indexOf(m) >= 0;
        return { name: m, cb: cb };
      });
      var form = h("div", { class: "panel" }, h("h2", {}, "Edit"),
        h("label", {}, "Name", name), h("label", {}, "Kind", kind), h("label", {}, "Access", access),
        h("label", {}, "New secret (4-12 digits or 8+ characters)", secret),
        h("label", { class: "inline" }, clearSecret, "Remove the secret"),
        h("label", {}, "Max members (0 = no limit)", max),
        h("div", {}, mods.map(function (m) { return h("label", { class: "inline" }, m.cb, m.name); })),
        btn("Save", function () {
          var b = { name: name.value, kind: kind.value, access: access.value, max_members: parseInt(max.value || "0", 10),
            modules: mods.filter(function (m) { return m.cb.checked; }).map(function (m) { return m.name; }) };
          if (clearSecret.checked) b.secret = null; else if (secret.value) b.secret = secret.value;
          act("PATCH", "rooms/" + id(rid), b, function () { pageRoom(rid); });
        }, "primary"));
      add(viewEl, form);
      add(viewEl, h("div", { class: "panel" }, btn("Delete this room", function () {
        confirmThen("Delete room " + r.name + " (" + r.id + ") and all its members? This cannot be undone.",
          function () { act("DELETE", "rooms/" + id(rid), {}, function () { render(); }); });
      }, "danger")));
    }, function (e) { showError(e.error); });
  }

  // ---- Host keys
  function pageHostKeys() {
    var listBox = h("div");
    function loadList() {                 // refresh only the list, so a just-shown key stays visible
      api("GET", "hostkeys").then(function (d) {
        clear(listBox);
        listBox.appendChild(keyTable(d.hostkeys));
      }, function (e) { showError(e.error); });
    }
    (function () {
      clear(viewEl);
      var label = h("input", { type: "text", maxlength: "40" });
      var max = h("input", { type: "number", min: "1", max: "100", value: "3" });
      var days = h("input", { type: "number", min: "1", max: "3650", placeholder: "never" });
      var shown = h("div");
      // collapsed into one bar, like Create room: the form opens on "New host key"
      var form = h("div", { hidden: true });
      var openBtn = btn("New host key", function () { setOpen(true); }, "primary");
      function setOpen(open) {
        form.hidden = !open; openBtn.hidden = open;
        if (open) label.focus(); else showError("");
      }
      add(viewEl, h("div", { class: "panel" },
        h("div", { class: "bar" }, h("h2", {}, "New host key"), openBtn), form, shown));
      add(form, [
        h("label", {}, "Label (who it is for)", label), h("label", {}, "Max rooms", max),
        h("label", {}, "Expires after days (optional)", days),
        btn("Create", function () {
          var b = { label: label.value, max_rooms: parseInt(max.value, 10) };
          if (days.value) b.expires_days = parseInt(days.value, 10);
          showError("");
          api("POST", "hostkeys", b).then(function (r) {
            clear(shown);
            var code = h("code", { class: "keybox" }, r.key);
            shown.appendChild(h("div", { class: "panel" }, h("b", {}, "New key (it won't be shown again)"), code,
              btn("Copy", function () {
                if (navigator.clipboard) navigator.clipboard.writeText(r.key).catch(function () {});
                var rng = document.createRange(); rng.selectNodeContents(code);
                var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(rng);
              })));
            label.value = "";
            setOpen(false);
            loadList();
          }, function (e) { showError(e.error); });
        }, "primary"), " ", btn("Close", function () { setOpen(false); })]);
      add(viewEl, listBox);
      loadList();
    })();
    function keyTable(keys) {
      return table(["Id", "Label", "Max rooms", "Rooms owned", "Expires", "Created", "State", ""],
        keys.map(function (k) {
          return { cells: [k.id, k.label, k.max_rooms, k.rooms_owned, k.expires_t ? when(k.expires_t) : "never", when(k.created_t),
            k.revoked ? "revoked" : "active",
            k.revoked ? "" : btn("Revoke", function () {
              confirmThen("Revoke host key '" + k.label + "'? It stops working for new rooms.",
                function () { act("POST", "hostkeys/" + k.id + "/revoke", {}, loadList); });
            }, "danger")] };
        }));
    }
  }

  // ---- Accounts
  function pageAccounts() {
    api("GET", "accounts").then(function (d) {
      clear(viewEl);
      add(viewEl, h("p", { class: "muted" }, "A disabled account cannot create rooms; its existing rooms keep working."));
      add(viewEl, table(["Id", "Name", "Providers", "Rooms owned", "Created", "State", ""],
        d.accounts.map(function (a) {
          return { cells: [a.id, a.display_name, a.providers.join(", "), a.rooms_owned, when(a.created_t),
            a.disabled ? "disabled" : "active",
            btn(a.disabled ? "Enable" : "Disable", function () {
              act("POST", "accounts/" + a.id + "/" + (a.disabled ? "enable" : "disable"), {});
            }, a.disabled ? "" : "danger")] };
        })));
    }, function (e) { showError(e.error); });
  }

  // ---- Feedback
  function pageFeedback() {
    api("GET", "status").then(function (s) {
      if (!s.feedback_enabled) { clear(viewEl); add(viewEl, h("p", { class: "muted" }, "The feedback inbox is off (no FEEDBACK_KEY).")); return null; }
      return api("GET", "feedback").then(function (d) {
        clear(viewEl);
        add(viewEl, h("p", { class: "muted" }, d.codes.length + " codes. Click one to read its items."));
        add(viewEl, table(["Code", "Items", "Newest"], d.codes.map(function (c) {
          return { code: c.code, cells: [c.code, c.items, when(c.newest_t)] };
        }), function (c) { feedbackCode(c.code); }));
      });
    }).catch(function (e) { showError(e.error); });
  }
  function feedbackCode(code) {
    showError("");
    api("GET", "feedback/" + id(code)).then(function (d) {
      clear(viewEl);
      add(viewEl, btn("< All codes", function () { render(); }));
      add(viewEl, h("h2", {}, code + " (" + d.items.length + " items)"));
      add(viewEl, btn("Delete all items of this code", function () {
        confirmThen("Delete all feedback items for this code?", function () {
          act("DELETE", "feedback/" + id(code), {}, function () { render(); });
        });
      }, "danger"));
      d.items.forEach(function (it) {
        add(viewEl, h("div", { class: "panel" },
          h("div", { class: "row" }, h("b", {}, (it.reviewer || "anonymous") + ", " + it.branch), h("span", { class: "muted" }, when(it.received_t)),
            btn("Delete", function () {
              act("DELETE", "feedback/" + id(code), { ids: [it.id] }, function () { feedbackCode(code); });
            }, "danger")),
          h("pre", {}, JSON.stringify(it, null, 2))));
      });
    }, function (e) { showError(e.error); });
  }

  // ---- Audit
  function pageAudit() {
    api("GET", "audit?limit=200").then(function (d) {
      clear(viewEl);
      add(viewEl, table(["When", "Via", "Action", "Detail"], d.items.map(function (a) {
        return { cells: [when(a.t), a.via, a.action, a.detail] };
      })));
    }, function (e) { showError(e.error); });
  }

  // ---- start: are we logged in already?
  api("GET", "me").then(function (me) { showApp(me.via); }, function (e) {
    showLogin();
    if (e.status && e.status !== 401) showError(e.error);
  });
})();
