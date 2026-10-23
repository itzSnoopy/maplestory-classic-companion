"""Tesseract OCR with preprocessing tuned for Maple's small pixel fonts."""
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pytesseract
    HAVE_OCR = True
except ImportError:
    HAVE_OCR = False

READY = False


def setup(tesseract_path):
    """Point pytesseract at tesseract.exe. Returns True if OCR is usable."""
    global READY
    if not HAVE_OCR:
        return False
    if tesseract_path and Path(tesseract_path).exists():
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
    try:
        pytesseract.get_tesseract_version()
        READY = True
        return True
    except Exception:
        return False


def prep(img, rc):
    """Preprocess a crop for Tesseract, driven by its region config:
    white_min: keep only near-white pixels (min RGB channel >= value) as black-on-white;
               best for Maple's crisp white pixel fonts. Otherwise grayscale + autocontrast,
               then optional threshold/invert.
    scale:     integer upscale (NEAREST for white_min to keep pixel edges, else LANCZOS)."""
    scale = rc.get("scale", 3)
    wm = rc.get("white_min")
    if wm is not None:
        rgb = img.convert("RGB")
        g = Image.new("L", rgb.size, 255)
        g.putdata([0 if min(p) >= wm else 255 for p in rgb.getdata()])
        g = g.resize((g.width * scale, g.height * scale), Image.NEAREST)
        return ImageOps.expand(g, border=10, fill=255)
    g = ImageOps.grayscale(img)
    g = g.resize((g.width * scale, g.height * scale), Image.LANCZOS)
    g = ImageOps.autocontrast(g)
    if rc.get("threshold") is not None:
        t = rc["threshold"]
        g = g.point(lambda p: 255 if p > t else 0)
    if rc.get("invert"):
        g = ImageOps.invert(g)
    return g


def _tess(img, rc, whitelist=None):
    cfg = f"--psm {rc.get('psm', 7)}"
    wl = whitelist if whitelist is not None else rc.get("whitelist")
    if wl:
        cfg += f" -c tessedit_char_whitelist={wl.replace(' ', '')}"
    return pytesseract.image_to_string(prep(img, rc), config=cfg).strip()


def green_columns(img):
    """x ranges of columns containing Maple's green bracket color (EXP bar '[', ']')."""
    rgb = img.convert("RGB")
    px, runs, start = rgb.load(), [], None
    for x in range(rgb.width):
        hit = any(g > r + 30 and g > b + 60 for r, g, b in (px[x, y] for y in range(rgb.height)))
        if hit and start is None:
            start = x
        elif not hit and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, rgb.width))
    return runs


def read_exp(img, rc):
    """EXP bar 'cur[pct%]': the green brackets OCR as '1', so split on them and read each part."""
    runs = green_columns(img)
    if len(runs) < 2:
        return _tess(img, rc)
    h = img.height
    cur = _tess(img.crop((0, 0, runs[0][0], h)), rc, "0123456789,")
    pct = _tess(img.crop((runs[0][1], 0, runs[1][0], h)), rc, "0123456789.%")
    return f"{cur}[{pct}]"


def trim_at_border(img):
    """Cut the crop at the first column with no background pixels (the minimap's right
    border, when the minimap is narrower than the crop). Text columns always include some
    background, because of the gap between the two title lines."""
    rgb = img.convert("RGB")
    # background = most common color of the left part, which is always the title bar
    # (the whole crop may reach far into the game scene when the box is wide)
    left = rgb.crop((0, 0, min(rgb.width, 80), rgb.height))
    colors = left.getcolors(left.width * left.height)
    bg = max(colors)[1]
    px = rgb.load()
    near = lambda p: sum(abs(a - b) for a, b in zip(p, bg)) < 40
    for x in range(10, rgb.width):
        if not any(near(px[x, y]) for y in range(rgb.height)):
            return img.crop((0, 0, x, img.height))
    return img


def read(img, region_cfg, kind=None):
    """OCR one cropped region using its config.
    glyphs: true  -> exact pixel-font matching (glyphs.py); Tesseract only if a glyph is unknown.
    kind='exp'    -> Tesseract with the green-bracket split."""
    if region_cfg.get("glyphs"):
        import glyphs
        text = glyphs.read(img, region_cfg.get("white_min", 230), region_cfg.get("templates"))
        if "?" not in text or not READY or region_cfg.get("fallback") is False:
            return text
    if not READY:
        return ""
    if region_cfg.get("trim_border"):
        img = trim_at_border(img)
    try:
        if kind == "exp" or region_cfg.get("split_green"):
            return read_exp(img, region_cfg)
        return _tess(img, region_cfg)
    except Exception as e:
        return f"<ocr error: {e}>"
