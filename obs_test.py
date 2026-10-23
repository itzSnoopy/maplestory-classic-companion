"""
obs_test.py - check the OBS connection and the crop regions.

  python obs_test.py                 connect, print OBS version + inputs, save one frame to frame.png
  python obs_test.py --crops         grab a frame, crop every configured region, OCR + parse each
  python obs_test.py --crops shot.png    same, but on an existing screenshot (no OBS needed)
  python obs_test.py --learn "12[80.00%]" [shot.png]   teach the EXP glyph reader what the bar says

Crops are written to crops/<region>.png (raw) and crops/<region>_prep.png (what Tesseract sees).
"""
import argparse
import json
import sys
from pathlib import Path

import ocr
import parse
from capture_obs import crop, from_config
from layout import anchor_for

ROOT = Path(__file__).parent


def connect_and_grab(cfg):
    try:
        obs = from_config(cfg)
    except Exception as e:
        sys.exit(f"Could not connect to OBS at {cfg['obs']['host']}:{cfg['obs']['port']}: {e}\n"
                 "Check Tools > WebSocket Server Settings (enabled, port, password).")
    v = obs.version()
    print(f"OBS {v.get('obsVersion')}  websocket {v.get('obsWebSocketVersion')}")
    print("\nInputs:")
    for i in obs.inputs():
        print(f"  {i['inputName']!r:40} {i.get('inputKind')}")
    src = cfg["obs"]["source"]
    try:
        img = obs.screenshot(src)
    except Exception as e:
        sys.exit(f"\nScreenshot of {src!r} failed: {e}\n"
                 "Set obs.source in config.json to the exact Game Capture name from the list above.")
    finally:
        obs.close()
    img.save(ROOT / "frame.png")
    print(f"\nSaved frame.png  ({img.width}x{img.height})  from {src!r}")
    if tuple(cfg.get("source_resolution") or ()) != img.size:
        print(f"Note: config source_resolution is {cfg.get('source_resolution')}; "
              f"set it to [{img.width}, {img.height}] before defining crops.")
    return img


def check_crops(cfg, img):
    if not ocr.setup(cfg.get("tesseract")):
        print("WARNING: Tesseract not available, saving crops only.")
    out = ROOT / "crops"
    out.mkdir(exist_ok=True)
    res = cfg.get("source_resolution")
    for name, rc in cfg["regions"].items():
        if not rc.get("box"):
            print(f"[{name}] no box set")
            continue
        c = crop(img, rc["box"], res, anchor_for(name, rc), name)
        c.save(out / f"{name}.png")
        ocr.prep(c, rc).save(out / f"{name}_prep.png")
        text = ocr.read(c, rc, kind=name)
        print(f"\n[{name}] OCR: {text!r}")
        if name == "exp":
            print("  parsed (cur, need, pct):", parse.parse_exp(text))
        elif name == "level":
            print("  parsed level:", parse.parse_level(text))
        elif name == "map":
            print("  parsed map:", parse.parse_map(text))
        elif name == "chat":
            for ln in parse.chat_lines(text):
                print(f"  {ln!r:60} -> {parse.parse_gain(ln)}")
    print(f"\nCrops saved under {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--crops", nargs="?", const="", metavar="IMAGE",
                    help="check crop regions (on IMAGE if given, else a fresh OBS frame)")
    ap.add_argument("--learn", nargs="+", metavar=("TEXT", "IMAGE"),
                    help="label EXP bar glyphs with the text it currently shows")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    if a.learn:
        import glyphs
        from PIL import Image
        img = Image.open(a.learn[1]).convert("RGB") if len(a.learn) > 1 else connect_and_grab(cfg)
        rc = cfg["regions"]["exp"]
        new = glyphs.learn(crop(img, rc["box"], cfg.get("source_resolution"), anchor_for("exp", rc), "exp"), a.learn[0],
                           rc.get("white_min", 230))
        print(f"Learned {len(new)} new glyph(s): {sorted(new.values())}")
        print(f"Known: {''.join(sorted(set(glyphs.load().values())))}")
    elif a.crops:
        from PIL import Image
        check_crops(cfg, Image.open(a.crops).convert("RGB"))
    else:
        frame = connect_and_grab(cfg)
        if a.crops is not None:
            check_crops(cfg, frame)
