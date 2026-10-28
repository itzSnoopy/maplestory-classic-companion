"""Observation and Source live in base/contract.py (shared by every layer); this keeps the old import path.

Register new sources in engine/sources/__init__.py. Caveat for sources on the OFFICIAL Classic World
server: anything that reads game memory or intercepts game traffic violates Nexon's terms and risks an
anti-cheat ban. That's the player's explicit call; never add one by default.
"""
from base.contract import ABSENT, OK, UNAVAILABLE, Observation, Source, describe  # noqa: F401
