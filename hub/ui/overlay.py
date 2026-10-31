"""overlay.py - what the windows drawn over the game have in common (skill bar, party panel, training
panel): a transparent-colour key, outlined text and fonts for PIL-drawn strips, and PanelHost, which
creates / removes such a panel to match its setting and gives the helper's menu its two toggles.

This module imports no PIL at load (font() does when called): the components' ui(shell) imports it
when the helper starts, the panels themselves only when one is switched on.
"""
KEY = "#010203"                 # transparent colour of the overlay windows


def font(size, bold=True):
    from PIL import ImageFont
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def text(d, xy, s, font, fill=(255, 255, 255, 255), anchor="mm"):
    """Draw s with a 1 px black outline (readable on any game background)."""
    x, y = xy
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        d.text((x + dx, y + dy), s, font=font, fill=(0, 0, 0, 255), anchor=anchor)
    d.text((x, y), s, font=font, fill=fill, anchor=anchor)


class PanelHost:
    """One panel over the game, switched by helper_settings.json[name]["enabled"].

    factory(root, get, put, k=, demo=, at=) -> a window object with destroy() and set_locked();
    it is imported and called only when the panel is first wanted. dy: where the panel starts in dev
    runs (--at), below the helper window, so the three don't overlap. what: wording for messages.
    """
    def __init__(self, shell, name, what, factory, dy=400):
        self.shell, self.name, self.what, self.factory, self.dy = shell, name, what, factory, dy
        self.panel = None

    def setting(self, key):
        return self.shell.settings(self.name).get(key)

    def view(self):
        """What the panel sees of its setting: switched off while the helper is an icon that hides panels."""
        cfg = self.shell.settings(self.name)
        return {**cfg, "enabled": False} if self.shell.panels_hidden else cfg

    def put(self, **kw):
        """Change the panel's settings (from the panel itself or the tray) and save them."""
        self.shell.save_settings(self.name, **kw)
        self.sync()

    def sync(self):
        """Create / remove the panel to match the setting."""
        sh = self.shell
        on = bool(self.setting("enabled"))
        if on and self.panel is None:
            try:
                at = (sh.at[0], sh.at[1] + self.dy) if sh.at else None
                self.panel = self.factory(sh.root, self.view, self.put,
                                          k=sh.k, demo=sh.demo, at=at)
            except Exception as e:
                print(f"{self.what} unavailable: {e}")
                sh.set_setting(self.name, enabled=False)
        elif not on and self.panel is not None:
            self.panel.destroy()
            self.panel = None

    def toggle(self):
        self.put(enabled=not self.setting("enabled"))

    def toggle_lock(self):
        self.put(locked=not self.setting("locked"))
        if self.panel:
            self.panel.set_locked(self.setting("locked"))

    def menu(self, cmd, label, lock_label):
        """The two tray / right-click entries (menu_items shape) and their commands on the shell."""
        sh = self.shell
        sh.add_command(cmd, self.toggle)
        sh.add_command(cmd + "_lock", self.toggle_lock)
        return [(label, cmd, lambda: self.setting("enabled"), None, None),
                (lock_label, cmd + "_lock", lambda: self.setting("locked"),
                 lambda: self.setting("enabled"), None)]
