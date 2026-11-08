"""party component: the party box under the minimap (rows from the 'party' fact: HP %, on your map,
leader, names) and the party link thread (partylink.py), which shares your numbers with your party
through the relay you host and writes party.json. The link is opt-in (Settings > Internet > Party link).

Subscribes: service_start (starts the thread), service_stop (stops it), fact (party).
Emits (on the link thread): relay_post (components add fields to the post), relay_reply (the relay's answer).
Publishes: the party section of live.json ({t, held, rows [{name, me, on_map, hp, leader, src, held, since, looks}]};
rows [] = no party): the sticky roster (roster.py), not the raw last look.
Offers: hub.ask("party_on_map") (another member is on your map, so EXP rises are shares), hub.ask("relay_link")
(the link's server / code / state and a request() for other relay calls).
Names come from the source (hub.source.party_name / party_reset, an optional Source hook).
Helper side (ui): the party panel over the game (party_ui.py), its menu toggles, its line in Settings >
General and the party code field under Settings > Internet > Party link (the server is the shared relay
address, hub/relay_addr.py, Settings > Internet > Relay).
"""
import copy
import time

from base.contract import Component
from components.party import partylink
from components.party.roster import Roster
from hub import helper_settings as HS
from hub import history as H


MATE_FRESH_S = 15.0         # a relay member's numbers count this long (same as the panel)


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
        """Under "Party link" on the Internet page: the party code (the relay address is the Relay section)."""
        import json
        import tkinter as tk
        from base.paths import DATA
        px = win.px
        win.s.setdefault("party", {})
        for k, v in self.settings_defaults["party"].items():
            win.s["party"].setdefault(k, v)
        for key, cap, hint in (("party.code", "Party code", "same on every PC"),):
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
        if pj.get("room_lost"):
            note = f"You're no longer in the room {pj.get('room_lost_name') or ''}. Pick another in the rooms panel."
        elif pj.get("pending"):
            note = "Waiting for the room owner to approve you."
        elif pj.get("ok") and pj.get("mode") == "v2":
            names = ", ".join(m.get("name", "?") for m in pj.get("members") or []) or "nobody else yet"
            note = f"Connected through your room. Also in it: {names}. (The party code below is not used while you are in a room.)"
        elif pj.get("ok"):
            names = ", ".join(m.get("name", "?") for m in pj.get("members") or []) or "nobody yet"
            note = f"Connected. Online with your code: {names}."
        elif pj.get("error") and pj.get("error") != "off":
            note = f"Last try failed: {pj['error']}"
        else:
            note = ("Old party link; rooms replace this soon. The tracker connects within a few seconds "
                    "of saving (needs the Relay server above).")
        win.label(f, note, muted=True, size=8, wrap=px(540)).pack(
            anchor="w", padx=(px(22), 0), pady=(px(4), 0))

    def on_save(self, win):
        pan = win.shell.settings("partypanel")      # moved / locked while the window was open: keep that
        win.s.setdefault("partypanel", {}).update({k: pan[k] for k in ("x", "y", "locked") if k in pan})
        if "party" in win.s:
            win.s["party"]["code"] = str(win.s["party"].get("code") or "").strip()

    def attach(self, hub):
        self.hub = hub
        self.link = None
        self.box = {"t": None, "rows": []}      # the sticky roster as of the last look
        self.roster = None                      # roster.Roster, made at the first look (needs hub.source)
        hub.on("service_start", self.on_start)
        hub.on("service_stop", self.on_stop)
        hub.on("fact", self.on_fact)
        hub.provide("party_on_map", self.party_on_map)
        hub.provide("relay_link", self.relay_link)
        hub.publish("party", lambda now, prof: self.box)

    def on_start(self, **_):
        self.link = partylink.PartyLink(log=self.hub.log, before=self.relay_post, after=self.relay_reply)
        self.link.start()                       # party.json: partners' numbers (relay/, opt-in)

    def relay_link(self):
        """Service: {server, code, ok, server_off, mode ("v1" | "v2" | "off"), room (v2 room id), request(kind, suffix,
        body, method) -> (status, json) for v1, call(module, action, body) -> (status, json) for v2} or None before the link exists."""
        return self.link.info() if self.link is not None else None

    def relay_post(self, me, mode="v1"):
        """Link thread, before each post: let components add fields to it (event relay_post)."""
        extra = {}
        self.hub.emit("relay_post", extra=extra, me=me, mode=mode)
        return extra

    def relay_reply(self, reply, server_off):
        """Link thread, after each answer: hand the whole reply to the components (event relay_reply)."""
        self.hub.emit("relay_reply", reply=reply, server_off=server_off)

    def on_stop(self, **_):
        if self.link is not None:
            self.link.stop()

    def on_fact(self, t, kind, value, prof=None, **_):
        if kind == "party":
            self._party(t, value, prof)

    def party_on_map(self, now=None, max_age=10.0):
        """Another party member is on your map right now (so your EXP rises are shares).
        Built from the sticky roster: a member held through a map-change blip keeps its last on_map (and
        on_map changes need 2 looks), so this does not flip on/off with the box. The box's last fact must
        be <= max_age old (facts stop when the game is gone); held rows count while the roster holds them."""
        now = now or time.time()
        p = self.box
        if not p.get("t") or now - p["t"] > max_age:
            return False
        return sum(1 for r in p["rows"] if r.get("on_map")) > 1

    def _mates(self, now):
        """Relay members fresh right now (party.json): {name: {hp [cur, max] | None, mp, level, map}}."""
        pj = H.read_json(H.ROOT / "party.json", {})
        if not pj.get("ok"):
            return {}
        lag = max(0.0, now - (pj.get("t") or now))      # age_s was true when the file was written
        out = {}
        for m in pj.get("members") or []:
            if m.get("name") and (m.get("age_s") or 0) + lag <= MATE_FRESH_S:
                hp = m.get("hp")
                out[m["name"]] = {"hp": hp if isinstance(hp, (list, tuple)) and len(hp) == 2 else None,
                                  "mp": m.get("mp"), "level": m.get("level"), "map": m.get("map")}
        return out

    def _party(self, t, rows, prof):
        src = self.hub.source
        c = (prof.char() if prof is not None else None) or {}
        me = c.get("name") or c.get("name_guess")
        mates = self._mates(time.time())
        known = [n for n in [me] + list(mates) if n]
        if self.roster is None:
            self.roster = Roster(getattr(src, "party_name", None), getattr(src, "party_reset", None))
        out = self.roster.update(t, rows, known, mates, me)
        self.box = {"t": round(t, 2), "rows": out, "held": self.roster.held}


def make_panel(root, get, put, **kw):
    """PanelHost's factory: the panel's module (PIL and all) loads only when it is switched on."""
    from components.party.party_ui import PartyPanel
    return PartyPanel(root, get, put, **kw)
