"""partyfx.py - party members' skills that act on you, as slots for the skill bar (pure: no tkinter).

PARTY_SKILLS is the one table to extend. An entry, by skill id:
  name   shown name
  jobs   party.json "job" spellings of the classes that cast it
  kind   "heal" = instant (pulse + the amount), "buff" = timed (countdown from the event; the length
         is skill_kinds_classic.json duration_s at `level`)
  event  the name of the usage event (live.json usage.recent) that says it hit me
  level  buff only: skill level assumed for the caster (their real level is not known)

credit_heals(state, members, heal_cast_times, now): the Cleric's side. What a party member's Heal did to
ME is told by that member (party.json member "healed" = [seq, last, avg], sent by their skill bar); a new seq
within HEAL_CREDIT_S after my own Heal cast is credited to that cast.

party_slots(party, events, now, my_map, table) -> [{id, name, from, kind, last_t, amount, ends,
level, dim}], in table order. party = party.json dict, events = live.json usage.recent.
"""
from gamedata import translate

STALE_S = 30.0          # party.json older than this (or ok false): nobody is in the party
HEAL_CREDIT_S = 12.0    # a member's healed seq going up counts for my Heal cast at most this long before
CREDIT_KEEP_S = 300.0   # credits are forgotten after this

PARTY_SKILLS = {
    "2301001": {"name": "Heal", "jobs": ["Cleric", "Priest", "Bishop"], "kind": "heal",
                "event": "Heal (party?)"},
}

# demo only (skillbar_ui --demo): a timed buff, to see how one looks. Not in the real table.
DEMO_BUFF = {"4101001": {"name": "Haste", "jobs": ["Assassin", "Night Lord", "Shadower"],
                         "kind": "buff", "event": "Haste (party?)", "level": 5}}


def duration(sid, level):
    k = translate.skill_kind(sid) or {}
    d = k.get("duration_s") or []
    return d[level - 1] if 0 < level <= len(d) else None


def credit_heals(state, members, heal_cast_times, now):
    """Credit my Heal casts with what party members report. Pure; `state` (a dict, start with {}) is kept
    by the caller between calls. members = party.json members (dicts with name and healed [seq, last, avg]),
    heal_cast_times = when my own Heal was cast (seconds, same clock as `now`).
    - the first sight of a member (or after they were gone from the list, or their seq went down: a new
      character / restart) is a baseline: no credit;
    - a member's seq going up while a cast of mine was within HEAL_CREDIT_S before `now` is a credit
      {name, amount: last, avg, t: now, cast_t: that newest cast};
    - credits older than CREDIT_KEEP_S are dropped.
    Returns the credits, oldest first (the same list object every call)."""
    seen = state.setdefault("seen", {})
    credits = state.setdefault("credits", [])
    cast = max((c for c in heal_cast_times or [] if 0 <= now - c <= HEAL_CREDIT_S), default=None)
    names = set()
    for m in members or []:
        if not isinstance(m, dict):
            continue
        name, h = m.get("name"), m.get("healed")
        if not name or not (isinstance(h, (list, tuple)) and len(h) == 3
                            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in h)):
            continue
        names.add(name)
        seq = int(h[0])
        prev = seen.get(name)
        seen[name] = seq
        if prev is not None and seq > prev and cast is not None:
            credits.append({"name": name, "amount": int(h[1]), "avg": int(h[2]), "t": now, "cast_t": cast})
    for name in [n for n in seen if n not in names]:
        del seen[name]
    credits[:] = [c for c in credits if now - c["t"] <= CREDIT_KEEP_S]
    return credits


def party_slots(party, events, now, my_map=None, table=None):
    table = PARTY_SKILLS if table is None else table
    if not isinstance(party, dict) or not party.get("ok"):
        return []
    try:
        if now - float(party.get("t") or 0) > STALE_S:
            return []
    except (TypeError, ValueError):
        return []
    members = [m for m in party.get("members") or [] if isinstance(m, dict)]
    out = []
    for sid, e in table.items():
        who = [m for m in members if m.get("job") in e["jobs"]]
        if not who:
            continue
        last = None
        for ev in events or []:
            if isinstance(ev, dict) and ev.get("name") == e["event"] and (
                    last is None or (ev.get("t") or 0) > (last.get("t") or 0)):
                last = ev
        slot = {"id": sid, "name": e["name"], "from": [m.get("name") or "?" for m in who],
                "kind": e["kind"], "last_t": last.get("t") if last else None,
                "amount": last.get("amount") if last else None, "ends": None,
                "dur": None, "level": e.get("level"),
                # dim when none of the casters is on my map (when the maps are known)
                "dim": bool(my_map) and all(m.get("map") for m in who)
                       and not any(m.get("map") == my_map for m in who)}
        if e["kind"] == "buff" and last:
            dur = duration(sid, e.get("level") or 1)
            if dur:
                slot["ends"], slot["dur"] = last["t"] + dur, dur
        out.append(slot)
    return out
