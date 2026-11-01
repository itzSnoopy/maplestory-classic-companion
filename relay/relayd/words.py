"""Friendly tokens: <monster>-<job>-<4 digits>, e.g. slime-cleric-4821 (all lowercase).

Easy to read out and type, so they are short on purpose; the server rate-limits wrong guesses per
address (auth.Lockouts, keys "!token" / "!hostkey") and the admin can switch to long random tokens
(setting `secure_tokens`). Always picked with `secrets`.
"""
import re
import secrets

MONSTERS = (
    "snail slime stump pig mushroom octopus bubbling sentinel wraith lupin zombie drake golem "
    "yeti pepe boar croco ligator fairy pixie werewolf balrog zakum mano stumpy faust mushmom"
).split()
JOBS = (
    "beginner warrior fighter crusader hero page knight paladin spearman dragonknight darkknight "
    "magician wizard mage archmage cleric priest bishop archer hunter ranger bowmaster crossbowman "
    "sniper marksman thief assassin hermit nightlord bandit chiefbandit shadower pirate brawler "
    "marauder buccaneer gunslinger outlaw corsair"
).split()

FRIENDLY_RE = re.compile(r"^[a-z]+-[a-z]+-\d{4}$")
FRIENDLY_MAX_LEN = 32           # longer strings are never friendly tokens (long tokens stay exact)


def friendly_token():
    return f"{secrets.choice(MONSTERS)}-{secrets.choice(JOBS)}-{secrets.randbelow(10000):04d}"


def normalize(token):
    """Trim; lowercase if (and only if) it then looks like a friendly token. Long tokens stay exact."""
    t = (token or "").strip()
    low = t.lower()
    if len(low) <= FRIENDLY_MAX_LEN and FRIENDLY_RE.match(low):
        return low
    return t
