"""Secrets, tokens, lockouts and account providers.

Providers turn credentials into an account. Today: `anon`, `hostkey` and `admin` (the account of rooms made on the admin page). Later providers
(`local` = username + password without email, `discord`) plug into PROVIDERS the same way:
authenticate(store, cfg, now, cred) -> Account or raise ApiError.
"""
import hashlib
import hmac
import secrets

from . import words
from .errors import ApiError

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024


def sha256hex(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _scrypt(secret, salt):
    return hashlib.scrypt(secret.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=SCRYPT_MAXMEM)


def hash_secret(secret):
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_scrypt(secret, salt).hex()}"


def verify_secret(secret, stored):
    try:
        scheme, salt_hex, h_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(secret, bytes.fromhex(salt_hex)), bytes.fromhex(h_hex))
    except (ValueError, TypeError, AttributeError):
        return False


def new_token():
    """A long random token (member tokens, and owner tokens / host keys in secure mode)."""
    return secrets.token_urlsafe(32)


def new_owner_token(secure=False):
    """Owner tokens: friendly `slime-cleric-4821`, or long random when secure. Unique per room only."""
    return new_token() if secure else words.friendly_token()


normalize_token = words.normalize     # case-insensitive friendly tokens; long tokens stay exact


class Lockouts:
    """Wrong-secret counters, memory only (no addresses on disk)."""

    def __init__(self):
        self.addr_limit, self.addr_lock_s = 5, 600          # per (address, room)
        self.room_limit, self.room_window_s, self.room_pause_s = 30, 3600, 900
        self._addr = {}         # (addr, room) -> [count, locked_until]
        self._room = {}         # room -> [fail times, paused_until]
        self._failed = {}       # room -> total failed joins (the owner sees it)
        # own (limit, lock seconds) for non-room keys; see TOKEN_LOCK in rooms.py
        self.special = {"!token": (10, 600)}

    def check(self, addr, room_id, now):
        a = self._addr.get((addr, room_id))
        if a and a[1] > now:
            raise ApiError(429, "too many wrong tries, wait a few minutes")
        r = self._room.get(room_id)
        if r and r[1] > now:
            raise ApiError(429, "joins to this room are paused for a while")

    def _prune(self, now):
        # drop only entries with no active lock, so flooding the table can't lift someone's lockout
        if len(self._addr) > 5000:
            for k in [k for k, a in self._addr.items() if a[1] <= now]:
                del self._addr[k]
        if len(self._room) > 5000:
            for k in [k for k, r in self._room.items()
                      if r[1] <= now and not any(now - t < self.room_window_s for t in r[0])]:
                del self._room[k]

    def fail(self, addr, room_id, now, track=True):
        """track=False: an unknown room id; counts against the address only."""
        self._prune(now)
        a = self._addr.setdefault((addr, room_id), [0, 0.0])
        limit, lock_s = self.special.get(room_id, (self.addr_limit, self.addr_lock_s))
        if room_id in self.special:
            # windowed count (these keys never reset on success): a quiet spell longer than the
            # lock time starts the count over, so a rare stale token never adds up to a lockout
            if len(a) < 3:
                a.append(now)
            elif now - a[2] > lock_s:
                a[0], a[2] = 0, now
        a[0] += 1
        if a[0] >= limit:
            a[0], a[1] = 0, now + lock_s
        if not track:
            return
        r = self._room.setdefault(room_id, [[], 0.0])
        r[0] = [t for t in r[0] if now - t < self.room_window_s] + [now]
        if len(r[0]) >= self.room_limit:
            r[0], r[1] = [], now + self.room_pause_s
        if track:
            self._failed[room_id] = self._failed.get(room_id, 0) + 1

    def ok(self, addr, room_id):
        self._addr.pop((addr, room_id), None)

    def failed_joins(self, room_id):
        return self._failed.get(room_id, 0)

    def forget_room(self, room_id):
        self._room.pop(room_id, None)
        self._failed.pop(room_id, None)


class Account:
    def __init__(self, id, room_limit=0, provider=""):
        self.id = id                    # None = not created yet (anon: made after all checks)
        self.room_limit = room_limit    # 0 = no per-credential limit
        self.provider = provider


class AnonProvider:
    name = "anon"

    def __init__(self):
        self._day = {}                  # address -> [window start, creates]

    def authenticate(self, store, cfg, now, cred):
        d = self._day.get(cred.get("addr", ""))
        if d and now - d[0] < 86400 and d[1] >= cfg.anon_creates_per_day:
            raise ApiError(429, "too many rooms created from this address today")
        return Account(None, 0, self.name)

    def created(self, now, cred):
        addr = cred.get("addr", "")
        if len(self._day) > 5000:
            self._day.clear()
        d = self._day.get(addr)
        if not d or now - d[0] >= 86400:
            d = self._day[addr] = [now, 0]
        d[1] += 1


class HostKeyProvider:
    name = "hostkey"

    def authenticate(self, store, cfg, now, cred):
        key = cred.get("host_key")
        key = words.normalize(key) if isinstance(key, str) else None
        row = store.host_key_by_hash(sha256hex(key)) if key else None
        if not row or row["revoked"] or (row["expires_t"] and row["expires_t"] <= now):
            raise ApiError(403, "bad host key")
        acct = row["account_id"]
        if acct is None:
            acct = store.create_account(row["label"] or "host", now)
            store.add_identity(acct, "hostkey", str(row["id"]))
            store.set_host_key_account(row["id"], acct)
        elif store.account_disabled(acct):
            raise ApiError(403, "account disabled")
        return Account(acct, row["max_rooms"], self.name)

    def created(self, now, cred):
        pass


class AdminProvider:
    """One shared account that owns the rooms made from the admin page (no secret, never logs in).

    It is an ordinary accounts row with identity (admin, admin), made on first use; no migration.
    Disabling it on the Accounts tab stops admin room creation too.
    """
    name = "admin"

    def authenticate(self, store, cfg, now, cred):
        ident = store.find_identity("admin", "admin")
        if ident:
            acct = ident["account_id"]
            if store.account_disabled(acct):
                raise ApiError(403, "account disabled")
        else:
            acct = store.create_account("admin", now)
            store.add_identity(acct, "admin", "admin")
        return Account(acct, 0, self.name)

    def created(self, now, cred):
        pass


PROVIDERS = {"anon": AnonProvider, "hostkey": HostKeyProvider,
             "admin": AdminProvider}     # later: local, discord
