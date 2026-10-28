"""settings_page.py - Settings > Discord: the fields and the live preview card of the Rich Presence.

DiscordPage(win, comp).build(frame) is called by the settings window (hub/settings_ui.py) through the
discord component's shell.add_settings_page; the window ticks it once a second (win.on_tick).
"""
import copy
import time
import tkinter as tk

from components.discord import text as T
from hub.ui.theme import BG, MUTED, GOLD, GREEN, RED, FIELD, TEXT, F

# Discord look for the preview card
DC_BG = "#232428"
DC_TEXT = "#f2f3f5"
DC_MUTED = "#b5bac1"
DC_GREEN = "#23a55a"


class DiscordPage:
    def __init__(self, win, comp):
        self.win, self.comp = win, comp
        self.preview_mode = tk.StringVar(value="playing")

    def build(self, f):
        win = self.win
        px = win.px
        win.title(f, "Discord status",
                  "Shows what you're doing on your Discord profile (Rich Presence). Text only: "
                  "no screenshots or game files are sent. Review the preview before turning it on.")
        cols = tk.Frame(f, bg=BG)
        cols.pack(fill="both", expand=True)
        left = tk.Frame(cols, bg=BG)
        left.pack(side="left", fill="y")
        right = tk.Frame(cols, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(px(24), 0))

        en = win.check(left, "discord.enabled", "Show on Discord")
        en.pack(anchor="w")
        self.en_note = win.label(left, "", muted=True, size=8, wrap=px(320))
        self.en_note.pack(anchor="w", pady=(0, px(8)))
        win.form_row(left, "Application ID", self.app_id_row,
                     "MapleStory's own ID adds your lines to the \"Playing MapleStory\" card "
                     "Discord already shows, with the game's icon. For a custom name and icon, "
                     "create an app at discord.com/developers and paste its Application ID.")
        win.form_row(left, "Line 1", lambda p: win.entry(p, "discord.details"))
        win.form_row(left, "Line 2", lambda p: win.entry(p, "discord.state"))
        win.form_row(left, "Icon hover text", lambda p: win.entry(p, "discord.large_text"))

        chips = tk.Frame(left, bg=BG)
        chips.pack(anchor="w", pady=(0, px(10)))
        win.label(chips, "Insert:", muted=True, size=8).pack(side="left", padx=(0, px(4)))
        for tok, desc in T.PLACEHOLDERS.items():
            c = tk.Label(chips, text=tok, font=("Consolas", 8), fg=GOLD, bg=FIELD, padx=px(4),
                         cursor="hand2")
            c.pack(side="left", padx=(0, px(3)))
            c.bind("<Button-1>", lambda e, t=tok: self.insert_token(t))

        win.form_row(left, "Show", self.show_toggles)
        win.form_row(left, "EXP % style", lambda p: win.radios(
            p, "discord.pct_style", [(k, v) for k, v in T.PCT_STYLES.items()]))
        win.check(left, "discord.show_elapsed", "Show elapsed play time").pack(anchor="w")
        win.form_row(left, "When you're not playing", lambda p: win.radios(
            p, "discord.when_not_playing", [("clear", "Hide status"), ("idle", 'Show "Idle"')]))

        # preview
        win.label(right, "PREVIEW", muted=True, size=8).pack(anchor="w")
        self.radios_preview(right).pack(anchor="w", pady=(px(2), px(6)))
        card = tk.Frame(right, bg=DC_BG, padx=px(14), pady=px(12))
        card.pack(anchor="nw", fill="x")
        self.pv_head = tk.Label(card, text="PLAYING A GAME", font=(F, 8, "bold"), fg=DC_MUTED,
                                bg=DC_BG, anchor="w")
        self.pv_head.pack(fill="x", pady=(0, px(8)))
        row = tk.Frame(card, bg=DC_BG)
        row.pack(fill="x")
        try:
            from PIL import ImageTk
            from hub.helper_ui import make_icon_image
            img = make_icon_image(GREEN, 256).resize((px(60), px(60)))
            self.pv_img = ImageTk.PhotoImage(img)
            self.pv_icon = tk.Label(row, image=self.pv_img, bg=DC_BG)
            self.pv_icon.pack(side="left", anchor="n")
        except Exception:
            pass
        txt = tk.Frame(row, bg=DC_BG)
        txt.pack(side="left", padx=(px(10), 0), anchor="n")
        self.pv = {}
        for key, font, color in (("app", (F, 10, "bold"), DC_TEXT), ("details", (F, 9), DC_TEXT),
                                 ("state", (F, 9), DC_TEXT), ("time", (F, 9), DC_GREEN)):
            l = tk.Label(txt, text="", font=font, fg=color, bg=DC_BG, anchor="w", justify="left",
                         wraplength=px(220))
            l.pack(anchor="w")
            self.pv[key] = l
        self.pv_hover = win.label(right, "", muted=True, size=8, wrap=px(300))
        self.pv_hover.pack(anchor="w", pady=(px(8), 0))
        self.pv_raw = tk.Label(right, text="", font=("Consolas", 8), fg=MUTED, bg=BG, anchor="w",
                               justify="left", wraplength=px(300))
        self.pv_raw.pack(anchor="w", pady=(px(8), 0))
        self.pv_status = win.label(right, "", muted=True, size=8, wrap=px(300))
        self.pv_status.pack(anchor="w", pady=(px(8), 0))
        win.on_tick(self.tick)
        return f

    def app_id_row(self, parent):
        win = self.win
        row = tk.Frame(parent, bg=BG)
        win.entry(row, "discord.client_id", 22).pack(side="left")
        b = win.button(row, "Use MapleStory's", lambda: win.vars["discord.client_id"].set(
            T.MAPLESTORY_APP_ID))
        b.configure(padx=win.px(8), pady=win.px(2), font=(F, 9))
        b.pack(side="left", padx=(win.px(6), 0))
        return row

    def show_toggles(self, parent):
        row = tk.Frame(parent, bg=BG)
        for key, text in T.SHOW_FIELDS:
            self.win.check(row, f"discord.show.{key}", text).pack(side="left", padx=(0, self.win.px(10)))
        return row

    def radios_preview(self, parent):
        row = tk.Frame(parent, bg=BG)
        for val, text in (("playing", "Playing"), ("not", "Not playing")):
            tk.Radiobutton(row, text=text, value=val, variable=self.preview_mode, font=(F, 9),
                           fg=TEXT, bg=BG, activebackground=BG, activeforeground=TEXT,
                           selectcolor=FIELD, highlightthickness=0, bd=0).pack(side="left",
                                                                               padx=(0, self.win.px(10)))
        return row

    def insert_token(self, tok):
        e = self.win.focused_entry
        if e is not None and e.winfo_exists():
            e.insert("insert", tok)
            e.focus_set()

    def tick(self):
        try:
            self.update_preview()
        except Exception as e:
            self.pv_status.configure(text=f"preview error: {e}", fg=RED)

    def update_preview(self):
        from hub.helper_ui import fmt_dur
        win = self.win
        d = win.s["discord"]
        snap = win.snapshot()
        if self.preview_mode.get() == "not":
            snap = copy.copy(snap)
            snap.state = "idle"
        act = T.build_activity(snap, win.s)
        game_app = T.uses_game_app(d)
        self.pv["app"].configure(text="MapleStory" if game_app else
                                 (d.get("app_name") or "MapleStory Classic"))
        img = d.get("large_image") and not game_app   # game app: Discord shows the game's icon
        if hasattr(self, "pv_icon"):
            self.pv_icon.configure(image=self.pv_img if img else "", width=0 if img else 1)
        if act is None:
            self.pv_head.configure(text="(NO STATUS SHOWN)")
            for k in ("details", "state", "time"):
                self.pv[k].configure(text="")
            self.pv["app"].configure(text="Nothing is shown while you're not playing.")
            self.pv_hover.configure(text="")
        else:
            self.pv_head.configure(text="PLAYING A GAME")
            self.pv["details"].configure(text=act.get("details", ""))
            self.pv["state"].configure(text=act.get("state", ""))
            ts = act.get("timestamps", {}).get("start")
            self.pv["time"].configure(text=f"{fmt_dur(time.time() - ts)} elapsed" if ts else "")
            hover = act.get("assets", {}).get("large_text")
            self.pv_hover.configure(text=f'Icon hover: "{hover}"' if hover else "")
        if act:
            lines = ["Sent to Discord:",
                     f"  line 1: {act.get('details', '-')}",
                     f"  line 2: {act.get('state', '-')}",
                     f"  hover:  {act.get('assets', {}).get('large_text', '-')}",
                     f"  timer:  {'yes' if act.get('timestamps') else 'no'}"]
        else:
            lines = ["Sent to Discord:", "  (clear status)"]
        self.pv_raw.configure(text="\n".join(lines))

        cid = (d.get("client_id") or "").strip()
        if d.get("enabled") and not cid.isdigit():
            self.en_note.configure(text="Needs an Application ID before it can connect.", fg=GOLD)
        elif d.get("enabled"):
            self.en_note.configure(text="Will turn on when you press Save.", fg=GOLD)
        else:
            self.en_note.configure(text="Off. Nothing is sent to Discord.", fg=MUTED)
        status = getattr(self.comp.presence, "status", "off")
        self.pv_status.configure(text=f"Connection: {status}", fg=MUTED)
