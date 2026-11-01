"""relic component: the room's relic hunt on the player's side.

Tracker side: every EXP rise becomes ELIGIBLE kills (gamedata/relic.py: mobs' worth x the map's in-band share for
your level), added up while a hunt is running and sent as the `relic` part of the party link's sync
(relay_post extra["_parts"]["relic"] = {"kills": x}). The relay rolls the drops; its answer comes back in
relay_reply parts["relic"]. Writes relic.json for the helper (status, found ids, next chance, together, the toast
trigger) and emits relic_found. Runs the Start / Stop commands the rooms panel writes (relic_cmd.json): a v2
room only, through the relay_link service's call().
Helper side (ui): a 3 s toast over the game corner when a piece is found, the menu entry "Open relic hunt" (the
relay's table page, the member token in the URL FRAGMENT so it never reaches a server log), Settings > General.

Subscribes: service_start, service_stop, exp_rise, relay_post, relay_reply. Emits: relic_found.
Asks: relay_link (party).
"""
import threading
import time
import traceback

from base import perf
from base.contract import Component
from hub import helper_settings as HS
from hub import history as H

CMD_POLL_S = 0.5


class RelicComponent(Component):
    name = "relic"
    settings_defaults = {"relic": HS.DEFAULTS["relic"]}

    # ------------------------------------------------------------------ tracker side
    def attach(self, hub):
        from components.relic import hunt
        self.hub = hub
        self.hunt = hunt.Hunt()
        self.worker = None
        self.stop_ev = threading.Event()
        hub.on("service_start", self.on_start)
        hub.on("service_stop", self.on_stop)
        hub.on("exp_rise", self.on_rise)
        hub.on("relay_post", self.on_post)
        hub.on("relay_reply", self.on_reply)

    def _enabled(self):
        return bool(self.hub.settings("relic").get("enabled", True))

    def _write(self, error=None):
        snap = self.hunt.snapshot(self._enabled())
        snap["error"] = error
        H.write_json(self.hub.root / "relic.json", snap, indent=None)

    def on_start(self, **_):
        self.stop_ev.clear()
        self._write()
        self.worker = threading.Thread(target=self._work, daemon=True, name="relic")
        self.worker.start()

    def on_stop(self, **_):
        self.stop_ev.set()

    def on_rise(self, t, map, d, **_):
        with perf.timed("relic"):
            if not self._enabled():
                return
            level = self.hub.state.level
            if not level:
                return
            from gamedata import relic
            self.hunt.add(relic.eligible_kills(map, d, level))

    def on_post(self, extra, mode="v1", **_):           # link thread
        if mode != "v2" or not self._enabled():
            return
        part = self.hunt.post_part()
        if part:
            extra.setdefault("_parts", {})["relic"] = part

    def on_reply(self, reply, **_):                     # link thread
        if reply.get("mode") != "v2":
            return
        parts = reply.get("parts") if isinstance(reply.get("parts"), dict) else {}
        ids = self.hunt.on_reply(parts.get("relic"))
        if ids:
            r = self.hunt.reply
            self.hub.emit("relic_found", ids=ids, found=r.get("found_count"), total=r.get("pieces_total"),
                          placed=r.get("placed_count"), difficulty=r.get("difficulty"), t=time.time())
        self._write()

    # ------------------------------------------------------------------ commands from the rooms panel
    def _work(self):
        cmd_path = self.hub.root / "relic_cmd.json"
        while not self.stop_ev.wait(CMD_POLL_S):
            try:
                if cmd_path.exists():
                    cmd = H.read_json(cmd_path, None)
                    try:
                        cmd_path.unlink()
                    except OSError:
                        pass
                    if isinstance(cmd, dict) and time.time() - (cmd.get("t") or 0) < 30:
                        self._command(cmd)
            except Exception:
                self.hub.log("relic worker failed:\n" + traceback.format_exc())

    def _command(self, cmd):
        link = self.hub.ask("relay_link")
        action = cmd.get("action")
        if not link or not link["ok"] or link.get("mode") != "v2" or action not in ("start", "stop"):
            return self._write("Not in a relay room")
        body = {}
        if action == "start":
            body["difficulty"] = cmd.get("difficulty")
            for k in ("pieces", "hours", "people"):
                if cmd.get(k) is not None:
                    body[k] = cmd[k]
        try:
            status, res = link["call"]("relic", action, body)
        except Exception as e:
            return self._write(f"Relay unreachable ({type(e).__name__})")
        self._write(None if status == 200 else ((res or {}).get("error") or f"Relay said {status}"))

    # ------------------------------------------------------------------ helper side
    def ui(self, shell):
        self.shell = shell
        shell.add_command("relic", self.open_table)
        shell.add_menu([("Open relic hunt", "relic", None, None, None)], slot="open")
        shell.add_settings_extra("General", self.settings_extra, order=36)
        if shell.root is not None:
            from components.relic import toast
            self.toaster = toast.Toaster(shell)
            shell.on_refresh(lambda snap: self.toaster.tick(), early=True)

    def open_table(self):
        """Open the relay's table page for the joined room in the browser. The token goes in the fragment."""
        from base import internet
        from hub import relay_addr, relay_room
        st = self.shell.all_settings()
        server, room = relay_addr.server(st), relay_room.get(st)
        if not (server and room and room["status"] == "active"):
            return self.shell.notify("Join a relay room first (rooms panel)")
        if not internet.allowed("party"):
            return self.shell.notify("Internet is off for the party link (Settings > Internet)")
        import webbrowser
        webbrowser.open(f"{server}/rooms/{room['id']}/relic#t={room['token']}")

    def settings_extra(self, win, f):
        import copy
        win.s.setdefault("relic", copy.deepcopy(self.settings_defaults["relic"]))
        win.check(f, "relic.enabled", "Relic hunt: count my kills for the room's hunt").pack(anchor="w", pady=(0, win.px(2)))
        win.check(f, "relic.toast", "Show \"+ Relic piece\" over the game when I find one").pack(anchor="w", pady=(0, win.px(4)))
