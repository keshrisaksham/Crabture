"""
editor/eyedropper_loupe.py — magnified colour-picker overlay for the eyedropper tool.

* ``EyedropperLoupe`` is a small child widget of the canvas viewport.  It draws
  a circular, pixel-exact magnification of the image around the pointer (with a
  pixel grid and a marker on the pixel that will be picked) and a readout pill
  with the colour as a swatch, HEX and RGB.  It never takes mouse input.
* ``make_eyedropper_cursor()`` builds an eyedropper-shaped cursor whose hot
  spot is the pipette tip, with a halo so it stays visible on any image.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget

CELLS = 17           # pixels shown across the loupe (odd, so one is centred)
CELL_PX = 8          # on-screen size of each magnified pixel
_PAD = 4
_PILL_H = 40
_PILL_W = 168
_GAP = 20            # distance from the pointer


def sample_radius() -> int:
    """How many image pixels the loupe shows on each side of the pointer."""
    return CELLS // 2


class EyedropperLoupe(QWidget):
    """Circular magnifier plus colour readout; follows the pointer."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._d = CELLS * CELL_PX
        self.resize(max(self._d, _PILL_W) + 2 * _PAD, self._d + 2 * _PAD + _PILL_H - 10)
        self._img = QImage()
        self._color: Optional[QColor] = None
        self.hide()

    # -- state -----------------------------------------------------------------

    def set_sample(self, img: QImage) -> None:
        """*img* is a CELLS x CELLS neighbourhood, centre pixel = the one under the pointer."""
        self._img = img
        c = QColor()
        if not img.isNull():
            c = img.pixelColor(CELLS // 2, CELLS // 2)
        self._color = QColor(c.red(), c.green(), c.blue()) if c.isValid() and c.alpha() > 0 else None
        self.update()

    def color(self) -> Optional[QColor]:
        return QColor(self._color) if self._color is not None else None

    def place(self, cursor: QPoint, bounds: QSize) -> None:
        """Put the loupe next to *cursor*, flipping so it stays inside *bounds*."""
        w, h = self.width(), self.height()
        x = cursor.x() + _GAP
        y = cursor.y() + _GAP
        if x + w > bounds.width():
            x = cursor.x() - _GAP - w
        if y + h > bounds.height():
            y = cursor.y() - 14 - h - _GAP        # the cursor icon extends upward from its tip
        x = max(0, min(x, max(0, bounds.width() - w)))
        y = max(0, min(y, max(0, bounds.height() - h)))
        self.move(x, y)
        if not self.isVisible():
            self.show()
        self.raise_()

    # -- painting ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self._d
        left = (self.width() - d) // 2
        circle = QRectF(left, _PAD, d, d)

        # Drop shadow
        p.setPen(Qt.PenStyle.NoPen)
        for i, a in enumerate((26, 18, 10)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawEllipse(circle.adjusted(-2 - i, -1 - i, 2 + i, 3 + i))

        clip = QPainterPath()
        clip.addEllipse(circle)
        p.save()
        p.setClipPath(clip)
        # Transparency checkerboard, then the magnified pixels (nearest neighbour).
        for cy in range(CELLS):
            for cx in range(CELLS):
                p.fillRect(QRect(left + cx * CELL_PX, _PAD + cy * CELL_PX, CELL_PX, CELL_PX),
                           QColor("#C9CDD6") if (cx + cy) % 2 else QColor("#EEF0F5"))
        if not self._img.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(QRect(left, _PAD, d, d), self._img)
        # Pixel grid
        p.setPen(QPen(QColor(0, 0, 0, 38), 1))
        for i in range(1, CELLS):
            p.drawLine(left + i * CELL_PX, _PAD, left + i * CELL_PX, _PAD + d)
            p.drawLine(left, _PAD + i * CELL_PX, left + d, _PAD + i * CELL_PX)
        p.restore()

        # Marker on the pixel that will be picked: dark + light ring so it shows on anything.
        mid = CELLS // 2
        cell = QRect(left + mid * CELL_PX, _PAD + mid * CELL_PX, CELL_PX, CELL_PX)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 230), 2))
        p.drawRect(cell.adjusted(-1, -1, 1, 1))
        p.setPen(QPen(QColor(255, 255, 255, 240), 1))
        p.drawRect(cell)

        # Ring
        p.setPen(QPen(QColor(0, 0, 0, 140), 1))
        p.drawEllipse(circle.adjusted(-2, -2, 2, 2))
        p.setPen(QPen(QColor(255, 255, 255, 245), 3))
        p.drawEllipse(circle.adjusted(0.5, 0.5, -0.5, -0.5))

        # Readout pill
        pill = QRectF((self.width() - _PILL_W) / 2, _PAD + d - 8, _PILL_W, _PILL_H - 4)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(QColor(22, 25, 32, 238))
        p.drawRoundedRect(pill, 10, 10)
        sw = QRectF(pill.left() + 9, pill.top() + (pill.height() - 20) / 2, 20, 20)
        if self._color is not None:
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            p.setBrush(self._color)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(sw)

        big = QFont(p.font())
        big.setPixelSize(13)
        big.setWeight(QFont.Weight.DemiBold)
        small = QFont(p.font())
        small.setPixelSize(10)
        tx = int(sw.right()) + 9
        tw = int(pill.right()) - tx - 8
        if self._color is not None:
            c = self._color
            line1 = c.name().upper()
            line2 = f"RGB {c.red()}, {c.green()}, {c.blue()}"
        else:
            line1, line2 = "No colour", "outside the image"
        p.setFont(big)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(tx, int(pill.top()) + 4, tw, 16), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   QFontMetrics(big).elidedText(line1, Qt.TextElideMode.ElideRight, tw))
        p.setFont(small)
        p.setPen(QColor(190, 196, 210))
        p.drawText(QRect(tx, int(pill.top()) + 20, tw, 14), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   QFontMetrics(small).elidedText(line2, Qt.TextElideMode.ElideRight, tw))
        p.end()


# ---------------------------------------------------------------------------------
#  Cursor
# ---------------------------------------------------------------------------------

_CURSOR_SIZE = 32
_cursor_cache: Optional[QCursor] = None


def make_eyedropper_cursor() -> QCursor:
    """An eyedropper cursor; the hot spot is the tip (bottom-left of the glyph)."""
    global _cursor_cache
    if _cursor_cache is not None:
        return _cursor_cache
    try:
        from crabture.ui import icons
        glyph = icons.pixmap("eyedropper", 26, QColor("#111111"))
        halo = icons.pixmap("eyedropper", 26, QColor("#FFFFFF"))
    except Exception:
        return QCursor(Qt.CursorShape.CrossCursor)
    if glyph.isNull():
        return QCursor(Qt.CursorShape.CrossCursor)

    ratio = glyph.devicePixelRatio() or 1.0
    canvas = QPixmap(int(_CURSOR_SIZE * ratio), int(_CURSOR_SIZE * ratio))
    canvas.setDevicePixelRatio(ratio)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    ox, oy = 3, _CURSOR_SIZE - 3 - 26      # glyph bottom-left sits at (3, size-3)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        p.drawPixmap(ox + dx, oy + dy, halo)
    p.drawPixmap(ox, oy, glyph)
    p.end()
    # The pipette tip is the glyph's bottom-left corner.
    _cursor_cache = QCursor(canvas, ox + 1, oy + 25)
    return _cursor_cache
