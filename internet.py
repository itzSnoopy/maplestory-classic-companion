"""internet.py - the one switch for anything that goes online.

Every feature that connects to the internet asks `allowed("<feature>")` right before it connects.
It's True only when both are on (helper Settings > Internet; helper_settings.json "internet"):
  - "allowed": the master switch (off = the app makes no internet connections at all)
  - "features": {<feature>: on/off}, one toggle per feature listed in FEATURES

Re-read on every call (cached by file time), so a change in Settings applies at once.
Adding an online feature = an entry in FEATURES + `if internet.allowed("x"):` before connecting;
the Settings page lists it by itself. Local-only things (Discord talks to the Discord app on this
PC, the diary page on 127.0.0.1) aren't internet and aren't listed.
"""
import json
import os

# name -> what the Settings page shows. "hosts": every address the feature connects to.
FEATURES = {
    "updates": {
        "label": "Automatic updates",
        "what": "Checks this app's GitHub releases for a new version and downloads it. "
                "Nothing about you or your game is sent.",
        "hosts": ["github.com", "release-assets.githubusercontent.com"],
    },
}
DEFAULTS = {"allowed": True, "features": {name: True for name in FEATURES}}

_cache = {"key": None, "val": None}


def _path():
    from paths import DATA
    return DATA / "helper_settings.json"


def state(path=None):
    """{"allowed": bool, "features": {name: bool}} with defaults filled in."""
    path = path or _path()
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        key = None
    if key is None or key != _cache["key"]:
        raw = {}
        if key is not None:
            try:
                raw = json.loads(open(path, encoding="utf-8").read()).get("internet", {}) or {}
            except (OSError, ValueError, AttributeError):
                raw = {}
        feats = raw.get("features") if isinstance(raw.get("features"), dict) else {}
        val = {"allowed": bool(raw.get("allowed", DEFAULTS["allowed"])),
               "features": {n: bool(feats.get(n, DEFAULTS["features"][n])) for n in FEATURES}}
        _cache.update(key=key, val=val)
    v = _cache["val"]
    return {"allowed": v["allowed"], "features": dict(v["features"])}


def allowed(feature, path=None):
    """True if `feature` may connect right now (master switch and its own toggle both on)."""
    if feature not in FEATURES:
        raise KeyError(f"unknown internet feature {feature!r}: add it to internet.FEATURES")
    s = state(path)
    return s["allowed"] and s["features"].get(feature, False)
