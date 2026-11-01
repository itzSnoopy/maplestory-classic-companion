"""hangout component: a small rooms panel over the game listing the rooms on your relay (hangouts and
parties, what they're doing). Helper side only: attach() does nothing. The panel polls
GET <server>/v2/lobby itself (components/hangout/lobby.py) while it is shown.

Subscribes: nothing. Emits: nothing.
Helper side (ui): the panel (PanelHost "hangout"), its two menu toggles, its line in Settings > General
and its feature checkbox under Settings > Internet > Hangout rooms. The server is the shared relay address
(hub/relay_addr.py, Settings > Internet > Relay).
Joining: the row button joins / leaves a v2 room (join.py, worker threads); the joined room is stored in
helper_settings.json relay.room, which the tracker's party link reads (hub/relay_room.py).
Online use only behind internet.allowed("hangout"), right before connecting (lobby.fetch, join.Client.call).
"""
import copy

from base.contract import Component
from hub import helper_settings as HS
from hub import relay_addr, relay_room


class HangoutComponent(Component):
    name = "hangout"
    settings_defaults = {"hangout": HS.DEFAULTS["hangout"]}

    def attach(self, hub):
        pass                                    # helper side only

    # -- the helper window
    def ui(self, shell):
        from hub.ui.overlay import PanelHost
        self.shell = shell
        self.host = host = PanelHost(shell, "hangout", "rooms panel", self.make_panel, dy=640)
        shell.add_menu(host.menu("hangout", "Rooms panel", "Lock rooms panel (click-through)"))
        shell.on_refresh(lambda snap: host.sync(), early=True)
        shell.add_settings_extra("General", self.settings_extra, order=40)
        shell.on_settings_save(self.on_save)

    def server(self):
        """The shared relay address (Settings > Internet > Relay), re-read each time so Settings apply."""
        return relay_addr.server(self.shell.all_settings())

    def make_panel(self, root, get, put, **kw):
        """PanelHost's factory: the panel's module (PIL and all) loads only when it is switched on."""
        from components.hangout.hangout_ui import HangoutPanel
        return HangoutPanel(root, get, put, server_fn=self.server, room_get=self.room, room_put=self.set_room, **kw)

    def room(self):
        """The joined room (helper_settings.json relay.room; the helper is its only writer)."""
        return relay_room.get(self.shell.all_settings())

    def set_room(self, room):
        """Store (or clear with None) the joined room and save helper_settings.json, keeping every other slice."""
        self.shell.save_settings("relay", room=room)

    def settings_extra(self, win, f):
        win.s.setdefault("hangout", copy.deepcopy(self.settings_defaults["hangout"]))
        win.check(f, "hangout.enabled", "Rooms panel over the game (hangout and party rooms)"
                  ).pack(anchor="w", pady=(0, win.px(2)))
        win.label(f, "Lists the rooms on your relay and what they're doing. Uses the relay server "
                     "under Settings > Internet > Relay.", muted=True,
                  wrap=win.px(380)).pack(anchor="w", pady=(0, win.px(6)))

    def on_save(self, win):
        pan = win.shell.settings("hangout")         # moved / locked while the window was open: keep that
        win.s.setdefault("hangout", {}).update({k: pan[k] for k in ("x", "y", "locked") if k in pan})
