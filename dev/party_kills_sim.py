"""Offline test of the party EXP model (gamedata/kills.party_kills / fit_bonus) and the learned-bonus store
(components/challenge/partyexp.py) on synthetic parties. No player data. Usage: python dev/party_kills_sim.py [OUT_DIR]

Model (player's observation, 2026-11-01): own kill = ceil(E*(0.2+0.8*L/sumL)), a teammate's = ceil(E*0.8*L/sumL),
the party bonus a SEPARATE amount (mode(base*b)) that can arrive in the same rise or in a rise of its own.

 1. the player's exact case: 4 x Lv 28, Horny Mushroom 41 -> 17 own / 9 teammate's, Green Mushroom 26 -> 11 / 6
 2. a known bonus (b, mode): base / base+bonus / lone bonus / teammate's, never read as the wrong owner;
    lone bonus counts 0 and is "explained"; ambiguous values are refused; 2 kills in a rise
 3. stand-in levels (a member not in the room): +-1 on the base amounts, only then
 4. estimating (no bonus learned yet): the bare base amounts count exactly, base + a grid bonus counts when unique
 5. calibration: (b, mode) recovered from noisy rises for sizes 2 / 3 / 4; noise and base-only data give no fit
 6. PartyBonus: samples persist in history/party_bonus.json, the fit comes back after a reload
"""
import math
import os
import random
import sys
import tempfile
import time
from pathlib import Path

os.environ["MAPLE_COMPANION_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gamedata import kills  # noqa: E402
from components.challenge import partyexp  # noqa: E402

fails = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


rng = random.Random(83)
maps = kills.map_mobs()

# 1 ----------------------------------------------------------------------------------------------------
PM = "Tree Dungeon, Forest Up North II"         # Slime 12, Green Mushroom 26, Horny Mushroom 41
pm = {n: e for n, e, _ in maps[PM]}
check(pm.get("Horny Mushroom") == 41 and pm.get("Green Mushroom") == 26, f"player's map has Horny 41 / Green 26 ({PM})")
L = [28, 28, 28]
check((kills.base_own(41, 28, L), kills.base_own(26, 28, L)) == (17, 11), "own kill: ceil(41*0.4)=17, ceil(26*0.4)=11")
check((kills.base_other(41, 28, L), kills.base_other(26, 28, L)) == (9, 6), "teammate's kill: ceil(41*0.8*28/112)=9, ceil(26*..)=6")
check(kills.party_kills(PM, 17, 28, L) == (1, 0) and kills.party_kills(PM, 11, 28, L) == (1, 0),
      "estimating: 17 and 11 are my Horny / Green kill")
check(kills.party_kills(PM, 9, 28, L) == (0, 1) and kills.party_kills(PM, 6, 28, L) == (0, 1),
      "estimating: 9 and 6 are a teammate's kill (counts 0 for me)")
check(kills.party_kills(PM, 34, 28, L) == (2, 0) and kills.party_kills(PM, 17 + 9, 28, L) == (1, 1),
      "two kills in one rise: 17+17 -> (2, 0), 17+9 -> (1, 1)")
fit = kills.BonusFit(0.3, "ceil")
check(kills.party_kills(PM, 16, 28, L, fit) == (0, 0) and kills.party_kills(PM, 18, 28, L, fit) == (0, 0),
      "exact ceil, tolerance 0: 16 and 18 are nothing")                # a made-up rule: Horny 17+6, Green 11+4, teammate's 9+3 / 6+2
check(kills.party_kills(PM, 17 + 6, 28, L, fit) == (1, 0) and kills.party_kills(PM, 11 + 4, 28, L, fit) == (1, 0),
      "learned 0.3 ceil: 23 and 15 are my kills with the bonus")
check(kills.party_kills(PM, 17, 28, L, fit) == (1, 0) and kills.party_kills(PM, 11, 28, L, fit) == (1, 0),
      "learned: the bare base (bonus not seen yet) still counts")
check(kills.party_kills(PM, 9 + 3, 28, L, fit) == (0, 1), "learned: a teammate's kill with the bonus (12) -> theirs")
check(kills.read_rise(PM, 2, 28, L, fit)[2] == "bonus" and kills.party_kills(PM, 2, 28, L, fit) == (0, 0),
      "learned: 2 is a lone bonus (explained, counts 0)")
check(kills.read_rise(PM, 6, 28, L, fit)[2] == "ambiguous",
      "learned: 6 = a teammate's Green kill or the bonus of my Horny: refused")

# 2 ----------------------------------------------------------------------------------------------------
# a map with several distinct mid-size mobs (shares of a few EXP up to ~100: where the model has a say)
MAP = next(m for m, ms in sorted(maps.items())
           if len({e for _, e, _ in ms}) >= 4 and 25 <= min(e for _, e, _ in ms) and max(e for _, e, _ in ms) <= 400)
EXPS = sorted({e for _, e, _ in maps[MAP]})
print(f"   test map has mobs of {EXPS} EXP")


def truth(exp, rule, lme, others, mine, form):
    """What the game would show: ('full' base+bonus in one rise, 'base' bonus unseen, 'split' = [base, bonus])."""
    base = kills.base_own(exp, lme, others) if mine else kills.base_other(exp, lme, others)
    bon = kills.bonus_amount(base, rule.b, rule.mode)
    return {"full": [base + bon], "base": [base], "split": [base, bon]}[form]


CASES = ((2, kills.BonusFit(0.15, "ceil"), 25, [22]), (3, kills.BonusFit(0.3, "floor"), 30, [28, 35]),
         (4, kills.BonusFit(0.4, "ceil"), 28, [28, 28, 33]), (4, kills.BonusFit(0.25, "round"), 20, [40, 15, 25]))
for size, rule, lme, others in CASES:
    wrong = right = refused = lone_bad = 0
    for _ in range(300):
        exp = rng.choice(EXPS)
        mine = rng.random() < 0.5
        form = rng.choice(("full", "base", "split"))
        rises = truth(exp, rule, lme, others, mine, form)
        for i, d in enumerate(rises):
            got = kills.party_kills(MAP, d, lme, others, rule)
            is_bonus = form == "split" and i == 1
            if is_bonus:
                lone_bad += got != (0, 0)         # a lone bonus never counts a kill... of ANY owner
                continue
            if got == (0, 0):
                refused += 1
            elif got == ((1, 0) if mine else (0, 1)):
                right += 1
            else:
                wrong += 1
    check(wrong == 0 and lone_bad == 0 and right > 150,
          f"size {size} {rule} Lv {lme}+{others}: right {right}, refused {refused}, wrong {wrong}, bonus-as-kill {lone_bad}")

rule = CASES[2][1]
lme, others = 28, [28, 28, 33]
e1, e2 = EXPS[-1], EXPS[-2]
d = truth(e1, rule, lme, others, True, "full")[0] + truth(e2, rule, lme, others, True, "full")[0]
r = kills.party_kills(MAP, d, lme, others, rule)
check(r in ((2, 0), (0, 0)), f"double kill, both mine, never read as a teammate's ({r})")
d = truth(e1, rule, lme, others, True, "full")[0] + truth(e1, rule, lme, others, False, "full")[0]
r = kills.party_kills(MAP, d, lme, others, rule)
check(r in ((1, 1), (0, 0)), f"mine + a teammate's in one look -> {r}")
# an ambiguous value: found by search, must be refused
amb = None
for d in range(1, 400):
    if kills.read_rise(MAP, d, lme, others, rule)[2] == "ambiguous":
        amb = d
        break
check(amb is not None and kills.party_kills(MAP, amb, lme, others, rule) == (0, 0),
      f"an ambiguous rise ({amb}) is refused")
check(kills.party_kills(MAP, 0, lme, others, rule) == (0, 0) and kills.party_kills(MAP, 50, lme, [], rule) == (0, 0)
      and kills.party_kills("No Such Map", 50, lme, others, rule) == (0, 0) and kills.party_kills(MAP, 50, 0, others, rule) == (0, 0),
      "no rise / no party / unknown map / unknown level: unknown")

# 3 ----------------------------------------------------------------------------------------------------
G = partyexp.Levels([28, 28, 28])
G.guessed = 1                                  # one of them is a stand-in for a member not in the room
check(all(kills.party_kills(PM, d, 28, G, fit) == (1, 0) for d in (16, 17, 18)),
      "stand-in level: 16 / 17 / 18 are my Horny kill (+-1 on the base)")
check(kills.party_kills(PM, 19, 28, G, fit) == (0, 0) and kills.party_kills(PM, 16, 28, [28, 28, 28], fit) == (0, 0)
      and kills.party_kills(PM, 18, 28, [28, 28, 28], fit) == (0, 0),
      "19 is nothing even with a stand-in; without one 16 / 18 are nothing (tolerance only for stand-ins)")

# 4 ----------------------------------------------------------------------------------------------------
# estimating: base + a bonus anywhere in the grid is counted when unique; here Horny with 0.4 ceil = 17 + 7 = 24
check(kills.party_kills(PM, 24, 28, L) == (1, 0), "estimating: 17 + a plausible bonus (24) -> mine")
check(kills.party_kills(PM, 200, 28, L) == (0, 0), "estimating: a rise nothing explains -> nothing")

# 5 ----------------------------------------------------------------------------------------------------


def make_rises(size, rule, n, lme=28, noise=0.2):
    rises = []
    while len(rises) < n:
        others = [rng.randint(lme - 6, lme + 8) for _ in range(size - 1)]
        if rng.random() < noise:
            rises.append((MAP, rng.randint(1, 600), lme, others))      # a quest, an unknown source
            continue
        exp = rng.choice(EXPS)
        form = rng.choice(("full", "full", "base", "split"))
        for d in truth(exp, rule, lme, others, rng.random() < 0.4, form):
            rises.append((MAP, d, lme, others))
    return rises


t0 = time.time()
for size, rule in ((2, kills.BonusFit(0.15, "ceil")), (3, kills.BonusFit(0.3, "floor")), (4, kills.BonusFit(0.4, "ceil")),
                   (4, kills.BonusFit(0.25, "round"))):
    rs = make_rises(size, rule, 120)
    fit = kills.fit_bonus(rs)
    good = fit is not None and abs(fit["b"] - rule.b) <= 0.06
    same = good and kills.score_bonus(rs, kills.BonusFit(fit["b"], fit["mode"]))[0] >= kills.score_bonus(rs, rule)[0] - 2
    check(good and same, f"size {size}: {rule} recovered from 120 noisy rises -> {fit and (fit['b'], fit['mode'])} "
                         f"({fit and fit['explained']}/{fit and fit['n']} explained, bare base {fit and fit['base']})")
print(f"   (4 fits took {time.time() - t0:.1f} s)")
check(kills.fit_bonus(make_rises(4, CASES[2][1], 10)) is None, "fewer than 15 rises: no fit")
junk = [(MAP, rng.randint(1, 900), 28, [28, 30, 26]) for _ in range(150)]
check(kills.fit_bonus(junk) is None, "pure noise: no rule invented")
bare = []
while len(bare) < 100:
    others = [rng.randint(22, 36) for _ in range(3)]
    bare.append((MAP, kills.base_own(rng.choice(EXPS), 28, others), 28, others))
bare = [(MAP, d, 28, o) for (_, d, _, o) in bare]
check(kills.fit_bonus(bare) is None and kills.score_bonus_base(bare) == 100,
      "bonus never seen (base amounts only): no fit, but every kill is explained by the base")
clean = make_rises(4, CASES[2][1], 100, noise=0)
ex, mi, ot = kills.score_bonus(clean, CASES[2][1])
check(ex >= 0.85 * len(clean), f"score_bonus at the true rule explains {ex}/{len(clean)} (mine {mi}, theirs {ot})")
check(kills.score_bonus(clean, kills.BonusFit(0.1, "floor"))[0] < ex, "a wrong rule explains fewer")

# 6 ----------------------------------------------------------------------------------------------------
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
out.mkdir(parents=True, exist_ok=True)
pf = out / "history" / "party_bonus.json"
if pf.exists():
    pf.unlink()
pb = partyexp.PartyBonus(out)
check(pb.bonus(4) == (None, True), "no samples: no rule, estimating")
for m, d, lme, lv in make_rises(4, CASES[2][1], 60):
    pb.add(4, m, d, lme, lv)
t0 = time.time()
while time.time() - t0 < 90 and (pb.fitting or 4 not in pb.learned()):
    time.sleep(0.1)
pb.save()
f, est = pb.bonus(4)
check(not est and f is not None and abs(f.b - 0.4) <= 0.06, f"fit after ~60 samples: {f}, not estimating ({time.time() - t0:.1f} s)")
pb2 = partyexp.PartyBonus(out)
check(pf.exists() and pb2.bonus(4) == (f, False) and len(pb2.samples["4"]) >= 60,
      "persisted: a reload has the rule and the samples")
check(pb2.bonus(2)[1] is True, "another size is still estimating")
(out / "party.json").write_text(
    '{"t": %f, "members": [{"name": "A", "level": 30, "map": "M", "state": "playing", "age_s": 1},'
    '{"name": "B", "level": 31, "map": "Other", "state": "playing", "age_s": 1},'
    '{"name": "C", "level": 32, "map": "M", "state": "playing", "age_s": 30}]}' % time.time())
lv = partyexp.party_levels(out, 28, "M")
check(lv == [30] and lv.guessed == 0, "party_levels: only fresh members on my map, none guessed")
box = {"t": time.time(), "rows": [{"name": "Me", "me": True, "on_map": True}, {"name": "A", "on_map": True},
                                  {"name": "X", "on_map": True}, {"name": "Y", "on_map": False}]}
lv = partyexp.party_levels(out, 28, "M", rows=box)
check(lv == [30, 28] and lv.guessed == 1, "party box row not in the room counts with my own level (1 guessed)")
check(partyexp.party_levels(out, 28, "Nowhere") is None, "alone on the map: None (solo reading)")

print("\n" + ("all passed" if not fails else f"{len(fails)} failed"))
sys.exit(1 if fails else 0)
