"""compose.py - builds the tracker service: the hub, the components and the source.

The one place that knows every layer. hub/tracker.py only sees a Hub and a Source; the components
(components/__init__.py) are attached here and the source is built from config.json here.

    make_hub(root=None)         Hub with every registered component attached (tests call this too)
    build_source(cfg, log)      the Source config.json asks for (Tesseract set up first)
    screen_source(cfg, frames)  a ScreenSource on given frames (tests, benches): the engine with the
                                game-name translator (gamedata/translate.py) handed in
    run_service(cfg, args)      what `companion.py --service` runs
    service_main(argv)          argument parsing for `--service` and for `python -m hub.tracker`
    helper_main(argv)           what `companion.py` (no --service) runs: the helper window, handed the
                                components, the updater, the setup window ... (HelperServices)
    portrait_maker()            the character close-up finder for saved frames (diary backfill; hub/tools.py asks)
    page_host()                 the diary server with every component's pages registered, for a process
                                that has no helper (hub/pages.py asks for it by name)
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
    from gamedata import translate
    return make_source({**cfg, "source": {**cfg.get("source", {}), "type": pick_type(cfg, log)}}, translate)


def screen_source(cfg, frames):
    """The one way to get a ready ScreenSource when the frames are not the live game window."""
    from engine.sources.screen import ScreenSource
    from gamedata import translate
    return ScreenSource(cfg, frames, translate)


def portrait_maker():
    """fn(frame) -> the character close-up of a saved full frame, or None (engine/portrait.py)."""
    from engine.portrait import NameTag
    return NameTag().portrait


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


class HelperServices:
    """What the helper window (hub/helper_ui.py) needs from the layers above and below it. Everything is
    imported when first used, so the helper starts as light as before."""

    def components(self):
        """The component classes whose ui(shell) joins the helper."""
        import components as registry
        return registry.load_all()

    @property
    def updater(self):
        from launcher import updater
        return updater

    def setup_window(self, app, on_done):
        from launcher.setup_ui import SetupWindow
        return SetupWindow(app, on_done=on_done)

    def whats_new(self, app, after_update):
        from launcher import whats_new
        return whats_new.check(app, after_update)

    def needs_setup(self):
        from engine import calibrate
        return calibrate.needs_setup()

    @property
    def capture(self):
        """Settings > General's "screen checks per second" (rate limits, CPU text)."""
        from engine import capture
        return capture


def helper_main(argv=None):
    from hub import helper_ui
    helper_ui.main(argv, services=HelperServices())


def page_host():
    """Register every component's pages (a headless shell: no window) and the diary component
    makes the diary server the page host. Returns the host."""
    import components as registry
    from hub import pages
    from hub.ui.shell import Shell
    shell = Shell()
    for cls in registry.load_all():
        try:
            cls().ui(shell)
        except Exception as e:
            print(f"{getattr(cls, 'name', cls)}: ui() failed: {e}")
    return pages.current()      # the diary component's ui() set it
