"""csv_replay source: replays a recorded sessions/<ts>/exp_log.csv as Observations.

Shows the tracker doesn't care where data comes from, and gives regression tests against
real play: change tracker logic, replay old sessions, compare journal/levels output.

config.json: "source": {"type": "csv_replay", "path": "sessions/<ts>/exp_log.csv"}
"""
import csv
from datetime import datetime

from sources.base import OK, Observation, Source


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class CsvReplaySource(Source):
    name = "csv_replay"
    retry_s = 0

    def __init__(self, cfg, path=None):
        path = path or cfg.get("source", {}).get("path")
        with open(path, newline="", encoding="utf-8") as f:
            self.rows = [r for r in csv.DictReader(f) if r.get("event") != "rejected"]
        self.i = 0

    def read(self, hints):
        if self.i >= len(self.rows):
            raise StopIteration
        r = self.rows[self.i]
        self.i += 1
        return Observation(
            t=datetime.fromisoformat(r["time"]).timestamp(), status=OK,
            exp_cur=_int(r.get("exp_current")), exp_pct=_float(r.get("exp_pct")),
            level=_int(r.get("level")), map=r.get("map") or None, source=self.name)
