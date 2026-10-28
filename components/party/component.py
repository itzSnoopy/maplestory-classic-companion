"""party component: the party link thread (partylink.py) shares your numbers with your party through
the relay you host, and writes party.json. Opt-in (Settings > Internet > Party link).

Subscribes: service_start (starts the thread), service_stop (stops it).
"""
from base.contract import Component
from components.party import partylink


class PartyComponent(Component):
    name = "party"

    def attach(self, hub):
        self.hub = hub
        self.link = None
        hub.on("service_start", self.on_start)
        hub.on("service_stop", self.on_stop)

    def on_start(self, **_):
        self.link = partylink.PartyLink(log=self.hub.log)    # party.json: partners' numbers (relay/, opt-in)
        self.link.start()

    def on_stop(self, **_):
        if self.link is not None:
            self.link.stop()
