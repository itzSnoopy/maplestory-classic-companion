"""shell.py - the helper window as seen by a component: Component.ui(shell) joins a feature to it.

The helper (hub/helper_ui.py App) owns one Shell. Components call, from ui(shell) only:

  shell.add_menu(items, slot)       entries for the tray and the right-click menu (menu_items shape:
                                    (label or fn, command, checked fn, enabled fn, visible fn)). slot
                                    "panels" = the on/off toggles, "open" = the "Open ..." entries
  shell.add_command(cmd, fn)        what a menu command does (runs in the window loop)
  shell.add_settings_page(name, build, after)   a page of the Settings window; build(win, frame) -> frame
  shell.add_settings_extra(slot, build, order)  a piece inside a core page: slot "General", or
                                    "internet.<feature>" under that feature's toggle; build(win, frame)
  shell.on_settings_save(fn)        fn(win) when Save is pressed, before the settings are written
  shell.on_refresh(fn, early)       fn(snapshot) about once a second (early: before the window redraws)
  shell.on_exit(fn)                 fn() when the helper closes
  shell.add_page(path, render)      a page for the local diary server (hub/pages.py)
  shell.set_camera(fn)              what the helper window's camera button does (a diary moment)
  shell.settings(name) / all_settings() / save_settings(name, **kw) / set_setting(name, **kw)
                                    the component's slice of helper_settings.json (same keys as ever)
  shell.post(cmd), shell.notify(text), shell.root, shell.k, shell.px(n), shell.at, shell.demo,
  shell.visible, shell.log_error(where)

Nothing here draws; a component builds its own widgets with the root and the settings window's
helpers. Without a window (the page server's own process) the Shell is "headless": root is None
and ui() must not create windows.
"""
from hub import pages


class Shell:
    def __init__(self, app=None):
        self.app = app
        self.menus = {"panels": [], "open": []}
        self.commands = {}
        self.settings_pages = []        # (name, build, after)
        self.extras = {}                # slot -> [(order, build)]
        self.save_hooks = []
        self.early_hooks = []
        self.refresh_hooks = []
        self.exit_hooks = []
        self.camera = None              # fn() behind the camera button

    # -- what the app offers
    @property
    def headless(self):
        return self.app is None

    @property
    def root(self):
        return self.app.root if self.app else None

    @property
    def k(self):
        return self.app.k if self.app else 1.0

    @property
    def demo(self):
        return self.app.demo if self.app else False

    @property
    def at(self):
        return self.app.dev_at if self.app else None

    @property
    def visible(self):
        return self.app.visible if self.app else False

    def px(self, n):
        return int(round(n * self.k))

    def post(self, cmd):
        if self.app:
            self.app.post(cmd)

    def notify(self, text):
        if self.app:
            self.app.notify(text)

    def log_error(self, where):
        if self.app:
            self.app.log_error(where)

    # -- settings (helper_settings.json)
    def all_settings(self):
        return self.app.settings if self.app else {}

    def settings(self, name):
        return self.all_settings().get(name) or {}

    def set_setting(self, name, **kw):
        """Change values in memory only."""
        if self.app:
            self.app.settings.setdefault(name, {}).update(kw)

    def save_settings(self, name, **kw):
        """Change values and write helper_settings.json (not in dev runs, which use --at)."""
        if not self.app:
            return
        self.set_setting(name, **kw)
        if not self.app.dev_at:
            try:
                self.app.HS.save(self.app.settings)
            except Exception as e:
                print(f"{name} settings save failed: {e}")

    # -- registration
    def add_menu(self, items, slot="panels"):
        self.menus[slot].extend(items)

    def add_command(self, cmd, fn):
        self.commands[cmd] = fn

    def add_settings_page(self, name, build, after=None):
        self.settings_pages.append((name, build, after))

    def add_settings_extra(self, slot, build, order=50):
        self.extras.setdefault(slot, []).append((order, build))

    def on_settings_save(self, fn):
        self.save_hooks.append(fn)

    def on_refresh(self, fn, early=False):
        (self.early_hooks if early else self.refresh_hooks).append(fn)

    def on_exit(self, fn):
        self.exit_hooks.append(fn)

    def add_page(self, path, render):
        pages.register_page(path, render)

    def add_api(self, path, handler):
        """A local POST endpoint on the page server: handler(req_dict) -> (status, obj)."""
        pages.register_api(path, handler)

    def set_camera(self, fn):
        self.camera = fn

    # -- used by the helper and the settings window
    def extras_for(self, slot):
        return [fn for _, fn in sorted(self.extras.get(slot, []), key=lambda x: x[0])]

    def page_names(self, core):
        """Names of the core pages with the components' pages placed after the page they name."""
        names, pending = list(core), list(self.settings_pages)
        while pending:
            placed = [p for p in pending if p[2] is None or p[2] in names]
            if not placed:
                names += [p[0] for p in pending]
                break
            for p in placed:
                names.insert(names.index(p[2]) + 1 if p[2] else len(names), p[0])
                pending.remove(p)
        return names

    def page_builder(self, name):
        return next((b for n, b, _ in self.settings_pages if n == name), None)
