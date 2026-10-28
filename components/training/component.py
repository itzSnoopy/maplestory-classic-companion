"""training component: the training cost panel over the game (training_ui.py): what this map costs you
(kills, MP per cast, potions and their price, per kill and per 1,000 EXP).

Helper side only (ui): the panel, its two menu toggles and its line in Settings > General. The numbers
come from live.json, which the skillbar component's feed writes (hub/live.py runs training.py); the
tracker has nothing to attach here.
"""
import copy

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


def make_panel(root, get, put, **kw):
    """PanelHost's factory: the panel's module (PIL and all) loads only when it is switched on."""
    from components.training.training_ui import TrainingPanel
    return TrainingPanel(root, get, put, **kw)
