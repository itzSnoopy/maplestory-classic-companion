"""Live outputs (sinks). Independent of where the data comes from.

A sink gets `show(text)` with the one-line live summary, and `show("")` when idle.
Files (journal, CSVs, digest) are written by tracker.py/history.py, not by sinks.
"""
from datetime import datetime


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}")


class ObsOverlay:
    """Updates an OBS Text source. Has its own WebSocket connection, so it works with any data source."""

    def __init__(self, cfg):
        self.cfg, self.source = cfg, cfg["overlay"]["text_source"]
        self.client, self.last, self.warned = None, None, False

    def show(self, text):
        if text == self.last:
            return
        try:
            if self.client is None:
                from capture_obs import from_config
                self.client = from_config(self.cfg)
            self.client.set_text(self.source, text)
            self.last, self.warned = text, False
        except Exception as e:
            self.close()
            if not self.warned:
                self.warned = True
                log(f"overlay update failed ({type(e).__name__}: {e})")

    def close(self):
        if self.client:
            self.client.close()
        self.client = None


def make_sinks(cfg):
    sinks = []
    if cfg.get("overlay", {}).get("enabled"):
        sinks.append(ObsOverlay(cfg))
    return sinks
