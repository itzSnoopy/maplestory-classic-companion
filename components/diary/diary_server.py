"""diary_server.py - serve the diary on this PC only, so the Story view's Edit / Generate buttons
can save. A page opened straight from disk (diary.html) can't write files; this can.

  python diary.py serve            opens http://127.0.0.1:8737/ in the browser (Ctrl+C to stop)

Listens on 127.0.0.1 only. Requests must carry the right Host and an X-Diary header, which a
web page on another site can't send without a CORS preflight this server never answers.
Serves the page, screenshots/diary/ and game_icons/ only. Screenshots are Nexon imagery: never expose this
server beyond localhost.
"""
import json
import mimetypes
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from components.diary import diary
from components.diary import diary_html

PORT = 8737


class Handler(BaseHTTPRequestHandler):
    server_version = "diary"

    def log_message(self, *a):        # quiet console
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.last_hit = time.time()
        if not self._host_ok():
            return self._send(403, '{"error": "host"}')
        path = self.path.split("?", 1)[0]
        if path in ("/", "/diary.html"):
            return self._send(200, diary_html.page(diary.ROOT, live=True), "text/html; charset=utf-8")
        if path == "/album.html":                      # the monster album (album.py)
            from components.album import album_html
            return self._send(200, album_html.page(diary.ROOT, live=True), "text/html; charset=utf-8")
        from urllib.parse import unquote
        rel = unquote(path.lstrip("/"))
        allowed = {(diary.ROOT / "screenshots" / "diary").resolve(), (diary.ROOT / "game_icons").resolve(),
                   (diary.ROOT / "assets" / "mob_icons").resolve(),
                   (diary.ROOT / "assets" / "item_icons").resolve()}
        f = (diary.ROOT / rel).resolve()
        if f.parent not in allowed or not f.is_file():    # pictures only, no other files
            return self._send(404, '{"error": "not found"}')
        self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

    def do_POST(self):
        self.server.last_hit = time.time()
        if not self._host_ok() or self.headers.get("X-Diary") != "1":
            return self._send(403, '{"error": "forbidden"}')
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(min(n, 10000)) or b"{}")
            eid = str(req.get("id") or "")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, '{"error": "bad request"}')
        if self.path == "/api/sources":
            off = req.get("off")
            if not isinstance(off, list):
                return self._send(400, '{"error": "bad request"}')
            known = set(diary.all_sources())
            diary.set_sources_off([str(x) for x in off if str(x) in known])
            return self._send(200, json.dumps({"off": sorted(diary.sources_off())}, ensure_ascii=False))
        if self.path == "/api/delete":
            e = diary.delete_entry(eid)
            if e is None:
                return self._send(404, '{"error": "no such entry"}')
            return self._send(200, json.dumps({"deleted": eid}, ensure_ascii=False))
        if self.path == "/api/caption":
            text = str(req.get("text") or "")[:300]
            e = diary.set_caption(text, eid=eid)
        elif self.path == "/api/generate":
            line = diary.generate_caption(eid=eid)
            e = diary._find(eid) if line else None
            if e is None:
                return self._send(409, json.dumps({"error": "No line in captions.json fits this entry."}))
        else:
            return self._send(404, '{"error": "not found"}')
        if e is None:
            return self._send(404, '{"error": "no such entry"}')
        custom = diary.custom_captions()
        story, source = diary.caption_info(e, custom)
        self._send(200, json.dumps({"story": story, "source": source, "custom": eid in custom},
                                   ensure_ascii=False))


_thread_server = None


def url(port=PORT):
    return f"http://127.0.0.1:{(_thread_server.server_port if _thread_server else port)}/"


def start_thread(port=PORT):
    """Serve from a daemon thread of the calling process (the helper); lives as long as it does.
    Returns the page's URL. A second call reuses the same server."""
    global _thread_server
    if _thread_server is None:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:                   # port busy (not ours): any free one
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.last_hit = time.time()
        threading.Thread(target=httpd.serve_forever, daemon=True, name="diary-server").start()
        _thread_server = httpd
    return url()


def running(port=PORT):
    """True when a diary server already answers on this PC."""
    import urllib.request
    if _thread_server is not None:
        port = _thread_server.server_port
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.6) as r:
            return r.status == 200 and b"diary" in r.read(4000).lower()
    except Exception:
        return False


def serve(port=PORT, open_browser=True, idle_exit_s=None):
    """idle_exit_s: stop after this many seconds without a request (background starts)."""
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:                       # port busy: any free one
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.last_hit = time.time()
    url = f"http://127.0.0.1:{httpd.server_port}/"
    print(f"Diary at {url}  (this PC only; Ctrl+C or close this window to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    if idle_exit_s:
        def watch():
            while time.time() - httpd.last_hit < idle_exit_s:
                time.sleep(15)
            httpd.shutdown()
        threading.Thread(target=watch, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import sys
    serve(open_browser="--no-open" not in sys.argv)
