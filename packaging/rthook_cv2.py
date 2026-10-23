# PyInstaller runtime hook. windows-capture imports cv2 at the top but only uses it to save
# frames to disk, which this app never does. OpenCV (~110 MB) is left out of the build, so a
# small stand-in module takes its place.
import sys
import types

try:
    import cv2  # noqa: F401
except ImportError:
    _m = types.ModuleType("cv2")

    def _missing(*a, **k):
        raise RuntimeError("OpenCV isn't included in this build")

    _m.imwrite = _missing
    sys.modules["cv2"] = _m
