"""settings_page.py - the challenge board's lines in Settings > General: on/off, don't disturb, background colour,
flame style picker with a small preview of each. Loaded by components/challenge/component.py when the page is built."""
import copy
import tkinter as tk

STYLE_LABELS = {"blaze": "Pixel blaze", "tongues": "Pixel tongues", "embers": "Low embers",
                "arcane": "Arcane blue", "poison": "Poison green", "painted": "Painted"}


def _preview(style, k):
    """A small picture of one flame style (the left side of the board, cropped)."""
    from PIL import Image, ImageDraw, ImageTk
    from components.challenge import board
    s = 2
    im = Image.new("RGBA", (board.PX * s, (board.PY + board.PH) * s), (0, 0, 0, 255))
    d = ImageDraw.Draw(im)
    board._flames(d, s, style, -1)
    im = im.resize((int(board.PX * k * 0.9), int(board.PH * k * 0.5)), Image.LANCZOS)
    return ImageTk.PhotoImage(im)


def build(win, f, defaults):
    px = win.px
    win.s.setdefault("challenge", copy.deepcopy(defaults))
    for key, v in defaults.items():
        win.s["challenge"].setdefault(key, v)
    win.check(f, "challenge.enabled", "Challenge board over the game (duels with friends on your relay)"
              ).pack(anchor="w", pady=(px(6), px(2)))
    win.check(f, "challenge.dnd", "Don't disturb: no challenge invitations (friends' buttons turn off)"
              ).pack(anchor="w", pady=(0, px(2)))
    win.label(f, "Kill races between friends: the most kills in 2 / 5 / 10 / 15 minutes wins. Needs the party link "
                 "(Internet > Party & friends link) and friends on a build with challenges. Opponent pictures come "
                 "from ClassicRank (Internet > Character pictures).", muted=True,
              wrap=px(380)).pack(anchor="w", pady=(0, px(6)))
    row = tk.Frame(f, bg=win.w["bg"])
    row.pack(anchor="w", pady=(0, px(4)))
    win.label(row, "Board background").pack(side="left")
    win.entry(row, "challenge.bg", width=10).pack(side="left", padx=(px(8), px(8)))
    win.label(row, "#rrggbb, default black", muted=True, size=8).pack(side="left")
    win.label(f, "Board flames").pack(anchor="w", pady=(px(4), px(2)))
    grid = tk.Frame(f, bg=win.w["bg"])
    grid.pack(anchor="w")
    var = tk.StringVar(value=win.get("challenge.flames"))
    var.trace_add("write", lambda *a: win.set("challenge.flames", var.get()))
    win.vars["challenge.flames"] = var
    win.flame_previews = []
    try:
        from components.challenge import board
        for i, style in enumerate(board.FLAME_STYLES):
            cell = tk.Frame(grid, bg=win.w["bg"])
            cell.grid(row=i // 3, column=i % 3, padx=(0, px(10)), pady=(0, px(4)), sticky="w")
            try:
                img = _preview(style, win.k if hasattr(win, "k") else 1.0)
                win.flame_previews.append(img)
                tk.Label(cell, image=img, bg=win.w["bg"], bd=0).pack(side="left")
            except Exception:
                pass
            tk.Radiobutton(cell, text=STYLE_LABELS[style], value=style, variable=var,
                           bg=win.w["bg"], fg="#e8e8e8", selectcolor="#2a2d36", activebackground=win.w["bg"],
                           activeforeground="#e8e8e8", highlightthickness=0, bd=0).pack(side="left")
    except Exception as e:
        win.label(f, f"(previews unavailable: {e})", muted=True).pack(anchor="w")
