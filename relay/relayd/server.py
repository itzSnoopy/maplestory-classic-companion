from http.server import ThreadingHTTPServer

from .legacy import Handler
from .rooms import Relay


def make_server(cfg, start_cleanup=False):
    app = Relay(cfg)
    srv = ThreadingHTTPServer((cfg.bind, cfg.port), Handler)
    srv.app = app
    if start_cleanup:
        app.start_cleanup()
    return srv
