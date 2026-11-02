"""challenge component: friendly kill-race duels between members of one party-relay code.

Tracker side: counts individual kills from the game's EXP lines (`exp_gain`, engine/msgcorner.py: a WHITE line =
my own kill, yellow = a teammate's share, solo and party alike); the EXP bar (`exp_rise`) is only a fallback for a
rise no EXP line covered within 2.5 s (solo: whole-mob match, gamedata/kills.py; partied: nothing, never guess).
Every gain is also appended to history/exp_gains.csv. Rides the
party link (events relay_post / relay_reply, service relay_link: no import of the party component) to send
each kill and follow the relay's score, runs the commands the party panel writes (challenge_cmd.json:
create / accept / decline), writes challenge.json for the helper's board and the panel, keeps the win /
loss record per opponent (history/challenges.json) and emits `challenge_end` when a duel is over.
Room activities: the hangout panel's Start button (owner / mod of the joined room) writes the same
challenge_cmd.json with action "start" {kind: "race", minutes} / "stop"; the relay makes a room-wide race (invited =
everyone live in the room; the starter plays only if live themselves).
Helper side: the versus board (board.py) over the game, its Settings extras and a `--demo`.

In a joined v2 room (relay_link mode "v2") the kills ride the room's `challenge` sync part and the commands go to
POST /v2/rooms/<id>/challenge/<action> (link["call"]); with only a party code it is the v1 endpoints, as before.

Subscribes: service_start (worker thread), service_stop, exp_gain, exp_rise, relay_post, relay_reply, challenge_end (journal).
Emits: challenge_end (result dict).
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
LOG_MAX = 50
GAINS_LOG_MAX = 5_000_000       # history/exp_gains.csv rotates to .old at this size


class ChallengeComponent(Component):
    name = "challenge"
    settings_defaults = {"challenge": HS.DEFAULTS["challenge"]}

    # ------------------------------------------------------------------ tracker side
    def attach(self, hub):
        from components.challenge import duel
        self.hub = hub
        self.duel = duel.Duel()
        self.worker = None
        self.stop_ev = threading.Event()
        hub.on("service_start", self.on_start)
        hub.on("service_stop", self.on_stop)
        hub.on("exp_rise", self.on_rise)
        hub.on("exp_gain", self.on_gain)
        hub.on("relay_post", self.on_post)
        hub.on("relay_reply", self.on_reply)
        hub.on("challenge_end", self.on_end)

    def _cfg(self):
        c = self.hub.settings("challenge")
        return bool(c.get("enabled", True)), bool(c.get("dnd", False))

    def _write(self):
        enabled, dnd = self._cfg()
        H.write_json(self.hub.root / "challenge.json", self.duel.snapshot(enabled, dnd), indent=None)

    def on_start(self, **_):
        self.stop_ev.clear()
        self.duel.done = {r.get("id") for r in H.read_json(self.hub.root / "history" / "challenges.json",
                                                            {}).get("log", [])}
        self._write()
        self.worker = threading.Thread(target=self._work, daemon=True, name="challenge")
        self.worker.start()

    def on_stop(self, **_):
        self.stop_ev.set()

    def _partied(self, map, party):
        """Is a party member on my map right now? (exp_rise's `party` flag, or fresh party.json / party box rows)"""
        if party:
            return True
        from components.challenge import partyexp
        return partyexp.party_levels(self.hub.root, self.hub.state.level or 1, map) is not None

    def on_rise(self, t, map, d, party=False, **_):
        with perf.timed("challenge"):
            enabled, _dnd = self._cfg()
            if not enabled:
                return
            if self.duel.active():                  # held until the EXP lines had their say (Duel.settle)
                self.duel.on_rise(t, map, d, self._partied(map, party))

    def on_gain(self, t, base=0, party_bonus=0, own=False, overflow=False, **_):
        """One EXP line pair from the game's message corner: logged (history/exp_gains.csv), and while a duel is
        live a white line (own) is one kill."""
        with perf.timed("challenge"):
            self._log_gain(t, base, party_bonus, own, overflow)
            enabled, _dnd = self._cfg()
            if enabled:
                self.duel.on_gain(t, base, party_bonus, own, overflow)

    def _log_gain(self, t, base, party_bonus, own, overflow):
        """history/exp_gains.csv: t, map, base, party_bonus, own, overflow (for checking a party session later)."""
        try:
            path = self.hub.root / "history" / "exp_gains.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > GAINS_LOG_MAX:
                path.replace(path.with_suffix(".csv.old"))
            new = not path.exists()
            with open(path, "a", encoding="utf-8", newline="") as f:
                if new:
                    f.write("t,map,base,party_bonus,own,overflow\n")
                m = str(self.hub.state.map or "").replace('"', "'")
                f.write(f'{t:.2f},"{m}",{base},{party_bonus},{int(bool(own))},{int(bool(overflow))}\n')
        except OSError:
            pass

    def on_post(self, extra, me, **_):                  # link thread
        self.duel.me = me.get("name")
        extra.update(self.duel.post_fields(*self._cfg()))

    def on_reply(self, reply, server_off, **_):         # link thread
        enabled, dnd = self._cfg()
        results = self.duel.on_reply(reply, server_off, enabled, dnd)
        for r in results:
            self._record(r)
            self.hub.emit("challenge_end", result=r)
        self._write()

    def on_end(self, result, **_):
        won = {True: "won", False: "lost", None: "tied"}[result["won"]]
        who = result["opponent"] if len(result["names"]) < 2 else f"{len(result['names'])} players"
        self.hub.journal(f"Challenge: {won} Kills {result['minutes']} min vs {who}, "
                         f"{result['score'][0]}-{result['score'][1]}")

    def _record(self, r):
        """history/challenges.json: {"vs": {me: {opponent: {won, lost, tied}}}, "log": [last results]}."""
        path = self.hub.root / "history" / "challenges.json"
        data = H.read_json(path, {})
        mine = data.setdefault("vs", {}).setdefault(r["me"], {})
        mine_score = r["score"][0]
        for o in r["ranking"]:
            if o["name"] == r["me"]:
                continue
            rec = mine.setdefault(o["name"], {"won": 0, "lost": 0, "tied": 0})
            rec["won" if mine_score > o["score"] else "lost" if mine_score < o["score"] else "tied"] += 1
        data["log"] = (data.get("log") or [])[-(LOG_MAX - 1):] + [r]
        H.write_json(path, data)

    # ------------------------------------------------------------------ commands from the panel
    def _work(self):
        cmd_path = self.hub.root / "challenge_cmd.json"
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
                self.hub.log("challenge worker failed:\n" + traceback.format_exc())

    def _command(self, cmd):
        link = self.hub.ask("relay_link")
        d = self.duel
        if not link or not link["ok"] or not d.me:
            d.error = ("Not connected to the party relay", time.time())
            return self._write()
        action = cmd.get("action")
        v2 = link.get("mode") == "v2"           # a joined room (v2 challenge module) or the party code (v1)
        try:
            if action == "create":
                body = {"mode": cmd.get("mode"), "minutes": cmd.get("minutes"), "target": cmd.get("target")}
                if v2:
                    status, res = link["call"]("challenge", "create", body)     # the name comes from the member token
                else:
                    status, res = link["request"]("challenge", "", {"name": d.me, **body})
            elif action == "start" and v2:
                # owner / mod starts a room-wide activity (relay: challenge/start); a v1 party code has none
                body = {"kind": cmd.get("kind") or "race", "minutes": cmd.get("minutes")}
                status, res = link["call"]("challenge", "start", body)
            elif action == "stop" and v2:
                status, res = link["call"]("challenge", "stop", {})
            elif action in ("accept", "decline", "cancel") and isinstance(cmd.get("id"), str):
                if v2:
                    status, res = link["call"]("challenge", action, {"id": cmd["id"]})
                else:
                    status, res = link["request"]("challenge", "/" + cmd["id"], {"name": d.me, "action": action})
            else:
                return
        except Exception as e:
            d.error = (f"Relay unreachable ({type(e).__name__})", time.time())
            return self._write()
        if status != 200:
            d.error = (res.get("error") or f"Relay said {status}", time.time())
        else:
            self._merge(res)
        self._write()

    def _merge(self, view):
        """Take one challenge view from a command's answer without waiting for the next post."""
        d = self.duel
        with d.lock:
            d.views = [v for v in d.views if v["id"] != view.get("id")] + [
                {k: v for k, v in view.items() if k != "server_t"}]
            d.got_t = time.time()

    # ------------------------------------------------------------------ helper side
    def ui(self, shell):
        from hub.ui.overlay import PanelHost
        self.host = host = PanelHost(shell, "challenge", "challenge board", make_board, dy=40)
        shell.on_refresh(lambda snap: host.sync(), early=True)
        from components.challenge.invite_ui import InviteHost      # the invitation popup (shown while an invite is open)
        self.invite_host = ih = InviteHost(shell)
        shell.on_refresh(lambda snap: ih.sync(), early=True)
        shell.add_settings_extra("General", self.settings_extra, order=35)
        shell.on_settings_save(self.on_save)
        shell.on_settings_save(lambda win: win.s.setdefault("challenge", {}).update(
            {k: v for k, v in shell.settings("challenge").items() if k in ("invite_x", "invite_y")}))

    def settings_extra(self, win, f):
        from components.challenge import settings_page
        settings_page.build(win, f, self.settings_defaults["challenge"])

    def on_save(self, win):
        pan = win.shell.settings("challenge")           # moved while the window was open: keep that
        win.s.setdefault("challenge", {}).update({k: pan[k] for k in ("x", "y", "locked") if k in pan})


def make_board(root, get, put, **kw):
    """PanelHost's factory: the board's module (PIL and all) loads only when it is wanted."""
    from components.challenge.board import Board
    return Board(root, get, put, **kw)
