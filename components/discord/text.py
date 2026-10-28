"""text.py - the Discord Rich Presence text: placeholders, the per-field toggles and the activity dict
that discord_rpc.Presence sends. (It was part of hub/helper_settings.py.)"""
import re

from hub import helper_settings as HS

MAPLESTORY_APP_ID = HS.MAPLESTORY_APP_ID


def uses_game_app(d):
    return str(d.get("client_id") or "").strip() == MAPLESTORY_APP_ID


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
    v = {"name": ch.get("name", ""), "job": HS.job(snap, settings),
         "channel": str(ch.get("channel") or "")}
    if snap is not None:
        from hub.helper_ui import fmt_dur
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
    if d.get("large_image") and len(large) >= 2 and not uses_game_app(d):
        # hover text needs an image to show on; the game's app has no art we may use,
        # so Discord shows the game's own icon there
        act["assets"] = {"large_image": d["large_image"], "large_text": large[:128]}
    if playing and d.get("show_elapsed") and getattr(snap, "session_start", None):
        act["timestamps"] = {"start": int(snap.session_start)}
    return act
