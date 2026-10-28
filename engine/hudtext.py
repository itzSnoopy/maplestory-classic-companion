"""Text the HUD draws -> numbers: the EXP bar ('12,113[29.90%]') and the level badge.
Formats are confirmed against real Classic World frames; log every change in NOTES.md."""
import re

PCT_RE = re.compile(r"(\d{1,3}[.,]\d{1,2})\s*%")
NUM_RE = re.compile(r"\d[\d,]*")


def to_int(s):
    return int(s.replace(",", "").replace(".", ""))


BRACKET_RE = re.compile(r"^\D*(\d[\d,]*)\s*\[\s*(\d{1,3})[.,](\d{2})")


def parse_exp(text):
    """Classic World (confirmed): '2[13.33%]' -> (2, None, 13.33). The bar shows no 'needed' value.
    Strict form first, so a misread '%' can't leak digits; loose fallback for other layouts."""
    m = BRACKET_RE.search(text)
    if m:
        pct = float(f"{m.group(2)}.{m.group(3)}")
        return to_int(m.group(1)), None, pct if pct <= 100 else None
    pct = None
    m = PCT_RE.search(text)
    if m:
        pct = float(m.group(1).replace(",", "."))
        if pct > 100:
            pct = None
        text = text[:m.start()] + " " + text[m.end():]
    nums = [to_int(n) for n in NUM_RE.findall(text)]
    cur = nums[0] if nums else None
    need = nums[1] if len(nums) > 1 and nums[1] > (cur or 0) else None
    if pct is None and cur is not None and need:
        pct = round(cur / need * 100, 2)
    return cur, need, pct


def parse_level(text):
    if "?" in (text or ""):         # an unknown badge digit ('1?' at Lv 17) is not level 1
        return None
    m = re.search(r"\d{1,3}", text)
    if m and 1 <= int(m.group()) <= 200:
        return int(m.group())
    return None
