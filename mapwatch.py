"""mapwatch.py - when to look at the map name, and when a new one counts ("stickiness").

The longer the player stays in a map, the less likely they leave in the next few seconds (long
grinds), so the minimap title is checked often right after a change and less often after:

    interval = min_s + step_s * (seconds in this map // every_s), at most max_s

Defaults: every 1 s, +1 s for each 5 s in the map, up to every 15 s (reached after ~70 s).
Maps passed through on the way somewhere are caught too (they used to fall between readings).

Settings: helper_settings.json -> "map_check" {min_s, max_s, step_s, every_s}. min/max are in the
helper's Settings > General; all four are re-read on every check (no restart needed).

A new name counts once two checks in a row agree (a fade between maps can garble one read). The
first name after start counts at once. Checks happen in sources/screen.py: on readings, and on the
~1/s death-box poll between them (same frame, so no extra capture). A check costs ~12 ms CPU
(full frame + find the minimap); Tesseract only runs when the title crop changed.
"""
import json
import os

DEFAULTS = {"min_s": 1, "max_s": 15, "step_s": 1, "every_s": 5}
_cache = {"key": None, "val": dict(DEFAULTS)}


def settings(path=None):
    """{min_s, max_s, step_s, every_s} from helper_settings.json, cached by file time."""
    if path is None:
        from paths import DATA
        path = DATA / "helper_settings.json"
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        return dict(DEFAULTS)
    if key != _cache["key"]:
        try:
            m = json.loads(open(path, encoding="utf-8").read()).get("map_check", {})
        except (OSError, ValueError, AttributeError):
            m = {}
        v = {}
        for k, d in DEFAULTS.items():
            try:
                v[k] = float(m.get(k, d))
            except (TypeError, ValueError):
                v[k] = float(d)
        v["min_s"] = max(0.5, v["min_s"])
        v["max_s"] = max(v["min_s"], v["max_s"])
        v["step_s"] = max(0.0, v["step_s"])
        v["every_s"] = max(0.5, v["every_s"])
        _cache.update(key=key, val=v)
    return dict(_cache["val"])


class MapWatch:
    def __init__(self, settings_fn=settings):
        self.settings = settings_fn
        self.name = self.raw = self.map_id = None    # the confirmed map
        self.since = None                            # when it was confirmed
        self.candidate = None                        # (name, raw, map_id) seen once
        self.next_t = 0.0

    def interval(self, t):
        s = self.settings()
        stay = 0.0 if self.since is None else max(0.0, t - self.since)
        return min(s["max_s"], s["min_s"] + s["step_s"] * (stay // s["every_s"]))

    def due(self, t):
        return t >= self.next_t

    def update(self, t, name, raw="", map_id=None):
        """Result of one check (name None: no minimap / no title). True when a new map counts."""
        changed = False
        if name is None or name == self.name:
            self.candidate = None
            if name is not None and self.since is None:     # back after a reset, same map
                self.since = t
        elif self.name is None or (self.candidate and self.candidate[0] == name):
            self.name, self.raw, self.map_id = name, raw, map_id
            self.since, self.candidate, changed = t, None, True
        else:
            self.candidate = (name, raw, map_id)
        # look again soon to confirm a candidate; otherwise follow the slowing schedule
        wait = self.settings()["min_s"] if self.candidate else self.interval(t)
        self.next_t = t + wait
        return changed

    def reset(self):
        """Game left the screen: start fast again when it's back (new map likely)."""
        self.since = None
        self.next_t = 0.0
