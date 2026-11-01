"""Settings from the environment. A flag left as None falls back to the value saved in the DB."""
import os
from dataclasses import dataclass, field
from typing import Optional

from . import netaccess

ADMIN_PASS_MIN = 12


class ConfigError(ValueError):
    pass


def _flag(v):
    v = (v or "").strip().lower()
    if not v:
        return None
    return v in ("1", "true", "yes", "on")


def _int(env, key, default):
    try:
        return int(env.get(key) or default)
    except ValueError:
        return default


def _float(env, key, default):
    try:
        return float(env.get(key) or default)
    except ValueError:
        return default


@dataclass
class Config:
    port: int = 8787
    bind: str = "0.0.0.0"
    data_dir: str = "./data"
    lobby: Optional[bool] = None
    allow_anyone_create: Optional[bool] = None
    legacy_v1: bool = True
    max_rooms: int = 500
    max_rooms_per_account: int = 5
    anon_creates_per_day: int = 3
    max_room_members: int = 0          # 0 = no server cap
    rate_per_s: float = 4.0            # per address
    rate_burst: float = 10.0
    sync_gap_s: float = 1.0            # min seconds between syncs of one token
    feedback_key: str = ""             # empty = the /v2/feedback inbox is off (404)
    feedback_codes: set = field(default_factory=set)    # allowlist; empty = any code of 6-64 chars
    feedback_ttl_days: float = 30.0
    feedback_max: int = 300
    feedback_gap_s: float = 30.0       # min seconds between feedback posts from one address
    admin_pass: str = ""               # empty (and no hash) = the admin page and API are off (404)
    admin_pass_hash: str = ""          # "scrypt$salt$hash" (auth.hash_secret); wins over admin_pass
    admin_nets: str = netaccess.DEFAULT_ADMIN_NETS     # comma CIDRs allowed on plain http
    trusted_proxies: str = ""          # comma IPs/CIDRs of reverse proxies in front (XFF is read)

    def validate(self):
        """Raise ConfigError for settings the server must not start with."""
        if self.admin_pass_hash:
            parts = self.admin_pass_hash.split("$")
            ok = len(parts) == 3 and parts[0] == "scrypt"
            try:
                bytes.fromhex(parts[1]), bytes.fromhex(parts[2])
            except (ValueError, IndexError):
                ok = False
            if not ok:
                raise ConfigError("ADMIN_PASS_HASH must look like scrypt$<salt hex>$<hash hex>")
        elif self.admin_pass and len(self.admin_pass) < ADMIN_PASS_MIN:
            raise ConfigError(f"ADMIN_PASS must be at least {ADMIN_PASS_MIN} characters")
        try:
            netaccess.parse_nets(self.admin_nets)
            netaccess.parse_nets(self.trusted_proxies)
        except ValueError as e:
            raise ConfigError(f"bad ADMIN_NETS / TRUSTED_PROXIES: {e}")

    @property
    def admin_enabled(self):
        return bool(self.admin_pass or self.admin_pass_hash)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        legacy = _flag(env.get("LEGACY_V1"))
        return cls(
            port=_int(env, "PORT", 8787),
            bind=env.get("BIND") or "0.0.0.0",
            data_dir=env.get("DATA_DIR") or "./data",
            lobby=_flag(env.get("LOBBY")),
            allow_anyone_create=_flag(env.get("ALLOW_ANYONE_CREATE")),
            legacy_v1=True if legacy is None else legacy,
            max_rooms=_int(env, "MAX_ROOMS", 500),
            max_rooms_per_account=_int(env, "MAX_ROOMS_PER_ACCOUNT", 5),
            anon_creates_per_day=_int(env, "ANON_CREATES_PER_DAY", 3),
            max_room_members=_int(env, "MAX_ROOM_MEMBERS", 0),
            feedback_key=env.get("FEEDBACK_KEY") or "",
            feedback_codes={c.strip() for c in (env.get("FEEDBACK_CODES") or "").split(",")
                            if c.strip()},
            feedback_ttl_days=_float(env, "FEEDBACK_TTL_DAYS", 30.0),
            feedback_max=_int(env, "FEEDBACK_MAX", 300),
            admin_pass=env.get("ADMIN_PASS") or "",
            admin_pass_hash=(env.get("ADMIN_PASS_HASH") or "").strip(),
            admin_nets=env.get("ADMIN_NETS") or netaccess.DEFAULT_ADMIN_NETS,
            trusted_proxies=env.get("TRUSTED_PROXIES") or "",
        )
