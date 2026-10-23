"""Settings for helper_ui (helper_settings.json) and the Discord presence text builder.

Kept separate from config.json on purpose: config.json holds the OBS password and the
tracker's crop boxes, and the settings window should never rewrite it.
The tracker reads one value from here at start: capture.source (see sources.pick_type).
"""
import copy
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "helper_settings.json"

DEFAULTS = {
    "capture": {
        "source": "wgc",                # wgc = game window (no OBS) · obs = OBS Window Capture
    },
    "character": {
        "name": "SnoopySparda",
        "job": "Beginner",
        "job_auto": True,               # use the job the tracker reads from the status bar
        "channel": "",
    },
    "discord": {
        "enabled": False,               # stays off until the user turns it on
        "client_id": "",                # Application ID from discord.com/developers
        "app_name": "MapleStory Classic",   # preview only; Discord shows the app's real name
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
}

PLACEHOLDERS = {
    "{name}": "character name",
    "{job}": "job",
    "{level}": "level",
    "{pct}": "EXP %",
    "{map}": "map",
    "{channel}": "channel",
    "{eta}": "time to next level",
    "{rate}": "EXP/hr",
}

PCT_STYLES = {"whole": "66%", "one": "66.6%", "two": "66.61%"}
SHOW_FIELDS = [("name", "Name"), ("job", "Job"), ("level", "Level"), ("pct", "EXP %"),
               ("map", "Map"), ("channel", "Channel")]


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


# ------------------------------------------------------------------ presence text
def fmt_pct(pct, style):
    if pct is None or style == "hidden":
        return ""
    if style == "one":
        return f"{int(pct * 10) / 10:.1f}%"      # truncate, never round up to 100%
    if style == "two":
        return f"{pct:.2f}%"
    return f"{int(pct)}%"


def values(snap, settings):
    """Placeholder values from a helper_ui Snapshot (or None) plus the character settings."""
    ch = settings["character"]
    d = settings["discord"]
    v = {"name": ch.get("name", ""), "job": job(snap, settings),
         "channel": str(ch.get("channel") or "")}
    if snap is not None:
        from helper_ui import fmt_dur
        v.update({
            "level": str(snap.level) if snap.level else "",
            "pct": fmt_pct(snap.pct, d.get("pct_style", "whole")),
            "map": snap.map or "",
            "eta": f"~{fmt_dur(snap.eta_s)}" if snap.eta_s is not None else "",
            "rate": f"{snap.rate:,} EXP/hr" if snap.rate else "",
        })
    for key, on in d.get("show", {}).items():
        if not on:
            v[key] = ""
    return v


_TOKEN = re.compile(r"\{(\w+)\}")


def render(template, vals):
    """Fill a template, dropping empty or hidden fields cleanly.
    Words whose placeholder is empty are removed together with a plain label word right
    before them ('Lv {level}', 'Ch {channel}'); parts between ' · ' that end up empty
    disappear with their separator."""
    parts = []
    for part in (template or "").split(" · "):
        words, keep = part.split(" "), []
        for w in words:
            names = _TOKEN.findall(w)
            if names and any(not vals.get(n) for n in names):
                if keep and not _TOKEN.search(keep[-1][0]) and keep[-1][1]:
                    keep.pop()                      # drop the label too ("Lv", "Ch")
                continue
            keep.append((w, not names))
        text = " ".join(_TOKEN.sub(lambda m: vals.get(m.group(1), ""), w) for w, _ in keep).strip()
        if text and any(_TOKEN.search(w) for w, _ in keep):
            parts.append(text)
        elif text and not _TOKEN.search(part):      # pure literal part, keep as typed
            parts.append(text)
    return " · ".join(parts)


def build_activity(snap, settings):
    """The Discord activity dict, or None to clear the presence."""
    d = settings["discord"]
    playing = snap is not None and snap.state == "playing"
    if snap is None or snap.state in ("stopped", "stale"):
        return None                     # tracker off or stuck: probably not in game at all
    if not playing and d.get("when_not_playing", "clear") == "clear":
        return None
    vals = values(snap, settings)
    act = {}
    if playing:
        details = render(d.get("details"), vals)
        state = render(d.get("state"), vals)
    else:
        details = render("{name} · Lv {level} {job}", vals)
        state = "Idle"
    # Discord wants 2–128 chars per line
    if len(details) >= 2:
        act["details"] = details[:128]
    if len(state) >= 2:
        act["state"] = state[:128]
    large = render(d.get("large_text"), vals)
    if d.get("large_image") and len(large) >= 2:      # hover text needs an image to show on
        act["assets"] = {"large_image": d["large_image"], "large_text": large[:128]}
    if playing and d.get("show_elapsed") and getattr(snap, "session_start", None):
        act["timestamps"] = {"start": int(snap.session_start)}
    return act
