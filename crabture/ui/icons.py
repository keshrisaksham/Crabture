"""
ui/icons.py — crisp vector icons for Crabture, drawn with QPainter.

Stroke-based line icons on a 24x24 grid (Lucide/Feather style).  No QtSvg is
used, so the Debian package only needs python3-pyqt6.

    icon(name, size=20, color=None) -> QIcon
    pixmap(name, size=20, color=None) -> QPixmap
    bind_icon(widget_or_action, name, size=20, color=None)
    retint()                       repaint every bound widget/action
    has_icon(name), ICON_NAMES

``color`` may be None (theme text colour, accent on hover, white when a
checkable button is checked, muted when disabled), a QColor / "#RRGGBB", or a
theme token name such as "accent_text" or "text_muted".  Icons resolve their
colours when painted, so a theme change recolours them automatically; for
widgets that cache pixmaps ``bind_icon`` re-applies the icon on the
``theme.signals.changed`` signal.

Run ``python3 ui/icons.py <out_dir>`` to render a contact sheet (light + dark).
"""

from __future__ import annotations

import math
import re
import sys
import weakref
from typing import Dict, List, Optional, Tuple, Union

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QIconEngine,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from crabture.ui import theme

# ---------------------------------------------------------------------------
#  Icon data.  Items: "d" string = stroked path; ("f", d) filled path;
#  ("c", cx, cy, r) / ("cf", ...) circle; ("r", x, y, w, h, rx) / ("rf", ...)
#  rounded rect; ("e", cx, cy, rx, ry) / ("ef", ...) ellipse.
# ---------------------------------------------------------------------------

_ICONS: Dict[str, list] = {
    # -- tools ------------------------------------------------------------
    "select": ["M5 3.5 L19.5 10 L13 12.5 L10.5 19.5 Z"],
    "arrow": ["M6 18 L18 6", "M9 6 H18 V15"],
    "rectangle": [("r", 3.5, 5.5, 17, 13, 2)],
    "highlight": ["M9 14.5 L15.5 8 L18.5 11 L12 17.5 H9 Z", "M15.5 8 L17 6.5 L20 9.5 L18.5 11", "M4 21 H20"],
    "pixelate": [("rf", 3, 3, 6, 6, 1), ("rf", 15, 3, 6, 6, 1), ("rf", 9, 9, 6, 6, 1),
                 ("rf", 3, 15, 6, 6, 1), ("rf", 15, 15, 6, 6, 1)],
    "blur": ["M12 3 C12 3 5.5 10.2 5.5 14.5 A6.5 6.5 0 0 0 18.5 14.5 C18.5 10.2 12 3 12 3 Z",
             "M9 15 A3 3 0 0 0 12 18"],
    "text": ["M5 7 V5 H19 V7", "M12 5 V19", "M9 19 H15"],
    "crop": ["M6 2 V16 A2 2 0 0 0 8 18 H22", "M18 22 V8 A2 2 0 0 0 16 6 H2"],
    "magnifier": [("c", 10.5, 10.5, 6.5), "M21 21 L15.5 15.5", "M7.8 9.5 A3 3 0 0 1 10.2 7.6"],
    "balloon": ["M20 11 A8 7 0 0 1 12 18 C10.7 18 9.5 17.8 8.5 17.4 L4.5 19.5 L5.6 15.9 A6.6 6.6 0 0 1 4 11 A8 7 0 0 1 20 11 Z"],
    "ellipse": [("e", 12, 12, 9, 6.5)],
    "line": ["M5.5 18.5 L18.5 5.5", ("cf", 5.5, 18.5, 1.8), ("cf", 18.5, 5.5, 1.8)],
    "pen": ["M17 3 A2.8 2.8 0 0 1 21 7 L8 20 L2.5 21.5 L4 16 Z", "M14.5 5.5 L18.5 9.5"],
    "step": [("c", 12, 12, 9), "M10 9.5 L12.5 8 V16.5"],
    "spotlight": [("r", 3, 3, 18, 18, 3), ("c", 12, 12, 5), ("cf", 12, 12, 1.6)],
    "sticker": [("c", 12, 12, 9), "M8 14 A4.5 4.5 0 0 0 16 14", ("cf", 9, 9.5, 1), ("cf", 15, 9.5, 1)],
    "eyedropper": ["M2 22 L3 21 H6 L15 12", "M3 21 V18 L12 9",
                   "M15 6 L18.4 2.6 A2.1 2.1 0 1 1 21.4 5.6 L18 9 L18.4 9.4 A2.1 2.1 0 1 1 15.4 12.4 L11.6 8.6 A2.1 2.1 0 1 1 14.6 5.6 Z"],
    # -- file / edit ------------------------------------------------------
    "undo": ["M9 14 L4 9 L9 4", "M4 9 H14.5 A5.5 5.5 0 0 1 14.5 20 H11"],
    "redo": ["M15 14 L20 9 L15 4", "M20 9 H9.5 A5.5 5.5 0 0 0 9.5 20 H13"],
    "open": ["M6 14 L7.5 11.1 A2 2 0 0 1 9.3 10 H20 A2 2 0 0 1 21.9 12.6 L20.3 18.6 A2 2 0 0 1 18.4 20 H4 A2 2 0 0 1 2 18 V5 A2 2 0 0 1 4 3 H7.9 A2 2 0 0 1 9.6 3.9 L10.4 5.1 A2 2 0 0 0 12.1 6 H18 A2 2 0 0 1 20 8 V10"],
    "folder": ["M20 20 A2 2 0 0 0 22 18 V8 A2 2 0 0 0 20 6 H12.1 A2 2 0 0 1 10.4 5.1 L9.6 3.9 A2 2 0 0 0 7.9 3 H4 A2 2 0 0 0 2 5 V18 A2 2 0 0 0 4 20 Z"],
    "save": ["M19 21 H5 A2 2 0 0 1 3 19 V5 A2 2 0 0 1 5 3 H16 L21 8 V19 A2 2 0 0 1 19 21 Z",
             "M17 21 V13 H7 V21", "M7 3 V8 H15"],
    "save_as": ["M12 21 H5 A2 2 0 0 1 3 19 V5 A2 2 0 0 1 5 3 H16 L21 8 V10", "M7 3 V8 H15", "M7 21 V15 H10",
                "M21.3 14.2 L15.6 19.9 L12.6 20.7 L13.4 17.7 L19.1 12 A1.4 1.4 0 0 1 21.3 14.2 Z"],
    "copy": [("r", 9, 9, 12, 12, 2), "M5 15 H4 A2 2 0 0 1 2 13 V4 A2 2 0 0 1 4 2 H13 A2 2 0 0 1 15 4 V5"],
    "paste": ["M9 2 H15 A1 1 0 0 1 16 3 V5 A1 1 0 0 1 15 6 H9 A1 1 0 0 1 8 5 V3 A1 1 0 0 1 9 2 Z",
              "M16 4 H18 A2 2 0 0 1 20 6 V20 A2 2 0 0 1 18 22 H6 A2 2 0 0 1 4 20 V6 A2 2 0 0 1 6 4 H8",
              "M12 11 V17", "M9.5 14.5 L12 17 L14.5 14.5"],
    "clipboard": ["M9 2 H15 A1 1 0 0 1 16 3 V5 A1 1 0 0 1 15 6 H9 A1 1 0 0 1 8 5 V3 A1 1 0 0 1 9 2 Z",
                  "M16 4 H18 A2 2 0 0 1 20 6 V20 A2 2 0 0 1 18 22 H6 A2 2 0 0 1 4 20 V6 A2 2 0 0 1 6 4 H8"],
    "export": ["M12 15 V3", "M7 8 L12 3 L17 8", "M5 14 V19 A2 2 0 0 0 7 21 H17 A2 2 0 0 0 19 19 V14"],
    # -- capture ----------------------------------------------------------
    "capture": ["M14.5 4 H9.5 L7 7 H4 A2 2 0 0 0 2 9 V18 A2 2 0 0 0 4 20 H20 A2 2 0 0 0 22 18 V9 A2 2 0 0 0 20 7 H17 Z",
                ("c", 12, 13, 3.5)],
    "capture_region": ["M3 8 V5 A2 2 0 0 1 5 3 H8", "M16 3 H19 A2 2 0 0 1 21 5 V8",
                       "M21 16 V19 A2 2 0 0 1 19 21 H16", "M8 21 H5 A2 2 0 0 1 3 19 V16", ("cf", 12, 12, 1.8)],
    "capture_fullscreen": ["M15 3 H21 V9", "M9 21 H3 V15", "M21 3 L14 10", "M3 21 L10 14"],
    "capture_window": [("r", 3, 4, 18, 16, 2), "M3 9 H21", ("cf", 6.2, 6.5, 0.9), ("cf", 9.2, 6.5, 0.9)],
    "capture_monitor": [("r", 2, 3.5, 20, 13, 2), "M8 21 H16", "M12 16.5 V21"],
    "capture_scroll": [("r", 6, 2.5, 12, 19, 2), "M9.5 9.5 L12 7 L14.5 9.5", "M9.5 14.5 L12 17 L14.5 14.5"],
    "scan": ["M3 8 V5 A2 2 0 0 1 5 3 H8", "M16 3 H19 A2 2 0 0 1 21 5 V8",
             "M21 16 V19 A2 2 0 0 1 19 21 H16", "M8 21 H5 A2 2 0 0 1 3 19 V16", "M7 12 H17"],
    # -- panels / misc ----------------------------------------------------
    "effects": ["M11 5 L13 11 L19 13 L13 15 L11 21 L9 15 L3 13 L9 11 Z", "M19 3 V7", "M17 5 H21", "M5 3 V6", "M3.5 4.5 H6.5"],
    "shield": ["M20 13 C20 18 16.5 20.5 12.3 21.95 A1 1 0 0 1 11.7 21.95 C7.5 20.5 4 18 4 13 V6 A1 1 0 0 1 5 5 C7 5 9.5 3.8 11.2 2.3 A1.2 1.2 0 0 1 12.8 2.3 C14.5 3.8 17 5 19 5 A1 1 0 0 1 20 6 Z",
               "M9 12 L11 14 L15 10"],
    "layers": ["M12 2.5 L2.5 7.5 L12 12.5 L21.5 7.5 Z", "M2.5 12 L12 17 L21.5 12", "M2.5 16.5 L12 21.5 L21.5 16.5"],
    "gallery": [("r", 3, 7, 18, 14, 2), "M6 3.5 H18", "M3 17 L8 12 L12 16 L15 13 L21 19"],
    "grid": [("r", 3, 3, 18, 18, 2), "M3 9 H21", "M3 15 H21", "M9 3 V21", "M15 3 V21"],
    "view_details": [("r", 3, 4, 18, 16, 2), "M3 9.5 H21", "M10 9.5 V20", "M16 9.5 V20"],
    "view_compact": ["M4 5.5 H20", "M4 9.5 H20", "M4 13.5 H20", "M4 17.5 H20"],
    "sort": ["M7 4 V20", "M3.5 16.5 L7 20 L10.5 16.5", "M17 20 V4", "M13.5 7.5 L17 4 L20.5 7.5"],
    "list": ["M8 6 H21", "M8 12 H21", "M8 18 H21", ("cf", 3.8, 6, 1.2), ("cf", 3.8, 12, 1.2), ("cf", 3.8, 18, 1.2)],
    "eye": ["M2 12 C2 12 5.5 5 12 5 C18.5 5 22 12 22 12 C22 12 18.5 19 12 19 C5.5 19 2 12 2 12 Z", ("c", 12, 12, 3)],
    "eye_off": ["M10.7 5.1 A10 10 0 0 1 12 5 C18.5 5 22 12 22 12 A13.2 13.2 0 0 1 20.3 14.7",
                "M6.6 6.6 A13.5 13.5 0 0 0 2 12 C2 12 5.5 19 12 19 A9.7 9.7 0 0 0 17.4 17.4",
                "M2.5 2.5 L21.5 21.5", "M14.1 14.1 A3 3 0 1 1 9.9 9.9"],
    "trash": ["M3 6 H21", "M19 6 V20 A2 2 0 0 1 17 22 H7 A2 2 0 0 1 5 20 V6", "M8 6 V4 A2 2 0 0 1 10 2 H14 A2 2 0 0 1 16 4 V6",
              "M10 11 V17", "M14 11 V17"],
    "up": ["M12 19 V5", "M5 12 L12 5 L19 12"],
    "down": ["M12 5 V19", "M19 12 L12 19 L5 12"],
    "plus": ["M5 12 H19", "M12 5 V19"],
    "minus": ["M5 12 H19"],
    "zoom_in": [("c", 10.5, 10.5, 6.5), "M21 21 L15.5 15.5", "M10.5 8 V13", "M8 10.5 H13"],
    "zoom_out": [("c", 10.5, 10.5, 6.5), "M21 21 L15.5 15.5", "M8 10.5 H13"],
    "fit": ["M4 14 H10 V20", "M20 10 H14 V4", "M14 10 L21 3", "M3 21 L10 14"],
    "actual_size": ["M5 9.5 L7.5 7.5 V16.5", ("cf", 12, 10, 1.1), ("cf", 12, 14.5, 1.1), "M16 9.5 L18.5 7.5 V16.5"],
    "sun": [("c", 12, 12, 4), "M12 2 V4", "M12 20 V22", "M4.9 4.9 L6.3 6.3", "M17.7 17.7 L19.1 19.1",
            "M2 12 H4", "M20 12 H22", "M6.3 17.7 L4.9 19.1", "M19.1 4.9 L17.7 6.3"],
    "moon": ["M12 3 A6 6 0 0 0 21 12 A9 9 0 1 1 12 3 Z"],
    "rename": [("r", 2.5, 6.5, 19, 11, 2), "M12 9.5 V14.5", "M10 9.5 H14", "M10 14.5 H14"],
    "settings": ["M20 7 H11", "M14 17 H5", ("c", 17, 17, 3), ("c", 7, 7, 3)],
    "more": [("cf", 5, 12, 1.7), ("cf", 12, 12, 1.7), ("cf", 19, 12, 1.7)],
    "check": ["M20 6 L9 17 L4 12"],
    "close": ["M18 6 L6 18", "M6 6 L18 18"],
    "info": [("c", 12, 12, 9), "M12 16.5 V11.5", ("cf", 12, 7.9, 1.1)],
    "warning": ["M21.7 18 L13.7 4 A2 2 0 0 0 10.3 4 L2.3 18 A2 2 0 0 0 4 21 H20 A2 2 0 0 0 21.7 18 Z", "M12 9 V13",
                ("cf", 12, 16.8, 1.1)],
    "image": [("r", 3, 3, 18, 18, 2), ("c", 9, 9, 2), "M21 15 L17.9 11.9 A2 2 0 0 0 15.1 11.9 L6 21"],
    "keyboard": [("r", 2, 5, 20, 14, 2), ("cf", 6.5, 9.2, 0.9), ("cf", 10.2, 9.2, 0.9), ("cf", 13.8, 9.2, 0.9),
                 ("cf", 17.5, 9.2, 0.9), ("cf", 6.5, 12.6, 0.9), ("cf", 17.5, 12.6, 0.9), "M9 15.4 H15"],
    "timer": ["M10 2 H14", "M12 14 L15 11", ("c", 12, 14, 8)],
    "wand": ["M20.6 3.4 L19.4 2.2 A1.2 1.2 0 0 0 17.7 2.2 L2.4 17.5 A1.2 1.2 0 0 0 2.4 19.2 L3.6 20.4 A1.2 1.2 0 0 0 5.3 20.4 L20.6 5.1 A1.2 1.2 0 0 0 20.6 3.4 Z",
             "M14 7 L17 10", "M5 3 V7", "M3 5 H7", "M19 14 V18", "M17 16 H21"],
    "palette": ["M12 22 A1 1 0 0 1 12 2 A10 9 0 0 1 22 11 A5 5 0 0 1 17 16 H14.75 A1.75 1.75 0 0 0 13.35 18.8 L13.65 19.2 A1.75 1.75 0 0 1 12.25 22 Z",
                ("cf", 13.5, 6.5, 1), ("cf", 17.5, 10.5, 1), ("cf", 8.5, 7.5, 1), ("cf", 6.5, 12.5, 1)],
    "history": ["M3 12 A9 9 0 1 0 12 3 A9.75 9.75 0 0 0 5.3 5.7 L3 8", "M3 3 V8 H8", "M12 7 V12 L16 14"],
    "chevron_down": ["M6 9 L12 15 L18 9"],
    "chevron_right": ["M9 6 L15 12 L9 18"],
    "search": [("c", 10.5, 10.5, 6.5), "M21 21 L15.5 15.5"],
    "refresh": ["M3 12 A9 9 0 0 1 12 3 A9.75 9.75 0 0 1 18.7 5.7 L21 8", "M21 3 V8 H16",
                "M21 12 A9 9 0 0 1 12 21 A9.75 9.75 0 0 1 5.3 18.3 L3 16", "M8 16 H3 V21"],
    # -- brand ------------------------------------------------------------
    "logo_mark": [("ef", 12, 15, 7.5, 5), ("cf", 8.6, 8.6, 1.4), ("cf", 15.4, 8.6, 1.4),
                  "M9.3 10.6 L8.8 9.4", "M14.7 10.6 L15.2 9.4",
                  "M5.2 13 C3.2 12.6 2 11 2.2 8.8 C2.4 7 3.5 6 4.6 6.4 C3.8 7.4 4.6 8.4 6 8.6 L6.7 11.4",
                  "M18.8 13 C20.8 12.6 22 11 21.8 8.8 C21.6 7 20.5 6 19.4 6.4 C20.2 7.4 19.4 8.4 18 8.6 L17.3 11.4",
                  "M6 18 L3.6 20.5", "M8.8 19.6 L7.6 22", "M15.2 19.6 L16.4 22", "M18 18 L20.4 20.5"],
}

ICON_NAMES: Tuple[str, ...] = tuple(_ICONS)


def has_icon(name: str) -> bool:
    return name in _ICONS


# ---------------------------------------------------------------------------
#  SVG-path parsing (M L H V C S Q T A Z, absolute and relative)
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _arc_to_cubics(x1, y1, rx, ry, phi_deg, fa, fs, x2, y2):
    """SVG endpoint arc -> list of cubic segments (c1x,c1y,c2x,c2y,x,y)."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x1, y1, x2, y2, x2, y2)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2.0
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2.0

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dth > 0:
        dth -= 2 * math.pi
    elif fs and dth < 0:
        dth += 2 * math.pi
    n = max(1, int(math.ceil(abs(dth) / (math.pi / 2) - 1e-9)))
    delta = dth / n
    t = 4.0 / 3.0 * math.tan(delta / 4.0)
    out = []
    a = th1
    for _ in range(n):
        ca, sa = math.cos(a), math.sin(a)
        cb, sb = math.cos(a + delta), math.sin(a + delta)
        p1 = (ca - t * sa, sa + t * ca)
        p2 = (cb + t * sb, sb - t * cb)
        p3 = (cb, sb)
        pts = []
        for (ux, uy) in (p1, p2, p3):
            px, py = ux * rx, uy * ry
            pts.append((cp * px - sp * py + cx, sp * px + cp * py + cy))
        out.append((pts[0][0], pts[0][1], pts[1][0], pts[1][1], pts[2][0], pts[2][1]))
        a += delta
    return out


def _parse_path(d: str) -> QPainterPath:
    tokens = _TOKEN.findall(d)
    path = QPainterPath()
    i = 0
    cx = cy = sx = sy = 0.0
    last_c = None   # previous cubic control point (for S)
    last_q = None
    cmd = ""

    def nums(count):
        nonlocal i
        vals = [float(tokens[i + k]) for k in range(count)]
        i += count
        return vals

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                path.closeSubpath()
                cx, cy = sx, sy
                last_c = last_q = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = nums(2)
            if rel:
                x, y = cx + x, cy + y
            path.moveTo(x, y)
            cx, cy, sx, sy = x, y, x, y
            cmd = "l" if rel else "L"
            last_c = last_q = None
        elif c == "L":
            x, y = nums(2)
            if rel:
                x, y = cx + x, cy + y
            path.lineTo(x, y)
            cx, cy = x, y
            last_c = last_q = None
        elif c == "H":
            (x,) = nums(1)
            x = cx + x if rel else x
            path.lineTo(x, cy)
            cx = x
            last_c = last_q = None
        elif c == "V":
            (y,) = nums(1)
            y = cy + y if rel else y
            path.lineTo(cx, y)
            cy = y
            last_c = last_q = None
        elif c == "C":
            x1, y1, x2, y2, x, y = nums(6)
            if rel:
                x1, y1, x2, y2, x, y = cx + x1, cy + y1, cx + x2, cy + y2, cx + x, cy + y
            path.cubicTo(x1, y1, x2, y2, x, y)
            last_c = (x2, y2)
            cx, cy = x, y
            last_q = None
        elif c == "S":
            x2, y2, x, y = nums(4)
            if rel:
                x2, y2, x, y = cx + x2, cy + y2, cx + x, cy + y
            x1, y1 = (2 * cx - last_c[0], 2 * cy - last_c[1]) if last_c else (cx, cy)
            path.cubicTo(x1, y1, x2, y2, x, y)
            last_c = (x2, y2)
            cx, cy = x, y
            last_q = None
        elif c == "Q":
            x1, y1, x, y = nums(4)
            if rel:
                x1, y1, x, y = cx + x1, cy + y1, cx + x, cy + y
            path.quadTo(x1, y1, x, y)
            last_q = (x1, y1)
            cx, cy = x, y
            last_c = None
        elif c == "T":
            x, y = nums(2)
            if rel:
                x, y = cx + x, cy + y
            x1, y1 = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
            path.quadTo(x1, y1, x, y)
            last_q = (x1, y1)
            cx, cy = x, y
            last_c = None
        elif c == "A":
            rx, ry, rot, fa, fs, x, y = nums(7)
            if rel:
                x, y = cx + x, cy + y
            for (a1, b1, a2, b2, ex, ey) in _arc_to_cubics(cx, cy, rx, ry, rot, int(fa), int(fs), x, y):
                path.cubicTo(a1, b1, a2, b2, ex, ey)
            cx, cy = x, y
            last_c = last_q = None
        else:  # unknown command: bail out of the loop
            break
    return path


_Shape = Tuple[QPainterPath, bool]          # (path, filled)
_COMPILED: Dict[str, List[_Shape]] = {}


def _compile(name: str) -> List[_Shape]:
    cached = _COMPILED.get(name)
    if cached is not None:
        return cached
    shapes: List[_Shape] = []
    for item in _ICONS[name]:
        if isinstance(item, str):
            shapes.append((_parse_path(item), False))
            continue
        kind = item[0]
        path = QPainterPath()
        filled = kind.endswith("f") and kind != "f"
        if kind == "f":
            path = _parse_path(item[1])
            filled = True
        elif kind in ("c", "cf"):
            _, x, y, r = item
            path.addEllipse(QPointF(x, y), r, r)
        elif kind in ("e", "ef"):
            _, x, y, rx, ry = item
            path.addEllipse(QPointF(x, y), rx, ry)
        elif kind in ("r", "rf"):
            _, x, y, w, h, r = item
            path.addRoundedRect(QRectF(x, y, w, h), r, r)
        shapes.append((path, filled))
    _COMPILED[name] = shapes
    return shapes


# ---------------------------------------------------------------------------
#  Rendering
# ---------------------------------------------------------------------------

ColorSpec = Union[None, str, QColor]
STROKE = 1.75      # in 24-unit grid


def _resolve_color(spec: ColorSpec, fallback_token: str) -> QColor:
    t = theme.tokens()
    if spec is None:
        return QColor(t[fallback_token])
    if isinstance(spec, QColor):
        return QColor(spec)
    if isinstance(spec, str) and spec in t:
        return QColor(t[spec])
    return QColor(spec)


def _render(name: str, width: int, height: int, color: QColor) -> QImage:
    img = QImage(max(1, width), max(1, height), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    side = min(width, height)
    scale = side / 24.0
    p.translate((width - side) / 2.0, (height - side) / 2.0)
    p.scale(scale, scale)
    # keep strokes at least ~1.4 physical pixels so small sizes stay legible
    stroke = max(STROKE, 1.45 / scale)
    pen = QPen(color, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    for path, filled in _compile(name):
        if filled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
        else:
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.end()
    return img


def _mode_color(spec: ColorSpec, mode: QIcon.Mode, state: QIcon.State) -> QColor:
    if mode == QIcon.Mode.Disabled:
        col = _resolve_color(spec, "text_disabled") if spec is None else _resolve_color(spec, "text")
        if spec is not None:
            col.setAlphaF(0.4)
        return col
    if state == QIcon.State.On:
        return _resolve_color(spec, "accent_text")
    if mode == QIcon.Mode.Active and spec is None:
        return _resolve_color(None, "accent_fg")
    return _resolve_color(spec, "text")


_cache: Dict[tuple, QPixmap] = {}


def _pixmap_for(name: str, w: int, h: int, mode: QIcon.Mode, state: QIcon.State, spec: ColorSpec) -> QPixmap:
    color = _mode_color(spec, mode, state)
    key = (name, w, h, color.rgba())
    pm = _cache.get(key)
    if pm is None:
        if len(_cache) > 2000:
            _cache.clear()
        pm = QPixmap.fromImage(_render(name, w, h, color))
        _cache[key] = pm
    return pm


class _IconEngine(QIconEngine):
    """Renders at the requested size/mode/state with the *current* theme."""

    def __init__(self, name: str, spec: ColorSpec = None) -> None:
        super().__init__()
        self._name = name
        self._spec = spec

    def clone(self) -> "_IconEngine":
        return _IconEngine(self._name, self._spec)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return QPixmap(_pixmap_for(self._name, size.width(), size.height(), mode, state, self._spec))

    def paint(self, painter: QPainter, rect, mode: QIcon.Mode, state: QIcon.State) -> None:
        dpr = painter.device().devicePixelRatio() if painter.device() is not None else 1.0
        w, h = max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr))
        pm = QPixmap(_pixmap_for(self._name, w, h, mode, state, self._spec))
        pm.setDevicePixelRatio(dpr)
        painter.drawPixmap(rect.topLeft(), pm)

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:
        return size

    def key(self) -> str:
        return "crabture-icon"


def icon(name: str, size: int = 20, color: ColorSpec = None) -> QIcon:
    """A theme-aware QIcon.  *size* is only a hint; the icon scales freely."""
    if name not in _ICONS:
        raise KeyError(f"unknown icon {name!r}")
    return QIcon(_IconEngine(name, color))


def pixmap(name: str, size: int = 20, color: ColorSpec = None) -> QPixmap:
    """A pixmap of *name* at *size* logical pixels (device-pixel-ratio aware)."""
    dpr = 1.0
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        dpr = max(1.0, screen.devicePixelRatio())
    px = max(1, round(size * dpr))
    pm = QPixmap(_pixmap_for(name, px, px, QIcon.Mode.Normal, QIcon.State.Off, color))
    pm.setDevicePixelRatio(dpr)
    return pm


# ---------------------------------------------------------------------------
#  Binding helper: re-apply icons on theme changes
# ---------------------------------------------------------------------------

_bound: List[Tuple["weakref.ref", str, int, ColorSpec]] = []


def bind_icon(target, name: str, size: int = 20, color: ColorSpec = None) -> None:
    """``target.setIcon(icon(...))`` now, and again whenever the theme changes.

    *target* is any object with ``setIcon`` (QAbstractButton, QAction, QMenu…)
    held through a weak reference.  Also sets the icon size on widgets that
    support ``setIconSize``.
    """
    target.setIcon(icon(name, size, color))
    if hasattr(target, "setIconSize"):
        target.setIconSize(QSize(size, size))
    _bound.append((weakref.ref(target), name, size, color))


def retint() -> None:
    """Re-apply every bound icon (called automatically on theme change)."""
    _cache.clear()
    alive = []
    for ref, name, size, color in _bound:
        obj = ref()
        if obj is None:
            continue
        try:
            obj.setIcon(icon(name, size, color))
            if hasattr(obj, "update"):
                obj.update()
        except RuntimeError:       # underlying C++ object already deleted
            continue
        alive.append((ref, name, size, color))
    _bound[:] = alive


def _on_theme_changed(_name: str) -> None:
    retint()


theme.signals.changed.connect(_on_theme_changed)


# ---------------------------------------------------------------------------
#  Contact sheet (python3 ui/icons.py OUT_DIR)
# ---------------------------------------------------------------------------

def contact_sheet(theme_name: str, scale_sizes=(16, 24, 48), cols: int = 8) -> QImage:
    from PyQt6.QtWidgets import QApplication

    theme.apply_theme(QApplication.instance(), theme_name)
    t = theme.tokens()
    cell_w, cell_h = 128, 110
    rows = math.ceil(len(ICON_NAMES) / cols)
    img = QImage(cols * cell_w, rows * cell_h, QImage.Format.Format_ARGB32)
    img.fill(QColor(t["surface"]))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    for idx, name in enumerate(ICON_NAMES):
        x0, y0 = (idx % cols) * cell_w, (idx // cols) * cell_h
        p.setPen(QColor(t["border"]))
        p.drawRect(x0, y0, cell_w - 1, cell_h - 1)
        x = x0 + 10
        for s in scale_sizes:
            p.drawImage(x, y0 + 12 + (48 - s) // 2, _render(name, s, s, QColor(t["text"])))
            x += s + 10
        p.setPen(QColor(t["text_muted"]))
        f = p.font()
        f.setPixelSize(11)
        p.setFont(f)
        p.drawText(x0 + 8, y0 + cell_h - 28, cell_w - 16, 20, int(Qt.AlignmentFlag.AlignLeft), name)
    p.end()
    return img


def _main() -> None:
    import os
    from PyQt6.QtWidgets import QApplication

    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    for name in ("light", "dark"):
        path = os.path.join(out, f"icons_{name}.png")
        contact_sheet(name).save(path)
        print(path)


if __name__ == "__main__":
    _main()
