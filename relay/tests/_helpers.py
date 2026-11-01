"""Shared helpers for the relayd tests: an in-process server on 127.0.0.1 and JSON calls."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RELAY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RELAY))
from relayd import clock  # noqa: E402
from relayd.config import Config  # noqa: E402
from relayd.server import make_server  # noqa: E402


class Base(unittest.TestCase):
    def cfg(self, **kw):
        base = dict(port=0, bind="127.0.0.1", data_dir=self.tmp, lobby=True,
                    allow_anyone_create=True, rate_per_s=10000, rate_burst=10000,
                    sync_gap_s=0, anon_creates_per_day=1000)
        base.update(kw)
        return Config(**base)

    def start(self, **kw):
        self.srv = make_server(self.cfg(**kw))
        self.app = self.srv.app
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.app.close()

    def setUp(self):
        clock.offset = 0.0
        self.tmp = tempfile.mkdtemp()
        self.start()

    def tearDown(self):
        self.stop()
        clock.offset = 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None, token=None, headers=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def mk(self, access="open", kind="party", owner="Alice", name="Room", **kw):
        body = dict(name=name, owner=owner, kind=kind, access=access, **kw)
        s, d = self.call("POST", "/v2/rooms", body)
        self.assertEqual(s, 200, d)
        return d["id"], d["token"]

    def join(self, rid, name, **kw):
        return self.call("POST", f"/v2/rooms/{rid}/join", dict(name=name, **kw))

    def joined(self, rid, name, **kw):
        s, d = self.join(rid, name, **kw)
        self.assertEqual(s, 200, d)
        return d["token"]

    def sync(self, rid, token, body=None):
        return self.call("POST", f"/v2/rooms/{rid}/sync", body or {}, token)
