"""Data sources for the tracker. The user picks wgc/OBS in the helper's Settings > General
(helper_settings.json capture.source); config.json "source": {"type": "<name>"} overrides it.

To add a source: implement sources.base.Source (only `read(hints) -> Observation` is
required), then register a factory below. Nothing else in the tracker needs to change.
"""
from sources.base import ABSENT, OK, UNAVAILABLE, Observation, Source  # noqa: F401


def _screen(cfg, **kw):
    from sources.screen import ScreenSource
    return ScreenSource(cfg, **kw)


def _wgc(cfg, **kw):
    from sources.screen import ScreenSource
    from sources.wgc import WgcFrames
    return ScreenSource(cfg, frames=kw.pop("frames", None) or WgcFrames(cfg), **kw)


def _csv_replay(cfg, **kw):
    from sources.csv_replay import CsvReplaySource
    return CsvReplaySource(cfg, **kw)


REGISTRY = {
    "screen": _screen,            # OBS frames -> glyph/Tesseract HUD reading
    "wgc": _wgc,                  # game window via Windows Graphics Capture -> same reading (default)
    "csv_replay": _csv_replay,   # replay a recorded exp_log.csv (tests, regression checks)
}


DEFAULT_TYPE = "wgc"
SETTING_TO_TYPE = {"wgc": "wgc", "obs": "screen"}    # helper_settings capture.source -> type


def pick_type(cfg, log=print):
    """Which source the tracker uses. config.json "source.type" wins (tests, csv replay); else the
    helper setting capture.source (Settings > General); else window capture. Window capture
    without the windows-capture package falls back to OBS."""
    kind = cfg.get("source", {}).get("type")
    why = "config.json"
    if not kind:
        try:
            import helper_settings
            kind = SETTING_TO_TYPE.get(helper_settings.load()["capture"]["source"], DEFAULT_TYPE)
            why = "helper setting"
        except Exception:
            kind, why = DEFAULT_TYPE, "default"
    if kind == "wgc":
        try:
            import windows_capture  # noqa: F401
        except ImportError:
            log("window capture needs `pip install windows-capture`; using OBS instead")
            return "screen"
    if kind == "screen":
        try:
            import obsws_python  # noqa: F401
        except ImportError:
            log("OBS support isn't installed in this build; using window capture instead")
            return "wgc"
    log(f"source: {kind} ({why})")
    return kind


def make_source(cfg, **kw):
    kind = cfg.get("source", {}).get("type", DEFAULT_TYPE)
    if kind not in REGISTRY:
        raise ValueError(f"unknown source type {kind!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[kind](cfg, **kw)
