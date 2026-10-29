"""skillbar component: HP/MP from the status bar, the regen clock, what the HP/MP went on, casts left
(components/skillbar/feed.py). Publishes the hp / mp / regen / skills / usage sections of live.json,
which the skill bar over the game reads.

Subscribes: service_start (builds the feed), facts_begin, fact (vitals, away, party).
Offers: hub.ask("usage") (mpuse.Usage; the training component prices it).
Asks: hub.ask("mp_price") / hub.ask("price_of") (training) for the skills list.
Between quick looks it runs the regen clock's bursts (hub.on_idle).
Helper side (ui): the skill bar over the game (skillbar_ui.py), its two menu toggles and its line in
Settings > General.
"""
import copy

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
        self.prof = None
        hub.on("service_start", self.on_start)
        hub.on("facts_begin", self.on_begin)
        hub.on("fact", self.on_fact)

    def on_start(self, **_):
        from components.skillbar import feed     # here, not at the top: the helper loads this module for ui() and must stay light
        hub = self.hub
        self.feed = f = feed.Feed(hub=hub)        # HP/MP regen, casts
        hub.provide("usage", lambda: f.usage)
        hub.on_idle(f.burst_looks)
        # live.json sections, evaluated in this order (see Feed: the regen section sets a prior)
        for name, fn in (("mp", f.section_mp), ("skills", f.section_skills), ("regen", f.section_regen),
                         ("hp", f.section_hp), ("usage", f.section_usage)):
            hub.publish(name, fn)

    def on_begin(self, prof, now=None, **_):
        self.feed.begin(prof, now)

    def on_fact(self, t, kind, value, prof=None, **_):
        self.feed.fact(t, kind, value, prof)


def make_bar(root, get, put, **kw):
    """PanelHost's factory: the bar's module (PIL and all) loads only when the bar is switched on."""
    from components.skillbar.skillbar_ui import SkillBar
    return SkillBar(root, get, put, **kw)
