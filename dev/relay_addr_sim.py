"""Offline test of hub/relay_addr.py (the one relay address) and the settings migration.

  python dev/relay_addr_sim.py

normalize() cases, server(), migrate() on dicts (party/hangout server -> relay, relay wins, nothing else
touched), HS.load() reading a scratch file, and the tracker-side partylink.settings() fallback on an
un-migrated file. Writes only to a temp folder. Never goes online.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
os.environ.setdefault("MAPLE_COMPANION_OFFLINE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from base import lean  # noqa: F401,E402,I001
from hub import helper_settings as HS  # noqa: E402
from hub import relay_addr as R  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({info})"))
    if not ok:
        fails.append(name)


CASES = [
    ("192.0.2.10", "http://192.0.2.10:8787"),
    ("relay.example.com", "http://relay.example.com:8787"),
    ("192.0.2.10:9000", "http://192.0.2.10:9000"),
    ("http://192.0.2.10", "http://192.0.2.10:8787"),
    ("https://relay.example.com", "https://relay.example.com"),
    ("https://relay.example.com:8443/", "https://relay.example.com:8443"),
    ("", ""), ("   ", ""), (None, ""),
    ("  relay.example.com  ", "http://relay.example.com:8787"),
    ("relay.example.com/", "http://relay.example.com:8787"),
    ("http://relay.example.com:8787//", "http://relay.example.com:8787"),
    ("[2001:db8::1]", "http://[2001:db8::1]:8787"),
    ("[2001:db8::1]:9000", "http://[2001:db8::1]:9000"),
    ("2001:db8::1", "http://[2001:db8::1]:8787"),
    ("https://[2001:db8::1]", "https://[2001:db8::1]"),
    ("HTTP://Relay.Example.com", "http://Relay.Example.com:8787"),
    ("host:abc", ""),
]
for text, want in CASES:
    got = R.normalize(text)
    check(f"normalize({text!r})", got == want, got)

check("server(settings)", R.server({"relay": {"server": " relay.example.com "}}) == "http://relay.example.com:8787")
check("server(no slice)", R.server({}) == "" and R.server(None) == "")

# migration
d = {"party": {"server": "http://203.0.113.5:8787", "code": "abc-123"}, "hangout": {"server": "other.example", "enabled": True},
     "discord": {"enabled": False}, "feedback": {"server": "fb.example", "code": "x"}}
before = copy.deepcopy(d)
R.migrate(d)
check("party server moves to relay", d["relay"] == {"server": "http://203.0.113.5:8787"}, d.get("relay"))
check("old keys removed", "server" not in d["party"] and "server" not in d["hangout"])
check("party code untouched", d["party"] == {"code": "abc-123"})
check("hangout rest untouched", d["hangout"] == {"enabled": True})
check("other slices untouched", d["discord"] == before["discord"] and d["feedback"] == before["feedback"])
d2 = {"relay": {"server": "new.example"}, "party": {"server": "old.example", "code": "c"}}
R.migrate(d2)
check("existing relay wins", d2["relay"]["server"] == "new.example" and d2["party"] == {"code": "c"}, d2)
d3 = {"hangout": {"server": "hang.example"}}
R.migrate(d3)
check("hangout server used when party has none", d3["relay"]["server"] == "hang.example")
d4 = {"party": {"server": "", "code": "c"}}
R.migrate(d4)
check("empty old server: no relay slice added", "relay" not in d4 and d4["party"] == {"code": "c"}, d4)
d5 = {"party": {"code": "c"}, "relay": {"server": "r.example"}}
snap = copy.deepcopy(d5)
R.migrate(d5)
check("already migrated: no change", d5 == snap)
check("garbage safe", R.migrate([]) == [] and R.migrate({"party": "x", "hangout": None}) == {"party": "x", "hangout": None})

# HS.load on a scratch file, and the tracker-side fallback
with tempfile.TemporaryDirectory() as tmp:
    f = Path(tmp) / "helper_settings.json"
    f.write_text(json.dumps(before), encoding="utf-8")
    HS.SETTINGS = f
    s = HS.load()
    check("load(): relay carried over", s["relay"]["server"] == "http://203.0.113.5:8787", s["relay"])
    check("load(): no server keys left in party / hangout", "server" not in s["party"] and "server" not in s["hangout"])
    check("load(): party code kept", s["party"]["code"] == "abc-123")
    check("load(): feedback server kept", s["feedback"]["server"] == "fb.example")
    check("load() leaves the file alone", json.loads(f.read_text(encoding="utf-8")) == before)
    from components.party import partylink
    partylink.DATA = Path(tmp)
    ps = partylink.settings()
    check("tracker fallback on an un-migrated file", ps == {"server": "http://203.0.113.5:8787", "code": "abc-123", "room": None}, ps)
    HS.save(s)
    ps2 = partylink.settings()
    check("tracker reads the migrated file", ps2 == ps, ps2)
    f.unlink()
    check("no file: empty", partylink.settings() == {"server": "", "code": "", "room": None})
    check("endpoint", partylink.endpoint("relay.example.com", "c1") == "http://relay.example.com:8787/v1/party/c1")

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
