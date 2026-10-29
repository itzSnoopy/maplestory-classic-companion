"""feedback component: players review the skill data in a page the app serves locally
(/skill-review.html, review_page.py) and press Send; the app posts the export to the owner's relay
feedback inbox (POST <server>/v1/feedback/<code>). Helper side only: attach() does nothing.

Subscribes: nothing. Emits: nothing.
Helper side (ui): the page (shell.add_page), the local API POST /api/feedback/send (shell.add_api, hosted
by the diary server with its Host + X-Diary checks), menu "Review skills (send feedback)", and the server /
code fields under Settings > Internet > Skill feedback.
Online use only behind internet.allowed("feedback"), right before connecting. Reviewer names and the code
are never logged.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from base import internet
from base.contract import Component
from hub import helper_settings as HS

PAGE = "/skill-review.html"
API = "/api/feedback/send"
STATUS_API = "/api/feedback/status"
TIMEOUT_S = 10


def validate(req):
    """Light check that req looks like a classic-skill-review/1 export (the relay checks strictly).
    Returns an error text or None."""
    if not isinstance(req, dict) or req.get("format") != "classic-skill-review/1":
        return "That is not a skill review export."
    rv = req.get("reviews")
    if not isinstance(rv, dict) or not rv:
        return "Answer at least one skill first."
    return None


def post_feedback(server, code, req, timeout=TIMEOUT_S):
    """Send the export. Returns (http status, obj) for the local API. Checks the internet gate itself."""
    err = validate(req)
    if err:
        return 400, {"error": err}
    server = str(server or "").strip().rstrip("/")
    code = str(code or "").strip()
    if not server or not code:
        return 400, {"error": "Set the feedback server and code in Settings > Internet."}
    if not server.startswith(("http://", "https://")):
        server = "http://" + server
    if not internet.allowed("feedback"):
        return 403, {"error": "Internet is off for Skill feedback (Settings > Internet)."}
    url = f"{server}/v1/feedback/{urllib.parse.quote(code, safe='')}"
    body = json.dumps(req, ensure_ascii=False).encode("utf-8")
    rq = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            try:
                out = json.loads(r.read(20000) or b"{}")
            except ValueError:
                out = {}
            return 200, {"ok": True, "id": out.get("id") if isinstance(out, dict) else None}
    except urllib.error.HTTPError as e:
        try:
            text = str((json.loads(e.read(4000)) or {}).get("error") or "")[:200]
        except (ValueError, AttributeError, TypeError):
            text = ""
        if e.code == 403:
            return 403, {"error": "The feedback server says the code is wrong (Settings > Internet)."}
        if e.code == 404:
            return 404, {"error": "This server has no feedback inbox."}
        if e.code in (400, 413, 429):
            return e.code, {"error": text or f"The feedback server refused it (error {e.code})."}
        return 502, {"error": text or f"The feedback server answered with error {e.code}."}
    except (urllib.error.URLError, OSError, ValueError):
        return 502, {"error": "Could not reach the feedback server. Check the address in "
                              "Settings > Internet and that it is running."}


def server_status(server, code, timeout=4):
    """Is the review server up with its feedback inbox on? GET <server>/health (sends nothing about
    the player). Returns {online: bool, reason: text}. Checks the internet gate itself."""
    server = str(server or "").strip().rstrip("/")
    if not server or not str(code or "").strip():
        return {"online": False, "reason": "Set the feedback server and code in Settings > Internet."}
    if not server.startswith(("http://", "https://")):
        server = "http://" + server
    if not internet.allowed("feedback"):
        return {"online": False, "reason": "Internet is off for Skill feedback (Settings > Internet)."}
    try:
        with urllib.request.urlopen(server + "/health", timeout=timeout) as r:
            h = json.loads(r.read(4000) or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return {"online": False, "reason": "The review server does not answer."}
    if not isinstance(h, dict) or not h.get("ok"):
        return {"online": False, "reason": "The review server does not answer."}
    if not h.get("feedback"):
        return {"online": False, "reason": "The server is up but its feedback inbox is off."}
    return {"online": True, "reason": ""}


class FeedbackComponent(Component):
    name = "feedback"
    settings_defaults = {"feedback": HS.DEFAULTS["feedback"]}

    def attach(self, hub):
        pass                                    # helper side only

    # -- the helper window
    def ui(self, shell):
        self.shell = shell
        shell.add_page(PAGE, self.render_page)
        shell.add_api(API, self.send)
        shell.add_api(STATUS_API, self.status)
        shell.add_command("skillreview", self.open)
        shell.add_menu([("Review skills (send feedback)", "skillreview", None, None, None)], slot="open")
        shell.add_settings_extra("internet.feedback", self.internet_extra)
        shell.on_settings_save(self.on_save)

    def _settings(self):
        sh = getattr(self, "shell", None)
        allv = sh.all_settings() if sh is not None else {}
        return allv or HS.load()

    def render_page(self, root, live):
        from components.feedback import review_page
        name = (self._settings().get("character") or {}).get("name") or ""
        return review_page.page(reviewer=name)

    def open(self):
        from hub import pages
        try:
            opened = pages.open_page(PAGE, in_process=True)
        except Exception as e:
            print(f"open skill review failed: {e}")
            opened = None
        if opened is None:          # Settings > Diary > "As a file": the page needs the local server to send
            self.shell.notify("The skill review page needs Settings > Diary > Open pages: Through localhost.")

    def send(self, req):
        fb = self._settings().get("feedback") or {}
        return post_feedback(fb.get("server"), fb.get("code"), req)

    def status(self, req):
        fb = self._settings().get("feedback") or {}
        return 200, server_status(fb.get("server"), fb.get("code"))

    def internet_extra(self, win, f):
        """Under "Skill feedback" on the Internet page: the owner's relay address and feedback code."""
        import tkinter as tk
        px = win.px
        win.s.setdefault("feedback", {})
        for k, v in self.settings_defaults["feedback"].items():
            win.s["feedback"].setdefault(k, v)
        party = str((win.s.get("party") or {}).get("server") or "").strip()
        for key, cap, hint in (("feedback.server", "Server", "e.g. " + (party or "http://1.2.3.4:8787")),
                               ("feedback.code", "Code", "from the person who runs the server")):
            row = tk.Frame(f, bg=win.w["bg"])
            row.pack(anchor="w", pady=(px(4), 0), padx=(px(22), 0))
            lb = win.label(row, cap)
            lb.configure(width=10)
            lb.pack(side="left")
            win.entry(row, key, width=32).pack(side="left", padx=(px(6), px(8)))
            win.label(row, hint, muted=True, size=8).pack(side="left")
        win.label(f, "Menu > Review skills (send feedback) opens the page. Sending needs both fields.",
                  muted=True, size=8, wrap=px(540)).pack(anchor="w", padx=(px(22), 0), pady=(px(4), 0))

    def on_save(self, win):
        if "feedback" in win.s:
            for k in ("server", "code"):
                win.s["feedback"][k] = str(win.s["feedback"].get(k) or "").strip()
