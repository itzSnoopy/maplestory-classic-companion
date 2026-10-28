"""skillbar component: owns the live feed (hub/live.py): HP/MP from the status bar, the regen clock,
what the HP/MP went on, the training costs of this map visit, the party box. Writes live.json (the
skill bar over the game and the party link read it).

Subscribes: service_start (builds the feed), facts, progress (training needs level/EXP).
Emits (through the feed): exp_rise. Offers: hub.ask("exp_rate"), hub.ask("visit_map").
Between quick looks it runs the regen clock's bursts (hub.on_idle).
Phase 3/4: hub/live.py still holds the vitals / mpuse / training / party code and merges
hub.published() into live.json; splitting those into their own components is left for later.
"""
import traceback

from base.contract import Component
from hub import live


class SkillbarComponent(Component):
    name = "skillbar"

    def attach(self, hub):
        self.hub = hub
        self.feed = None
        hub.on("service_start", self.on_start)
        hub.on("facts", self.on_facts)
        hub.on("progress", self.on_progress)

    def on_start(self, **_):
        hub = self.hub
        self.feed = feed = live.Live(hub=hub)         # live.json for the skill bar (HP/MP regen, casts)
        hub.provide("exp_rate", feed.exp_rate)
        hub.provide("visit_map",
                    lambda: feed.training.visit.name if feed.training.visit else hub.state.map)
        hub.on_idle(feed.burst_looks)

    def on_facts(self, facts, prof, **_):
        try:
            self.feed.update(facts, prof)
        except Exception:
            self.hub.log("character update failed: " + traceback.format_exc())

    def on_progress(self, level, cur, need, **_):
        self.feed.training.set_progress(level, cur, need)
