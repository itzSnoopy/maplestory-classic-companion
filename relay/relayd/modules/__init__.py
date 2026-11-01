"""Room modules: small features a room can switch on. Each room lists the modules it uses."""


class Module:
    name = ""
    persistent = False      # True = keeps state in SQLite (module_state); False = memory only

    def on_sync(self, room, member, part, now):
        """Called on every sync of an enabled module. `part` is what the client sent for it (or None).
        Return the reply part (dict) or None."""
        return None

    def actions(self):
        """{action name: fn(room, member, body, now) -> dict} for POST /v2/rooms/<id>/<module>/<action>."""
        return {}

    def tick(self, room, now):
        pass

    def admin_view(self, room):
        return {}

    def online(self, room_id, now):
        """Rows within their TTL (lobby 'online'); 0 when the module has no such idea."""
        return 0

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
