"""
capture/winapi.py — the Win32 calls screen capture needs on Windows (via ctypes, no extra packages).

    active_window_rect()      focused window's visible frame, in NATIVE (physical) pixels
    scroll_down_at(x, y, n)   move the pointer and turn the mouse wheel — drives "scrolling capture"

Qt reports screen geometry in device-independent ("logical") pixels while Win32 speaks physical
pixels.  ``native_to_logical`` / ``logical_to_native`` convert between the two for a set of screens
(each ``(QRect geometry, devicePixelRatio)``).  A Qt screen's ``geometry().topLeft()`` is its native
position, only its size is divided by the ratio — so a conversion is anchored at that corner.

Everything that touches ``ctypes.windll`` is imported lazily, so this module can be imported (and the
geometry helpers tested) on any platform.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence, Tuple

from PyQt6.QtCore import QPoint, QRect

Screen = Tuple[QRect, float]          # (logical geometry, device pixel ratio)

# Windows that are the desktop itself: "capture the active window" must not return the whole wallpaper.
_SHELL_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")

DWMWA_EXTENDED_FRAME_BOUNDS = 9
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


# =========================================================================
#  Geometry (pure)
# =========================================================================

def _native_rect(geometry: QRect, ratio: float) -> QRect:
    return QRect(geometry.x(), geometry.y(),
                 max(1, round(geometry.width() * ratio)), max(1, round(geometry.height() * ratio)))


def _pick_screen_native(point: QPoint, screens: Sequence[Screen]) -> Optional[Screen]:
    best, best_dist = None, None
    for geometry, ratio in screens:
        r = _native_rect(geometry, ratio)
        if r.contains(point):
            return geometry, ratio
        dx = max(r.left() - point.x(), 0, point.x() - r.right())
        dy = max(r.top() - point.y(), 0, point.y() - r.bottom())
        dist = dx * dx + dy * dy
        if best_dist is None or dist < best_dist:
            best, best_dist = (geometry, ratio), dist
    return best


def native_to_logical(native: QRect, screens: Sequence[Screen]) -> QRect:
    """A rectangle in physical pixels -> Qt logical coordinates (using the screen under its centre)."""
    picked = _pick_screen_native(native.center(), screens)
    if picked is None:
        return QRect(native)
    geometry, ratio = picked
    ratio = ratio or 1.0
    return QRect(
        round(geometry.x() + (native.x() - geometry.x()) / ratio),
        round(geometry.y() + (native.y() - geometry.y()) / ratio),
        max(1, round(native.width() / ratio)),
        max(1, round(native.height() / ratio)),
    )


def logical_to_native(point: QPoint, screens: Sequence[Screen]) -> QPoint:
    """A Qt logical point -> physical pixels (using the screen that contains it)."""
    for geometry, ratio in screens:
        if geometry.contains(point):
            return QPoint(round(geometry.x() + (point.x() - geometry.x()) * ratio),
                          round(geometry.y() + (point.y() - geometry.y()) * ratio))
    return QPoint(point)


# =========================================================================
#  Win32
# =========================================================================

def available() -> bool:
    return sys.platform == "win32"


def active_window_rect() -> Optional[QRect]:
    """Visible frame of the foreground window in physical pixels, or None (desktop, minimised, error)."""
    if not available():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32                              # type: ignore[attr-defined]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd):
            return None
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value in _SHELL_CLASSES:
            return None

        rect = wintypes.RECT()
        got = False
        try:
            dwm = ctypes.windll.dwmapi                              # type: ignore[attr-defined]
            dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            # The "extended frame" excludes the invisible resize margin / drop shadow Windows 10+ adds.
            got = dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                            ctypes.sizeof(rect)) == 0
        except (OSError, AttributeError):
            got = False
        if not got and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        out = QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        return out if out.width() > 0 and out.height() > 0 else None
    except Exception:                                              # noqa: BLE001 - best effort
        return None


def scroll_down_at(x: int, y: int, notches: int = 3) -> bool:
    """Put the pointer at physical pixel (x, y) and scroll the wheel down *notches* clicks."""
    if not available():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32                              # type: ignore[attr-defined]
        user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_size_t]
        user32.SetCursorPos(int(x), int(y))
        down = (-WHEEL_DELTA) & 0xFFFFFFFF
        for _ in range(max(1, notches)):
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, down, 0)
        return True
    except Exception:                                              # noqa: BLE001
        return False
