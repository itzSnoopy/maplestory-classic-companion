"""Live outputs (sinks). Independent of where the data comes from.

A sink gets `show(text)` with the one-line live summary, and `show("")` when idle, and `close()`
at the end. Files (journal, CSVs, digest) are written by tracker.py/history.py, not by sinks.
None are built in today; the floating window (helper_ui.py) reads status.json instead.
"""


def make_sinks(cfg):
    return []
