"""Tests for party_relay.py (stdlib unittest, no app imports, only talks to 127.0.0.1).

    python relay/test_relay.py
"""
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import party_relay as R  # noqa: E402

KEY = "test-owner-key"
CODE = "feedback-test-1"


def export(branch="Magician", reviewer="Tester", reviews=None, **extra):
    d = {"format": "classic-skill-review/1", "branch": branch, "reviewer": reviewer,
         "exported": "2026-01-01 10:00", "data_fetched": "2026-01-01", "count": 1,
         "reviews": reviews if reviews is not None else
         {"2001000": {"name": "Magic Guard", "status": "ok", "kind": "buff", "mode": "until death",
                      "note": "fine"}}}
    d.update(extra)
    return d


class RelayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), R.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self._saved = {k: getattr(R, k) for k in
                       ("FEEDBACK_KEY", "FEEDBACK_CODES", "FEEDBACK_TTL_DAYS", "FEEDBACK_MAX",
                        "FEEDBACK_GAP_S", "ALLOWED")}
        R.FEEDBACK_KEY = KEY
        R.FEEDBACK_CODES = set()
        R.FEEDBACK_TTL_DAYS = 30
        R.FEEDBACK_MAX = 300
        R.FEEDBACK_GAP_S = 0
        R.ALLOWED = set()
        for d in (R.parties, R.buckets, R.fb_last):
            d.clear()
        R.feedback.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(R, k, v)

    def call(self, method, path, body=None, key=None, raw=None):
        R.buckets.clear()       # the per-address request bucket is not under test here
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if key is not None:
            req.add_header("X-Feedback-Key", key)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def post(self, body, **kw):
        return self.call("POST", f"/v1/feedback/{CODE}", body, **kw)

    def inbox(self, code=CODE):
        return self.call("GET", f"/v1/feedback/{code}", key=KEY)

    # ---- party still works
    def test_party(self):
        s, r = self.call("POST", "/v1/party/party-code-1", {"name": "Abc", "level": 12, "junk": 1})
        self.assertEqual(s, 200)
        self.assertEqual(r["members"][0]["name"], "Abc")
        self.assertNotIn("junk", r["members"][0])
        s, r = self.call("GET", "/v1/party/party-code-1")
        self.assertEqual((s, len(r["members"])), (200, 1))
        s, _ = self.call("POST", "/v1/party/party-code-1", {"name": "bad name!"})
        self.assertEqual(s, 400)

    def test_party_body_cap_stays_4k(self):
        s, _ = self.call("POST", "/v1/party/party-code-1", raw=b"{" + b" " * 5000 + b"}")
        self.assertEqual(s, 413)

    # ---- feedback
    def test_disabled_without_key(self):
        R.FEEDBACK_KEY = ""
        self.assertEqual(self.post(export())[0], 404)
        self.assertEqual(self.call("GET", f"/v1/feedback/{CODE}", key="x")[0], 404)
        self.assertEqual(self.call("DELETE", f"/v1/feedback/{CODE}", key="x")[0], 404)
        s, r = self.call("GET", "/health")
        self.assertFalse(r["feedback"])

    def test_post_and_fetch(self):
        s, r = self.post(export())
        self.assertEqual(s, 200)
        self.assertTrue(r["ok"] and len(r["id"]) == 12)
        s, r = self.inbox()
        self.assertEqual(s, 200)
        self.assertEqual(len(r["items"]), 1)
        it = r["items"][0]
        self.assertEqual((it["branch"], it["reviewer"], it["count"]), ("Magician", "Tester", 1))
        self.assertIn("received_t", it)
        self.assertIn("server_t", r)
        self.assertEqual(it["reviews"]["2001000"]["mode"], "until death")

    def test_branch_all_mixed_classes(self):
        reviews = {"1001004": {"name": "Power Strike", "status": "ok"},
                   "2001000": {"name": "Magic Guard", "status": "wrong", "kind": "buff"},
                   "3001004": {"name": "Arrow Blow", "status": "unsure", "note": "?"}}
        s, r = self.post(export(branch="all", reviews=reviews, count=500))
        self.assertEqual(s, 200)
        it = self.inbox()[1]["items"][0]
        self.assertEqual(it["branch"], "all")
        self.assertEqual(sorted(it["reviews"]), ["1001004", "2001000", "3001004"])
        self.assertEqual(it["count"], 3)         # len(reviews), not the claimed count

    def test_wrong_key(self):
        self.post(export())
        self.assertEqual(self.call("GET", f"/v1/feedback/{CODE}", key="nope")[0], 403)
        self.assertEqual(self.call("GET", f"/v1/feedback/{CODE}")[0], 403)
        self.assertEqual(self.call("DELETE", f"/v1/feedback/{CODE}", key="nope")[0], 403)
        self.assertEqual(len(R.feedback), 1)

    def test_code_allowlist(self):
        R.FEEDBACK_CODES = {"only-this-code"}
        self.assertEqual(self.post(export())[0], 403)
        self.assertEqual(self.call("GET", f"/v1/feedback/{CODE}", key=KEY)[0], 403)
        self.assertEqual(self.call("POST", "/v1/feedback/only-this-code", export())[0], 200)

    def test_too_big_and_empty(self):
        big = export(reviews={"1": {"name": "x", "status": "ok", "note": "y" * 70000}})
        self.assertEqual(self.post(big)[0], 413)
        self.assertEqual(self.call("POST", f"/v1/feedback/{CODE}", raw=b"")[0], 413)

    def test_bad_input(self):
        self.assertEqual(self.post(export(format="other/1"))[0], 400)
        self.assertEqual(self.post(export(branch="bad!"))[0], 400)
        self.assertEqual(self.post(export(branch=""))[0], 400)
        self.assertEqual(self.post(export(reviewer="x" * 25))[0], 400)
        self.assertEqual(self.post(export(reviewer="a\x07b"))[0], 400)
        self.assertEqual(self.post(export(reviews={}))[0], 400)
        self.assertEqual(self.post(export(reviews={"abc": {"status": "ok"}}))[0], 400)
        self.assertEqual(self.post(export(reviews={"1": {"status": "great"}}))[0], 400)
        self.assertEqual(self.post(export(reviews={"1": {"mode": "forever"}}))[0], 400)
        self.assertEqual(self.post(export(reviews={"1": {"status": "", "note": ""}}))[0], 400)
        self.assertEqual(self.call("POST", f"/v1/feedback/{CODE}", raw=b"{nope")[0], 400)
        s, r = self.call("POST", f"/v1/feedback/{CODE}", [1, 2])
        self.assertEqual(s, 400)
        self.assertIn("error", r)
        self.assertEqual(R.feedback, [])

    def test_anonymous_ok(self):
        self.assertEqual(self.post(export(reviewer="  "))[0], 200)
        self.assertEqual(self.inbox()[1]["items"][0]["reviewer"], "")

    def test_note_cut_and_junk_dropped(self):
        reviews = {"1": {"name": "A", "status": "ok", "note": "n" * 800, "evil": "x"},
                   "2": {"name": "B", "status": "", "kind": "", "mode": "", "note": ""},
                   "3": {"name": "C", "status": "wrong", "kind": "attack"}}
        s, _ = self.post(export(reviews=reviews, extra="field", ip="1.2.3.4"))
        self.assertEqual(s, 200)
        it = self.inbox()[1]["items"][0]
        self.assertEqual(len(it["reviews"]["1"]["note"]), 500)
        self.assertNotIn("evil", it["reviews"]["1"])
        self.assertNotIn("2", it["reviews"])
        self.assertNotIn("extra", it)
        self.assertNotIn("ip", it)
        self.assertEqual(it["count"], 2)

    def test_one_post_per_30s(self):
        R.FEEDBACK_GAP_S = 30
        self.assertEqual(self.post(export())[0], 200)
        s, r = self.post(export())
        self.assertEqual(s, 429)
        self.assertEqual(r["error"], "one send per 30 s")
        R.fb_last.clear()
        self.assertEqual(self.post(export())[0], 200)

    def test_ttl(self):
        self.post(export())
        R.feedback[0][1]["received_t"] -= 31 * 86400
        self.assertEqual(self.inbox()[1]["items"], [])
        self.assertEqual(R.feedback, [])

    def test_max_drops_oldest(self):
        R.FEEDBACK_MAX = 3
        ids = []
        for i in range(5):
            ids.append(self.post(export(reviewer=f"R{i}"))[1]["id"])
        items = self.inbox()[1]["items"]
        self.assertEqual([i["id"] for i in items], ids[2:])      # oldest first
        self.assertEqual([i["reviewer"] for i in items], ["R2", "R3", "R4"])

    def test_delete(self):
        ids = [self.post(export(reviewer=f"R{i}"))[1]["id"] for i in range(3)]
        self.post(export(), )
        s, r = self.call("DELETE", f"/v1/feedback/{CODE}", {"ids": [ids[0], "nosuchid"]}, key=KEY)
        self.assertEqual((s, r), (200, {"deleted": 1}))
        self.assertEqual(len(self.inbox()[1]["items"]), 3)
        s, r = self.call("DELETE", f"/v1/feedback/{CODE}", key=KEY)
        self.assertEqual((s, r), (200, {"deleted": 3}))
        self.assertEqual(self.inbox()[1]["items"], [])

    def test_delete_other_code_untouched(self):
        self.call("POST", "/v1/feedback/other-code-1", export())
        self.post(export())
        self.call("DELETE", f"/v1/feedback/{CODE}", key=KEY)
        self.assertEqual(len(self.inbox("other-code-1")[1]["items"]), 1)

    def test_health_source(self):
        s, r = self.call("GET", "/health")
        self.assertEqual(s, 200)
        self.assertTrue(r["ok"] and r["feedback"])
        self.assertTrue(r["source"].startswith("https://github.com/"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
