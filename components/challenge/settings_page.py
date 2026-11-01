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


def _size_preview(scale):
    """The whole demo board at `scale` percent x 0.4 (so the three sizes show their relative size), as a PhotoImage."""
    from PIL import ImageTk
    from components.challenge import board, sprites
    sil = sprites.silhouette()
    ke = scale / 100.0
    st = board.demo_state(15, lambda n: sil, "live")
    pic = board.to_rgb(board.draw_state(st, "blaze", "#000000", 2, ke), ke * 0.4)
    return ImageTk.PhotoImage(pic)


def _size_picker(win, f, px):
    win.label(f, "Board size").pack(anchor="w", pady=(px(4), px(2)))
    row = tk.Frame(f, bg=win.w["bg"])
    row.pack(anchor="w")
    try:
        from components.challenge import board
        cur = int(win.get("challenge.scale") or board.DEFAULT_SCALE)
        var = tk.IntVar(value=cur if cur in board.SCALES else board.DEFAULT_SCALE)
        var.trace_add("write", lambda *a: (win.set("challenge.scale", var.get()), show()))
        win.vars["challenge.scale"] = var
        prev = tk.Label(f, bg=win.w["bg"], bd=0)
        win.size_previews = {}

        def show():
            v = var.get()
            if v not in win.size_previews:
                win.size_previews[v] = _size_preview(v)
            prev.configure(image=win.size_previews[v])

        for sc in board.SCALES:
            tk.Radiobutton(row, text=f"{sc}%" + ("  (default)" if sc == board.DEFAULT_SCALE else ""), value=sc,
                           variable=var, bg=win.w["bg"], fg="#e8e8e8", selectcolor="#2a2d36",
                           activebackground=win.w["bg"], activeforeground="#e8e8e8", highlightthickness=0,
                           bd=0).pack(side="left", padx=(0, px(10)))
        prev.pack(anchor="w", pady=(px(4), px(2)))
        show()
        win.label(f, "Preview shown at 40% of real size. Names stay readable at 50%; moving the board is kept "
                     "when you change the size.", muted=True, size=8, wrap=px(380)).pack(anchor="w", pady=(0, px(4)))
    except Exception as e:
        win.label(f, f"(size preview unavailable: {e})", muted=True).pack(anchor="w")


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
    _size_picker(win, f, px)
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
