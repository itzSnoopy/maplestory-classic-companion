"""live.py - live.json, the feed the helper's overlays read (skill bar, training panel, party panel):
rewritten about once a second while playing, whenever a batch of facts has a 'vitals' look in it.

The hub only composes the file. t and character are its own; every other key is a section some
component publishes (hub.publish(name, fn); fn(now, prof) is called at write time):

  t, character
  hp / mp     {cur, max, tick, period, next (epoch s), sure_s, ticks_seen, clock, check_every,
              checks, restarts}                                   skillbar (meter.Meter.state)
  regen       {mp: {skill, level, says}}                          skillbar
  skills      [{id, name, level, max, mp, casts, icon}]           skillbar
  usage       {since, hp/mp {kind: {name: {n, amount}}}, recent [events], levels_seen}   skillbar
  training    this map visit: kills, casts, potions, mesos, per kill / hour / to level   training
  party       {t, rows [{name, me, on_map, hp (% from the box, None off your map), leader}]}   party
              the party box under the minimap, last look; rows [] = no party box

Sections are evaluated in the order they were published; the file lists the known ones in KEYS order
(unknown ones after), so its layout doesn't depend on which component started first.
"""
import time

from hub import history as H

WRITE_EVERY_S = 0.9
KEYS = ("hp", "mp", "regen", "skills", "usage", "training", "party")


class LiveFile:
    def __init__(self, hub, path=None):
        self.hub = hub
        self.path = path
        self.last_write = 0.0

    def _path(self):
        return self.path or (H.ROOT / "live.json")

    def after_facts(self, facts, prof, now=None):
        """Called when a batch of facts has been dealt out: writes when it holds a vitals look."""
        now = now or time.time()
        if now - self.last_write >= WRITE_EVERY_S and any(k == "vitals" for _, k, _ in facts):
            self.last_write = now
            H.write_json(self._path(), self.snapshot(prof, now), indent=None)

    def snapshot(self, prof, now):
        c = (prof.char() if prof is not None else None) or {}
        got = {name: (fn(now, prof) if callable(fn) else fn) for name, fn in self.hub.published().items()}
        out = {"t": round(now, 2), "character": c.get("name") or c.get("name_guess")}
        for k in KEYS:
            if k in got:
                out[k] = got.pop(k)
        out.update(got)
        return out
