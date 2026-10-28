"""discord component: Rich Presence ("Playing MapleStory" with your level and map) from the helper window.

Helper side only (ui): Settings > Discord, and a presence updated with every refresh while it is on.
Off by default; nothing is sent until the player turns it on (and Settings > Internet allows it).
The tracker has nothing to attach.
"""
from base.contract import Component
from hub import helper_settings as HS


class DiscordComponent(Component):
    name = "discord"
    settings_defaults = {"discord": HS.DEFAULTS["discord"]}     # the store keeps the slices in one file

    def ui(self, shell):
        self.shell = shell
        self.presence = None          # discord_rpc.Presence, created only when Discord is on
        shell.add_settings_page("Discord", self.page, after="Character")
        shell.on_settings_save(self.on_save)
        shell.on_refresh(self.update)
        shell.on_exit(self.close)

    def page(self, win, frame):
        from components.discord.settings_page import DiscordPage
        return DiscordPage(win, self).build(frame)

    def on_save(self, win):
        win.s["discord"]["client_id"] = str(win.s["discord"]["client_id"]).strip()

    def update(self, snap):
        sh = self.shell
        d = sh.settings("discord")
        cid = str(d.get("client_id") or "").strip()
        if d.get("enabled") and cid.isdigit() and not sh.demo:
            if self.presence is None:
                from components.discord.discord_rpc import Presence
                self.presence = Presence()
            from components.discord import text
            self.presence.update(cid, text.build_activity(snap, sh.all_settings()))
        elif self.presence is not None:
            self.presence.disable()

    def close(self):
        if self.presence:
            import time
            self.presence.disable()
            time.sleep(1.5)           # let the worker clear the status before we go
            self.presence.stop()
