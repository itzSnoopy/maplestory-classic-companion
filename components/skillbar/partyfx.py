"""partyfx.py - party members' skills that act on you, as slots for the skill bar (pure: no tkinter).

PARTY_SKILLS is the one table to extend. An entry, by skill id:
  name   shown name
  jobs   party.json "job" spellings of the classes that cast it
  kind   "heal" = instant (pulse + the amount), "buff" = timed (countdown from the event; the length
         is skill_kinds_classic.json duration_s at `level`)
  event  the name of the usage event (live.json usage.recent) that says it hit me
  level  buff only: skill level assumed for the caster (their real level is not known)
  same_as  another entry's id with the same picture (Haste 4201001 = 4101001): no slot of its own, its jobs count for
         that entry's ONE slot (keyed and hidden by the first id)
  icon   buff only, True: the buff's state comes from the game's buff list (live.json usage.party_buffs, read by
         engine/buffs.py find_loose), not from usage events: lit while the icon shows, exact seconds left when its
         countdown digits were read (the last 60 s) or the minutes digit changed, else an estimate: first seen + duration at
         `level`, kept inside the minutes digit's range (the feed publishes `ends` and `est`)

credit_heals(state, members, heal_cast_times, now): the Cleric's side. What a party member's Heal did to
ME is told by that member (party.json member "healed" = [seq, last, avg], sent by their skill bar); a new seq
within HEAL_CREDIT_S after my own Heal cast is credited to that cast.

party_slots(party, events, now, my_map, table, buffs) -> [{id, name, from, kind, last_t, amount, ends,
dur, level, dim, on, est}], in table order. party = party.json dict, events = live.json usage.recent, buffs =
live.json usage.party_buffs ({id: {since, ends, est, mins}} for the watched buff icons showing now; for entries with icon).
"""
from gamedata import translate

STALE_S = 30.0          # party.json older than this (or ok false): nobody is in the party
HEAL_CREDIT_S = 12.0    # a member's healed seq going up counts for my Heal cast at most this long before
CREDIT_KEEP_S = 300.0   # credits are forgotten after this

PARTY_SKILLS = {
    "2301001": {"name": "Heal", "jobs": ["Cleric", "Priest", "Bishop"], "kind": "heal",
                "event": "Heal (party?)"},
    # Haste (same picture for both): level 7 = 160 s (the player's Haste from a party member gave Speed +16 = level 7,
    # skills_classic.json); a caster's real level is not known
    "4101001": {"name": "Haste", "jobs": ["Assassin", "Hermit", "Night Lord"], "kind": "buff",
                "event": "Haste (party?)", "level": 7, "icon": True},
    "4201001": {"name": "Haste", "jobs": ["Bandit", "Chief Bandit", "Shadower"], "kind": "buff",
                "event": "Haste (party?)", "level": 7, "icon": True, "same_as": "4101001"},
}

# demo only (skillbar_ui --demo): a timed buff, to see how one looks. Not in the real table.
DEMO_BUFF = {"4101001": {"name": "Haste", "jobs": ["Assassin", "Night Lord", "Shadower"],
                         "kind": "buff", "event": "Haste (party?)", "level": 5}}


def speed(sid, level):
    """The "Speed +N" its skill text states at `level` (Haste), or None."""
    import re
    sk = next((s for s in translate.skills() if s["id"] == sid), None)
    lv = (sk or {}).get("levels") or []
    m = re.search(r"speed \+(\d+)", lv[level - 1], re.I) if 0 < level <= len(lv) else None
    return int(m.group(1)) if m else None


def duration(sid, level):
    k = translate.skill_kind(sid) or {}
    d = k.get("duration_s") or []
    return d[level - 1] if 0 < level <= len(d) else None


def levels_in(sid, lo, hi):
    """Skill levels whose duration is within [lo, hi] seconds (the minutes digit at a cast narrows the level)."""
    d = (translate.skill_kind(sid) or {}).get("duration_s") or []
    return [i + 1 for i, v in enumerate(d) if lo <= v <= hi]


def level_for_duration(sid, secs, tol=4.0):
    """The level whose duration is nearest to `secs`, only when within +-tol seconds (else None: leave it unknown)."""
    d = (translate.skill_kind(sid) or {}).get("duration_s") or []
    best = min(range(len(d)), key=lambda i: abs(d[i] - secs), default=None)
    return best + 1 if best is not None and abs(d[best] - secs) <= tol else None


def group_jobs(table, sid):
    """Jobs of an entry plus the entries that are `same_as` it."""
    return list(table[sid]["jobs"]) + [j for o in table.values() if o.get("same_as") == sid for j in o["jobs"]]


def casters(party, sid, now, table=None):
    """Names of the party.json members whose job casts `sid` (or its same_as twin); [] when the party link is off,
    stale or has none."""
    table = PARTY_SKILLS if table is None else table
    sid = (table.get(sid) or {}).get("same_as") or sid
    if sid not in table or not isinstance(party, dict) or not party.get("ok"):
        return []
    try:
        if now - float(party.get("t") or 0) > STALE_S:
            return []
    except (TypeError, ValueError):
        return []
    jobs = group_jobs(table, sid)
    return [m.get("name") or "?" for m in party.get("members") or [] if isinstance(m, dict) and m.get("job") in jobs]


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


def party_slots(party, events, now, my_map=None, table=None, buffs=None):
    """One slot per table entry (same_as twins share one) that has a caster of its jobs in party.json; an `icon` entry
    also has a slot whenever its icon shows (buffs has its id), with or without the party link."""
    table = PARTY_SKILLS if table is None else table
    members = []
    if isinstance(party, dict) and party.get("ok"):
        try:
            if now - float(party.get("t") or 0) <= STALE_S:
                members = [m for m in party.get("members") or [] if isinstance(m, dict)]
        except (TypeError, ValueError):
            members = []
    out = []
    for sid, e in table.items():
        if e.get("same_as"):
            continue
        jobs = group_jobs(table, sid)
        who = [m for m in members if m.get("job") in jobs]
        b = (buffs or {}).get(sid) if isinstance(buffs, dict) and e["kind"] == "buff" and e.get("icon") else None
        if not who and not b:
            continue
        last = None
        for ev in events or []:
            if isinstance(ev, dict) and ev.get("name") == e["event"] and (
                    last is None or (ev.get("t") or 0) > (last.get("t") or 0)):
                last = ev
        if e.get("icon"):               # caption: the caster when exactly one is known, else the skill's name
            names = [who[0].get("name") or "?"] if len(who) == 1 else [e["name"]]
        else:
            names = [m.get("name") or "?" for m in who]
        slot = {"id": sid, "name": e["name"], "from": names,
                "kind": e["kind"], "last_t": last.get("t") if last else None,
                "amount": last.get("amount") if last else None, "ends": None,
                "dur": None, "level": e.get("level"), "lvl_src": "assumed",
                # dim when none of the casters is on my map (when the maps are known)
                "dim": bool(who) and bool(my_map) and all(m.get("map") for m in who)
                       and not any(m.get("map") == my_map for m in who)}
        slot["on"], slot["est"] = None, False
        if e["kind"] == "buff" and e.get("icon"):
            # the buff list says: lit while the icon shows; exact end from the digits / the minutes digit, else an
            # estimate (the feed publishes the end, `est`, and the level it assumes or learned)
            lv = (b or {}).get("level") or e.get("level") or 1
            dur = duration(sid, lv)
            slot["on"], slot["dur"], slot["level"] = bool(b), dur, lv
            slot["speed"] = speed(sid, lv)                  # Haste: "+16" under the slot (at the learned / assumed level)
            if b:
                slot["lvl_src"] = "learned" if b.get("src") == "learned" else "assumed"
                slot["last_t"] = b.get("since")             # a new "since" = a (re)cast: the bar pulses
                if b.get("ends"):
                    slot["ends"], slot["est"] = b["ends"], bool(b.get("est"))     # the feed says exact (digits) or estimated
                elif dur and b.get("since"):
                    slot["ends"], slot["est"] = b["since"] + dur, True
                slot["dim"] = False
        elif e["kind"] == "buff" and last:
            dur = duration(sid, e.get("level") or 1)
            if dur:
                slot["ends"], slot["dur"] = last["t"] + dur, dur
        out.append(slot)
    return out
