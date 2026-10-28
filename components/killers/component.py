"""killers component: answers the tracker's death_guess event with its best guess of who killed you."""
import traceback

from base.contract import Component
from components.killers import killers


class KillersComponent(Component):
    name = "killers"

    def attach(self, hub):
        self.hub = hub
        hub.on("death_guess", self.guess)

    def guess(self, map, level, killer, **_):
        """Fill `killer` (killers.attribute() plus label = the name when sure, else None). The journal
        and the diary read it from the death event; never breaks death tracking."""
        try:
            a = killers.attribute(map, level)
        except Exception:
            self.hub.log("killer guess failed: " + traceback.format_exc())
            return
        killer.update(a)
        killer["label"] = killers.label(a)
