"""Tests for the relayd feedback inbox (/v2/feedback, ported from the v1 tests, stored in SQLite).

    python relay/tests/test_v2_feedback.py
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, clock  # noqa: E402

KEY = "test-owner-key"
CODE = "feedback-test-1"
DAY = 86400


def export(branch="Magician", reviewer="Tester", reviews=None, **extra):
    d = {"format": "classic-skill-review/1", "branch": branch, "reviewer": reviewer,
         "exported": "2026-01-01 10:00", "data_fetched": "2026-01-01", "count": 1,
         "reviews": reviews if reviews is not None else
         {"2001000": {"name": "Magic Guard", "status": "ok", "kind": "buff", "mode": "until death",
                      "note": "fine"}}}
    d.update(extra)
    return d


class FbBase(Base):
    FB = dict(feedback_key=KEY, feedback_gap_s=0)

    def setUp(self):
        super().setUp()
        self.stop()
        self.start(**self.FB)

    def post(self, body, code=CODE, **kw):
        return self.call("POST", f"/v2/feedback/{code}", body, **kw)

    def inbox(self, code=CODE, key=KEY):
        return self.call("GET", f"/v2/feedback/{code}", headers={"X-Feedback-Key": key})

    def delete(self, body=None, code=CODE, key=KEY):
        return self.call("DELETE", f"/v2/feedback/{code}", body, headers={"X-Feedback-Key": key})

    def rows(self):
        return self.app.store._q("SELECT * FROM feedback")


class TestFeedback(FbBase):
    def test_disabled_without_key(self):
        self.stop()
        self.start(feedback_key="")
        self.assertEqual(self.post(export())[0], 404)
        self.assertEqual(self.inbox(key="x")[0], 404)
        self.assertEqual(self.delete(key="x")[0], 404)
        self.assertEqual(self.call("GET", "/v2/feedback")[0], 404)
        s, r = self.call("GET", "/health")
        self.assertFalse(r["feedback"])

    def test_health_true_when_enabled(self):
        s, r = self.call("GET", "/health")
        self.assertTrue(r["ok"] and r["feedback"])

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
        self.assertEqual(self.post(export(branch="all", reviews=reviews, count=500))[0], 200)
        it = self.inbox()[1]["items"][0]
        self.assertEqual(it["branch"], "all")
        self.assertEqual(sorted(it["reviews"]), ["1001004", "2001000", "3001004"])
        self.assertEqual(it["count"], 3)         # len(reviews), not the claimed count

    def test_wrong_key(self):
        self.post(export())
        self.assertEqual(self.inbox(key="nope")[0], 403)
        self.assertEqual(self.call("GET", f"/v2/feedback/{CODE}")[0], 403)
        self.assertEqual(self.delete(key="nope")[0], 403)
        self.assertEqual(len(self.rows()), 1)

    def test_code_allowlist(self):
        self.app.cfg.feedback_codes = {"only-this-code"}
        self.assertEqual(self.post(export())[0], 403)
        self.assertEqual(self.inbox()[0], 403)
        self.assertEqual(self.post(export(), code="only-this-code")[0], 200)
        self.app.cfg.feedback_codes = set()
        self.assertEqual(self.post(export(), code="bad")[0], 403)      # too short a code

    def test_too_big_and_empty(self):
        big = export(reviews={"1": {"name": "x", "status": "ok", "note": "y" * 70000}})
        self.assertEqual(self.post(big)[0], 413)
        self.assertEqual(self.call("POST", f"/v2/feedback/{CODE}", raw=b"")[0], 413)

    def test_big_body_only_allowed_here(self):
        reviews = {str(i): {"name": "S", "status": "ok", "note": "n" * 500} for i in range(1, 101)}
        self.assertEqual(self.post(export(reviews=reviews))[0], 200)       # ~55 KB
        s, _ = self.call("POST", "/v2/rooms", raw=b" " * 9000)             # others stay at 8 KB
        self.assertEqual(s, 413)

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
        self.assertEqual(self.post(export(count=-1))[0], 400)
        self.assertEqual(self.call("POST", f"/v2/feedback/{CODE}", raw=b"{nope")[0], 400)
        s, r = self.call("POST", f"/v2/feedback/{CODE}", [1, 2])
        self.assertEqual(s, 400)
        self.assertIn("error", r)
        self.assertEqual(self.rows(), [])

    def test_anonymous_ok(self):
        self.assertEqual(self.post(export(reviewer="  "))[0], 200)
        self.assertEqual(self.inbox()[1]["items"][0]["reviewer"], "")

    def test_note_cut_and_junk_dropped(self):
        reviews = {"1": {"name": "A", "status": "ok", "note": "n" * 800, "evil": "x"},
                   "2": {"name": "B", "status": "", "kind": "", "mode": "", "note": ""},
                   "3": {"name": "C", "status": "wrong", "kind": "attack"}}
        self.assertEqual(self.post(export(reviews=reviews, extra="field", ip="1.2.3.4"))[0], 200)
        it = self.inbox()[1]["items"][0]
        self.assertEqual(len(it["reviews"]["1"]["note"]), 500)
        self.assertNotIn("evil", it["reviews"]["1"])
        self.assertNotIn("2", it["reviews"])
        self.assertNotIn("extra", it)
        self.assertNotIn("ip", it)
        self.assertEqual(it["count"], 2)

    def test_one_post_per_30s(self):
        self.app.cfg.feedback_gap_s = 30
        self.assertEqual(self.post(export())[0], 200)
        s, r = self.post(export())
        self.assertEqual(s, 429)
        self.assertEqual(r["error"], "one send per 30 s")
        self.assertEqual(self.post(export(branch="bad!"))[0], 429)         # rate check comes first
        clock.offset += 31
        self.assertEqual(self.post(export())[0], 200)

    def test_rejected_post_does_not_use_the_gap(self):
        self.app.cfg.feedback_gap_s = 30
        self.assertEqual(self.post(export(branch="bad!"))[0], 400)
        self.assertEqual(self.post(export())[0], 200)

    def test_ttl(self):
        self.post(export())
        clock.offset += 31 * DAY
        self.assertEqual(self.inbox()[1]["items"], [])
        self.assertEqual(self.rows(), [])

    def test_ttl_setting(self):
        self.app.cfg.feedback_ttl_days = 2
        self.post(export(reviewer="Old"))
        clock.offset += 1 * DAY
        self.post(export(reviewer="New"))
        clock.offset += 1.5 * DAY                                          # Old is 2.5 d, New 1.5 d
        self.assertEqual([i["reviewer"] for i in self.inbox()[1]["items"]], ["New"])

    def test_max_drops_oldest(self):
        self.app.cfg.feedback_max = 3
        ids = [self.post(export(reviewer=f"R{i}"))[1]["id"] for i in range(5)]
        items = self.inbox()[1]["items"]
        self.assertEqual([i["id"] for i in items], ids[2:])                 # oldest first
        self.assertEqual([i["reviewer"] for i in items], ["R2", "R3", "R4"])
        self.assertEqual(len(self.rows()), 3)

    def test_delete(self):
        ids = [self.post(export(reviewer=f"R{i}"))[1]["id"] for i in range(3)]
        self.post(export())
        s, r = self.delete({"ids": [ids[0], "nosuchid"]})
        self.assertEqual((s, r), (200, {"deleted": 1}))
        self.assertEqual(len(self.inbox()[1]["items"]), 3)
        s, r = self.delete()
        self.assertEqual((s, r), (200, {"deleted": 3}))
        self.assertEqual(self.inbox()[1]["items"], [])

    def test_delete_bad_body(self):
        self.post(export())
        self.assertEqual(self.delete({"ids": "x"})[0], 400)
        self.assertEqual(self.delete([1])[0], 400)
        self.assertEqual(len(self.inbox()[1]["items"]), 1)

    def test_delete_other_code_untouched(self):
        self.post(export(), code="other-code-1")
        self.post(export())
        self.delete()
        self.assertEqual(len(self.inbox("other-code-1")[1]["items"]), 1)


class TestFeedbackDisk(FbBase):
    def test_survives_restart(self):
        a = self.post(export(reviewer="One"))[1]["id"]
        b = self.post(export(reviewer="Two"), code="other-code-1")[1]["id"]
        self.stop()
        self.start(**self.FB)
        items = self.inbox()[1]["items"]
        self.assertEqual([(i["id"], i["reviewer"]) for i in items], [(a, "One")])
        self.assertEqual([i["id"] for i in self.inbox("other-code-1")[1]["items"]], [b])
        self.assertEqual(self.delete()[1], {"deleted": 1})
        self.stop()
        self.start(**self.FB)
        self.assertEqual(self.inbox()[1]["items"], [])
        self.assertEqual(len(self.inbox("other-code-1")[1]["items"]), 1)

    def test_ttl_applies_after_restart(self):
        self.post(export())
        self.stop()
        clock.offset += 31 * DAY
        self.start(**self.FB)
        self.assertEqual(self.inbox()[1]["items"], [])

    def test_only_cleaned_item_stored_no_address(self):
        self.post(export(ip="1.2.3.4", extra="x"))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("127.0.0.1", rows[0]["item"])
        self.assertNotIn("1.2.3.4", rows[0]["item"])
        self.assertEqual(set(rows[0]), {"id", "code", "received_t", "item"})
        # nothing in the whole database mentions the address either
        self.app.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con = sqlite3.connect(str(Path(self.tmp) / "relay.db"))
        dump = "\n".join(con.iterdump())
        con.close()
        self.assertNotIn("127.0.0.1", dump)

    def test_migration_adds_table_to_old_db(self):
        # a db made at migration 1 gets the feedback table when opened by this version
        self.stop()
        con = sqlite3.connect(str(Path(self.tmp) / "relay.db"))
        con.execute("DROP TABLE feedback")
        con.execute("UPDATE meta SET version=1")
        con.commit()
        con.close()
        self.start(**self.FB)
        self.assertEqual(self.post(export())[0], 200)


if __name__ == "__main__":
    unittest.main(verbosity=1)
