"""
ui/common.py — small helpers shared by the Crabture UI modules.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QImageReader, QPixmap
from PyQt6.QtWidgets import QFrame, QWidget

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tiff"}


def human_size(n: int) -> str:
    """12345 -> '12.1 KB'."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def set_role(widget: QWidget, role: str) -> QWidget:
    """Set the ``role`` dynamic property the theme's stylesheet keys on."""
    widget.setProperty("role", role)
    return widget


def divider(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setProperty("role", "vdivider" if vertical else "divider")
    if vertical:
        line.setFixedWidth(1)
    else:
        line.setFixedHeight(1)
    return line


def make_thumbnail(path: str, th_w: int, th_h: int) -> Optional[Tuple[QIcon, QSize]]:
    """Decode *path* at thumbnail size.  Returns (icon, original size) or None."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    original = QSize(size) if size.isValid() else QSize()
    if size.isValid():
        size.scale(th_w, th_h, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
        img = reader.read()
    else:
        # Format cannot report its size up front: decode fully, then scale.
        img = reader.read()
        if not img.isNull():
            original = img.size()
            img = img.scaled(
                th_w, th_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    if img.isNull():
        return None
    return QIcon(QPixmap.fromImage(img)), original
