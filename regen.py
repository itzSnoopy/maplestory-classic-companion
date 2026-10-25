"""regen.py - how big this character's regen ticks are, from numbers that don't change between
maps: max MP and the recovery skill's level, plus a few learned constants kept per character
(history/characters.json "regen", character.Profile). Watching the screen only reconciles them.

MP tick = floor(max MP x the % in Improved MP Recovery's text) + an extra that depends on what
you do. Measured 2026-10-23/24: max 606 -> +9 moving / +12 standing; max 709 -> +10 moving /
+13 sitting. So the extras are +3 (moving) and +6 (standing or sitting): `mp_extra` [3, 6].
HP: no formula yet (Improved HP Recovery's text gives no amount; a Magician has none): the sizes
seen are kept per character (+50 sitting on the user's chair, 2026-10-24) in `hp_sizes`.

Reconciling: a tick that fits the math changes nothing. MP: the same unexplained extra seen
LEARN_AFTER times is added to `mp_extra`. HP: a size seen LEARN_AFTER times joins `hp_sizes`.
"""
import math
import re
from collections import Counter

MP_SKILLS = ("2000000", "1110000", "1210000")      # Improved MP Recovery (Mage, Crusader, WK)
DEFAULT = {"mp_extra": [3, 6], "hp_sizes": []}
LEARN_AFTER = 3
MAX_HP_SIZES = 4


def mp_pct(skill_text):
    """'Regenerates 1% of Max MP every 10 seconds' -> 1; anything else -> 0."""
    m = re.search(r"(\d+)% of Max MP", skill_text or "")
    return int(m.group(1)) if m else 0


def mp_flat(skill_text):
    """'Recover 3 additional MP every 10 sec' (3rd job) -> 3; else 0."""
    m = re.search(r"Recover (\d+) additional MP", skill_text or "")
    return int(m.group(1)) if m else 0


class RegenModel:
    def __init__(self, data=None, log=None):
        self.data = {**DEFAULT, **(data or {})}
        self.data["mp_extra"] = list(self.data.get("mp_extra") or DEFAULT["mp_extra"])
        self.data["hp_sizes"] = list(self.data.get("hp_sizes") or [])
        self.log = log or (lambda s: None)
        self.odd = Counter()                 # (meter, value) -> unexplained sightings

    def mp_ticks(self, max_mp, texts):
        """Expected MP tick sizes (most likely first) for this max MP and the recovery skill
        texts at the character's levels."""
        if not max_mp:
            return []
        pct = max((mp_pct(t) for t in texts), default=0)
        flat = sum(mp_flat(t) for t in texts)
        part = math.floor(max_mp * pct / 100) + flat
        return [part + e for e in self.data["mp_extra"]]

    def hp_ticks(self):
        return list(self.data["hp_sizes"])

    def observe(self, k, d, max_v, texts):
        """A regen tick of size d was seen on meter k. Returns True when the stored numbers
        changed (save them)."""
        if k == "mp":
            if d in self.mp_ticks(max_v, texts):
                return False
            pct = max((mp_pct(t) for t in texts), default=0)
            extra = d - math.floor(max_v * pct / 100) - sum(mp_flat(t) for t in texts)
            if extra <= 0:
                return False
            self.odd[("mp", extra)] += 1
            if self.odd[("mp", extra)] >= LEARN_AFTER:
                self.data["mp_extra"] = sorted(set(self.data["mp_extra"]) | {extra})
                self.log(f"regen: MP ticks of +{d} (max {max_v}) seen {LEARN_AFTER}x: extra "
                         f"+{extra} added to this character's numbers")
                return True
            return False
        if d in self.data["hp_sizes"]:
            return False
        self.odd[("hp", d)] += 1
        if self.odd[("hp", d)] >= LEARN_AFTER:
            self.data["hp_sizes"] = (self.data["hp_sizes"] + [d])[-MAX_HP_SIZES:]
            self.log(f"regen: HP ticks of +{d} seen {LEARN_AFTER}x: kept for this character")
            return True
        return False
