"""
capture/overlay.py — On-screen UI used while capturing.

  • RegionSelector        – frozen-screen overlay for ONE screen (drag a rectangle).
  • RegionSelectionSession – creates one RegionSelector per screen and reports the
                            first confirmed / cancelled result.
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
from typing import List, Optional

from PyQt6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
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
#  RegionSelector — one frozen-screen overlay
# =========================================================================

class RegionSelector(QWidget):
    """Full-screen "frozen" overlay for one screen.

    Emits ``selected(QRect)`` with the chosen rectangle in *global logical*
    coordinates, or ``cancelled()``.  Drag to select; afterwards drag the
    handles / interior to adjust.  Enter or double-click confirms, Esc or
    right-click cancels, Shift while dragging forces a square, arrow keys
    nudge by one pixel.
    """

    selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    MIN_SELECTION = 3
    HANDLE = 8

    def __init__(self, pixmap: QPixmap, origin: QPoint, scale: float, screen, parent=None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._origin = QPoint(origin)
        self._scale = scale if scale > 0 else 1.0
        self._screen = screen
        self._geom: QRect = screen.geometry()

        self._sel = QRect()
        self._anchor = QPoint()
        self._mode: Optional[str] = None      # "new" | "resize" | "move"
        self._handle: Optional[int] = None
        self._last = QPoint()
        self._cursor_pos = QPoint(-1, -1)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(self._geom)

    # -- helpers -----------------------------------------------------------

    def _bounds(self) -> QRect:
        return QRect(0, 0, self.width(), self.height())

    def _to_source(self, local: QRect) -> QRect:
        """Local widget rect → pixels of the frozen pixmap."""
        return logical_to_physical(
            local.translated(self._geom.topLeft()), self._origin, self._scale,
            self._pixmap.size(),
        )

    def selection_global(self) -> QRect:
        """Current selection in global logical coordinates."""
        return self._sel.translated(self._geom.topLeft())

    def has_selection(self) -> bool:
        return (
            self._sel.width() >= self.MIN_SELECTION
            and self._sel.height() >= self.MIN_SELECTION
        )

    def _confirm(self) -> None:
        if self.has_selection():
            self.selected.emit(self.selection_global())

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        full = self._to_source(self._bounds())
        p.drawPixmap(QRectF(self.rect()), self._pixmap, QRectF(full))
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))

        if not self._sel.isNull() and self._sel.width() > 0 and self._sel.height() > 0:
            src = self._to_source(self._sel)
            p.drawPixmap(QRectF(self._sel), self._pixmap, QRectF(src))
            p.setPen(QPen(QColor("#00BFFF"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._sel.adjusted(0, 0, -1, -1))
            # Handles.
            p.setBrush(QColor("#FFFFFF"))
            p.setPen(QPen(QColor("#0078D4"), 1))
            h = self.HANDLE
            for pt in handle_points(self._sel):
                p.drawRect(pt.x() - h // 2, pt.y() - h // 2, h, h)
            self._paint_readout(p)
        else:
            self._paint_hint(p)

    def _paint_readout(self, p: QPainter) -> None:
        w = round(self._sel.width() * self._scale)
        h = round(self._sel.height() * self._scale)
        text = f"{w} × {h}"
        font = QFont(self.font())
        font.setPointSize(10)
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        tw, th = fm.horizontalAdvance(text) + 14, fm.height() + 6
        x = self._sel.left()
        y = self._sel.top() - th - 6
        if y < 0:
            y = self._sel.top() + 6
            x = self._sel.left() + 6
        box = QRect(x, y, tw, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 190))
        p.drawRoundedRect(box, 5, 5)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_hint(self, p: QPainter) -> None:
        text = "Drag to select a region  •  Enter / double-click: confirm  •  Esc / right-click: cancel  •  Shift: square"
        font = QFont(self.font())
        font.setPointSize(11)
        p.setFont(font)
        fm = p.fontMetrics()
        tw, th = fm.horizontalAdvance(text) + 28, fm.height() + 14
        box = QRect((self.width() - tw) // 2, 24, tw, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 190))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # -- mouse -------------------------------------------------------------

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        hit = hit_test(self._sel, pos, self.HANDLE) if self.has_selection() else None
        if hit is None:
            self._mode = "new"
            self._anchor = pos
            self._sel = QRect()
        elif hit == MOVE:
            self._mode = "move"
            self._last = pos
        else:
            self._mode = "resize"
            self._handle = hit
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        pos = e.position().toPoint()
        self._cursor_pos = pos
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self._mode == "new":
            self._sel = normalized_drag_rect(self._anchor, pos, shift, self._bounds())
            self.update()
        elif self._mode == "resize" and self._handle is not None:
            new = resize_with_handle(self._sel, self._handle, pos, self._bounds())
            if shift:
                side = max(new.width(), new.height())
                new = QRect(new.x(), new.y(), side, side)
                new = move_rect_within(new, QPoint(0, 0), self._bounds())
            self._sel = new
            self.update()
        elif self._mode == "move":
            self._sel = move_rect_within(self._sel, pos - self._last, self._bounds())
            self._last = pos
            self.update()
        else:
            hit = hit_test(self._sel, pos, self.HANDLE) if self.has_selection() else None
            self.setCursor(
                _HANDLE_CURSORS.get(hit, Qt.CursorShape.CrossCursor)
                if hit is not None else Qt.CursorShape.CrossCursor
            )

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._mode = None
        self._handle = None
        if not self.has_selection():
            self._sel = QRect()
        self.update()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.has_selection() \
                and self._sel.contains(e.position().toPoint()):
            self._confirm()

    # -- keyboard ----------------------------------------------------------

    def _reapply_drag(self, shift: bool) -> None:
        """Re-shape the rectangle being dragged when Shift is pressed/released."""
        if self._mode == "new":
            self._sel = normalized_drag_rect(self._anchor, self._cursor_pos, shift, self._bounds())
            self.update()

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
            self._confirm()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) \
                and self.has_selection():
            d = {
                Qt.Key.Key_Left: QPoint(-1, 0), Qt.Key.Key_Right: QPoint(1, 0),
                Qt.Key.Key_Up: QPoint(0, -1), Qt.Key.Key_Down: QPoint(0, 1),
            }[key]
            self._sel = move_rect_within(self._sel, d, self._bounds())
            self.update()
        else:
            super().keyPressEvent(e)


class RegionSelectionSession(QObject):
    """One RegionSelector per screen; the first confirm/cancel wins.

    A selection is confined to a single screen (a Wayland client cannot place
    one window across several outputs).
    """

    selected = pyqtSignal(QRect)   # global logical coordinates
    cancelled = pyqtSignal()

    def __init__(self, pixmap: QPixmap, origin: QPoint, scale: float, parent=None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._origin = QPoint(origin)
        self._scale = scale
        self._widgets: List[RegionSelector] = []
        self._done = False

    def start(self) -> None:
        screens = QGuiApplication.screens()
        under_cursor = QGuiApplication.screenAt(QCursor.pos())
        for screen in screens:
            w = RegionSelector(self._pixmap, self._origin, self._scale, screen)
            w.selected.connect(self._on_selected)
            w.cancelled.connect(self._on_cancelled)
            self._widgets.append(w)
            w.showFullScreen()
            if screen is under_cursor or (under_cursor is None and screen is screens[0]):
                w.activateWindow()
                w.setFocus()

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
) -> RegionSelectionSession:
    """Open the frozen-screen overlays and return the (already started) session."""
    session = RegionSelectionSession(pixmap, origin, scale, parent)
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
        self._chk_scroll.setToolTip("Scroll the content for you (X11 + xdotool)")
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
