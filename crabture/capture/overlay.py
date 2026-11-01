"""
capture/overlay.py — On-screen UI used while capturing.

  • RegionSelector        – frozen-screen overlay for ONE screen (drag a rectangle).
  • RegionSelectionSession – creates one RegionSelector per screen and reports the
                            first confirmed / cancelled result.
  • MonitorPicker / MonitorPickSession – "click the monitor to capture" overlays.
  • ScrollCaptureStrip    – small always-on-top control strip for scrolling capture.
  • RegionOutline         – four thin windows drawn just OUTSIDE a region (X11 only).
  • CountdownOverlay      – tiny "3… 2… 1…" box shown during a capture delay.

Coordinates
-----------
"Logical" coordinates are Qt's device-independent global coordinates (what
``QScreen.geometry()`` uses).  "Physical" coordinates are pixels of the frozen
desktop pixmap.  The mapping is uniform::

    physical = (logical - origin) * scale

where *origin* is the logical top-left of the virtual desktop and *scale* is
physical pixels per logical pixel.  The pure helpers below implement that and
are unit-tested without any display.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


# =========================================================================
#  Pure geometry helpers (no display needed)
# =========================================================================

def logical_to_physical(
    rect: QRect,
    origin: QPoint,
    scale: float,
    bounds: Optional[QSize] = None,
) -> QRect:
    """Map a logical global *rect* to pixels of the frozen desktop pixmap.

    The result is clamped to ``QRect(0, 0, bounds.width(), bounds.height())``
    when *bounds* is given.
    """
    x0 = round((rect.x() - origin.x()) * scale)
    y0 = round((rect.y() - origin.y()) * scale)
    x1 = round((rect.x() + rect.width() - origin.x()) * scale)
    y1 = round((rect.y() + rect.height() - origin.y()) * scale)
    out = QRect(x0, y0, max(0, x1 - x0), max(0, y1 - y0))
    if bounds is not None:
        out = out.intersected(QRect(0, 0, bounds.width(), bounds.height()))
    return out


def normalized_drag_rect(p0: QPoint, p1: QPoint, square: bool, bounds: QRect) -> QRect:
    """Rectangle spanned by a drag from *p0* to *p1*, kept inside *bounds*.

    With *square* the shorter side grows to match the longer one, keeping the
    anchor corner at *p0* and shrinking the square if it would leave *bounds*.
    """
    x0 = min(max(p0.x(), bounds.left()), bounds.right() + 1)
    y0 = min(max(p0.y(), bounds.top()), bounds.bottom() + 1)
    x1 = min(max(p1.x(), bounds.left()), bounds.right() + 1)
    y1 = min(max(p1.y(), bounds.top()), bounds.bottom() + 1)
    dx, dy = x1 - x0, y1 - y0
    if square:
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        side = max(abs(dx), abs(dy))
        # Room available in the drag direction.
        room_x = (bounds.right() + 1 - x0) if sx > 0 else (x0 - bounds.left())
        room_y = (bounds.bottom() + 1 - y0) if sy > 0 else (y0 - bounds.top())
        side = min(side, room_x, room_y)
        dx, dy = sx * side, sy * side
    left, top = min(x0, x0 + dx), min(y0, y0 + dy)
    return QRect(left, top, abs(dx), abs(dy))


# Handle ids (clockwise from top-left) + "move" for the interior.
TL, T, TR, R, BR, B, BL, L, MOVE = range(9)
_HANDLE_CURSORS = {
    TL: Qt.CursorShape.SizeFDiagCursor, BR: Qt.CursorShape.SizeFDiagCursor,
    TR: Qt.CursorShape.SizeBDiagCursor, BL: Qt.CursorShape.SizeBDiagCursor,
    T: Qt.CursorShape.SizeVerCursor, B: Qt.CursorShape.SizeVerCursor,
    L: Qt.CursorShape.SizeHorCursor, R: Qt.CursorShape.SizeHorCursor,
    MOVE: Qt.CursorShape.SizeAllCursor,
}


def handle_points(rect: QRect) -> List[QPoint]:
    """Centres of the 8 handles in id order TL, T, TR, R, BR, B, BL, L."""
    x0, y0 = rect.left(), rect.top()
    x1, y1 = rect.x() + rect.width(), rect.y() + rect.height()
    xm, ym = (x0 + x1) // 2, (y0 + y1) // 2
    return [
        QPoint(x0, y0), QPoint(xm, y0), QPoint(x1, y0), QPoint(x1, ym),
        QPoint(x1, y1), QPoint(xm, y1), QPoint(x0, y1), QPoint(x0, ym),
    ]


def hit_test(rect: QRect, pos: QPoint, margin: int = 8) -> Optional[int]:
    """Which handle (or ``MOVE`` for the interior) is under *pos*, if any."""
    if rect.isNull():
        return None
    best, best_d = None, margin * margin + 1
    for hid, pt in enumerate(handle_points(rect)):
        d = (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2
        if d < best_d:
            best, best_d = hid, d
    if best is not None:
        return best
    if rect.contains(pos):
        return MOVE
    return None


def resize_with_handle(
    rect: QRect, handle: int, pos: QPoint, bounds: QRect, min_size: int = 2,
) -> QRect:
    """New rectangle after dragging *handle* to *pos* (clamped to *bounds*)."""
    left, top = rect.x(), rect.y()
    right, bottom = rect.x() + rect.width(), rect.y() + rect.height()
    px = min(max(pos.x(), bounds.left()), bounds.right() + 1)
    py = min(max(pos.y(), bounds.top()), bounds.bottom() + 1)
    if handle in (TL, L, BL):
        left = px
    if handle in (TR, R, BR):
        right = px
    if handle in (TL, T, TR):
        top = py
    if handle in (BL, B, BR):
        bottom = py
    # Allow dragging past the opposite edge: normalise.
    l, r = min(left, right), max(left, right)
    t, b = min(top, bottom), max(top, bottom)
    if r - l < min_size:
        r = l + min_size
    if b - t < min_size:
        b = t + min_size
    return QRect(l, t, r - l, b - t)


def move_rect_within(rect: QRect, delta: QPoint, bounds: QRect) -> QRect:
    """Translate *rect* by *delta*, keeping it inside *bounds*."""
    out = rect.translated(delta)
    dx = dy = 0
    if out.left() < bounds.left():
        dx = bounds.left() - out.left()
    if out.x() + out.width() > bounds.x() + bounds.width():
        dx = (bounds.x() + bounds.width()) - (out.x() + out.width())
    if out.top() < bounds.top():
        dy = bounds.top() - out.top()
    if out.y() + out.height() > bounds.y() + bounds.height():
        dy = (bounds.y() + bounds.height()) - (out.y() + out.height())
    return out.translated(dx, dy)


# =========================================================================
#  Frozen-screen overlays
# =========================================================================

PILL_BG = QColor(20, 22, 28, 215)
ACCENT = QColor("#00BFFF")


def button_rects(sel: QRect, bounds: QRect, w: int = 104, h: int = 30, gap: int = 8,
                 margin: int = 10) -> Tuple[QRect, QRect]:
    """Where the ✔ Capture and ✖ Cancel buttons go for selection *sel*.

    Preferably just below the selection's right edge; above it when the bottom is
    too close to the screen edge; inside it when the selection fills the screen.
    """
    total = w * 2 + gap
    x = min(max(sel.right() + 1 - total, bounds.left() + margin), bounds.right() + 1 - total - margin)
    y = sel.bottom() + 1 + margin
    if y + h > bounds.bottom() - margin:
        y = sel.top() - margin - h
    if y < bounds.top() + margin:
        y = max(bounds.top() + margin, min(sel.bottom() - margin - h, bounds.bottom() - margin - h))
    ok = QRect(x, y, w, h)
    return ok, QRect(x + w + gap, y, w, h)


class _FrozenOverlay(QWidget):
    """Base of the overlays that show a frozen picture of ONE screen.

    The frozen desktop is cut to this screen and scaled ONCE to the widget's device
    pixels (and a dimmed copy made), so painting is a plain blit — scaling the whole
    desktop on every mouse move is what used to make the selector stutter on 4K.
    """

    def __init__(self, pixmap: QPixmap, origin: QPoint, scale: float, screen, parent=None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._origin = QPoint(origin)
        self._scale = scale if scale > 0 else 1.0
        self._screen = screen
        self._geom: QRect = screen.geometry()
        self._bright: Optional[QPixmap] = None
        self._dim: Optional[QPixmap] = None
        self._cache_key = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(self._geom)

    # -- geometry ----------------------------------------------------------

    def _bounds(self) -> QRect:
        return QRect(0, 0, self.width(), self.height())

    def _to_source(self, local: QRect) -> QRect:
        """Local widget rect → pixels of the frozen desktop pixmap."""
        return logical_to_physical(
            local.translated(self._geom.topLeft()), self._origin, self._scale, self._pixmap.size())

    def _ensure_cache(self) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        dpr = self.devicePixelRatioF()
        key = (w, h, round(dpr, 3))
        if key == self._cache_key and self._bright is not None:
            return
        src = self._pixmap.copy(self._to_source(self._bounds()))
        bright = src.scaled(round(w * dpr), round(h * dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        bright.setDevicePixelRatio(dpr)
        dim = QPixmap(bright)
        p = QPainter(dim)
        p.fillRect(QRect(0, 0, w, h), QColor(0, 0, 0, 120))
        p.end()
        self._bright, self._dim, self._cache_key = bright, dim, key

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._cache_key = None
        super().resizeEvent(event)

    # -- helpers -----------------------------------------------------------

    def _pill(self, p: QPainter, box: QRect, text: str, bg: QColor = PILL_BG,
              fg: QColor = QColor("#FFFFFF"), radius: int = 8) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(box, radius, radius)
        p.setPen(fg)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _text_box(self, p: QPainter, text: str, pad_x: int = 14, pad_y: int = 7) -> QRect:
        fm = p.fontMetrics()
        return QRect(0, 0, fm.horizontalAdvance(text) + 2 * pad_x, fm.height() + 2 * pad_y)

    def enterEvent(self, event) -> None:  # noqa: N802
        # Hand keyboard focus to the screen the pointer is on (X11; Wayland decides itself).
        if not self.isActiveWindow():
            self.activateWindow()
        self.setFocus()
        super().enterEvent(event)


# ---------------------------------------------------------------------------
#  Region selection
# ---------------------------------------------------------------------------

class RegionSelector(_FrozenOverlay):
    """Full-screen "frozen" overlay for one screen.

    Emits ``selected(QRect)`` with the chosen rectangle in *global logical*
    coordinates, or ``cancelled()``.  Drag to select; afterwards drag the handles /
    interior to adjust.  The ✔ button, Enter or a double-click confirms; Esc, the ✖
    button or a right-click cancels; Shift while dragging forces a square; the arrow
    keys nudge by one pixel.  With ``confirm='release'`` the capture happens as soon
    as the mouse button is released.
    """

    selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()
    selection_started = pyqtSignal()          # a new drag began here (other screens drop theirs)
    confirm_requested = pyqtSignal()          # Enter pressed (the session picks the screen that has a selection)
    nudge_requested = pyqtSignal(QPoint)

    MIN_SELECTION = 3
    HANDLE = 8
    LOUPE_CELLS = 11
    LOUPE_ZOOM = 10

    def __init__(self, pixmap: QPixmap, origin: QPoint, scale: float, screen, parent=None,
                 confirm: str = "button") -> None:
        super().__init__(pixmap, origin, scale, screen, parent)
        self._confirm_mode = confirm
        self._sel = QRect()
        self._anchor = QPoint()
        self._mode: Optional[str] = None      # "new" | "resize" | "move"
        self._handle: Optional[int] = None
        self._last = QPoint()
        self._cursor_pos = QPoint(-1, -1)
        self._btn_down: Optional[str] = None  # "ok" | "cancel" while a button is pressed

    # -- selection ---------------------------------------------------------

    def selection_global(self) -> QRect:
        """Current selection in global logical coordinates."""
        return self._sel.translated(self._geom.topLeft())

    def has_selection(self) -> bool:
        return self._sel.width() >= self.MIN_SELECTION and self._sel.height() >= self.MIN_SELECTION

    def clear_selection(self) -> None:
        if not self._sel.isNull():
            self._set_selection(QRect())

    def _set_selection(self, new: QRect) -> None:
        old = self._sel
        self._sel = new
        # Repaint both rectangles with room for handles, readout, buttons and loupe.
        dirty = old.united(new) if not old.isNull() and not new.isNull() else (old if new.isNull() else new)
        if dirty.isNull():
            self.update()
        else:
            self.update(dirty.adjusted(-60, -60, 60, 110))
        if old.isNull() != new.isNull():
            self.update()

    def confirm(self) -> None:
        if self.has_selection():
            self.selected.emit(self.selection_global())

    def nudge(self, d: QPoint) -> None:
        if self.has_selection():
            self._set_selection(move_rect_within(self._sel, d, self._bounds()))

    def _buttons(self) -> Optional[Tuple[QRect, QRect]]:
        if self._confirm_mode == "release" or self._mode is not None or not self.has_selection():
            return None
        return button_rects(self._sel, self._bounds())

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        self._ensure_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._dim)
        font = QFont(self.font())
        font.setPointSize(10)
        p.setFont(font)

        if self._sel.width() > 0 and self._sel.height() > 0:
            dpr = self.devicePixelRatioF()
            src = QRectF(self._sel.x() * dpr, self._sel.y() * dpr, self._sel.width() * dpr, self._sel.height() * dpr)
            p.drawPixmap(QRectF(self._sel), self._bright, src)
            p.setPen(QPen(ACCENT, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._sel.adjusted(0, 0, -1, -1))
            if self.has_selection():
                p.setBrush(QColor("#FFFFFF"))
                p.setPen(QPen(QColor("#0078D4"), 1))
                h = self.HANDLE
                for pt in handle_points(self._sel):
                    p.drawRect(pt.x() - h // 2, pt.y() - h // 2, h, h)
            self._paint_readout(p)
            btns = self._buttons()
            if btns is not None:
                bold = QFont(font)
                bold.setBold(True)
                p.setFont(bold)
                self._pill(p, btns[0], "✔  Capture", QColor("#00A152"))
                self._pill(p, btns[1], "✖  Cancel", QColor("#E53935"))
                p.setFont(font)
        elif self._mode is None:
            self._paint_hint(p)
        if self._cursor_pos.x() >= 0 and self._btn_down is None and self._mode in (None, "new"):
            self._paint_loupe(p)

    def _paint_readout(self, p: QPainter) -> None:
        w = round(self._sel.width() * self._scale)
        h = round(self._sel.height() * self._scale)
        text = f"{w} × {h}"
        bold = QFont(p.font())
        bold.setBold(True)
        p.setFont(bold)
        box = self._text_box(p, text, 8, 3)
        x, y = self._sel.left(), self._sel.top() - box.height() - 6
        if y < 0:
            y = self._sel.top() + 6
            x = self._sel.left() + 6
        box.moveTopLeft(QPoint(x, y))
        self._pill(p, box, text, QColor(0, 0, 0, 190), radius=5)

    def _paint_hint(self, p: QPainter) -> None:
        if self._confirm_mode == "release":
            text = "Drag to capture a region  •  Release to capture  •  Esc / right-click: cancel  •  Shift: square"
        else:
            text = "Drag to select a region  •  Enter / ✔: capture  •  Esc / right-click: cancel  •  Shift: square"
        big = QFont(p.font())
        big.setPointSize(11)
        p.setFont(big)
        box = self._text_box(p, text, 14, 7)
        box.moveTopLeft(QPoint((self.width() - box.width()) // 2, 24))
        self._pill(p, box, text)
        p.setFont(QFont(self.font()))

    def _paint_loupe(self, p: QPainter) -> None:
        """Magnified pixels around the pointer, with its coordinates and colour."""
        n, z = self.LOUPE_CELLS, self.LOUPE_ZOOM
        size = n * z
        centre = self._to_source(QRect(self._cursor_pos, QSize(1, 1))).topLeft()
        r = n // 2
        sample = self._pixmap.copy(QRect(centre.x() - r, centre.y() - r, n, n))
        if sample.isNull():
            return
        sample.setDevicePixelRatio(1.0)
        pad = 18
        x = self._cursor_pos.x() + pad
        y = self._cursor_pos.y() + pad
        if x + size + 4 > self.width():
            x = self._cursor_pos.x() - pad - size
        if y + size + 26 > self.height():
            y = self._cursor_pos.y() - pad - size - 26
        box = QRect(x, y, size, size)
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 230))
        p.drawRoundedRect(box.adjusted(-3, -3, 3, 26), 6, 6)
        p.drawPixmap(box, sample)
        mid = QRect(x + r * z, y + r * z, z, z)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#000000"), 3))
        p.drawRect(mid)
        p.setPen(QPen(QColor("#FFFFFF"), 1))
        p.drawRect(mid)
        img = sample.toImage()
        col = img.pixelColor(r, r) if not img.isNull() else QColor()
        small = QFont(p.font())
        small.setPointSize(8)
        p.setFont(small)
        p.setPen(QColor("#FFFFFF"))
        gx = round((self._cursor_pos.x() + self._geom.x() - self._origin.x()) * self._scale)
        gy = round((self._cursor_pos.y() + self._geom.y() - self._origin.y()) * self._scale)
        p.drawText(QRect(x, y + size + 2, size, 20), Qt.AlignmentFlag.AlignCenter,
                   f"{gx}, {gy}   {col.name().upper() if col.isValid() else ''}")
        p.restore()

    # -- mouse -------------------------------------------------------------

    def _button_at(self, pos: QPoint) -> Optional[str]:
        btns = self._buttons()
        if btns is None:
            return None
        if btns[0].contains(pos):
            return "ok"
        if btns[1].contains(pos):
            return "cancel"
        return None

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        btn = self._button_at(pos)
        if btn is not None:
            self._btn_down = btn
            return
        hit = hit_test(self._sel, pos, self.HANDLE) if self.has_selection() else None
        if hit is None:
            self._mode = "new"
            self._anchor = pos
            self.selection_started.emit()
            self._set_selection(QRect())
            self.update()
        elif hit == MOVE:
            self._mode = "move"
            self._last = pos
        else:
            self._mode = "resize"
            self._handle = hit
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        pos = e.position().toPoint()
        old_cursor = self._cursor_pos
        self._cursor_pos = pos
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self._mode == "new":
            self._set_selection(normalized_drag_rect(self._anchor, pos, shift, self._bounds()))
        elif self._mode == "resize" and self._handle is not None:
            new = resize_with_handle(self._sel, self._handle, pos, self._bounds())
            if shift:
                side = max(new.width(), new.height())
                new = move_rect_within(QRect(new.x(), new.y(), side, side), QPoint(0, 0), self._bounds())
            self._set_selection(new)
        elif self._mode == "move":
            self._set_selection(move_rect_within(self._sel, pos - self._last, self._bounds()))
            self._last = pos
        else:
            if self._button_at(pos) is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                hit = hit_test(self._sel, pos, self.HANDLE) if self.has_selection() else None
                self.setCursor(_HANDLE_CURSORS.get(hit, Qt.CursorShape.CrossCursor)
                               if hit is not None else Qt.CursorShape.CrossCursor)
        # repaint the loupe at its old and new place
        reach = (self.LOUPE_CELLS * self.LOUPE_ZOOM + 60)
        for c in (old_cursor, pos):
            if c.x() >= 0:
                self.update(QRect(c.x() - reach, c.y() - reach, reach * 2, reach * 2))

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._btn_down is not None:
            down, self._btn_down = self._btn_down, None
            if self._button_at(e.position().toPoint()) == down:
                if down == "ok":
                    self.confirm()
                else:
                    self.cancelled.emit()
            self.update()
            return
        was_new = self._mode == "new"
        self._mode = None
        self._handle = None
        if not self.has_selection():
            self._set_selection(QRect())
        elif was_new and self._confirm_mode == "release":
            self.confirm()
            return
        self.update()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.has_selection() \
                and self._sel.contains(e.position().toPoint()):
            self.confirm()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._cursor_pos.x() >= 0:
            self._cursor_pos = QPoint(-1, -1)
            self.update()
        super().leaveEvent(event)

    # -- keyboard ----------------------------------------------------------

    def _reapply_drag(self, shift: bool) -> None:
        """Re-shape the rectangle being dragged when Shift is pressed/released."""
        if self._mode == "new":
            self._set_selection(normalized_drag_rect(self._anchor, self._cursor_pos, shift, self._bounds()))

    def keyReleaseEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Shift:
            self._reapply_drag(False)
        else:
            super().keyReleaseEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        key = e.key()
        if key == Qt.Key.Key_Shift:
            self._reapply_drag(True)
        elif key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm_requested.emit()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            d = {Qt.Key.Key_Left: QPoint(-step, 0), Qt.Key.Key_Right: QPoint(step, 0),
                 Qt.Key.Key_Up: QPoint(0, -step), Qt.Key.Key_Down: QPoint(0, step)}[key]
            self.nudge_requested.emit(d)
        else:
            super().keyPressEvent(e)


class RegionSelectionSession(QObject):
    """One RegionSelector per screen; the first confirm/cancel wins.

    A selection is confined to a single screen (a Wayland client cannot place one
    window across several outputs).  Keyboard focus lives on only one overlay, so
    Enter and the arrow keys are routed to whichever overlay actually holds the
    selection, and starting a new drag on one screen clears the selection on the others.
    """

    selected = pyqtSignal(QRect)   # global logical coordinates
    cancelled = pyqtSignal()

    def __init__(self, pixmap: QPixmap, origin: QPoint, scale: float, parent=None,
                 confirm: str = "button") -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._origin = QPoint(origin)
        self._scale = scale
        self._confirm = confirm
        self._widgets: List[RegionSelector] = []
        self._done = False

    def start(self) -> None:
        screens = QGuiApplication.screens()
        under_cursor = QGuiApplication.screenAt(QCursor.pos()) or (screens[0] if screens else None)
        # Show the pointer's screen last so it ends up with keyboard focus.
        for screen in sorted(screens, key=lambda s: s is under_cursor):
            w = RegionSelector(self._pixmap, self._origin, self._scale, screen, confirm=self._confirm)
            w.selected.connect(self._on_selected)
            w.cancelled.connect(self._on_cancelled)
            w.selection_started.connect(lambda src=w: self._on_started(src))
            w.confirm_requested.connect(self._on_confirm_requested)
            w.nudge_requested.connect(self._on_nudge)
            self._widgets.append(w)
            w.showFullScreen()
            if screen is under_cursor:
                w.activateWindow()
                w.setFocus()

    def _on_started(self, source: RegionSelector) -> None:
        for w in self._widgets:
            if w is not source:
                w.clear_selection()

    def _on_confirm_requested(self) -> None:
        for w in self._widgets:
            if w.has_selection():
                w.confirm()
                return

    def _on_nudge(self, d: QPoint) -> None:
        for w in self._widgets:
            if w.has_selection():
                w.nudge(d)
                return

    def _on_selected(self, rect: QRect) -> None:
        if self._done:
            return
        self._done = True
        self.close()
        self.selected.emit(rect)

    def _on_cancelled(self) -> None:
        if self._done:
            return
        self._done = True
        self.close()
        self.cancelled.emit()

    def close(self) -> None:
        for w in self._widgets:
            w.hide()
            w.deleteLater()
        self._widgets.clear()


def start_region_selection(
    pixmap: QPixmap, origin: QPoint, scale: float, parent: Optional[QObject] = None,
    confirm: str = "button",
) -> RegionSelectionSession:
    """Open the frozen-screen overlays and return the (already started) session."""
    session = RegionSelectionSession(pixmap, origin, scale, parent, confirm)
    session.start()
    return session


# ---------------------------------------------------------------------------
#  Window picking (Wayland: windows are found in the frozen picture)
# ---------------------------------------------------------------------------

class WindowPicker(_FrozenOverlay):
    """Frozen screen; the window under the pointer is outlined, a click captures it.

    Wayland tells an application nothing about other windows, so the outline comes from
    ``windetect.EdgeMap`` (borders found in the picture).  When the guess is wrong, ↑ / the scroll wheel
    step to a larger candidate (the window around a panel) and ↓ to a smaller one; Space switches to
    dragging a region by hand.  Enter or a click captures, Esc or a right click cancels.
    """

    selected = pyqtSignal(QRect)       # global logical rectangle
    manual = pyqtSignal()
    cancelled = pyqtSignal()

    ANIM_MS = 110

    def __init__(self, pixmap, origin, scale, screen, edges, parent=None) -> None:
        super().__init__(pixmap, origin, scale, screen, parent)
        self._edges = edges
        self._cands: List[QRect] = []               # global logical, smallest to largest
        self._index = 0
        self._shown = QRectF()                      # the outline as drawn (animated), local logical
        self._cell = None
        self._pointer = QPoint(-1, -1)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.ANIM_MS)
        self._anim.valueChanged.connect(self._on_anim)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -- geometry ------------------------------------------------------------

    def _phys_to_logical(self, r: QRect) -> QRect:
        return QRect(round(self._origin.x() + r.x() / self._scale), round(self._origin.y() + r.y() / self._scale),
                     round(r.width() / self._scale), round(r.height() / self._scale))

    def _current(self) -> Optional[QRect]:
        return self._cands[self._index] if self._cands else None

    def _target_local(self) -> QRectF:
        cur = self._current()
        return QRectF(cur.translated(-self._geom.topLeft())) if cur is not None else QRectF()

    def _move_outline(self) -> None:
        """Glide the drawn outline to the current candidate."""
        target = self._target_local()
        if self._shown.isNull() or target.isNull():
            self._anim.stop()
            self._shown = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(QRectF(self._shown))
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._shown = QRectF(value)
        self.update()

    # -- hover ---------------------------------------------------------------

    def _update_hover(self, local: QPoint, force: bool = False) -> None:
        self._pointer = local
        g = local + self._geom.topLeft()
        px = round((g.x() - self._origin.x()) * self._scale)
        py = round((g.y() - self._origin.y()) * self._scale)
        cell = (px // 12, py // 12)
        if cell == self._cell and not force:
            return
        self._cell = cell
        phys, best = self._edges.candidates_at(px, py)
        old = self._current()
        self._cands = [self._phys_to_logical(r) for r in phys]
        self._index = best
        if self._current() != old:
            self._move_outline()

    def _step(self, direction: int) -> None:
        """+1: the next larger candidate, -1: the next smaller one."""
        if not self._cands:
            return
        new = min(max(self._index + direction, 0), len(self._cands) - 1)
        if new != self._index:
            self._index = new
            self._move_outline()

    # -- events --------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self._update_hover(event.position().toPoint())      # outline at once, before the pointer moves

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        pos = self.mapFromGlobal(QCursor.pos())
        if self.rect().contains(pos):
            self._update_hover(pos)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._update_hover(e.position().toPoint())

    def wheelEvent(self, e) -> None:  # noqa: N802
        dy = e.angleDelta().y()
        if dy:
            self._step(1 if dy > 0 else -1)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
        elif e.button() == Qt.MouseButton.LeftButton:
            self._update_hover(e.position().toPoint())
            self._accept()

    def _accept(self) -> None:
        cur = self._current()
        if cur is not None:
            self.selected.emit(QRect(cur))
        else:
            self.manual.emit()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        key = e.key()
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_R):
            self.manual.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept()
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_PageUp):
            self._step(1)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Minus, Qt.Key.Key_PageDown):
            self._step(-1)
        else:
            super().keyPressEvent(e)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        self._ensure_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._dim)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shown = self._shown.intersected(QRectF(self._bounds()))
        cur = self._current()
        if cur is not None and not shown.isEmpty():
            dpr = self.devicePixelRatioF()
            local = shown.toAlignedRect()
            src = QRect(round(local.x() * dpr), round(local.y() * dpr), round(local.width() * dpr), round(local.height() * dpr))
            p.drawPixmap(local.topLeft(), self._bright, src)
            p.setPen(QPen(ACCENT, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(shown.adjusted(1.5, 1.5, -1.5, -1.5))
            label = f"{round(cur.width() * self._scale)} × {round(cur.height() * self._scale)}"
            if len(self._cands) > 1:
                label += f"   ({self._index + 1}/{len(self._cands)})"
            small = QFont(self.font())
            small.setPointSize(11)
            p.setFont(small)
            box = self._text_box(p, label, 10, 4)
            inside = shown.top() + 8 + box.height() < shown.bottom()
            box.moveTopLeft(QPoint(round(shown.left()) + 8, round(shown.top()) + 8 if inside else round(shown.top())))
            self._pill(p, box, label, radius=6)
        font = QFont(self.font())
        font.setPointSize(12)
        p.setFont(font)
        hint = ("Click: capture this window   •   ↑ / ↓ or scroll: bigger / smaller   •   "
                "Space: draw a region   •   Esc: cancel")
        hbox = self._text_box(p, hint, 16, 7)
        hbox.moveCenter(QPoint(self.width() // 2, 40))
        self._pill(p, hbox, hint, radius=10)


class WindowPickSession(QObject):
    """A :class:`WindowPicker` on every screen; the first click, Space or Esc wins."""

    selected = pyqtSignal(QRect)
    manual = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, pixmap: QPixmap, origin: QPoint, scale: float, parent=None) -> None:
        super().__init__(parent)
        self._pixmap, self._origin, self._scale = pixmap, QPoint(origin), scale
        self._widgets: List[WindowPicker] = []
        self._done = False

    def start(self) -> None:
        from crabture.capture.windetect import EdgeMap
        edges = EdgeMap(self._pixmap.toImage())
        screens = QGuiApplication.screens()
        under_cursor = QGuiApplication.screenAt(QCursor.pos()) or (screens[0] if screens else None)
        for screen in sorted(screens, key=lambda s: s is under_cursor):
            w = WindowPicker(self._pixmap, self._origin, self._scale, screen, edges)
            w.selected.connect(lambda r: self._finish("selected", r))
            w.manual.connect(lambda: self._finish("manual", QRect()))
            w.cancelled.connect(lambda: self._finish("cancelled", QRect()))
            self._widgets.append(w)
            w.showFullScreen()
            if screen is under_cursor:
                w.activateWindow()
                w.setFocus()

    def _finish(self, what: str, rect: QRect) -> None:
        if self._done:
            return
        self._done = True
        self.close()
        if what == "selected":
            self.selected.emit(rect)
        elif what == "manual":
            self.manual.emit()
        else:
            self.cancelled.emit()

    def close(self) -> None:
        for w in self._widgets:
            w.hide()
            w.deleteLater()
        self._widgets.clear()


def start_window_picking(pixmap: QPixmap, origin: QPoint, scale: float,
                         parent: Optional[QObject] = None) -> WindowPickSession:
    session = WindowPickSession(pixmap, origin, scale, parent)
    session.start()
    return session


# ---------------------------------------------------------------------------
#  Monitor picking
# ---------------------------------------------------------------------------

class MonitorPicker(_FrozenOverlay):
    """One overlay per monitor: the one under the pointer lights up; click to capture it.

    Used instead of asking the toolkit "which monitor is the pointer on?", which is
    unreliable on Wayland (a client only learns the pointer position over its own windows).
    """

    selected = pyqtSignal(QRect)       # the monitor's global logical geometry
    cancelled = pyqtSignal()

    def __init__(self, pixmap, origin, scale, screen, index: int, parent=None) -> None:
        super().__init__(pixmap, origin, scale, screen, parent)
        self._index = index
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._ensure_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bright if self._hover else self._dim)
        if self._hover:
            p.setPen(QPen(ACCENT, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.rect().adjusted(3, 3, -3, -3))
        w = round(self._geom.width() * self._scale)
        h = round(self._geom.height() * self._scale)
        name = self._screen.name() or f"Monitor {self._index + 1}"
        big = QFont(self.font())
        big.setPointSize(22)
        big.setBold(True)
        p.setFont(big)
        title = f"Monitor {self._index + 1}  •  {name}"
        box = self._text_box(p, title, 26, 14)
        box.moveCenter(self.rect().center())
        self._pill(p, box, title, radius=14)
        small = QFont(self.font())
        small.setPointSize(12)
        p.setFont(small)
        sub = f"{w} × {h}   —   {'click to capture' if self._hover else 'move the pointer here'}   •   Esc: cancel"
        sbox = self._text_box(p, sub, 16, 6)
        sbox.moveTopLeft(QPoint(box.center().x() - sbox.width() // 2, box.bottom() + 10))
        self._pill(p, sbox, sub, radius=10)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
        elif e.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(QRect(self._geom))

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(e)


class MonitorPickSession(QObject):
    """Shows a :class:`MonitorPicker` on every screen; the first click or Esc wins."""

    selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    def __init__(self, pixmap: QPixmap, origin: QPoint, scale: float, parent=None) -> None:
        super().__init__(parent)
        self._pixmap, self._origin, self._scale = pixmap, QPoint(origin), scale
        self._widgets: List[MonitorPicker] = []
        self._done = False

    def start(self) -> None:
        screens = QGuiApplication.screens()
        under_cursor = QGuiApplication.screenAt(QCursor.pos()) or (screens[0] if screens else None)
        for i, screen in sorted(enumerate(screens), key=lambda t: t[1] is under_cursor):
            w = MonitorPicker(self._pixmap, self._origin, self._scale, screen, i)
            w.selected.connect(lambda r: self._finish(True, r))
            w.cancelled.connect(lambda: self._finish(False, QRect()))
            self._widgets.append(w)
            w.showFullScreen()
            if screen is under_cursor:
                w.activateWindow()
                w.setFocus()

    def _finish(self, ok: bool, rect: QRect) -> None:
        if self._done:
            return
        self._done = True
        self.close()
        (self.selected.emit(rect) if ok else self.cancelled.emit())

    def close(self) -> None:
        for w in self._widgets:
            w.hide()
            w.deleteLater()
        self._widgets.clear()


def start_monitor_picking(pixmap: QPixmap, origin: QPoint, scale: float,
                          parent: Optional[QObject] = None) -> MonitorPickSession:
    session = MonitorPickSession(pixmap, origin, scale, parent)
    session.start()
    return session


# =========================================================================
#  ScrollCaptureStrip
# =========================================================================

_STRIP_QSS = """
QWidget#strip { background: rgba(30,30,30,235); border-radius: 10px; }
QLabel { color: #EEE; font-size: 12px; }
QCheckBox { color: #EEE; font-size: 12px; }
QPushButton { background: #3D5FD9; color: white; border: none; border-radius: 6px;
              padding: 5px 10px; font-weight: bold; }
QPushButton:hover { background: #5278F8; }
QPushButton#cancel { background: #E53935; }
QPushButton#done { background: #00A152; }
"""


class ScrollCaptureStrip(QWidget):
    """Always-on-top control strip for scrolling capture.

    Signals: ``frame_requested`` (Capture frame button / Space), ``done``
    (Done / Enter), ``cancelled`` (Cancel / Esc), ``auto_changed(bool)`` and
    ``autoscroll_changed(bool)``.
    """

    frame_requested = pyqtSignal()
    done = pyqtSignal()
    cancelled = pyqtSignal()
    auto_changed = pyqtSignal(bool)
    autoscroll_changed = pyqtSignal(bool)

    def __init__(
        self,
        allow_autoscroll: bool = False,
        auto_default: bool = True,
        always_hide_for_grab: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._always_hide = always_hide_for_grab
        self.setObjectName("strip")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(_STRIP_QSS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        self._label = QLabel("Frames: 1")
        lay.addWidget(self._label)

        self._btn_frame = QPushButton("Capture frame (Space)")
        self._btn_frame.clicked.connect(self.frame_requested)
        lay.addWidget(self._btn_frame)

        self._chk_auto = QCheckBox("Auto-capture")
        self._chk_auto.setChecked(auto_default)
        self._chk_auto.setToolTip("Grab a frame every ~0.6 s while you scroll")
        self._chk_auto.toggled.connect(self.auto_changed)
        lay.addWidget(self._chk_auto)

        self._chk_scroll = QCheckBox("Auto-scroll")
        self._chk_scroll.setToolTip("Scroll the content for you (Windows, or X11 with xdotool)")
        self._chk_scroll.toggled.connect(self.autoscroll_changed)
        self._chk_scroll.setVisible(allow_autoscroll)
        lay.addWidget(self._chk_scroll)

        btn_done = QPushButton("Done (Enter)")
        btn_done.setObjectName("done")
        btn_done.clicked.connect(self.done)
        lay.addWidget(btn_done)

        btn_cancel = QPushButton("Cancel (Esc)")
        btn_cancel.setObjectName("cancel")
        btn_cancel.clicked.connect(self.cancelled)
        lay.addWidget(btn_cancel)

        self.adjustSize()

    # -- state -------------------------------------------------------------

    def auto_capture_enabled(self) -> bool:
        return self._chk_auto.isChecked()

    def autoscroll_enabled(self) -> bool:
        return self._chk_scroll.isVisible() and self._chk_scroll.isChecked()

    def set_frame_count(self, n: int) -> None:
        self._label.setText(f"Frames: {n}")

    def set_status(self, text: str) -> None:
        self._label.setText(text)

    # -- placement ---------------------------------------------------------

    def place_near(self, region: QRect, screen_geom: QRect) -> None:
        """Move the strip outside *region* when there is room on its screen.

        (Wayland ignores client-side positioning; the compositor decides.)
        """
        self.adjustSize()
        w, h = self.width(), self.height()
        margin = 12
        x = min(max(region.center().x() - w // 2, screen_geom.left()), screen_geom.right() - w)
        below = region.bottom() + margin
        above = region.top() - margin - h
        if below + h <= screen_geom.bottom():
            y = below
        elif above >= screen_geom.top():
            y = above
        else:
            y = screen_geom.bottom() - h - margin     # overlaps region: needs hiding
        self.move(x, y)

    def needs_hide_for_grab(self, region: QRect) -> bool:
        """True when the strip may end up inside the grabbed *region*."""
        if self._always_hide:
            return True
        return self.frameGeometry().intersects(region)

    # -- keys --------------------------------------------------------------

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Space:
            self.frame_requested.emit()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.done.emit()
        elif e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(e)


# =========================================================================
#  RegionOutline (X11) and CountdownOverlay
# =========================================================================

class RegionOutline:
    """Four thin always-on-top bars hugging the OUTSIDE of *region*.

    Being outside, they never appear in grabs of the region itself.  Only
    meaningful where clients can position windows (X11).
    """

    THICKNESS = 2

    def __init__(self, region: QRect) -> None:
        t = self.THICKNESS
        x, y, w, h = region.x(), region.y(), region.width(), region.height()
        rects = [
            QRect(x - t, y - t, w + 2 * t, t),      # top
            QRect(x - t, y + h, w + 2 * t, t),      # bottom
            QRect(x - t, y, t, h),                  # left
            QRect(x + w, y, t, h),                  # right
        ]
        self._bars: List[QWidget] = []
        for r in rects:
            bar = QWidget()
            bar.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowTransparentForInput
                | Qt.WindowType.Tool
            )
            bar.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            bar.setStyleSheet("background: #00BFFF;")
            bar.setGeometry(r)
            self._bars.append(bar)

    def show(self) -> None:
        for bar in self._bars:
            bar.show()

    def close(self) -> None:
        for bar in self._bars:
            bar.hide()
            bar.deleteLater()
        self._bars.clear()


class CountdownOverlay(QWidget):
    """Small rounded box counting down a capture delay."""

    finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(150, 150)
        self._remaining_ms = 0
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    def start(self, delay_ms: int) -> None:
        self._remaining_ms = max(0, int(delay_ms))
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.geometry()
            self.move(g.center() - QPoint(self.width() // 2, self.height() // 2))
        self.show()
        self._timer.start()
        self.update()

    def cancel(self) -> None:
        self._timer.stop()
        self.hide()

    def seconds_left(self) -> int:
        return max(0, math.ceil(self._remaining_ms / 1000))

    def _tick(self) -> None:
        self._remaining_ms -= self._timer.interval()
        if self._remaining_ms <= 0:
            self._timer.stop()
            self.hide()
            self.finished.emit()
        else:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 190))
        p.drawRoundedRect(self.rect().adjusted(4, 4, -4, -4), 22, 22)
        font = QFont(self.font())
        font.setPointSize(54)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.seconds_left()))
