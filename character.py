"""character.py - each character's long-lived facts, kept from short glimpses of game windows.

Sources hand over "facts" (sources/screen.py: panels.py reads the Character Stat, Skill and Change
Channel windows; charname.py says who is playing). This module decides what to believe and keeps
history/characters.json:

  current      the character playing now (key = status-bar name fingerprint, charname.py)
  characters   per character: name, world, job,
                 stats    level, HP/MP max, fame, STR/DEX/INT/LUK, unspent AP and SP (+ seen times)
                 skills   by skill id: name, job, level, max level (+ seen times)
                 channel  world + channel, and how it was learned
                 changes  the last 200 changes ("INT 76 -> 77", "Magic Claw 5 -> 6")

Players glance at these windows rarely (at a level-up, or once in half an hour), maybe for one
frame. So one read is enough when it's consistent; only reads that don't add up wait for a second
matching read:
  - stats: STR+DEX+INT+LUK+AP = 25 + 5 x (level - 1) (true at Lv 12 and Lv 14 for the player), or
    no stat went down since the last accepted read
  - skills: a level that didn't go down and isn't above the skill's max level
  - AP / SP / HP / MP max / fame: taken with the window read they came in
Values go down only with reset items, so a drop needs two matching reads (a tooltip over "77"
can't make it "7" by itself).

Channel (recorded 2026-10-23 at 10 fps, NOTES 21:05): the Change Channel window opens with the
current channel highlighted, so the first read after it opens is the channel ("sure"). A switch
has no confirmation: the window closes and the screen fades to black for ~1.2 s right after
(Cancel: no fade). The picked cell is often never on screen (double-click switches at once), so a
switch only says "ch ? (switched from 21, maybe 18)" until the window is opened again.

Nothing here knows about pixels; any source that produces the same dicts can feed it.
"""
import json
import time
from datetime import datetime

import history as H

LABEL = {"level": "Level", "str": "STR", "dex": "DEX", "int": "INT", "luk": "LUK", "ap": "AP",
         "fame": "Fame", "hp_max": "Max HP", "mp_max": "Max MP", "sp": "SP"}
BASE_STATS = ("str", "dex", "int", "luk")
WINDOW_GAP_S = 4        # a window not seen for this long counts as closed
SWITCH_DARK_S = 3       # window last seen -> screen dark within this = a channel switch
MAX_CHANGES = 200
MAX_QUEUE = 50          # facts held while it's not known yet who is playing


def _iso(t):
    return datetime.fromtimestamp(t).isoformat(timespec="seconds")


def _ch_text(c):
    """'Windia ch 31', or 'Windia ch ? (switched from 21, maybe 18)' until it's seen again."""
    w = c.get("world") or ""
    if c.get("ch"):
        return f"{w} ch {c['ch']}".strip()
    extra = f"switched from {c['from']}" if c.get("from") else "unknown"
    if c.get("maybe"):
        extra += f", maybe {c['maybe']}"
    return f"{w} ch ? ({extra})".strip()


def stats_add_up(level, stats, ap):
    """Every level gives 5 AP; a new character's four stats sum to 25."""
    if not level or ap is None or any(stats.get(k) is None for k in BASE_STATS):
        return False
    return sum(stats[k] for k in BASE_STATS) + ap == 25 + 5 * (level - 1)


COMMIT_SCORE = 8      # a screenshot scoring this (of 10) or more is kept at once
MIN_SCORE = 4         # below this a screenshot is never used, even if the next one agrees


def _drops(old, new, keys):
    return [k for k in keys if new.get(k) is not None and old.get(k) is not None and new[k] < old[k]]


def score_stat(r, saved):
    """(score 0-10, [reasons]) for one Character Stat screenshot. `saved` = stored values."""
    s, why = 10, []
    missing = [k for k in ("level", "str", "dex", "int", "luk", "ap", "hp", "mp", "exp")
               if r.get(k) is None]
    if missing:
        s -= min(4, len(missing))
        why.append("unreadable: " + ", ".join(missing))
    if not stats_add_up(r.get("level"), r, r.get("ap")):
        s -= 3
        why.append("STR+DEX+INT+LUK+AP doesn't match the level")
    hud = r.get("hud") or {}
    if hud.get("level") and r.get("level") and hud["level"] != r["level"]:
        s -= 3
        why.append(f"level {r['level']} but the HUD says {hud['level']}")
    if hud.get("exp") is not None and r.get("exp") and r["exp"][0] != hud["exp"]:
        s -= 2
        why.append(f"EXP {r['exp'][0]} but the HUD says {hud['exp']}")
    if any(r.get(k) and r[k][0] > r[k][1] for k in ("hp", "mp")):
        s -= 2
        why.append("HP/MP above max")
    if r.get("fit") is not None and r["fit"] < 0.6:
        s -= 1
        why.append(f"loose layout fit {r['fit']}")
    d = _drops(saved, r, BASE_STATS + ("level",))
    if d:
        s -= 4
        why.append("lower than saved: " + ", ".join(d))
    return max(0, s), why


def score_skills(r, saved):
    """(score, reasons) for one Skill window screenshot. `saved` = {skill id: level}."""
    s, why = 10, []
    rows = r.get("skills", [])
    unnamed = sum(1 for x in rows if not x.get("id"))
    unread = sum(1 for x in rows if x.get("level") is None)
    over = [x["name"] for x in rows if x.get("max") and (x.get("level") or 0) > x["max"]]
    if unnamed:
        s -= unnamed
        why.append(f"{unnamed} skill name(s) not recognized")
    if unread:
        s -= unread
        why.append(f"{unread} level(s) unreadable")
    if r.get("sp") is None:
        s -= 1
        why.append("SP unreadable")
    if over:
        s -= 4 * len(over)
        why.append("above max level: " + ", ".join(over))
    low = [x["name"] for x in rows if x.get("id") in saved and x.get("level") is not None
           and saved[x["id"]] is not None and x["level"] < saved[x["id"]]]
    if low:
        s -= 4
        why.append("lower than saved: " + ", ".join(low))
    return max(0, s), why


def score_channel(r, saved):
    """(score, reasons) for one Change Channel screenshot. `saved` = stored channel dict."""
    s, why = 10, []
    if (r.get("cells") or 0) < 40:
        s -= 2
        why.append(f"only {r.get('cells')} channel cells visible")
    if not r.get("world"):
        s -= 1
        why.append("world name unread")
    if saved.get("sure") and saved.get("ch") not in (None, r.get("channel")):
        s -= 3
        why.append(f"says ch {r.get('channel')}, saved ch {saved['ch']}")
    return max(0, s), why


def score_who(v, known):
    """(score, reasons) for the status-bar name. `known` = characters seen before."""
    if not v.get("clean"):
        return 0, ["name on the status bar looks covered"]
    if v["fp"] in known:
        return 10, []
    if v.get("after_away"):
        return 7, ["new character (after the game left the screen)"]
    return 3, ["new character name mid-play (something over the name?)"]


class Profile:
    def __init__(self, note=None, path=None, log=None):
        self.path = path
        self.note = note or (lambda text: None)     # changes: journal + log
        self.log = log or self.note                 # scores / waiting: tracker log only
        self.db = H.read_json(self._path(), {}) or {}
        self.db.setdefault("characters", {})
        self.pending = {}           # (who, section, key) -> value seen once
        self.queue = []             # facts held while it's not settled who is playing
        self.who_pending = None     # new character fingerprint waiting for the next screenshot
        self.ch_open = None         # {"first": ch, "last": ch, "world": w, "t": last seen}
        self.dirty = False

    def _path(self):
        return self.path or (H.HISTORY / "characters.json")

    @property
    def who(self):
        return self.db.get("current")

    def char(self, who=None):
        who = who or self.who
        return self.db["characters"].setdefault(who, {}) if who else None

    # ------------------------------------------------------------------ input
    def update(self, facts, t=None):
        """facts: list of (t, kind, value). kind 'who' | 'stat' | 'skills' | 'channel' | 'away'."""
        now = t or time.time()
        for f in facts:
            ft, kind, v = f
            if kind == "who":
                self._who(ft, v)
                if self.queue and self.who and not self.who_pending:
                    q, self.queue = self.queue, []
                    for g in q:
                        self._apply(*g)
            elif (self.who is None or self.who_pending) and kind in ("stat", "skills", "channel"):
                self.queue = (self.queue + [f])[-MAX_QUEUE:]
            else:
                self._apply(ft, kind, v)
        self._tick(now)
        if self.dirty:
            self.db["updated"] = _iso(now)
            H.write_json(self._path(), self.db, indent=1)
            self.dirty = False

    def _apply(self, t, kind, v):
        if kind == "stat":
            self._stat(t, v)
        elif kind == "skills":
            self._skills(t, v)
        elif kind == "channel":
            self._channel(t, v)
        elif kind == "away":
            self._away(t)

    def _who(self, t, v):
        fp = v.get("fp")
        if not fp:
            return
        if fp == self.who:
            if self.who_pending:
                self.who_pending = None         # back to the known one: it was a blip
                self.queue = []
            return
        sc, why = score_who(v, self.db["characters"])
        if fp == self.who_pending and v.get("clean"):
            sc = COMMIT_SCORE                   # the next screenshot agrees: validated
        if sc < MIN_SCORE:
            return                              # covered name / mid-play change: ignore
        if sc < COMMIT_SCORE and self.who_pending != fp:
            self.who_pending = fp               # wait for the next screenshot to agree
            self.log(f"possible character switch ({fp}, score {sc}/10: {'; '.join(why)}); "
                      "waiting for the next screenshot")
            return
        self.who_pending = None
        c = self.char(fp)
        if not c.get("name") and v.get("guess"):
            c["name_guess"] = v["guess"]        # Tesseract's try; the Stat window gives the real one
        c["last_seen"] = _iso(t)
        prev = self.char()
        self.db["current"] = fp
        self.ch_open = None
        self.note(f"Character: {self._name(prev) if prev is not None else '-'} -> {self._name(c)}")
        self.dirty = True

    @staticmethod
    def _name(c):
        return c.get("name") or c.get("name_guess") or "?"

    def _keep(self, section, key, value, t, label, trusted, extra=None):
        """Store data[section][key] = value. `trusted`: the screenshot scored >= COMMIT_SCORE;
        otherwise a change waits until the next screenshot shows the same value."""
        c = self.char()
        sec = c.setdefault(section, {})
        old = sec.get(key)
        old_v = old.get("value") if isinstance(old, dict) else None
        pk = (self.who, section, key)
        if old_v == value:
            old["seen"] = _iso(t)
            self.pending.pop(pk, None)
            self.dirty = True
            return False
        if not trusted and self.pending.get(pk) != value:
            self.pending[pk] = value            # low score: wait for the next screenshot
            return False
        self.pending.pop(pk, None)
        sec[key] = {"value": value, "seen": _iso(t), "since": _iso(t), **(extra or {})}
        if old_v is not None:
            self._change(t, label, old_v, value)
        self.dirty = True
        return True

    def _change(self, t, what, old, new):
        c = self.char()
        ch = c.setdefault("changes", [])
        ch.append({"t": _iso(t), "what": what, "from": old, "to": new})
        del ch[:-MAX_CHANGES]
        self.note(f"{self._name(c)}: {what} {old} -> {new}")

    def _scored(self, t, kind, sc, why):
        """Remember the last screenshot's score; log the ones that have to wait."""
        c = self.char()
        prev = (c.get("last_reads") or {}).get(kind) or {}
        c.setdefault("last_reads", {})[kind] = {"t": _iso(t), "score": sc, "why": why}
        if sc < COMMIT_SCORE and (prev.get("score"), prev.get("why")) != (sc, why):
            self.log(f"{kind} window scored {sc}/10 ({'; '.join(why)}): "
                      + ("not used" if sc < MIN_SCORE else "waiting for the next screenshot"))
        self.dirty = True
        return sc >= COMMIT_SCORE

    def _stat(self, t, r):
        c = self.char()
        saved = {k: (v or {}).get("value") for k, v in (c.get("stats") or {}).items()}
        sc, why = score_stat(r, saved)
        trusted = self._scored(t, "stat", sc, why)
        if sc < MIN_SCORE:
            return
        if r.get("name") and c.get("name") != r["name"]:
            c["name"] = r["name"]
            c.pop("name_guess", None)
        if r.get("job"):
            c["job"] = r["job"]
        new = {k: r.get(k) for k in ("level", "str", "dex", "int", "luk", "ap", "fame")}
        if r.get("hp") and r["hp"][0] <= r["hp"][1]:
            new["hp_max"] = r["hp"][1]
        if r.get("mp") and r["mp"][0] <= r["mp"][1]:
            new["mp_max"] = r["mp"][1]
        for k, v in new.items():
            if v is not None:
                self._keep("stats", k, v, t, LABEL[k], trusted)
        c["stats_seen"] = _iso(t)
        c["stats_add_up"] = stats_add_up(new["level"], new, new["ap"])
        self.dirty = True

    def _skills(self, t, r):
        c = self.char()
        saved = {k: (v or {}).get("value") for k, v in (c.get("skills") or {}).items()}
        sc, why = score_skills(r, saved)
        trusted = self._scored(t, "skills", sc, why)
        if sc < MIN_SCORE:
            return
        for s in r.get("skills", []):
            lv = s.get("level")
            if not s.get("id") or lv is None:
                continue
            if s.get("max") and lv > s["max"]:
                continue                        # impossible: a misread
            self._keep("skills", s["id"], lv, t, s["name"], trusted,
                       {"name": s["name"], "job": s.get("job"), "max": s.get("max")})
        if r.get("sp") is not None:
            self._keep("stats", "sp", r["sp"], t, LABEL["sp"], trusted)
        c["skills_seen"] = _iso(t)
        self.dirty = True

    def _channel(self, t, r):
        ch, world = r.get("channel"), r.get("world")
        if not ch:
            return
        o = self.ch_open
        if o is None or t - o["t"] > WINDOW_GAP_S:
            # the window just opened: its highlight is the channel the player is on now
            # (recorded 2026-10-23: after each switch the window reopened on the new channel).
            # But a look comes once a second and players start clicking within ~0.6 s, so the
            # first read is scored: under 8 (e.g. it contradicts a sure saved channel) it waits.
            # The next screenshot validates it (same channel), or shows the saved one (keep it).
            cur = self.char().get("channel") or {}
            sc, why = score_channel(r, cur)
            ok = self._scored(t, "channel", sc, why)
            self.ch_open = {"first": ch, "last": ch, "world": world, "t": t, "ok": ok,
                            "held": cur.get("ch") if cur.get("sure") else None}
            if ok:
                self._set_channel(t, ch, world, "seen in the Change Channel window")
        else:
            prev = o["last"]
            o.update(last=ch, t=t, world=world or o["world"])
            if not o["ok"] and (ch == o["first"] == prev or ch == o["held"]):
                # validated by the next screenshot (same channel), or the saved one shows up
                o.update(first=ch, ok=True)
                self._set_channel(t, ch, o["world"], "seen in the Change Channel window")

    def _set_channel(self, t, ch, world, how):
        c = self.char()
        cur = c.get("channel") or {}
        world = world or cur.get("world")
        if world:
            c["world"] = world
        if cur.get("ch") == ch and cur.get("world") == world and cur.get("sure", True):
            cur["seen"] = _iso(t)
            self.dirty = True
            return
        if cur:
            self._change(t, "Channel", _ch_text(cur), f"{world or ''} ch {ch}".strip())
        c["channel"] = {"ch": ch, "world": world, "sure": True, "since": _iso(t), "seen": _iso(t),
                        "how": how}
        self.dirty = True

    def _away(self, t):
        """The screen went dark / the game left the screen. Right after the Change Channel window
        closed, that's a channel switch (the game fades to black ~1.2 s; Cancel doesn't)."""
        o = self.ch_open
        if not o or not (0 <= t - o["t"] <= SWITCH_DARK_S):
            return
        self.ch_open = None
        if not o["ok"]:
            o["first"] = o["held"]          # the first read never got validated
        c = self.char()
        cur = c.get("channel") or {}
        # the picked cell is often never on screen (a double-click switches at once), so the
        # new channel is only a guess until the window is opened again
        maybe = o["last"] if o["last"] != o["first"] else None
        c["channel"] = {"ch": None, "maybe": maybe, "from": o["first"], "world": o["world"] or cur.get("world"),
                        "sure": False, "since": _iso(t), "seen": _iso(t),
                        "how": "switched (screen went dark after the window closed); "
                               "the new channel shows the next time the window opens"}
        self._change(t, "Channel", f"{o['world'] or ''} ch {o['first']}".strip(), _ch_text(c["channel"]))
        self.dirty = True

    def _tick(self, now):
        o = self.ch_open
        if o and now - o["t"] > max(WINDOW_GAP_S, SWITCH_DARK_S):
            self.ch_open = None             # closed without the screen going dark: cancelled
            # (an unvalidated first read is dropped: the saved channel stays until the next window)

    # ------------------------------------------------------------------ learned elsewhere
    def learn_skill_level(self, sid, level, t, name=None):
        """A skill level worked out from what its casts cost (mpuse.levels_seen): stored like a
        Skill window read, but only upwards (SP is never taken back) and marked "from casts"."""
        c = self.char()
        if c is None:
            return False
        old = ((c.get("skills") or {}).get(sid) or {}).get("value") or 0
        if level <= old:
            return False
        extra = dict((c.get("skills") or {}).get(sid) or {})
        extra.pop("value", None), extra.pop("seen", None), extra.pop("since", None)
        extra.update(how="from what its casts cost", name=name or extra.get("name"))
        return self._keep("skills", sid, level, t, name or sid, True, extra)

    def regen(self):
        """This character's regen numbers (live.py): {"mp": {...}, "hp": {...}, "clock": {...}}."""
        c = self.char()
        return c.setdefault("regen", {}) if c is not None else {}

    def save_regen(self, data):
        c = self.char()
        if c is not None and c.get("regen") != data:
            c["regen"] = data
            self.dirty = True

    # ------------------------------------------------------------------ output
    @staticmethod
    def _summary(c):
        st = {k: v.get("value") for k, v in (c.get("stats") or {}).items() if isinstance(v, dict)}
        ch = c.get("channel") or {}
        return {
            "name": c.get("name") or c.get("name_guess"), "job": c.get("job"), "world": c.get("world"),
            "channel": _ch_text(ch) if ch else None,
            "channel_since": ch.get("since"),
            "stats": st or None, "stats_seen": c.get("stats_seen"),
            "skills": {v["name"]: f"{v['value']}/{v.get('max') or '?'}"
                       for _, v in sorted((c.get("skills") or {}).items())} or None,
            "skills_seen": c.get("skills_seen"),
            "recent_changes": [f"{x['t'][:16]} {x['what']} {x['from']} -> {x['to']}"
                               for x in (c.get("changes") or [])[-5:]],
        }

    def summary(self):
        """Small dict for digest.json: the current character in full, the others by name."""
        cur = self.db["characters"].get(self.who) if self.who else None
        return {
            **(self._summary(cur) if cur else {}),
            "other_characters": [self._name(c) for k, c in self.db["characters"].items()
                                 if k != self.who] or None,
        }


def summary():
    """For history.build_digest: read the file, no state."""
    try:
        return Profile().summary()
    except Exception:
        return None


if __name__ == "__main__":
    print(json.dumps(Profile().summary(), indent=1))
