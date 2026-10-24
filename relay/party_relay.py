"""party_relay.py - the tiny server two (or up to 6) trackers use to see each other's numbers.

Each tracker posts its own state (name, level, EXP %, EXP/hr, HP, MP, map) every few seconds
under a shared party code, and gets back everyone else's under that code. Nothing is written to
disk: members drop out 90 s after their last update, and a restart forgets everything.

Python 3.8+, standard library only.

  PORT=8787 python3 party_relay.py

Environment:
  PORT          listen port (default 8787)
  BIND          listen address (default 0.0.0.0)
  PARTY_CODES   optional comma list of allowed party codes; empty = any code of 6-64 chars
                (set it on a public server so strangers can't use yours)

API (JSON):
  POST /v1/party/<code>   body {"name": ..., <fields>}  -> {"members": [...], "server_t": ...}
  GET  /v1/party/<code>                                  -> the same, without posting
  GET  /health                                           -> {"ok": true}
Each member comes back as its fields + "name" + "age_s" (seconds since its last post).
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TTL_S = 90
MAX_BODY = 4096
MAX_MEMBERS = 8
MAX_CODES = 500
RATE_PER_S = 4          # requests per second per address (burst 10)
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
NAME_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
# what a member may send: field -> check
FIELDS = {
    "level": lambda v: isinstance(v, int) and 0 < v < 300,
    "job": lambda v: isinstance(v, str) and len(v) <= 32,
    "map": lambda v: isinstance(v, str) and len(v) <= 64,
    "state": lambda v: v in ("playing", "idle", "waiting_for_source", "stopped"),
    "exp_cur": lambda v: isinstance(v, int) and v >= 0,
    "exp_need": lambda v: isinstance(v, int) and v >= 0,
    "exp_pct": lambda v: isinstance(v, (int, float)) and 0 <= v <= 100,
    "exp_hr": lambda v: isinstance(v, (int, float)) and v >= 0,
    "eta": lambda v: isinstance(v, str) and len(v) <= 16,
    "hp": lambda v: _pair(v),
    "mp": lambda v: _pair(v),
    "channel": lambda v: isinstance(v, (int, str)) and len(str(v)) <= 4,
    "v": lambda v: isinstance(v, str) and len(v) <= 16,
}


def _pair(v):
    return (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and 0 <= x < 10 ** 6
                                                         for x in v))


ALLOWED = {c.strip() for c in os.environ.get("PARTY_CODES", "").split(",") if c.strip()}
parties = {}            # code -> {name: (t, fields)}
buckets = {}            # address -> (tokens, t)
lock = threading.Lock()


def _rate_ok(addr):
    now = time.time()
    tok, t = buckets.get(addr, (10.0, now))
    tok = min(10.0, tok + (now - t) * RATE_PER_S)
    if tok < 1:
        buckets[addr] = (tok, now)
        return False
    buckets[addr] = (tok - 1, now)
    if len(buckets) > 5000:
        buckets.clear()
    return True


def _members(code, now):
    p = parties.get(code) or {}
    for n in [n for n, (t, _) in p.items() if now - t > TTL_S]:
        del p[n]
    if not p:
        parties.pop(code, None)
    return [{**f, "name": n, "age_s": round(now - t, 1)} for n, (t, f) in p.items()]


class Handler(BaseHTTPRequestHandler):
    server_version = "party-relay/1"

    def log_message(self, fmt, *args):      # quiet: no per-request lines (no names in logs)
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _code(self):
        m = re.match(r"^/v1/party/([^/?]+)$", self.path)
        if not m:
            return None
        code = m.group(1)
        if not CODE_RE.match(code) or (ALLOWED and code not in ALLOWED):
            return False
        return code

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if not _rate_ok(self.client_address[0]):
            return self._send(429, {"error": "slow down"})
        code = self._code()
        if code is None:
            return self._send(404, {"error": "not found"})
        if code is False:
            return self._send(403, {"error": "unknown party code"})
        with lock:
            return self._send(200, {"members": _members(code, time.time()),
                                    "server_t": time.time()})

    def do_POST(self):
        if not _rate_ok(self.client_address[0]):
            return self._send(429, {"error": "slow down"})
        code = self._code()
        if code is None:
            return self._send(404, {"error": "not found"})
        if code is False:
            return self._send(403, {"error": "unknown party code"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(413, {"error": "body too large or empty"})
        try:
            d = json.loads(self.rfile.read(n))
        except ValueError:
            return self._send(400, {"error": "bad json"})
        name = d.get("name") if isinstance(d, dict) else None
        if not isinstance(name, str) or not NAME_RE.match(name):
            return self._send(400, {"error": "bad name"})
        fields = {k: v for k, v in d.items() if k in FIELDS and FIELDS[k](v)}
        now = time.time()
        with lock:
            if code not in parties and len(parties) >= MAX_CODES:
                return self._send(503, {"error": "server full"})
            p = parties.setdefault(code, {})
            if name not in p and len(p) >= MAX_MEMBERS:
                oldest = min(p, key=lambda k: p[k][0])
                del p[oldest]
            p[name] = (now, fields)
            return self._send(200, {"members": _members(code, now), "server_t": now})


def main():
    port = int(os.environ.get("PORT", "8787"))
    bind = os.environ.get("BIND", "0.0.0.0")
    srv = ThreadingHTTPServer((bind, port), Handler)
    print(f"party relay on {bind}:{port}"
          + (f", {len(ALLOWED)} allowed code(s)" if ALLOWED else ", any party code"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
