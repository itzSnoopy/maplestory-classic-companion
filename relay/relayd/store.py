"""SQLite storage. All SQL lives here. One connection, guarded by a lock."""
import json
import os
import secrets
import sqlite3
import threading

from .auth import sha256hex

MIGRATIONS = [
    """
    CREATE TABLE settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE accounts(id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT,
        created_t REAL, disabled INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE identities(account_id INTEGER NOT NULL, provider TEXT NOT NULL,
        subject TEXT NOT NULL, secret_hash TEXT, PRIMARY KEY(provider, subject));
    CREATE TABLE host_keys(id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT,
        key_hash TEXT UNIQUE NOT NULL, max_rooms INTEGER NOT NULL DEFAULT 1,
        expires_t REAL, revoked INTEGER NOT NULL DEFAULT 0, account_id INTEGER);
    CREATE TABLE rooms(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
        access TEXT NOT NULL, secret_hash TEXT, owner_account INTEGER,
        max_members INTEGER NOT NULL DEFAULT 0, modules TEXT NOT NULL DEFAULT '[]',
        created_t REAL, last_active_t REAL);
    CREATE TABLE members(room_id TEXT NOT NULL, name TEXT NOT NULL, token_hash TEXT NOT NULL,
        role TEXT NOT NULL, status TEXT NOT NULL, joined_t REAL, last_seen_t REAL,
        PRIMARY KEY(room_id, name));
    CREATE INDEX members_token ON members(token_hash);
    CREATE TABLE bans(room_id TEXT NOT NULL, name TEXT NOT NULL, t REAL,
        PRIMARY KEY(room_id, name));
    CREATE TABLE module_state(room_id TEXT NOT NULL, module TEXT NOT NULL, json TEXT,
        PRIMARY KEY(room_id, module));
    """,
    """
    CREATE TABLE feedback(id TEXT PRIMARY KEY, code TEXT NOT NULL, received_t REAL NOT NULL,
        item TEXT NOT NULL);
    CREATE INDEX feedback_code ON feedback(code, received_t);
    """,
    """
    CREATE TABLE admin_audit(id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL,
        action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', via TEXT NOT NULL DEFAULT '');
    ALTER TABLE host_keys ADD COLUMN created_t REAL;
    """,
]
AUDIT_KEEP = 2000

ROOM_FIELDS = {"name", "kind", "access", "secret_hash", "max_members", "modules"}
MEMBER_FIELDS = {"role", "status"}


def _room(row):
    if row is None:
        return None
    d = dict(row)
    d["modules"] = json.loads(d["modules"] or "[]")
    return d


class Store:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(data_dir, "relay.db"), check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def close(self):
        with self.lock:
            self.db.close()

    def _migrate(self):
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS meta(version INTEGER NOT NULL)")
            row = self.db.execute("SELECT version FROM meta").fetchone()
            ver = row[0] if row else 0
            if not row:
                self.db.execute("INSERT INTO meta(version) VALUES(0)")
            for i in range(ver, len(MIGRATIONS)):
                self.db.executescript("BEGIN;" + MIGRATIONS[i] + f"UPDATE meta SET version={i + 1};COMMIT;")

    def _q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._q(sql, args)
        return rows[0] if rows else None

    def _w(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            return cur.rowcount, cur.lastrowid

    def _tx(self, stmts):
        with self.lock:
            self.db.execute("BEGIN")
            try:
                for sql, args in stmts:
                    self.db.execute(sql, args)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    # ---- settings
    def get_setting(self, k):
        r = self._one("SELECT v FROM settings WHERE k=?", (k,))
        return r["v"] if r else None

    def set_setting(self, k, v):
        self._w("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, v))

    # ---- accounts, identities, host keys
    def create_account(self, display_name, now):
        return self._w("INSERT INTO accounts(display_name, created_t) VALUES(?,?)",
                       (display_name, now))[1]

    def account_disabled(self, account_id):
        r = self._one("SELECT disabled FROM accounts WHERE id=?", (account_id,))
        return bool(r and r["disabled"])

    def add_identity(self, account_id, provider, subject, secret_hash=None):
        self._w("INSERT INTO identities(account_id, provider, subject, secret_hash) VALUES(?,?,?,?)",
                (account_id, provider, subject, secret_hash))

    def find_identity(self, provider, subject):
        return self._one("SELECT * FROM identities WHERE provider=? AND subject=?",
                         (provider, subject))

    def create_host_key(self, label, max_rooms, expires_t, with_id=False, created_t=None):
        """Make a host key; returns the plain key (or (id, key)); only its hash is stored."""
        key = "hk_" + secrets.token_urlsafe(24)
        _, kid = self._w("INSERT INTO host_keys(label, key_hash, max_rooms, expires_t, created_t) "
                         "VALUES(?,?,?,?,?)",
                         (label, sha256hex(key), max_rooms, expires_t, created_t))
        return (kid, key) if with_id else key

    def host_key_by_hash(self, key_hash):
        return self._one("SELECT * FROM host_keys WHERE key_hash=?", (key_hash,))

    def set_host_key_account(self, key_id, account_id):
        self._w("UPDATE host_keys SET account_id=? WHERE id=?", (account_id, key_id))

    def revoke_host_key(self, key_id):
        self._w("UPDATE host_keys SET revoked=1 WHERE id=?", (key_id,))

    # ---- rooms
    def insert_room(self, id, name, kind, access, secret_hash, owner_account, max_members,
                    modules, now):
        self._w("INSERT INTO rooms(id,name,kind,access,secret_hash,owner_account,max_members,"
                "modules,created_t,last_active_t) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (id, name, kind, access, secret_hash, owner_account, max_members,
                 json.dumps(modules), now, now))

    def get_room(self, id):
        return _room(self._one("SELECT * FROM rooms WHERE id=?", (id,)))

    def update_room(self, id, **fields):
        sets, args = [], []
        for k, v in fields.items():
            if k not in ROOM_FIELDS:
                raise ValueError(k)
            sets.append(f"{k}=?")
            args.append(json.dumps(v) if k == "modules" else v)
        if sets:
            self._w(f"UPDATE rooms SET {','.join(sets)} WHERE id=?", (*args, id))

    def touch_room(self, id, t):
        self._w("UPDATE rooms SET last_active_t=? WHERE id=?", (t, id))

    def delete_room(self, id):
        self._tx([(f"DELETE FROM {t} WHERE room_id=?", (id,))
                  for t in ("members", "bans", "module_state")]
                 + [("DELETE FROM rooms WHERE id=?", (id,))])

    def count_rooms(self):
        return self._one("SELECT COUNT(*) AS n FROM rooms")["n"]

    def count_rooms_owned(self, account_id):
        return self._one("SELECT COUNT(*) AS n FROM rooms WHERE owner_account=?",
                         (account_id,))["n"]

    def list_lobby(self, limit=500):
        rows = self._q(
            "SELECT r.id, r.name, r.kind, r.access, r.modules, (SELECT COUNT(*) FROM members m "
            "WHERE m.room_id=r.id AND m.status='active') AS members FROM rooms r "
            "WHERE r.access IN ('open','approval','private') "
            "ORDER BY r.last_active_t DESC LIMIT ?", (limit,))
        for r in rows:
            r["modules"] = json.loads(r["modules"] or "[]")
        return rows

    # ---- members
    def add_member(self, room_id, name, token_hash, role, status, now):
        self._w("INSERT INTO members(room_id,name,token_hash,role,status,joined_t,last_seen_t) "
                "VALUES(?,?,?,?,?,?,?)", (room_id, name, token_hash, role, status, now, now))

    def get_member(self, room_id, name):
        return self._one("SELECT * FROM members WHERE room_id=? AND name=?", (room_id, name))

    def member_by_token(self, room_id, token_hash):
        return self._one("SELECT * FROM members WHERE room_id=? AND token_hash=?",
                         (room_id, token_hash))

    def list_members(self, room_id):
        return self._q("SELECT name, role, status FROM members WHERE room_id=? "
                       "ORDER BY joined_t, name", (room_id,))

    def count_members(self, room_id):
        return self._one("SELECT COUNT(*) AS n FROM members WHERE room_id=?", (room_id,))["n"]

    def set_member(self, room_id, name, **fields):
        sets, args = [], []
        for k, v in fields.items():
            if k not in MEMBER_FIELDS:
                raise ValueError(k)
            sets.append(f"{k}=?")
            args.append(v)
        if sets:
            self._w(f"UPDATE members SET {','.join(sets)} WHERE room_id=? AND name=?",
                    (*args, room_id, name))

    def remove_member(self, room_id, name):
        self._w("DELETE FROM members WHERE room_id=? AND name=?", (room_id, name))

    def touch_member(self, room_id, name, t):
        self._w("UPDATE members SET last_seen_t=? WHERE room_id=? AND name=?", (t, room_id, name))

    # ---- bans
    def add_ban(self, room_id, name, now):
        self._w("INSERT OR REPLACE INTO bans(room_id,name,t) VALUES(?,?,?)", (room_id, name, now))

    def remove_ban(self, room_id, name):
        return self._w("DELETE FROM bans WHERE room_id=? AND name=?", (room_id, name))[0] > 0

    def is_banned(self, room_id, name):
        return self._one("SELECT 1 AS x FROM bans WHERE room_id=? AND name=?",
                         (room_id, name)) is not None

    # ---- module state
    def get_module_state(self, room_id, module):
        r = self._one("SELECT json FROM module_state WHERE room_id=? AND module=?",
                      (room_id, module))
        return json.loads(r["json"]) if r and r["json"] else None

    def set_module_state(self, room_id, module, value):
        self._w("INSERT INTO module_state(room_id,module,json) VALUES(?,?,?) "
                "ON CONFLICT(room_id,module) DO UPDATE SET json=excluded.json",
                (room_id, module, json.dumps(value)))

    # ---- feedback (cleaned items only; no addresses)
    def add_feedback(self, id, code, received_t, item):
        self._w("INSERT INTO feedback(id,code,received_t,item) VALUES(?,?,?,?)",
                (id, code, received_t, json.dumps(item)))

    def list_feedback(self, code):
        return [json.loads(r["item"]) for r in self._q(
            "SELECT item FROM feedback WHERE code=? ORDER BY received_t, rowid", (code,))]

    def prune_feedback(self, cutoff_t, max_items):
        with self.lock:
            self._w("DELETE FROM feedback WHERE received_t < ?", (cutoff_t,))
            self._w("DELETE FROM feedback WHERE rowid NOT IN (SELECT rowid FROM feedback "
                    "ORDER BY received_t DESC, rowid DESC LIMIT ?)", (max(0, max_items),))

    def delete_feedback(self, code, ids=None):
        if ids is None:
            return self._w("DELETE FROM feedback WHERE code=?", (code,))[0]
        n = 0
        for i in ids:
            n += self._w("DELETE FROM feedback WHERE code=? AND id=?", (code, i))[0]
        return n

    # ---- admin: overview lists (read only, plus a few flags)
    def counts(self, now):
        one = lambda sql, a=(): self._one(sql, a)["n"]      # noqa: E731
        return {"rooms": one("SELECT COUNT(*) AS n FROM rooms"),
                "members": one("SELECT COUNT(*) AS n FROM members WHERE status='active'"),
                "accounts": one("SELECT COUNT(*) AS n FROM accounts"),
                "host_keys_active": one("SELECT COUNT(*) AS n FROM host_keys WHERE revoked=0 AND "
                                        "(expires_t IS NULL OR expires_t>?)", (now,)),
                "feedback_items": one("SELECT COUNT(*) AS n FROM feedback")}

    def admin_rooms(self):
        rows = self._q(
            "SELECT r.id, r.name, r.kind, r.access, r.secret_hash IS NOT NULL AS has_secret, "
            "r.max_members, r.modules, r.created_t, r.last_active_t, r.owner_account, "
            "a.display_name AS owner_name, "
            "(SELECT COUNT(*) FROM members m WHERE m.room_id=r.id AND m.status='active') AS members, "
            "(SELECT COUNT(*) FROM members m WHERE m.room_id=r.id AND m.status='pending') AS pending "
            "FROM rooms r LEFT JOIN accounts a ON a.id=r.owner_account ORDER BY r.created_t DESC")
        for r in rows:
            r["modules"] = json.loads(r["modules"] or "[]")
            r["has_secret"] = bool(r["has_secret"])
        return rows

    def admin_members(self, room_id):
        return self._q("SELECT name, role, status, joined_t, last_seen_t FROM members "
                       "WHERE room_id=? ORDER BY joined_t, name", (room_id,))

    def room_owner(self, room_id):
        return self._one("SELECT * FROM members WHERE room_id=? AND role='owner'", (room_id,))

    def account(self, account_id):
        return self._one("SELECT * FROM accounts WHERE id=?", (account_id,))

    def admin_accounts(self):
        return self._q(
            "SELECT a.id, a.display_name, a.created_t, a.disabled, "
            "(SELECT group_concat(DISTINCT i.provider) FROM identities i WHERE i.account_id=a.id) "
            "AS providers, "
            "(SELECT COUNT(*) FROM rooms r WHERE r.owner_account=a.id) AS rooms_owned "
            "FROM accounts a ORDER BY a.id")

    def set_account_disabled(self, account_id, flag):
        self._w("UPDATE accounts SET disabled=? WHERE id=?", (1 if flag else 0, account_id))

    def admin_host_keys(self):
        return self._q(
            "SELECT h.id, h.label, h.max_rooms, h.expires_t, h.revoked, h.account_id, h.created_t, "
            "(SELECT COUNT(*) FROM rooms r WHERE r.owner_account=h.account_id) AS rooms_owned "
            "FROM host_keys h ORDER BY h.id DESC")

    def host_key(self, key_id):
        return self._one("SELECT id, label FROM host_keys WHERE id=?", (key_id,))

    def feedback_codes(self):
        return self._q("SELECT code, COUNT(*) AS items, MAX(received_t) AS newest_t "
                       "FROM feedback GROUP BY code ORDER BY newest_t DESC")

    # ---- admin audit (no addresses, no secrets: callers keep `detail` clean)
    def add_audit(self, t, action, detail, via):
        with self.lock:
            self._w("INSERT INTO admin_audit(t,action,detail,via) VALUES(?,?,?,?)",
                    (t, action, detail, via))
            self._w("DELETE FROM admin_audit WHERE id <= "
                    "(SELECT MAX(id) FROM admin_audit) - ?", (AUDIT_KEEP,))

    def list_audit(self, limit):
        return self._q("SELECT id, t, action, detail, via FROM admin_audit "
                       "ORDER BY id DESC LIMIT ?", (limit,))

    def backup_to(self, path):
        """Consistent copy of the whole database (works while the server runs)."""
        with self.lock:
            dst = sqlite3.connect(path)
            try:
                self.db.backup(dst)
            finally:
                dst.close()

    # ---- cleanup
    def purge_members_before(self, t):
        """Delete members idle since before t; returns [(room_id, name)]."""
        with self.lock:
            gone = self._q("SELECT room_id, name FROM members WHERE last_seen_t < ?", (t,))
            self._w("DELETE FROM members WHERE last_seen_t < ?", (t,))
        return [(g["room_id"], g["name"]) for g in gone]

    def idle_rooms_before(self, t):
        return [r["id"] for r in self._q("SELECT id FROM rooms WHERE last_active_t < ?", (t,))]
