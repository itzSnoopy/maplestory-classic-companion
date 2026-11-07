"""partyfx.py - party members' skills that act on you, as slots for the skill bar (pure: no tkinter).

PARTY_SKILLS is the one table to extend. An entry, by skill id:
  name   shown name
  jobs   party.json "job" spellings of the classes that cast it
  kind   "heal" = instant (pulse + the amount), "buff" = timed (countdown from the event; the length
         is skill_kinds_classic.json duration_s at `level`)
  event  the name of the usage event (live.json usage.recent) that says it hit me
  level  buff only: skill level assumed for the caster (their real level is not known)

party_slots(party, events, now, my_map, table) -> [{id, name, from, kind, last_t, amount, ends,
level, dim}], in table order. party = party.json dict, events = live.json usage.recent.
"""
from gamedata import translate

STALE_S = 30.0          # party.json older than this (or ok false): nobody is in the party

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
