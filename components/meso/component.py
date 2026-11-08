"""meso component (DEV ONLY, parked until after 1.0): a small floating meso counter checking the message
corner's meso reads against the inventory.

Helper side only: attach() does nothing. The panel tails history/gains.csv (written by components/gains in
the tracker: one row per corner line, kind=meso carries the pickup), adds each pickup to the last value
typed from the inventory, and logs a drift mark (estimate vs inventory) to history/meso_marks.csv.

Dev only: the menu entry exists in source runs and local test builds (version.TEST_BASE set), never in a
GitHub release.

Subscribes: nothing. Emits: nothing.
"""
from base import paths, version
from base.contract import Component


def dev_build():
    return (not paths.FROZEN) or bool(version.TEST_BASE)


class MesoComponent(Component):
    name = "meso"
    settings_defaults = {"meso": {"enabled": False, "locked": False, "x": None, "y": None}}

    def attach(self, hub):
        pass                                    # helper side only

    def ui(self, shell):
        if not dev_build():
            return
        from hub.ui.overlay import PanelHost
        self.host = host = PanelHost(shell, "meso", "meso counter", self.make_panel, dy=760)
        shell.add_menu(host.menu("meso", "Meso counter (dev)", "Lock meso counter"))
        shell.on_refresh(lambda snap: host.sync(), early=True)

    def make_panel(self, root, get, put, **kw):
        from components.meso.meso_ui import MesoPanel
        return MesoPanel(root, get, put, data=paths.DATA, **kw)
