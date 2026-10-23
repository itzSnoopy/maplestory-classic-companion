"""Where a HUD region sits in a frame of any size.

Crop boxes in config.json are in 1920x1080 game pixels (`source_resolution`). The game draws
its UI at a fixed pixel size at every resolution (measured 2026-10-23, 1920x1080 vs 800x600
window, exact pixel match), so a different window size *moves* the UI instead of scaling it:

- "bottom-center": status bar (EXP, level, job). Shift by ((W-1920)/2, H-1080).
- "minimap": the minimap window sits at a position proportional to the window size, and the
  player can drag it, so it is *found* (minimap.py: WORLD button, then the "MINI MAP" header
  `templates/minimap_header.png`, cut at MINIMAP_REF from a 1920x1080 frame) and the box is
  shifted by where the header is.
- "top-left": no shift.
- None / "scale": old behavior, scale the box (for an OBS canvas that resizes the picture).

At the reference size the boxes are used as they are, except "minimap", which is still looked up
(a moved minimap at 1920x1080 is handled too).
"""
import json
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

MINIMAP_TEMPLATE = Path(__file__).parent / "templates" / "minimap_header.png"
MINIMAP_REF = (16, 7, 66, 23)       # where the template was cut, in 1920x1080 frame pixels
MATCH_MAX = 6.0                     # mean abs gray difference that still counts as found

# default anchor per config region name; a region's own "anchor" key in config.json wins
DEFAULT_ANCHORS = {"exp": "bottom-center", "level": "bottom-center", "job": "bottom-center",
                   "map": "minimap"}


def anchor_for(name, rc):
    return (rc or {}).get("anchor", DEFAULT_ANCHORS.get(name))


_tpl = None


def _template():
    global _tpl
    if _tpl is None and MINIMAP_TEMPLATE.exists():
        _tpl = np.asarray(Image.open(MINIMAP_TEMPLATE).convert("L"), dtype=np.int16)
    return _tpl


def best_match(g, t):
    """(mean abs difference, x, y) of the best spot of template t in gray array g, every pixel
    tried (pixel fonts mismatch badly 1 px off, so a coarse grid can miss the true spot)."""
    from numpy.lib.stride_tricks import sliding_window_view
    h, w = t.shape
    if g.shape[0] < h or g.shape[1] < w:
        return 1e9, 0, 0
    d = np.abs(sliding_window_view(g, (h, w)) - t).mean(axis=(2, 3))
    y, x = np.unravel_index(np.argmin(d), d.shape)
    return float(d[y, x]), int(x), int(y)


LAYOUTS = Path(__file__).parent / "layouts.json"
_layouts = None


def layouts():
    """Per-size table {"WxH": {"boxes": {region: box}, ...}} recorded by `python layout.py record`."""
    global _layouts
    if _layouts is None:
        try:
            _layouts = json.loads(LAYOUTS.read_text(encoding="utf-8")).get("sizes", {})
        except (OSError, ValueError):
            _layouts = {}
    return _layouts


def place(img, box, ref=None, anchor=None, name=None):
    """Box [x1, y1, x2, y2] given in `ref`-size pixels -> box in this frame's pixels.
    A box recorded for this exact frame size in layouts.json wins over the computed one."""
    W, H = img.size
    rec = layouts().get(f"{W}x{H}", {}).get("boxes", {}).get(name) if name else None
    if rec:
        return list(rec)
    if anchor == "minimap":
        import minimap
        off = minimap.full_offset(img)
        if off is None:                      # no minimap on screen: fall back to top-left
            off = (0, 0)
        return [box[0] + off[0], box[1] + off[1], box[2] + off[0], box[3] + off[1]]
    if not ref or tuple(ref) == (W, H) or anchor == "top-left":
        return list(box)
    RW, RH = ref
    if anchor == "bottom-center":
        dx, dy = (W - RW) // 2, H - RH
        return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
    sx, sy = W / RW, H / RH                  # "scale" / None
    return [round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)]


def make_minimap_template(frame_path, out=MINIMAP_TEMPLATE):
    """Cut the template from a 1920x1080 frame whose minimap is at its usual spot."""
    im = Image.open(frame_path).convert("L").crop(MINIMAP_REF)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    print(f"saved {out} {im.size}")


def record(img, cfg, save=True):
    """Measure this frame's size: compute the status-bar boxes, check they read, and store them
    in layouts.json under "WxH". Also notes where the minimap was seen. Returns the entry."""
    import glyphs
    import minimap
    import ocr
    from jobread import DEFAULT_RC as JOB_RC, snap as snap_job
    ocr.setup(cfg.get("tesseract"))
    ref = tuple(cfg.get("source_resolution") or (1920, 1080))
    regions = {**{k: v for k, v in cfg["regions"].items() if v.get("box") and k != "map"},
               "job": {**JOB_RC, **(cfg.get("job_read") or {})}}
    W, H = img.size
    boxes, checks = {}, {}
    for name, rc in regions.items():
        b = place(img, rc["box"], ref, anchor_for(name, rc))     # computed, not the table
        boxes[name] = b
        c = img.crop(tuple(b))
        if name == "exp":
            checks[name] = glyphs.read(c, rc.get("white_min", 230))
        elif name == "level":
            checks[name] = glyphs.read(c, rc.get("white_min", 240), rc.get("templates"))
        elif name == "job":
            checks[name] = snap_job(ocr._tess(c, rc))
    state, mbox, _ = minimap.locate(img, cfg["regions"]["map"], ref)
    ok = "?" not in checks.get("exp", "?") and checks.get("exp") and checks.get("level")
    entry = {"recorded": date.today().isoformat(), "verified": bool(ok), "boxes": boxes,
             "check": checks, "minimap_seen": {"state": state, "box": mbox}}
    print(f"{W}x{H}: {'OK' if ok else 'NOT VERIFIED'}  {checks}  minimap={state} {mbox}")
    if save and ok:
        try:
            data = json.loads(LAYOUTS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"reference": list(ref),
                    "note": "Per window size: exact crop boxes (game pixels of the client area). "
                            "Written by `python layout.py record`; sizes not listed are computed "
                            "from the anchors in layout.py. The minimap is always located live.",
                    "sizes": {}}
        data["sizes"][f"{W}x{H}"] = entry
        LAYOUTS.write_text(json.dumps(data, indent=1), encoding="utf-8")
        global _layouts
        _layouts = None
        print(f"saved to {LAYOUTS.name}")
    elif save:
        print("not saved: the EXP bar or level didn't read at the computed spots")
    return entry


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "learn-minimap":
        make_minimap_template(sys.argv[2])
    elif len(sys.argv) in (2, 3) and sys.argv[1] == "record":
        cfg = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
        if len(sys.argv) == 3:
            frame = Image.open(sys.argv[2]).convert("RGB")
        else:
            from sources.wgc import WgcFrames
            f = WgcFrames(cfg)
            frame = f.grab()
            f.close()
        record(frame, cfg)
    else:
        print("usage: python layout.py record [FRAME.png]     (no frame = grab the game window)\n"
              "       python layout.py learn-minimap FRAME_1920x1080.png")
