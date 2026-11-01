"""One clock for the whole server; tests move `offset` to fake the passing of time."""
import time

offset = 0.0


def now():
    return time.time() + offset
