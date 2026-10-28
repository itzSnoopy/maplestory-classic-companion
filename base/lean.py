"""Keep each process small. Import this FIRST in every entry point, before numpy or windows-capture.

1. numpy's math library (OpenBLAS) starts one helper thread per CPU core, each with a 32 MB
   scratch buffer: on a 28-core PC that was ~850 MB of RAM in the tracker (measured
   2026-10-23, dev/bench.py where). Our arrays are tiny, so threads gain nothing. One thread.
   The same caps reach Tesseract (it inherits the environment).
2. windows-capture imports OpenCV (~85 MB in RAM) only to save frames to files, which we never
   do. A stand-in module takes its place (same as packaging/rthook_cv2.py in the release build).
"""
import os
import sys
import types

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OMP_THREAD_LIMIT"):
    os.environ.setdefault(_v, "1")

if "cv2" not in sys.modules and not os.environ.get("MAPLE_COMPANION_REAL_CV2"):
    _m = types.ModuleType("cv2")

    def _missing(*a, **k):
        raise RuntimeError("OpenCV is replaced by a stand-in here (lean.py); "
                           "set MAPLE_COMPANION_REAL_CV2=1 to load the real one")

    _m.imwrite = _missing
    sys.modules["cv2"] = _m
