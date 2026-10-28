"""Data sources for the tracker. Default: `wgc` (the game window); config.json
"source": {"type": "<name>"} picks another (tests, csv replay).

To add a source: implement sources.base.Source (only `read(hints) -> Observation` is
required), then register a factory below. Nothing else in the tracker needs to change.
"""
from engine.sources.base import ABSENT, OK, UNAVAILABLE, Observation, Source  # noqa: F401


def _wgc(cfg, translate, **kw):
    from engine.sources.screen import ScreenSource
    from engine.sources.wgc import WgcFrames
    return ScreenSource(cfg, kw.pop("frames", None) or WgcFrames(cfg), translate, **kw)


def _csv_replay(cfg, translate, **kw):         # replays finished readings: nothing to translate
    from engine.sources.csv_replay import CsvReplaySource
    return CsvReplaySource(cfg, **kw)


REGISTRY = {
    "wgc": _wgc,                  # game window via Windows Graphics Capture -> same reading (default)
    "csv_replay": _csv_replay,   # replay a recorded exp_log.csv (tests, regression checks)
}


DEFAULT_TYPE = "wgc"


def pick_type(cfg, log=print):
    """Which source the tracker uses: config.json "source.type" if set (tests, csv replay), else
    window capture."""
    kind = cfg.get("source", {}).get("type")
    why = "config.json"
    if not kind:
        kind, why = DEFAULT_TYPE, "default"
    if kind == "wgc":
        try:
            import windows_capture  # noqa: F401
        except ImportError:
            log("window capture needs `pip install windows-capture`")
    log(f"source: {kind} ({why})")
    return kind


def make_source(cfg, translate, **kw):
    """translate: gamedata.translate, handed in by launcher/compose.py (the engine knows no game names)."""
    kind = cfg.get("source", {}).get("type", DEFAULT_TYPE)
    if kind not in REGISTRY:
        raise ValueError(f"unknown source type {kind!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[kind](cfg, translate, **kw)
