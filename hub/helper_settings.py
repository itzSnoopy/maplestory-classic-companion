"""Settings for helper_ui (helper_settings.json): defaults, load, save. (The Discord presence text
builder is components/discord/text.py.)

Kept separate from config.json on purpose: config.json holds the tracker's crop boxes and
timings, and the settings window should never rewrite it.
"""
import copy
import json
import os
from pathlib import Path

from base.paths import DATA as ROOT
SETTINGS = ROOT / "helper_settings.json"

# Discord's own "MapleStory" entry (detectable-games list, matches maplestory.exe). Sending
# presence under it puts our lines on the "Playing MapleStory" card Discord already shows.
MAPLESTORY_APP_ID = "359510095811444736"


DEFAULTS = {
    "character": {
        "name": "",
        "job": "Beginner",
        "job_auto": True,               # use the job the tracker reads from the status bar
        "channel": "",
    },
    "discord": {
        "enabled": False,               # stays off until the user turns it on
        "client_id": MAPLESTORY_APP_ID, # the game's own Discord entry; or your own app's ID
        "app_name": "MapleStory Classic",   # preview only, own app; Discord shows its real name
        "details": "{name} · Lv {level} {job}",
        "state": "{map} · Ch {channel} · {pct}",
        "large_text": "MapleStory Classic World",
        "large_image": "icon",          # art asset key in the Discord app; "" = app has no images
        "show": {"name": True, "job": True, "level": True, "pct": True, "map": True,
                 "channel": True},      # per-field toggles; a hidden field drops out of the text
        "pct_style": "whole",           # whole 66% · one 66.6% · two 66.61%
        "show_elapsed": True,
        "when_not_playing": "clear",    # clear (no presence) or idle (show "Idle")
    },
    "diary": {                          # Settings > Diary (diary_popup.py); the tracker reads the
        "moments": True,                # moment timing on every check (diary.moment_settings)
        "moment_min": 45,               # random moment every min..max minutes of active play
        "moment_max": 120,
        "popup": True,                  # card next to the helper window on new diary entries
        "popup_seconds": 8,
        "open_via": "localhost",        # localhost (127.0.0.1, no folder paths shown) or file
    },
    "capture": {                        # Settings > General slider; capture.py (tracker follows live)
        "fps": 2,                       # screen checks per second while playing, 1-60 (0.4.0: 2)
    },
    "internet": {                       # Settings > Internet; internet.py (one toggle per feature)
        "allowed": True,                # master switch: off = no internet connections at all
        "features": {"updates": True,   # automatic updates (updater.py, packaged app only)
                     "party": True,     # party link (partylink.py; needs server + code below)
                     "icons": True,     # item pictures for the training panel (itemicons.py)
                     "feedback": True}, # skill review "Send" (components/feedback; needs server + code)
    },
    "updates": {                        # Settings > Internet > Automatic updates; updater.py
        "min_s": 30,                    # first check 30 s after start, then doubling while
        "max_s": 300,                   # nothing is new, up to every 5 min
    },
    "skillbar": {                       # Settings > General / tray; skillbar_ui.py over the game
        "enabled": True,                # HP/MP regen wheels + casts left, fed by live.json (on by default)
        "locked": False,                # click-through (clicks go to the game underneath)
        "x": None, "y": None,           # screen position; None = top-center
        "hidden": [],                   # skill ids the player removed from the bar (right-click)
        "show": "casts",                # skill corner: casts (left) | mp (per cast) | mesos (per cast)
    },
    "party": {                          # Settings > Internet > Party link; partylink.py (tracker)
        "server": "",                   # your relay, e.g. http://203.0.113.5:8787 (relay/README.md)
        "code": "",                     # same code on every PC in the party
    },
    "feedback": {                       # Settings > Internet > Skill feedback; components/feedback
        "server": "",                   # the owner's relay (relay/README.md); no fallback to the party server
        "code": "",                     # feedback code the owner gives reviewers
    },
    "trainingpanel": {                  # Settings > General / tray; training_ui.py over the game
        "enabled": False,               # what training on this map costs (optional, user 2026-10-25)
        "locked": False,
        "x": None, "y": None,           # None = left side, under the party panel
    },
    "partypanel": {                     # Settings > General / tray; party_ui.py over the game
        "enabled": True,                # shows only while a party box or partner is seen
        "friends": True,                # compact list of everyone else with the code (hangout)
        "locked": False,
        "x": None, "y": None,           # None = left side, under the minimap
    },
    "map_check": {                      # Settings > General; the tracker re-reads it (mapwatch.py)
        "min_s": 1,                     # check the map name every min_s right after a change,
        "max_s": 15,                    # slowing to every max_s the longer you stay:
        "step_s": 1,                    # +step_s for each every_s in the map (file only)
        "every_s": 5,
    },
    "regen_check": {                    # vitals.Meter: the HP/MP regen clock's checks ("stickiness")
        "min_ticks": 1,                 # after a tick is caught, check every min_ticks ticks,
        "max_ticks": 6,                 # spreading to every max_ticks (1 min) while on time:
        "step": 2,                      # +1 tick per `step` on-time checks; a map / channel
        "burst_fps": 10,                # change starts over. A check = HP/MP strip only, at
        "pad_s": 0.6,                   # burst_fps for +-pad_s around the predicted tick
    },
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    s = _merge(DEFAULTS, data)
    if s["discord"].get("pct_style") == "hidden":          # older setting -> toggle
        s["discord"]["pct_style"] = "whole"
        s["discord"]["show"]["pct"] = False
    return s


def job(snap, settings):
    """The job to show: read from the screen when auto is on and known, else the typed one."""
    ch = settings["character"]
    if ch.get("job_auto", True) and snap is not None and getattr(snap, "job", None):
        return snap.job
    return ch.get("job", "")


def save(settings):
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SETTINGS)
