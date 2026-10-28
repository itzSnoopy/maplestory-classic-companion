"""Turn OCR text into numbers. All formats here are UNCONFIRMED until checked
against real Classic World frames; log every change in NOTES.md."""
import re

PLUS_RE = re.compile(r"\+\s?(\d[\d,]*)")
EXP_WORD_RE = re.compile(r"exp|experience", re.I)


def to_int(s):
    return int(s.replace(",", "").replace(".", ""))


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
