"""tools.py - what a command line needs from the layers below the hub (the hub may not import them).

Loaded by name through the launcher, like hub/pages.py does for the page host:

    portrait_maker()   fn(frame) -> the character close-up of a saved full frame (PIL image) or None,
                       the same finder the screen source uses for the diary's pictures
"""
import importlib


def portrait_maker():
    return importlib.import_module("launcher.compose").portrait_maker()
