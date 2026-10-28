"""pages.py - pages that components serve on the local diary server (127.0.0.1:8737).

The diary server (components/diary/diary_server.py) hosts them; a component registers its page with
register_page(path, render) from its ui(shell) (shell.add_page) and never imports the server or
the other component:

    register_page("/album.html", lambda root, live: html_text)

open_page(path) opens a registered page in the browser through the host (the diary server, which
registers itself with set_host). serve(...) runs the server in this process. In a process that has no
host yet (a command line like `python -m components.album.album open`) the launcher composes one
(launcher/compose.py:page_host), loaded by name so the hub doesn't import upward.
"""
import importlib

_pages = {}          # path -> render(root, live) -> html text
_host = None


def register_page(path, render):
    _pages[path] = render


def page(path):
    """The render function registered for path, or None."""
    return _pages.get(path)


def paths():
    return sorted(_pages)


def set_host(host):
    """host: an object with open_page(path, in_process) and serve(...) (the diary server module)."""
    global _host
    _host = host


def current():
    """The host set so far, or None."""
    return _host


def host():
    if _host is None:
        importlib.import_module("launcher.compose").page_host()      # sets the host
    return _host


def open_page(path, in_process=False):
    """Open a registered page in the browser. Returns what was opened, or None when the player chose
    files (Settings > Diary > As a file) or no server could start: the caller opens a file then."""
    return host().open_page(path, in_process=in_process)


def serve(**kw):
    """Run the page server in this process (blocks)."""
    return host().serve(**kw)
