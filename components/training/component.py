"""training component: what training on a map costs you (components/training/training.py): kills from
the EXP bar, MP per cast, potions and their price, per kill and per 1,000 EXP. Publishes the training
section of live.json, which the training cost panel over the game (training_ui.py) reads.

Subscribes: service_start, facts_begin (another character: the visit is closed and a new one starts),
fact (map, exp, party), progress (level / EXP / EXP needed).
Emits: exp_rise (every EXP rise on a quick look; the album listens).
Offers: hub.ask("exp_rate"), ("visit_map"), ("mp_price") and ("price_of").
Asks: hub.ask("usage") (skillbar's HP/MP accounting), hub.ask("party_on_map") (party).
Helper side (ui): the panel, its two menu toggles and its line in Settings > General.
"""
import copy
import time

from base.contract import Component
from hub import helper_settings as HS


class TrainingComponent(Component):
    name = "training"
    settings_defaults = {"trainingpanel": HS.DEFAULTS["trainingpanel"]}   # the store keeps the slices in one file

    def ui(self, shell):
        from hub.ui.overlay import PanelHost
        self.host = host = PanelHost(shell, "trainingpanel", "training panel", make_panel, dy=760)
        shell.add_menu(host.menu("trainingpanel", "Training cost panel", "Lock training panel (click-through)"))
        shell.on_refresh(lambda snap: host.sync(), early=True)
        shell.add_settings_extra("General", self.settings_extra, order=20)
        shell.on_settings_save(self.on_save)

    def settings_extra(self, win, f):
        win.s.setdefault("trainingpanel", copy.deepcopy(self.settings_defaults["trainingpanel"]))
        win.check(f, "trainingpanel.enabled", "Training cost panel over the game (MP, potions, mesos)"
                  ).pack(anchor="w", pady=(0, win.px(2)))
        win.label(f, "What this map costs you: kills, MP per cast, potions used and their shop "
                     "price, per kill and per 1,000 EXP, and the potions you'd need to level up at "
                     "this pace. Starts over on every map.", muted=True,
                  wrap=win.px(380)).pack(anchor="w", pady=(0, win.px(6)))

    def on_save(self, win):
        trn = win.shell.settings("trainingpanel")   # moved / locked / priced while the window was open: keep that
        win.s.setdefault("trainingpanel", {}).update(
            {k: trn[k] for k in ("x", "y", "locked", "mp_item", "hp_item", "prices") if k in trn})

    # -- the tracker process
    def attach(self, hub):
        self.hub = hub
        self.training = None
        self.who = None
        hub.on("service_start", self.on_start)
        hub.on("facts_begin", self.on_begin)
        hub.on("fact", self.on_fact)
        hub.on("progress", self.on_progress)

    def on_start(self, **_):
        hub = self.hub
        if hub.ask("usage") is None:        # no skillbar: nothing to account against
            return
        self._new_training()
        hub.provide("exp_rate", self.exp_rate)
        hub.provide("visit_map", lambda: self.training.visit.name if self.training.visit else hub.state.map)
        hub.provide("mp_price", lambda: self.training.mp_price())       # (potion, mesos per MP, MP per potion)
        hub.provide("price_of", lambda: self.training._price)           # fn(item name) -> mesos or None
        hub.publish("training", lambda now, prof: self.training.summary(now))

    def _new_training(self, t=None):
        from components.training import training       # here: the helper loads this module for ui()
        hub = self.hub
        if self.training is not None:
            self.training.close(t or time.time())
        self.training = tr = training.Training(hub.ask("usage"))
        # every EXP rise also goes out as the exp_rise event (the monster album listens), with the
        # party state *now*: EXP is only shared with members on your map (the party box row
        # includes you), not "partied at some point of this visit" (visit.party, sticky:
        # 2026-10-25 a whole solo stretch on Caution Falling Down was skipped because of it)
        tr.on_rise = lambda t, m, d, _visit_party: hub.emit(
            "exp_rise", t=t, map=m, d=d, party=bool(hub.ask("party_on_map", False)), gap=tr.exp_gap)

    def exp_rate(self, now=None):
        """EXP/hr on this map visit (exprate.py summary), or None outside a visit."""
        v = self.training.visit if self.training is not None else None
        return v.rate.summary(now or time.time()) if v is not None else None

    def on_begin(self, prof, now=None, **_):
        if self.training is not None and prof is not None and prof.who != self.who:
            self.who = prof.who                  # another character: the visit is closed, a new one starts
            self._new_training(now)

    def on_fact(self, t, kind, value, **_):
        tr = self.training
        if tr is None:
            return
        if kind == "map":                        # a new map visit for the training costs
            tr.on_map(t, value)
        elif kind == "exp":                      # EXP bar on a quick look: kills
            tr.on_exp(t, value["cur"], value["pct"])
        elif kind == "party" and len(value) > 1 and tr.visit is not None:
            tr.visit.party = True                # EXP here includes party shares

    def on_progress(self, level, cur, need, **_):
        if self.training is not None:
            self.training.set_progress(level, cur, need)


def make_panel(root, get, put, **kw):
    """PanelHost's factory: the panel's module (PIL and all) loads only when it is switched on."""
    from components.training.training_ui import TrainingPanel
    return TrainingPanel(root, get, put, **kw)
