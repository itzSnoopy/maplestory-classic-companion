"""compose.py - builds the tracker service: the hub, the components and the source.

The one place that knows every layer. hub/tracker.py only sees a Hub and a Source; the components
(components/__init__.py) are attached here and the source is built from config.json here.

    make_hub(root=None)         Hub with every registered component attached (tests call this too)
    build_source(cfg, log)      the Source config.json asks for (Tesseract set up first)
    run_service(cfg, args)      what `companion.py --service` runs
    service_main(argv)          argument parsing for `--service` and for `python -m hub.tracker`
"""
import argparse


def make_hub(root=None, components=None):
    """A Hub whose components are attached. root: where outputs go (tests: a scratch folder;
    default is the data folder). components: classes to attach instead of the registry's."""
    import components as registry
    from hub import tracker
    from hub.bus import Hub
    if root is not None:
        tracker.set_root(root)
    hub = Hub(tracker.ROOT, tracker.log)
    for cls in (registry.load_all() if components is None else components):
        c = cls()
        c.attach(hub)
        hub.components.append(c)
    return hub


def build_source(cfg, log):
    from engine import ocr
    from engine.sources import make_source, pick_type
    if not ocr.setup(cfg.get("tesseract")):
        log("WARNING: Tesseract not available: map names and some EXP digits will be unread")
    return make_source({**cfg, "source": {**cfg.get("source", {}), "type": pick_type(cfg, log)}})


def run_service(cfg, args, source=None, sinks=None, hub=None):
    from hub import tracker
    hub = hub or make_hub()
    # built inside tracker.run once its lock is held, so a second copy never opens the game window
    source = source or (lambda: build_source(cfg, tracker.log))
    tracker.run(cfg, args, source=source, sinks=sinks, hub=hub)


def service_main(argv=None):
    from base.paths import load_config
    from hub import tracker
    ap = argparse.ArgumentParser(description=tracker.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--interval", type=float, help="slowest capture interval (overrides config)")
    ap.add_argument("--level", type=int, help="current level, if the level crop isn't reliable")
    ap.add_argument("--service", action="store_true",
                    help="background mode: log to tracker_live.log (implied under pythonw)")
    a = ap.parse_args(argv)
    run_service(load_config(a.config), a)
