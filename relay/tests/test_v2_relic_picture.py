"""Tests for relic auto-fit and the hunt's picture (gallery folder + uploads).

    python relay/tests/test_v2_relic_picture.py
Images are built in the test from bytes (tiny headers); no image files are committed.
"""
import json
import os
import random
import struct
import sys
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import Base, clock  # noqa: E402
from relayd import pictures  # noqa: E402
from test_v2_relic import AlwaysFind, RelicBase  # noqa: E402


def png(w=128, h=96, pad=0):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk + b"\x00" * pad


def jpeg(w=200, h=100, pad=0):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xda\x00\x02" + b"\x00" * pad + b"\xff\xd9"


def webp_l(w=100, h=80, pad=0):
    bits = (w - 1) | ((h - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5 + pad) + b"\x2f" + struct.pack("<I", bits) + b"\x00" * pad
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_x(w=300, h=200):
    payload = b"\x00\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    body = b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_v(w=64, h=64):
    frame = b"\x10\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", w, h)
    body = b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


class Picture(RelicBase):
    def raw(self, method, path, data=None, token=None, ctype=None):
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def upload(self, data, who="Owner1", ctype="image/png"):
        s, body, _ = self.raw("POST", f"/v2/rooms/{self.rid}/relic/upload", data, self.member(who), ctype)
        return s, json.loads(body)

    def gallery_put(self, name, data):
        d = os.path.join(self.tmp, "relic_gallery")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)

    def gallery(self):
        s, d = self.call("GET", "/v2/relic/gallery")
        self.assertEqual(s, 200, d)
        return d["images"]


class TestAutofit(RelicBase):
    def test_default_off(self):
        self.begin()
        self.assertFalse(self.state()["autofit"])
        self.live("Owner1")
        r = self.kills("Owner1", 1)
        self.assertFalse(r["autofit"])
        self.assertEqual(self.state()["placed_count"], 0)

    def test_only_true_counts(self):
        for v in ("true", 1, "yes", None):
            self.act("Owner1", "stop")
            self.begin(autofit=v)
            self.assertFalse(self.state()["autofit"], v)

    def test_found_goes_into_slot_and_finishes(self):
        self.begin("easy", autofit=True)
        self.live("Owner1")
        self.live("Bob")
        st = self.state()
        self.assertTrue(st["autofit"])
        total = st["pieces_total"]
        r = self.kills("Owner1", 1)
        self.assertTrue(r["autofit"])
        self.assertEqual(len(r["found"]), 1)
        pid = r["found"][0]
        st = self.state()
        self.assertEqual(st["placed_count"], 1)
        p = st["pieces"][pid]
        self.assertEqual(p["state"], "placed")
        self.assertEqual(p["placed_by"], "Owner1")
        b = st["board"]
        self.assertAlmostEqual(p["x"], b["x"] + (pid % st["cols"] + 0.5) * b["w"] / st["cols"], places=3)
        # a placed piece cannot be moved
        s, d = self.act("Bob", "move", piece=pid, x=0.5, y=0.5)
        self.assertEqual(s, 409, d)
        # the rest, by two finders
        for i in range(total - 1):
            self.kills("Owner1" if i % 2 else "Bob", 1)
            clock.offset += 61           # the per-minute cap is a token bucket
        st = self.state()
        self.assertTrue(st["finished"])
        self.assertEqual(st["placed_count"], total)
        self.assertEqual(st["found_count"], total)
        self.assertEqual(sum(st["finders"].values()), total)
        self.assertTrue(st["most_found"] in st["finders"])
        self.assertTrue(st["last_placed_by"] in ("Owner1", "Bob"))
        self.assertEqual(len(st["gallery"]), 1)
        self.assertEqual(self.lobby_activity(), "Relic hunt complete")

    def test_views(self):
        self.begin("easy", autofit=True)
        s, d = self.call("GET", f"/v2/rooms/{self.rid}", token=self.owner)
        self.assertTrue(d["relic"]["autofit"])
        self.assertTrue(self.app.modules["relic"].admin_view({"id": self.rid})["hunt"]["autofit"])


class TestGallery(Picture):
    def test_listing_and_serving(self):
        self.gallery_put("b scene.png", png(300, 200))
        self.gallery_put("a.jpg", jpeg(640, 480))
        self.gallery_put("c.webp", webp_l(100, 50))
        self.gallery_put("fake.png", b"not an image at all, just text")
        self.gallery_put("anim.gif", b"GIF89a" + b"\x00" * 30)
        self.gallery_put(".hidden.png", png())
        self.gallery_put("bad;name.png", png())
        imgs = self.gallery()
        self.assertEqual([i["name"] for i in imgs], ["a.jpg", "b scene.png", "c.webp"])
        self.assertEqual((imgs[0]["w"], imgs[0]["h"]), (640, 480))
        self.assertEqual(imgs[1]["url"], "/rooms/gallery/b%20scene.png")
        s, body, hd = self.raw("GET", imgs[1]["url"])
        self.assertEqual(s, 200)
        self.assertEqual(body, png(300, 200))
        self.assertEqual(hd["Content-Type"], "image/png")
        self.assertEqual(hd["X-Content-Type-Options"], "nosniff")
        self.assertEqual(hd["Cache-Control"], "public, max-age=3600")
        s, body, hd = self.raw("GET", "/rooms/gallery/a.jpg")
        self.assertEqual(hd["Content-Type"], "image/jpeg")

    def test_missing_folder_is_created_and_empty(self):
        self.assertEqual(self.gallery(), [])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "relic_gallery")))

    def test_refusals(self):
        self.gallery_put("fake.png", b"x" * 100)
        secret = os.path.join(self.tmp, "secret.png")
        with open(secret, "wb") as f:
            f.write(png())
        for path in ("/rooms/gallery/missing.png", "/rooms/gallery/fake.png", "/rooms/gallery/..%2Fsecret.png",
                     "/rooms/gallery/%2e%2e%2fsecret.png", "/rooms/gallery/..%5Csecret.png",
                     "/rooms/gallery/", "/rooms/gallery/a/b.png", "/rooms/gallery/x.svg"):
            s, _, _ = self.raw("GET", path)
            self.assertEqual(s, 404, path)
        s, _, _ = self.raw("POST", "/rooms/gallery/a.png", b"")
        self.assertEqual(s, 404)

    def test_symlink_out_is_refused(self):
        outside = os.path.join(self.tmp, "outside.png")
        with open(outside, "wb") as f:
            f.write(png())
        self.gallery_put("seed.png", png())
        link = os.path.join(self.tmp, "relic_gallery", "link.png")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        self.assertEqual([i["name"] for i in self.gallery()], ["seed.png"])
        s, _, _ = self.raw("GET", "/rooms/gallery/link.png")
        self.assertEqual(s, 404)

    def test_csp_allows_blob_images_only(self):
        for path in ("/rooms", f"/rooms/{self.rid}/relic", "/rooms/relic.js"):
            s, _, hd = self.raw("GET", path)
            self.assertEqual(s, 200, path)
            csp = hd["Content-Security-Policy"]
            self.assertIn("img-src 'self' data: blob:", csp)
            self.assertIn("script-src 'self';", csp)
            self.assertNotIn("blob:", csp.replace("img-src 'self' data: blob:", ""))
            self.assertEqual(hd["Cache-Control"], "no-store")


class TestUpload(Picture):
    def test_good_images(self):
        for data, ctype, wh in ((png(128, 96), "image/png", (128, 96)), (jpeg(200, 100), "image/jpeg", (200, 100)),
                                (webp_l(100, 80), "image/webp", (100, 80)), (webp_x(300, 200), "image/webp", (300, 200)),
                                (webp_v(64, 70), "image/webp", (64, 70))):
            clock.offset += 700
            s, d = self.upload(data, ctype=ctype)
            self.assertEqual(s, 200, d)
            self.assertEqual((d["w"], d["h"]), wh)
            self.assertRegex(d["upload"], r"^[0-9a-f]{16}$")
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "relic_uploads", self.rid)))

    def test_refused(self):
        gif = b"GIF89a" + struct.pack("<HH", 100, 100) + b"\x00" * 20
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"></svg>'
        for data, ctype in ((gif, "image/gif"), (svg, "image/svg+xml"), (gif, "image/png"), (svg, "image/png"),
                            (png(), "image/jpeg"), (png(63, 100), "image/png"), (png(100, 63), "image/png"),
                            (png(4097, 100), "image/png"), (jpeg(10, 10), "image/jpeg"), (png(), "text/plain"),
                            (png(), None), (b"\x89PNG\r\n\x1a\n", "image/png")):
            self.app.modules["relic"].uploads.clear()        # every attempt counts toward the limit: start fresh
            s, d = self.upload(data, ctype=ctype)
            self.assertEqual(s, 400, (ctype, d))
            self.assertIn("error", d)
        self.app.modules["relic"].uploads.clear()
        s, body, _ = self.raw("POST", f"/v2/rooms/{self.rid}/relic/upload", b"", self.member("Owner1"), "image/png")
        self.assertEqual(s, 400)

    def test_bad_attempts_count_toward_the_limit(self):
        for _ in range(pictures.UPLOAD_RATE[0]):
            self.assertEqual(self.upload(png(63, 100))[0], 400)
        s, d = self.upload(png())
        self.assertEqual(s, 429, d)

    def test_too_big(self):
        data = png(pad=pictures.MAX_UPLOAD)          # header + padding: over the limit
        try:
            s, body, _ = self.raw("POST", f"/v2/rooms/{self.rid}/relic/upload", data, self.member("Owner1"), "image/png")
        except (urllib.error.URLError, ConnectionError, OSError):
            return                                   # the server closed the connection before we finished sending
        self.assertEqual(s, 413)
        # exactly at the limit is fine
        s, d = self.upload(png(pad=pictures.MAX_UPLOAD - len(png())))
        self.assertEqual(s, 200, d)

    def test_who_may_upload(self):
        data = png()
        s, d = self.upload(data, who="Plain")
        self.assertEqual(s, 403, d)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "relic_uploads", self.rid)))
        s, body, _ = self.raw("POST", f"/v2/rooms/{self.rid}/relic/upload", data, "wrong-token-xx", "image/png")
        self.assertEqual(s, 401)
        s, body, _ = self.raw("POST", f"/v2/rooms/{self.rid}/relic/upload", data, None, "image/png")
        self.assertEqual(s, 401)
        self.assertEqual(self.upload(data)[0], 200)

    def test_promoted_mod_may(self):
        self.member("Modder")
        s, d = self.call("POST", f"/v2/rooms/{self.rid}/members/Modder/promote", token=self.owner)
        self.assertEqual(s, 200, d)
        self.assertEqual(self.upload(png(), who="Modder")[0], 200)

    def test_rate_limit(self):
        for i in range(6):
            s, d = self.upload(png(pad=i))
            self.assertEqual(s, 200, d)
        s, d = self.upload(png(pad=99))
        self.assertEqual(s, 429, d)
        clock.offset += 601
        self.assertEqual(self.upload(png(pad=99))[0], 200)

    def test_keeps_five(self):
        ids = []
        for i in range(7):
            if i % 6 == 0:
                clock.offset += 601
            s, d = self.upload(png(pad=i))
            self.assertEqual(s, 200, d)
            ids.append(d["upload"])
            os.utime(os.path.join(self.tmp, "relic_uploads", self.rid, d["upload"] + ".png"), (1000 + i, 1000 + i))
        left = sorted(os.listdir(os.path.join(self.tmp, "relic_uploads", self.rid)))
        self.assertEqual(len(left), 5)
        self.assertEqual(left, sorted(i + ".png" for i in ids[2:]))

    def test_room_delete_removes_folder(self):
        self.assertEqual(self.upload(png())[0], 200)
        folder = os.path.join(self.tmp, "relic_uploads", self.rid)
        self.assertTrue(os.path.isdir(folder))
        s, d = self.call("DELETE", f"/v2/rooms/{self.rid}", token=self.owner)
        self.assertEqual(s, 200, d)
        self.assertFalse(os.path.exists(folder))


class TestPickingAndReading(Picture):
    def test_start_with_gallery(self):
        self.gallery_put("scene one.png", png(400, 250))
        d = self.begin("easy", picture={"gallery": "scene one.png"})
        self.assertEqual(d["picture"], {"kind": "gallery", "url": "/rooms/gallery/scene%20one.png", "w": 400, "h": 250})
        self.assertEqual(self.state()["picture"]["kind"], "gallery")
        s, body, hd = self.raw("GET", f"/v2/rooms/{self.rid}/relic/picture", token=self.member("Owner1"))
        self.assertEqual(s, 200)
        self.assertEqual(body, png(400, 250))

    def test_start_with_upload_and_read(self):
        s, up = self.upload(jpeg(320, 200), ctype="image/jpeg")
        self.assertEqual(s, 200)
        d = self.begin("easy", picture={"upload": up["upload"]}, autofit=True)
        pic = d["picture"]
        self.assertEqual((pic["kind"], pic["w"], pic["h"]), ("upload", 320, 200))
        self.assertTrue(pic["url"].startswith(f"/v2/rooms/{self.rid}/relic/picture"))
        url = pic["url"]
        bob = self.member("Bob")
        s, body, hd = self.raw("GET", url, token=bob)              # any member
        self.assertEqual(s, 200)
        self.assertEqual(body, jpeg(320, 200))
        self.assertEqual(hd["Content-Type"], "image/jpeg")
        self.assertEqual(hd["X-Content-Type-Options"], "nosniff")
        self.assertEqual(hd["Cache-Control"], "private, max-age=600")
        for tok in (None, "wrong-token-xx"):
            s, _, _ = self.raw("GET", url, token=tok)
            self.assertEqual(s, 401)
        # not a member of another room
        rid2, own2 = self.mk(kind="hangout", owner="Zed")
        s, _, _ = self.raw("GET", f"/v2/rooms/{rid2}/relic/picture", token=own2)
        self.assertEqual(s, 404)
        s, _, _ = self.raw("GET", f"/v2/rooms/{self.rid}/relic/picture", token=own2)
        self.assertEqual(s, 401)

    def test_upload_of_another_room_is_not_usable(self):
        rid2, own2 = self.mk(kind="hangout", owner="Zed")
        s, body, _ = self.raw("POST", f"/v2/rooms/{rid2}/relic/upload", png(), own2, "image/png")
        self.assertEqual(s, 200, body)
        uid = json.loads(body)["upload"]
        s, d = self.act("Owner1", "start", difficulty="easy", picture={"upload": uid})
        self.assertEqual(s, 400, d)

    def test_bad_pictures(self):
        self.gallery_put("fake.png", b"x" * 64)
        for pic in ({"gallery": "nope.png"}, {"gallery": "fake.png"}, {"gallery": "../x.png"}, {"gallery": 5},
                    {"upload": "0123456789abcdef"}, {"upload": "../../etc"}, {"upload": 7}, {}, {"url": "x"},
                    {"gallery": "a.png", "upload": "0123456789abcdef"}, "a.png", ["a.png"], 5):
            s, d = self.act("Owner1", "start", difficulty="easy", picture=pic)
            self.assertEqual(s, 400, (pic, d))
        self.assertFalse(self.state()["active"])               # nothing started

    def test_no_picture_is_the_drawn_scene(self):
        d = self.begin("easy")
        self.assertIsNone(d["picture"])
        self.assertFalse(d["autofit"])
        s, _, _ = self.raw("GET", f"/v2/rooms/{self.rid}/relic/picture", token=self.member("Owner1"))
        self.assertEqual(s, 404)

    def test_upload_kept_while_hunt_runs(self):
        s, up = self.upload(png(pad=1))
        self.begin("easy", picture={"upload": up["upload"]})
        for i in range(2, 6):
            self.assertEqual(self.upload(png(pad=i))[0], 200)
        clock.offset += 601
        for i in range(6, 10):
            self.assertEqual(self.upload(png(pad=i))[0], 200)
        s, body, _ = self.raw("GET", f"/v2/rooms/{self.rid}/relic/picture", token=self.member("Owner1"))
        self.assertEqual(s, 200)

    def test_picture_survives_restart_state(self):
        self.gallery_put("p.png", png(256, 256))
        self.begin("easy", picture={"gallery": "p.png"}, autofit=True)
        self.app.modules["relic"].state.clear()                # forces a reload from SQLite
        st = self.state()
        self.assertTrue(st["autofit"])
        self.assertEqual(st["picture"]["kind"], "gallery")


class TestSniff(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(pictures.sniff(png(7, 9))[2:], (7, 9))
        self.assertEqual(pictures.sniff(jpeg(1000, 20))[2:], (1000, 20))
        self.assertEqual(pictures.sniff(webp_l(4096, 1))[2:], (4096, 1))
        self.assertIsNone(pictures.sniff(b""))
        self.assertIsNone(pictures.sniff(b"\xff\xd8\xff"))
        self.assertIsNone(pictures.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8L"))


if __name__ == "__main__":
    unittest.main()
