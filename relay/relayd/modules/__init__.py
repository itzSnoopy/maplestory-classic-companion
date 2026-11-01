"""Room modules: small features a room can switch on. Each room lists the modules it uses."""


class Module:
    name = ""
    persistent = False      # True = keeps state in SQLite (module_state); False = memory only
    relay = None            # set by Relay: lets a module reach the store and READ other modules
                            # through a small public method (e.g. presence.live_rows). Never
                            # import another module's internals.

    def on_sync(self, room, member, part, now):
        """Called on every sync of an enabled module. `part` is what the client sent for it (or None).
        Return the reply part (dict) or None."""
        return None

    def actions(self):
        """{action name: fn(room, member, body, now) -> dict} for POST /v2/rooms/<id>/<module>/<action>."""
        return {}

    def tick(self, room, now):
        pass

    def active_rooms(self):
        """Room ids this module holds live state for (the hourly cleanup ticks them)."""
        return []

    def admin_view(self, room):
        return {}

    def online(self, room_id, now):
        """Rows within their TTL (lobby 'online'); 0 when the module has no such idea."""
        return 0

    def lobby_activity(self, room):
        """A short string for the lobby card ("Duel running") or None. Called for every listed room
        on each lobby request: keep it cheap (no SQL, no loops over members)."""
        return None

    def owner_view(self, room, now):
        """Extra data for the owner / mods in GET /v2/rooms/<id>: {"activities": {kind: {label, minutes}},
        "current": a view or None} or None."""
        return None

    def on_member_removed(self, room_id, name):
        pass

    def on_room_deleted(self, room_id):
        pass


REGISTRY = {}       # name -> Module class


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


def build_registry():
    """Fresh module instances (state belongs to one server instance)."""
    return {name: cls() for name, cls in REGISTRY.items()}


from . import presence  # noqa: E402,F401  (registers itself)
from . import challenge  # noqa: E402,F401
