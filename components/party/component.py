"""party component: the party box under the minimap (rows from the 'party' fact: HP %, on your map,
leader, names) and the party link thread (partylink.py), which shares your numbers with your party
through the relay you host and writes party.json. The link is opt-in (Settings > Internet > Party link).

Subscribes: service_start (starts the thread), service_stop (stops it), fact (party).
Publishes: the party section of live.json ({t, rows [{name, me, on_map, hp, leader}]}; rows [] = no box).
Offers: hub.ask("party_on_map") (another member is on your map, so EXP rises are shares).
Names come from the source (hub.source.party_name / party_reset, an optional Source hook).
Helper side (ui): the party panel over the game (party_ui.py), its menu toggles, its line in Settings >
General and the server / code fields under Settings > Internet > Party link.
"""
import copy
import time

from base.contract import Component
from components.party import partylink
from hub import helper_settings as HS
from hub import history as H


class PartyComponent(Component):
    name = "party"
    settings_defaults = {"party": HS.DEFAULTS["party"], "partypanel": HS.DEFAULTS["partypanel"]}

    # -- the helper window
    def ui(self, shell):
        from hub.ui.overlay import PanelHost
        self.host = host = PanelHost(shell, "partypanel", "party panel", make_panel, dy=520)
        shell.add_menu(host.menu("partypanel", "Party panel", "Lock party panel (click-through)"))
        shell.on_refresh(lambda snap: host.sync(), early=True)
        shell.add_settings_extra("General", self.settings_extra, order=30)
        shell.add_settings_extra("internet.party", self.internet_extra)
        shell.on_settings_save(self.on_save)

    def settings_extra(self, win, f):
        win.s.setdefault("partypanel", copy.deepcopy(self.settings_defaults["partypanel"]))
        win.check(f, "partypanel.enabled", "Party panel over the game (party HP bars, friends list)"
                  ).pack(anchor="w", pady=(0, win.px(2)))
        win.label(f, "Shows when you're in a party or friends are online. Friends who run this app "
                     "show their level, EXP, map and HP: set up Internet > Party & friends link "
                     "(same code for everyone). Right-click it to hide the friends list.", muted=True,
                  wrap=win.px(380)).pack(anchor="w", pady=(0, win.px(6)))

    def internet_extra(self, win, f):
        """Under "Party link" on the Internet page: your relay's address and the party code."""
        import json
        import tkinter as tk
        from base.paths import DATA
        px = win.px
        win.s.setdefault("party", {})
        for k, v in self.settings_defaults["party"].items():
            win.s["party"].setdefault(k, v)
        for key, cap, hint in (("party.server", "Server", "e.g. http://1.2.3.4:8787"),
                               ("party.code", "Party code", "same on every PC")):
            row = tk.Frame(f, bg=win.w["bg"])
            row.pack(anchor="w", pady=(px(4), 0), padx=(px(22), 0))
            lb = win.label(row, cap)
            lb.configure(width=10)
            lb.pack(side="left")
            win.entry(row, key, width=32).pack(side="left", padx=(px(6), px(8)))
            win.label(row, hint, muted=True, size=8).pack(side="left")
        try:
            pj = json.loads((DATA / "party.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pj = {}
        if pj.get("ok"):
            names = ", ".join(m.get("name", "?") for m in pj.get("members") or []) or "nobody yet"
            note = f"Connected. Online with your code: {names}."
        elif pj.get("error") and pj.get("error") != "off":
            note = f"Last try failed: {pj['error']}"
        else:
            note = ("The tracker connects within a few seconds of saving. Server setup: the "
                    "relay/ folder (README).")
        win.label(f, note, muted=True, size=8, wrap=px(540)).pack(
            anchor="w", padx=(px(22), 0), pady=(px(4), 0))

    def on_save(self, win):
        pan = win.shell.settings("partypanel")      # moved / locked while the window was open: keep that
        win.s.setdefault("partypanel", {}).update({k: pan[k] for k in ("x", "y", "locked") if k in pan})
        if "party" in win.s:
            for k in ("server", "code"):
                win.s["party"][k] = str(win.s["party"].get(k) or "").strip()

    def attach(self, hub):
        self.hub = hub
        self.link = None
        self.box = {"t": None, "rows": []}      # the party box at the last look
        hub.on("service_start", self.on_start)
        hub.on("service_stop", self.on_stop)
        hub.on("fact", self.on_fact)
        hub.provide("party_on_map", self.party_on_map)
        hub.publish("party", lambda now, prof: self.box)

    def on_start(self, **_):
        self.link = partylink.PartyLink(log=self.hub.log)    # party.json: partners' numbers (relay/, opt-in)
        self.link.start()

    def on_stop(self, **_):
        if self.link is not None:
            self.link.stop()

    def on_fact(self, t, kind, value, prof=None, **_):
        if kind == "party":
            self._party(t, value, prof)

    def party_on_map(self, now=None, max_age=10.0):
        """Another party member is on your map right now (so your EXP rises are shares)."""
        now = now or time.time()
        p = self.box
        if not p.get("t") or now - p["t"] > max_age:
            return False
        return sum(1 for r in p["rows"] if r.get("on_map")) > 1

    def _party(self, t, rows, prof):
        src = self.hub.source
        name_of = getattr(src, "party_name", None)
        c = (prof.char() if prof is not None else None) or {}
        me = c.get("name") or c.get("name_guess")
        mates = [m.get("name") for m in (H.read_json(H.ROOT / "party.json", {}).get("members")
                                         or [])]
        known = [n for n in [me] + mates if n]
        if len(rows) != len(self.box["rows"]) and getattr(src, "party_reset", None):
            src.party_reset()                    # row names no longer line up
        out = []
        for i, r in enumerate(rows):
            name = name_of(i, r.get("mask"), known, t) if name_of else None
            out.append({"name": name, "me": bool(me and name == me), "on_map": r["on_map"],
                        "hp": r["hp"], "leader": r["leader"]})
        # one unreadable row and one partner not matched to any row: that's them
        missing = [n for n in mates if n and n not in {o["name"] for o in out}]
        blank = [o for o in out if not o["name"] or o["name"] not in known]
        if len(missing) == 1 and len(blank) == 1:
            blank[0]["name"] = missing[0]
        self.box = {"t": round(t, 2), "rows": out}


def make_panel(root, get, put, **kw):
    """PanelHost's factory: the panel's module (PIL and all) loads only when it is switched on."""
    from components.party.party_ui import PartyPanel
    return PartyPanel(root, get, put, **kw)
