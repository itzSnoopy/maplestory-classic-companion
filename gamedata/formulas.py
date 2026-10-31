"""formulas.py - the HP/MP and skill math in one place. Pure functions, no state, stdlib only.

Callers: components/skillbar (regen.py, mpuse.py, feed.py). The texts come from
gamedata/skills_classic.json (`levels[]` per-level text, `mp[]`, `time[]`).

Each formula has an entry in STATUS: (status, source).
  verified  a Classic source says so (the skill text / MeowDB)
  measured  fitted from recordings of the player's own character (date = NOTES / memory entry)
  guess     plausible, not measured yet: don't build anything that needs it to be exact
"""
import math
import re

STATUS = {
    "mp_pct": ("verified", "Improved MP Recovery text: 'Regenerates N% of Max MP every 10 seconds'"),
    "mp_flat": ("verified", "3rd job text: 'Recover N additional MP every 10 sec' (Crusader / WK)"),
    "mp_tick_floor": ("measured", "floor(max MP x %) rounding: max 606 -> 6, 709 -> 7 (2026-10-23/24)"),
    "mp_tick_extra": ("measured", "+3 moving, +6 standing / sitting on top of the % part, mp_extra "
                                  "[3, 6] (max 606: +9 / +12; max 709: +10 / +13; 2026-10-24)"),
    "item_bonus": ("verified", "Improved MP/HP Recovery text: 'increases recovery from items by N%'"),
    "item_bonus_applied": ("measured", "Lv 5 = +14%: a Blue Potion's 200 shows as 228 (2026-10-24)"),
    "item_bonus_on_pct_items": ("guess", "whether the bonus applies to % potions (Mana Elixir) is "
                                         "unmeasured; both readings are accepted"),
    "potion_amount": ("verified", "potions_classic.json (MeowDB): flat hp/mp or % of max"),
    "skill_mp_cost": ("verified", "skills_classic.json `mp[level-1]` (MeowDB per-level text)"),
    "buff_pct": ("verified", "skill text 'Max HP +N%' (Hyper Body)"),
    "buff_seconds": ("verified", "skill `time[level-1]` / text 'for N sec'"),
    "buff_max_rounding": ("guess", "shown max = floor(base x (1 + N/100)) +-1; no Hyper Body seen yet"),
    "buff_mp_part": ("guess", "Hyper Body's data text says HP only; a Max MP part is unconfirmed"),
    "recovery_tick": ("measured", "Recovery Lv 3 (72 HP in 30 sec): +12 HP every 5.0-5.2 s, 6 ticks, "
                                  "first ~5.9 s after the cast, own beat apart from HP regen (2026-10-30)"),
    "magic_guard_split": ("measured", "toggle (one cast, on until it ends); each hit: MP part = floor(damage "
                                      "x N% from the text), HP takes the rest. Lv 2 (33%), 45 hits in KPQ "
                                      "2026-10-30: 55 -> 18 MP + 37 HP, 133 -> 43 + 90"),
}


# -- regen ---------------------------------------------------------------------------------
def mp_pct(text):
    """'Regenerates 1% of Max MP every 10 seconds' -> 1; anything else -> 0."""
    m = re.search(r"(\d+)% of Max MP", text or "")
    return int(m.group(1)) if m else 0


def mp_flat(text):
    """'Recover 3 additional MP every 10 sec' (3rd job) -> 3; else 0."""
    m = re.search(r"Recover (\d+) additional MP", text or "")
    return int(m.group(1)) if m else 0


def mp_part(max_mp, pct, flat=0):
    """The part of an MP tick the skill text promises: floor(max MP x pct / 100) + flat."""
    return math.floor(max_mp * pct / 100) + flat


def mp_tick(max_mp, pct, flat=0, extra=0):
    """One MP tick: the skill's part + the extra that depends on what you do (moving / standing)."""
    return mp_part(max_mp, pct, flat) + extra


# -- items -----------------------------------------------------------------------------------
def item_bonus_pct(text):
    """'... increases recovery from items by 14%' -> 14; else 0."""
    m = re.search(r"from items by (\d+)%", text or "")
    return int(m.group(1)) if m else 0


def potion_amount(flat, pct, max_v, bonus=0):
    """What an item adds, as [amounts]: the flat part with the recovery skill's bonus, then the
    % part with the bonus, then (only when there is a bonus) the % part without it."""
    out = []
    if flat:
        out.append(int(flat * (100 + bonus) / 100 + 1e-6))
    if pct and max_v:
        out.append(int(max_v * pct / 100 * (100 + bonus) / 100 + 1e-6))
        if bonus:
            out.append(int(max_v * pct / 100 + 1e-6))
    return out


# -- skills ------------------------------------------------------------------------------------
def skill_mp_costs(record):
    """[MP cost per level] of a skill record (index = level - 1); [] when none."""
    return (record or {}).get("mp") or []


def skill_mp_cost(record, level):
    """MP cost of a cast at `level` (0 when unknown / no cost)."""
    mp = skill_mp_costs(record)
    return mp[level - 1] if 0 < (level or 0) <= len(mp) else 0


def level_text(record, level):
    texts = (record or {}).get("levels") or []
    return texts[level - 1] if 0 < (level or 0) <= len(texts) else ""


def buff_pct(record, level, stat="hp"):
    """'Max HP +10% for 100 sec' -> 10 for stat 'hp'; 'Max MP +N%' for 'mp' (only if the text
    says MP). 0 when the level text has no such part."""
    word = "HP" if stat == "hp" else "MP"
    m = re.search(r"Max %s \+(\d+)%%" % word, level_text(record, level))
    return int(m.group(1)) if m else 0


def buff_seconds(record, level):
    """How long the buff lasts at `level`: the `time` list, else 'for N sec' in the text; 0 unknown."""
    times = (record or {}).get("time") or []
    if 0 < (level or 0) <= len(times) and times[level - 1]:
        return times[level - 1]
    m = re.search(r"for (\d+) sec", level_text(record, level))
    return int(m.group(1)) if m else 0


RECOVERY_TICKS = 6          # Recovery heals in 6 equal parts...
RECOVERY_EVERY_S = 5        # ...one every 5 s (measured 2026-10-30, see STATUS)


def recovery_tick(text):
    """Recovery's HP per tick from its level text: 'Recover HP 72 in 30 sec' -> 12; 0 if unknown."""
    m = re.search(r"Recover HP (\d+) in (\d+) sec", text or "")
    return int(m.group(1)) // RECOVERY_TICKS if m else 0


def magic_guard_pct(text):
    """'Replace 33% of HP damage with MP while active' -> 33; 0 if the text doesn't say."""
    m = re.search(r"Replace (\d+)% of HP damage", text or "")
    return int(m.group(1)) if m else 0


def magic_guard_split(damage, pct):
    """One hit under Magic Guard: (MP part, HP part); the MP part is rounded down (STATUS)."""
    mp = math.floor(damage * pct / 100)
    return mp, damage - mp


def magic_guard_mp_for_hp(hp_part, pct):
    """The MP part that goes with an HP loss of `hp_part` from one hit, or None if no whole damage
    number gives exactly that HP part."""
    if hp_part <= 0 or not 0 < pct < 100:
        return None
    for dmg in range(hp_part, int(hp_part * 100 / (100 - pct)) + 3):
        mp, hp = magic_guard_split(dmg, pct)
        if hp == hp_part:
            return mp
    return None


def magic_guard_reach(hp, mp, pct):
    """What the skill bar shows for Magic Guard: (absorb, ehp). The player's definition
    (2026-10-31, revised): absorb = Magic Guard % x current MP (1,046 MP at 33% -> 345), effective
    HP = HP + absorb. Each moves only with its own bars. (Strictly, you die after
    magic_guard_limit damage.)"""
    if not pct:
        return 0, max(hp or 0, 0)
    absorb = math.floor(max(mp or 0, 0) * pct / 100)
    return absorb, max(hp or 0, 0) + absorb


def magic_guard_limit(hp, mp, pct):
    """Damage you can take before dying with Magic Guard on: min(HP + MP, HP / (1 - pct))."""
    if not hp or hp <= 0 or not 0 < pct < 100:
        return max(hp or 0, 0)
    return min(hp + max(mp or 0, 0), math.floor(hp * 100 / (100 - pct)))


def buffed_max(base, pct):
    """The max shown while a '+pct%' max buff is on (rounding is a guess, see STATUS)."""
    return math.floor(base * (100 + pct) / 100)
