"""Skill feedback inbox for the server owner (not a room module). Items live in SQLite.

POST /v2/feedback/<code>  a "classic-skill-review/1" export (64 KB max)  -> {ok, id}
GET / DELETE              header X-Feedback-Key (DELETE: optional {"ids": [...]})
Off unless FEEDBACK_KEY is set. Only the cleaned copy is stored, never an address.
"""
import hmac
import re
import secrets
import threading

from . import clock
from .errors import ApiError

FORMAT = "classic-skill-review/1"
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9 ]{1,32}$")
SKILL_ID_RE = re.compile(r"^\d{1,9}$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
STATUSES = ("ok", "wrong", "unsure", "")
KINDS = ("", "attack", "buff", "passive", "other")
MODES = ("", "timed", "toggle", "until death")


def _bad(msg):
    return ApiError(400, msg)


def clean_submission(d):
    """Validate a review export; return the cleaned copy (unknown fields dropped) or raise 400."""
    if not isinstance(d, dict):
        raise _bad("body must be an object")
    if d.get("format") != FORMAT:
        raise _bad("unknown format")
    branch = d.get("branch")
    if not isinstance(branch, str) or not BRANCH_RE.match(branch):
        raise _bad("bad branch")
    reviewer = d.get("reviewer", "")
    if not isinstance(reviewer, str):
        raise _bad("bad reviewer")
    reviewer = reviewer.strip()
    if len(reviewer) > 24 or CTRL_RE.search(reviewer):
        raise _bad("bad reviewer")
    exported = d.get("exported", "")
    if not isinstance(exported, str) or len(exported) > 40:
        raise _bad("bad exported")
    out = {"format": FORMAT, "branch": branch, "reviewer": reviewer, "exported": exported}
    fetched = d.get("data_fetched")
    if fetched is not None:
        if not isinstance(fetched, str) or len(fetched) > 40:
            raise _bad("bad data_fetched")
        out["data_fetched"] = fetched
    cnt = d.get("count", 0)
    if isinstance(cnt, bool) or not isinstance(cnt, int) or not 0 <= cnt <= 1000:
        raise _bad("bad count")
    rv = d.get("reviews")
    if not isinstance(rv, dict) or not 1 <= len(rv) <= 400:
        raise _bad("reviews must have 1-400 entries")
    reviews = {}
    for k, v in rv.items():
        if not isinstance(k, str) or not SKILL_ID_RE.match(k) or not isinstance(v, dict):
            raise _bad("bad review entry")
        name = v.get("name", "")
        if not isinstance(name, str) or len(name) > 64:
            raise _bad("bad skill name")
        e = {"name": name}
        for field, allowed in (("status", STATUSES), ("kind", KINDS), ("mode", MODES)):
            val = v.get(field, "")
            if not isinstance(val, str) or val not in allowed:
                raise _bad("bad " + field)
            e[field] = val
        note = v.get("note", "")
        if not isinstance(note, str):
            raise _bad("bad note")
        e["note"] = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", "", note)[:500]
        if e["status"] or e["kind"] or e["mode"] or e["note"]:
            reviews[k] = e
    if not reviews:
        raise _bad("nothing to store")
    out["count"] = len(reviews)
    out["reviews"] = reviews
    return out


class Feedback:
    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg
        self.lock = threading.Lock()
        self._last = {}         # address -> time of last post (memory only)

    @property
    def enabled(self):
        return bool(self.cfg.feedback_key)

    def check_code(self, code):
        if not CODE_RE.match(code) or (self.cfg.feedback_codes and code not in self.cfg.feedback_codes):
            raise ApiError(403, "unknown feedback code")

    def check_key(self, key):
        if not hmac.compare_digest(key.encode(), self.cfg.feedback_key.encode()):
            raise ApiError(403, "bad key")

    def gap_ok(self, addr):
        if clock.now() - self._last.get(addr, -1e18) < self.cfg.feedback_gap_s:
            raise ApiError(429, "one send per 30 s")

    def mark_sent(self, addr):
        with self.lock:
            if len(self._last) > 5000:
                self._last.clear()
            self._last[addr] = clock.now()

    def prune(self, now):
        self.store.prune_feedback(now - self.cfg.feedback_ttl_days * 86400, self.cfg.feedback_max)

    def post(self, code, body):
        item = clean_submission(body)
        now = clock.now()
        item["id"] = secrets.token_hex(6)
        item["received_t"] = now
        with self.lock:
            self.store.add_feedback(item["id"], code, now, item)
            self.prune(now)
        return {"ok": True, "id": item["id"]}

    def get(self, code):
        now = clock.now()
        self.prune(now)
        return {"items": self.store.list_feedback(code), "server_t": now}

    def delete(self, code, body):
        if not isinstance(body, dict):
            raise ApiError(400, "body must be an object")
        ids = None
        if body.get("ids") is not None:
            if not isinstance(body["ids"], list) or not all(isinstance(x, str) for x in body["ids"]):
                raise ApiError(400, "ids must be a list of strings")
            ids = body["ids"]
        self.prune(clock.now())
        return {"deleted": self.store.delete_feedback(code, ids)}
