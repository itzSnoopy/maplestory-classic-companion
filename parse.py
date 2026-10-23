"""Turn OCR text into numbers. All formats here are UNCONFIRMED until checked
against real Classic World frames; log every change in NOTES.md."""
import re

PCT_RE = re.compile(r"(\d{1,3}[.,]\d{1,2})\s*%")
NUM_RE = re.compile(r"\d[\d,]*")
PLUS_RE = re.compile(r"\+\s?(\d[\d,]*)")
EXP_WORD_RE = re.compile(r"exp|experience", re.I)


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
    m = re.search(r"\d{1,3}", text)
    if m and 1 <= int(m.group()) <= 200:
        return int(m.group())
    return None


def parse_map(text):
    """Minimap title is two lines, 'Maple Road' / 'Mushroom Town - West Entrance'.
    Return the cleaned last line (the map); None if it's too short to trust."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    t = lines[-1] if lines else ""
    t = re.sub(r"[^A-Za-z0-9 :'&\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # trailing roman numeral misread with l/1/| for I ("Ground Il" -> "Ground II")
    t = re.sub(r"(?<=\s)[Il1|]+$", lambda m: "I" * len(m.group()), t)
    return t if len(t) >= 3 else None


def parse_gain(line):
    """'You have gained experience (+12) (+3)' or '+12 (+3)' -> (base, bonus) or None.
    First +N is the base EXP; any further +N values are summed as party/other bonus."""
    if not EXP_WORD_RE.search(line) and not line.lstrip("( ").startswith("+"):
        return None
    if re.search(r"meso", line, re.I):
        return None
    amts = [to_int(a) for a in PLUS_RE.findall(line)]
    if not amts:
        return None
    return amts[0], sum(amts[1:])


def chat_lines(text):
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _norm(line):
    return re.sub(r"\s+", "", line).lower()


def new_chat_lines(prev, cur):
    """Lines in `cur` that weren't in `prev`, treating the chat box as a list that scrolls up.
    Finds the longest suffix of prev that equals a prefix of cur; everything after it is new."""
    p, c = [_norm(x) for x in prev], [_norm(x) for x in cur]
    for k in range(min(len(p), len(c)), 0, -1):
        if p[-k:] == c[:k]:
            return cur[k:]
    return cur
