"""layered.py - show a PIL RGBA image on a Tk Toplevel with per-pixel alpha (smooth, anti-aliased
edges), via Win32 UpdateLayeredWindow. A transparent-colour key (-transparentcolor) can only cut
pixels fully in or out, so round shapes look jagged; this keeps the soft edge.

Fully transparent pixels let clicks through; the rest go to the Toplevel as usual.
Windows only; set_image() returns False when it can't, so the caller can fall back.
"""
import ctypes
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
ULW_ALPHA = 2


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


_typed = False
_dll = {}


def _libs():
    """Own copies of user32/gdi32 (not the process-wide shared ones), so the argtypes set here don't
    change how other code in the process calls the same functions."""
    if not _dll:
        _dll["u"], _dll["g"] = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    return _dll["u"], _dll["g"]


def _types():
    """64-bit handles: without argtypes ctypes passes them as 32-bit ints (OverflowError)."""
    global _typed
    if _typed:
        return
    u, g = _libs()
    H = wintypes.HANDLE
    u.GetParent.restype, u.GetParent.argtypes = wintypes.HWND, [wintypes.HWND]
    u.GetWindowLongW.restype, u.GetWindowLongW.argtypes = ctypes.c_long, [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongW.restype, u.SetWindowLongW.argtypes = ctypes.c_long, [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    u.GetDC.restype, u.GetDC.argtypes = wintypes.HDC, [wintypes.HWND]
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = wintypes.HDC, [wintypes.HDC]
    g.CreateDIBSection.restype = wintypes.HBITMAP
    g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD]
    g.SelectObject.restype, g.SelectObject.argtypes = wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ]
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    u.UpdateLayeredWindow.restype = wintypes.BOOL
    u.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                      wintypes.HDC, ctypes.c_void_p, wintypes.COLORREF,
                                      ctypes.c_void_p, wintypes.DWORD]
    _typed = True


def hwnd_of(top):
    """The real top-level window handle of a Tk Toplevel (Tk wraps it in a frame window)."""
    _types()
    top.update_idletasks()
    return _libs()[0].GetParent(top.winfo_id())


def set_image(top, img):
    """Show img (PIL RGBA) as the whole Toplevel, alpha per pixel. True if it worked."""
    try:
        _types()
        u, g = _libs()
        hwnd = hwnd_of(top)
        if not hwnd:
            return False
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not ex & WS_EX_LAYERED:
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        w, h = img.size
        # premultiplied BGRA, top-down rows (what UpdateLayeredWindow wants)
        r, gr, b, a = img.convert("RGBA").split()
        from PIL import Image, ImageChops
        r, gr, b = (ImageChops.multiply(c, a) for c in (r, gr, b))
        data = Image.merge("RGBA", (b, gr, r, a)).tobytes()

        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        screen = u.GetDC(None)
        mem = g.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bmp = g.CreateDIBSection(mem, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        if not bmp:
            g.DeleteDC(mem)
            u.ReleaseDC(None, screen)
            return False
        ctypes.memmove(bits, data, len(data))
        old = g.SelectObject(mem, bmp)
        size, src = wintypes.SIZE(w, h), wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)       # AC_SRC_OVER, per-pixel alpha
        ok = u.UpdateLayeredWindow(hwnd, screen, None, ctypes.byref(size), mem, ctypes.byref(src),
                                   0, ctypes.byref(blend), ULW_ALPHA)
        g.SelectObject(mem, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mem)
        u.ReleaseDC(None, screen)
        return bool(ok)
    except Exception:
        return False
