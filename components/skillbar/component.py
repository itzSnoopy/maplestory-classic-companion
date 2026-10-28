"""skillbar component: owns the live feed (hub/live.py): HP/MP from the status bar, the regen clock,
what the HP/MP went on, the training costs of this map visit, the party box. Writes live.json (the
skill bar over the game and the party link read it).

Subscribes: service_start (builds the feed), facts, progress (training needs level/EXP).
Emits (through the feed): exp_rise. Offers: hub.ask("exp_rate"), hub.ask("visit_map").
Between quick looks it runs the regen clock's bursts (hub.on_idle).
Phase 3/4: hub/live.py still holds the vitals / mpuse / training / party code and merges
hub.published() into live.json; splitting those into their own components is left for later.
Helper side (ui): the skill bar over the game (skillbar_ui.py), its two menu toggles and its line in
Settings > General.
"""
import copy
import traceback

from base.contract import Component
from hub import helper_settings as HS


class SkillbarComponent(Component):
    name = "skillbar"
    settings_defaults = {"skillbar": HS.DEFAULTS["skillbar"]}     # the store keeps the slices in one file

    # -- the helper window
    def ui(self, shell):
        from hub.ui.overlay import PanelHost
        self.host = host = PanelHost(shell, "skillbar", "skill bar", make_bar, dy=400)
        shell.add_menu(host.menu("skillbar", "Skill bar", "Lock skill bar (click-through)"))
        shell.on_refresh(lambda snap: host.sync(), early=True)
        shell.add_settings_extra("General", self.settings_extra, order=10)
        shell.on_settings_save(self.on_save)

    def settings_extra(self, win, f):
        win.s.setdefault("skillbar", copy.deepcopy(self.settings_defaults["skillbar"]))
        win.check(f, "skillbar.enabled", "Skill bar over the game (HP/MP regen, casts left)"
                  ).pack(anchor="w", pady=(0, win.px(2)))
        win.label(f, "Drag it anywhere; right-click to lock it (clicks then go to the game). "
                     "Unlock from the tray menu.", muted=True, wrap=win.px(380)).pack(anchor="w", pady=(0, win.px(6)))

    def on_save(self, win):
        # the bar may have been moved / locked while the window was open: keep that
        bar = win.shell.settings("skillbar")
        win.s.setdefault("skillbar", {}).update({k: bar[k] for k in ("x", "y", "locked", "hidden", "show") if k in bar})

    def attach(self, hub):
        self.hub = hub
        self.feed = None
        hub.on("service_start", self.on_start)
        hub.on("facts", self.on_facts)
        hub.on("progress", self.on_progress)

    def on_start(self, **_):
        from hub import live          # here, not at the top: the helper loads this module for ui() and must stay light
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


def make_bar(root, get, put, **kw):
    """PanelHost's factory: the bar's module (PIL and all) loads only when the bar is switched on."""
    from components.skillbar.skillbar_ui import SkillBar
    return SkillBar(root, get, put, **kw)
