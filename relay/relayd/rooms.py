"""Rooms, members, lobby, sync dispatch and cleanup. No HTTP in here."""
import re
import secrets
import threading

from . import auth, clock, netaccess
from .admin import Admin
from .errors import ApiError
from .modules import build_registry
from .feedback import Feedback
from .store import Store

NAME_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")        # same as v1
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
ROOM_ID_RE = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"      # no 0 o 1 l i
KINDS = ("party", "hangout")
ACCESS = ("open", "approval", "private", "unlisted")
DEFAULT_MODULES = ["presence", "challenge"]
SEEN_WRITE_S = 600                  # SQLite last_seen / last_active written at most this often
MEMBER_IDLE_S = 30 * 86400
ROOM_IDLE_S = 90 * 86400
MEMBER_ACTIONS = ("approve", "deny", "kick", "ban", "unban", "promote", "demote")
MOD_ACTIONS = ("approve", "deny", "kick")
HOSTKEY_LOCK = "!hostkey"           # Lockouts key for wrong host keys (not a valid room id)


def _obj(body):
    if not isinstance(body, dict):
        raise ApiError(400, "body must be a JSON object")
    return body


def _room_name(v):
    if not isinstance(v, str) or not 1 <= len(v) <= 40 or CTRL_RE.search(v) or not v.strip():
        raise ApiError(400, "room name must be 1-40 characters")
    return v


def _check_name(v):
    if not isinstance(v, str) or not NAME_RE.match(v):
        raise ApiError(400, "bad character name")
    return v


def _secret_ok(s):
    return isinstance(s, str) and (re.fullmatch(r"\d{4,12}", s) is not None or 8 <= len(s) <= 128)


class Relay:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg.data_dir)
        self.modules = build_registry()
        for m in self.modules.values():
            m.relay = self
        self.feedback = Feedback(self.store, cfg)
        self.started = clock.now()
        self.access = netaccess.Access(cfg)
        self.admin = Admin(self)
        self.lockouts = auth.Lockouts()
        self.providers = {n: c() for n, c in auth.PROVIDERS.items()}
        self.lock = threading.RLock()       # serialises changes to rooms and members
        self.buckets = {}                   # address -> (tokens, t)
        self._seen = {}                     # (room, name) -> fresh last_seen (memory)
        self._seen_db = {}                  # (room, name) -> when it was last written to SQLite
        self._room_seen = {}
        self._room_seen_db = {}
        self._sync_last = {}                # token hash -> time of last sync
        self._stop = threading.Event()
        self._thread = None

    # ---- housekeeping
    def close(self):
        self._stop.set()
        self.store.close()

    def start_cleanup(self):
        def loop():
            while not self._stop.wait(3600):
                try:
                    self.cleanup()
                except Exception:
                    pass
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def rate_ok(self, addr):
        now = clock.now()
        burst = self.cfg.rate_burst
        tok, t = self.buckets.get(addr, (burst, now))
        tok = min(burst, tok + (now - t) * self.cfg.rate_per_s)
        if tok < 1:
            self.buckets[addr] = (tok, now)
            return False
        self.buckets[addr] = (tok - 1, now)
        if len(self.buckets) > 5000:
            self.buckets.clear()
        return True

    def flag(self, name):
        v = getattr(self.cfg, name)
        if v is not None:
            return v
        return self.store.get_setting(name) == "1"

    def cleanup(self, now=None):
        now = clock.now() if now is None else now
        with self.lock:
            members = self.store.purge_members_before(now - MEMBER_IDLE_S)
            for room_id, name in members:
                self._member_gone(room_id, name)
            rooms = self.store.idle_rooms_before(now - ROOM_IDLE_S)
            for rid in rooms:
                self._delete_room(rid)
            for m in self.modules.values():
                for rid in m.active_rooms():
                    room = self.store.get_room(rid)
                    if room:
                        m.tick(room, now)
            self.feedback.prune(now)
        return {"members": len(members), "rooms": len(rooms)}

    # ---- helpers
    def _cap(self, n):
        cap = self.cfg.max_room_members
        if cap > 0 and (n == 0 or n > cap):
            return cap
        return n

    def _member_gone(self, room_id, name):
        self._seen.pop((room_id, name), None)
        self._seen_db.pop((room_id, name), None)
        for m in self.modules.values():
            m.on_member_removed(room_id, name)

    def _delete_room(self, room_id):
        self.store.delete_room(room_id)
        self.lockouts.forget_room(room_id)
        for d in (self._seen, self._seen_db):
            for k in [k for k in d if k[0] == room_id]:
                del d[k]
        self._room_seen.pop(room_id, None)
        self._room_seen_db.pop(room_id, None)
        for m in self.modules.values():
            m.on_room_deleted(room_id)

    def _touch(self, room_id, name, now):
        k = (room_id, name)
        self._seen[k] = now
        if now - self._seen_db.get(k, -1e18) >= SEEN_WRITE_S:
            self.store.touch_member(room_id, name, now)
            self._seen_db[k] = now
        self._room_seen[room_id] = now
        if now - self._room_seen_db.get(room_id, -1e18) >= SEEN_WRITE_S:
            self.store.touch_room(room_id, now)
            self._room_seen_db[room_id] = now

    @staticmethod
    def public(room):
        return {"id": room["id"], "name": room["name"], "kind": room["kind"],
                "access": room["access"], "max_members": room["max_members"],
                "modules": room["modules"]}

    def member_auth(self, room_id, header, allow_pending=False):
        """Bearer token -> (room, member). 401 whatever is wrong, so nothing leaks."""
        token = ""
        if header and header.startswith("Bearer "):
            token = header[7:].strip()
        m = self.store.member_by_token(room_id, auth.sha256hex(token)) if token else None
        room = self.store.get_room(room_id) if m else None
        if not m or not room:
            raise ApiError(401, "missing or bad token")
        if m["status"] != "active" and not allow_pending:
            raise ApiError(403, "waiting for approval", status="pending")
        return room, m

    # ---- lobby
    def lobby(self):
        if not self.flag("lobby"):
            return {"enabled": False, "rooms": []}
        now = clock.now()
        out = []
        for r in self.store.list_lobby():
            pres = self.modules.get("presence") if "presence" in r["modules"] else None
            out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "access": r["access"],
                        "members": r["members"], "online": pres.online(r["id"], now) if pres else 0})
        return {"enabled": True, "rooms": out}

    # ---- create
    def _room_spec(self, body):
        """Validate the fields of a new room. Shared by the public API and the admin page."""
        body = _obj(body)
        name = _room_name(body.get("name"))
        owner = _check_name(body.get("owner"))
        kind, access = body.get("kind"), body.get("access")
        if kind not in KINDS:
            raise ApiError(400, "kind must be party or hangout")
        if access not in ACCESS:
            raise ApiError(400, "access must be open, approval, private or unlisted")
        secret = body.get("secret")
        if secret is not None and not _secret_ok(secret):
            raise ApiError(400, "secret must be a 4-12 digit PIN or 8-128 characters")
        if access in ("private", "unlisted") and secret is None:
            raise ApiError(400, "this access type needs a secret")
        mm = body.get("max_members")
        if mm is None:
            mm = 6 if kind == "party" else 0
        if isinstance(mm, bool) or not isinstance(mm, int) or not 0 <= mm <= 100000:
            raise ApiError(400, "bad max_members")
        return {"name": name, "owner": owner, "kind": kind, "access": access,
                "secret_hash": auth.hash_secret(secret) if secret is not None else None,
                "max_members": self._cap(mm)}

    def _check_capacity(self):
        if self.store.count_rooms() >= self.cfg.max_rooms:
            raise ApiError(503, "the server has no room for more rooms")

    def _store_room(self, spec, account_id, now):
        """Insert the room and its owner (call with self.lock held). -> the create reply."""
        for _ in range(20):
            rid = "".join(secrets.choice(ID_ALPHABET) for _ in range(8))
            rid = rid[:4] + "-" + rid[4:]
            if not self.store.get_room(rid):
                break
        token = auth.new_token()
        self.store.insert_room(rid, spec["name"], spec["kind"], spec["access"], spec["secret_hash"],
                               account_id, spec["max_members"], list(DEFAULT_MODULES), now)
        self.store.add_member(rid, spec["owner"], auth.sha256hex(token), "owner", "active", now)
        return {"id": rid, "token": token, "room": self.public(self.store.get_room(rid))}

    def create(self, addr, body, header_key):
        body = _obj(body)
        key = body.get("host_key") or header_key
        if not key and not self.flag("allow_anyone_create"):
            raise ApiError(403, "creating rooms is not open to everyone; a host key is needed")
        spec = self._room_spec(body)
        now = clock.now()
        cred = {"addr": addr, "host_key": key}
        provider = self.providers["hostkey" if key else "anon"]
        if key:                                  # wrong host keys are guessed per address, like room secrets
            self.lockouts.check(addr, HOSTKEY_LOCK, now)
        with self.lock:
            try:
                acct = provider.authenticate(self.store, self.cfg, now, cred)
            except ApiError as e:
                if key and e.msg == "bad host key":
                    self.lockouts.fail(addr, HOSTKEY_LOCK, now, track=False)
                raise
            if key:
                self.lockouts.ok(addr, HOSTKEY_LOCK)
            self._check_capacity()
            if acct.id is not None:
                owned = self.store.count_rooms_owned(acct.id)
                if owned >= self.cfg.max_rooms_per_account:
                    raise ApiError(409, "room limit for this account reached")
                if acct.room_limit and owned >= acct.room_limit:
                    raise ApiError(409, "room limit for this host key reached")
            if acct.id is None:
                acct.id = self.store.create_account("anon", now)
            provider.created(now, cred)
            return self._store_room(spec, acct.id, now)

    def create_admin(self, body):
        """Admin page: no host key / anyone-can-create check, no per-account limits; MAX_ROOMS still applies."""
        spec = self._room_spec(body)
        now = clock.now()
        with self.lock:
            acct = self.providers["admin"].authenticate(self.store, self.cfg, now, {})
            self._check_capacity()
            return self._store_room(spec, acct.id, now)

    # ---- join / me / leave
    def join(self, addr, room_id, body):
        body = _obj(body)
        name = _check_name(body.get("name"))
        secret = body.get("secret")
        if secret is not None and not isinstance(secret, str):
            raise ApiError(400, "bad secret")
        now = clock.now()
        self.lockouts.check(addr, room_id, now)
        room = self.store.get_room(room_id)
        if not room:
            self.lockouts.fail(addr, room_id, now, track=False)     # same trail as a wrong secret
            raise ApiError(404, "no such room")
        if room["secret_hash"]:
            if not secret or not auth.verify_secret(secret, room["secret_hash"]):
                self.lockouts.fail(addr, room_id, now)
                if room["access"] == "unlisted":
                    raise ApiError(404, "no such room")
                raise ApiError(403, "wrong secret")
        token = auth.new_token()
        with self.lock:
            if self.store.is_banned(room_id, name):
                raise ApiError(403, "you are banned from this room")
            if self.store.get_member(room_id, name):
                raise ApiError(409, "name taken")
            if room["max_members"] and self.store.count_members(room_id) >= room["max_members"]:
                raise ApiError(409, "room is full")
            status = "pending" if room["access"] == "approval" else "active"
            self.store.add_member(room_id, name, auth.sha256hex(token), "member", status, now)
        self.lockouts.ok(addr, room_id)
        return {"token": token, "status": status, "role": "member"}

    def me(self, room_id, header):
        room, m = self.member_auth(room_id, header, allow_pending=True)
        return {"name": m["name"], "role": m["role"], "status": m["status"],
                "room": self.public(room)}

    def leave(self, room_id, header):
        room, m = self.member_auth(room_id, header, allow_pending=True)
        with self.lock:
            if m["role"] == "owner":
                if self.store.count_members(room_id) > 1:
                    raise ApiError(409, "owner cannot leave while others are in the room")
                self._delete_room(room_id)
            else:
                self.store.remove_member(room_id, m["name"])
                self._member_gone(room_id, m["name"])
        return {"ok": True}

    # ---- room view / edit / delete
    def view(self, room_id, header):
        room, m = self.member_auth(room_id, header)
        members = self.store.list_members(room_id)
        out = self.public(room)
        out["members"] = [x for x in members if x["status"] == "active"]
        if m["role"] in ("owner", "mod"):
            out["requests"] = [x["name"] for x in members if x["status"] == "pending"]
            out["failed_joins"] = self.lockouts.failed_joins(room_id)
        return out

    def patch(self, room_id, header, body):
        room, m = self.member_auth(room_id, header)
        if m["role"] != "owner":
            raise ApiError(403, "owner only")
        return self._patch(room, body)

    def _patch(self, room, body):
        """Validate and apply a room edit. Shared by the owner and the admin page."""
        room_id = room["id"]
        body = _obj(body)
        upd = {}
        if "name" in body:
            upd["name"] = _room_name(body["name"])
        if "kind" in body:
            if body["kind"] not in KINDS:
                raise ApiError(400, "bad kind")
            upd["kind"] = body["kind"]
        access = body.get("access", room["access"])
        if access not in ACCESS:
            raise ApiError(400, "bad access")
        upd["access"] = access
        has_secret = room["secret_hash"] is not None
        if "secret" in body:
            s = body["secret"]
            if s is None:
                has_secret = False
                upd["secret_hash"] = None
            elif _secret_ok(s):
                has_secret = True
                upd["secret_hash"] = auth.hash_secret(s)
            else:
                raise ApiError(400, "secret must be a 4-12 digit PIN or 8-128 characters")
        if access in ("private", "unlisted") and not has_secret:
            raise ApiError(400, "this access type needs a secret")
        if "max_members" in body:
            mm = body["max_members"]
            if isinstance(mm, bool) or not isinstance(mm, int) or not 0 <= mm <= 100000:
                raise ApiError(400, "bad max_members")
            upd["max_members"] = self._cap(mm)
        if "modules" in body:
            mods = body["modules"]
            if (not isinstance(mods, list) or len(set(mods)) != len(mods)
                    or any(x not in self.modules for x in mods)):
                raise ApiError(400, "modules must be a list of known module names")
            upd["modules"] = mods
        with self.lock:
            self.store.update_room(room_id, **upd)
            room = self.store.get_room(room_id)
        return self.public(room)

    def delete(self, room_id, header):
        room, m = self.member_auth(room_id, header)
        if m["role"] != "owner":
            raise ApiError(403, "owner only")
        with self.lock:
            self._delete_room(room_id)
        return {"ok": True}

    # ---- members
    def member_action(self, room_id, header, name, action):
        room, actor = self.member_auth(room_id, header)
        return self._member_action(room_id, actor["role"], name, action)

    def _member_action(self, room_id, actor_role, name, action):
        """actor_role: owner | mod | member | admin (the admin page; also may make_owner)."""
        if action not in MEMBER_ACTIONS and not (actor_role == "admin" and action == "make_owner"):
            raise ApiError(404, "no such action")
        if actor_role == "member" or (actor_role == "mod" and action not in MOD_ACTIONS):
            raise ApiError(403, "not allowed")
        _check_name(name)
        now = clock.now()
        with self.lock:
            if action == "unban":
                if not self.store.remove_ban(room_id, name):
                    raise ApiError(404, "that name is not banned")
                return {"ok": True}
            t = self.store.get_member(room_id, name)
            if t is None:
                if action == "ban":
                    self.store.add_ban(room_id, name, now)
                    return {"ok": True}
                raise ApiError(404, "no such member")
            if action == "make_owner" and t["role"] == "owner":
                raise ApiError(409, "already the owner")
            if t["role"] == "owner" or (actor_role == "mod" and t["role"] != "member"):
                raise ApiError(403, "not allowed")
            if action == "make_owner":
                if t["status"] != "active":
                    raise ApiError(409, "only active members can become owner")
                old = self.store.room_owner(room_id)
                if old:
                    self.store.set_member(room_id, old["name"], role="mod")
                self.store.set_member(room_id, name, role="owner")
            elif action == "approve":
                if t["status"] != "pending":
                    raise ApiError(409, "not waiting for approval")
                self.store.set_member(room_id, name, status="active")
            elif action == "deny":
                if t["status"] != "pending":
                    raise ApiError(409, "not waiting for approval")
                self.store.remove_member(room_id, name)
            elif action == "promote":
                if t["role"] != "member" or t["status"] != "active":
                    raise ApiError(409, "only active members can be promoted")
                self.store.set_member(room_id, name, role="mod")
            elif action == "demote":
                if t["role"] != "mod":
                    raise ApiError(409, "not a mod")
                self.store.set_member(room_id, name, role="member")
            else:                                       # kick / ban
                self.store.remove_member(room_id, name)
                if action == "ban":
                    self.store.add_ban(room_id, name, now)
                self._member_gone(room_id, name)
        return {"ok": True}

    # ---- sync and module actions
    def sync(self, room_id, header, body):
        room, m = self.member_auth(room_id, header)
        body = _obj(body)
        now = clock.now()
        th = auth.sha256hex(header[7:].strip())
        if now - self._sync_last.get(th, -1e18) < self.cfg.sync_gap_s:
            raise ApiError(429, "one sync per second")
        if len(self._sync_last) > 10000:
            self._sync_last.clear()
        self._sync_last[th] = now
        self._touch(room_id, m["name"], now)
        out = {"server_t": now}
        for mod_name in room["modules"]:
            mod = self.modules.get(mod_name)
            if mod:
                part = mod.on_sync(room, m, body.get(mod_name), now)
                if part is not None:
                    out[mod_name] = part
        return out

    def module_action(self, room_id, header, module, action, body):
        room, m = self.member_auth(room_id, header)
        mod = self.modules.get(module) if module in room["modules"] else None
        fn = mod.actions().get(action) if mod else None
        if not fn:
            raise ApiError(404, "not found")
        return fn(room, m, _obj(body), clock.now())
