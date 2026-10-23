"""Builds the Discord placeholder icon: Fluent Emoji maple leaf (MIT) -> autumn recolor,
40 px pixelation + dark outline, PLACEHOLDER watermark.

    python dev/make_leaf_placeholder.py art      writes art/leaf_placeholder.png
Copy that to discord_icon.png in the project root.
"""
import colorsys, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter
sp = sys.argv[1]
src = Image.open(f"{sp}/fluent_maple_leaf_3d.png").convert("RGBA")
# 1) recolor pink -> autumn orange/red, keep the shading (lightness)
px = src.load()
out = Image.new("RGBA", src.size)
o = out.load()
for y in range(src.height):
    for x in range(src.width):
        r, g, b, a = px[x, y]
        if not a:
            o[x, y] = (0, 0, 0, 0); continue
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        h2 = 0.045 + (l - 0.45) * 0.22         # darker = deep red, lighter = orange/gold
        s2 = 0.95
        l2 = max(0.18, min(0.72, (l - 0.5) * 1.35 + 0.5))   # more contrast
        rr, gg, bb = colorsys.hls_to_rgb(h2 % 1, l2, s2)
        o[x, y] = (int(rr * 255), int(gg * 255), int(bb * 255), a)
# 2) pixelate: 40 px, 16 colors, hard alpha, 1 px dark outline
n = 40
small = out.resize((n, n), Image.BOX)
alpha = small.getchannel("A").point(lambda v: 255 if v >= 110 else 0)
rgb = small.convert("RGB").quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGBA")
rgb.putalpha(alpha)
pad = Image.new("RGBA", (n + 4, n + 4), (0, 0, 0, 0)); pad.alpha_composite(rgb, (2, 2))
a = pad.load(); res = pad.copy(); r_ = res.load()
for y in range(n + 4):
    for x in range(n + 4):
        if a[x, y][3] == 0 and any(0 <= x + dx < n + 4 and 0 <= y + dy < n + 4 and a[x + dx, y + dy][3]
                                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            r_[x, y] = (70, 22, 10, 255)
# small highlight pixels top-left of each lobe feel: brighten pixels next to the outline on top-left
leaf = res.resize((1024, 1024), Image.NEAREST)   # Discord recommends 1024x1024
# 3) watermark
def font(sz):
    for f in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        try: return ImageFont.truetype(f, sz)
        except OSError: pass
    return ImageFont.load_default()
wm = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
d = ImageDraw.Draw(wm)
f = font(132)
text = "PLACEHOLDER"
l, t, r, b = d.textbbox((0, 0), text, font=f, stroke_width=12)
d.text(((1024 - (r - l)) / 2 - l, (1024 - (b - t)) / 2 - t), text, font=f,
       fill=(255, 255, 255, 225), stroke_width=12, stroke_fill=(30, 20, 18, 225))
wm = wm.rotate(28, resample=Image.BICUBIC)
final = leaf.copy()
final.alpha_composite(wm)
final.save(f"{sp}/leaf_placeholder.png")
print(f"{sp}/leaf_placeholder.png")
