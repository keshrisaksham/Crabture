"""
editor/items.py — Custom QGraphicsItem subclasses for Crabture.

V3 rewrite:
  • ResizeHandle / HandleGroup — proper child-item handles with hover cursors.
  • ArrowItem, EditableTextItem, SpeechBalloonItem — annotation items.
  • MagnifierItem — dynamic live-paint magnifier (grabs from bg pixmap).
  • ResizableRectItem — rectangles / highlights with drag-to-resize.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QInputDialog,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Live-paint support (pixelate / blur / magnifier) & chrome suppression
# ═══════════════════════════════════════════════════════════════════════════
#
# Z-order note: item Z-values are owned by LayerManager (it overwrites any
# setZValue() made here), so items below never set their own Z.
#
# Effect items re-render the scene beneath them.  To do that without hiding
# themselves (which would schedule another repaint -> repaint loop) they push
# themselves onto ``_GRAB_STACK``; the grab then paints every other item by
# hand, skipping the grabber, its children and anything at/above its Z-value.
# Because an effect item only ever sees items *below* it, stacked effects
# compose (the lower effect is painted into the upper one's grab) and the
# recursion always terminates.

_GRAB_STACK: List[QGraphicsItem] = []
_GRAB_MAX_DEPTH = 8

# While > 0, selection boxes and resize handles are not painted.  Used when
# grabbing the scene for an effect and available to export code.
_CHROME_SUPPRESSED = 0


@contextmanager
def chrome_suppressed():
    """Context manager: don't paint selection boxes / resize handles.

    Wrap ``scene.render()`` in this for export / clipboard so editor chrome
    never ends up in the saved image.
    """
    global _CHROME_SUPPRESSED
    _CHROME_SUPPRESSED += 1
    try:
        yield
    finally:
        _CHROME_SUPPRESSED -= 1


def _chrome_hidden() -> bool:
    return _CHROME_SUPPRESSED > 0


def _top_item(item: QGraphicsItem) -> QGraphicsItem:
    while item.parentItem() is not None:
        item = item.parentItem()
    return item


def _hidden_from_grab(item: QGraphicsItem) -> bool:
    """True if *item* must not paint because an effect item is grabbing."""
    if not _GRAB_STACK:
        return False
    top = _top_item(item)
    grabber = _top_item(_GRAB_STACK[-1])
    return top is grabber or top.zValue() >= grabber.zValue()


def _grab_scene_content(item: QGraphicsItem, rect: QRectF) -> Tuple[QPixmap, QRectF]:
    """Snapshot the scene content below *item* within *rect* (local coords).

    Returns ``(pixmap, scene_rect)`` where *scene_rect* is the integer-aligned
    scene rectangle the pixmap covers 1:1, or ``(null pixmap, empty rect)``.
    """
    null = (QPixmap(), QRectF())
    scene = item.scene()
    if scene is None or len(_GRAB_STACK) >= _GRAB_MAX_DEPTH:
        return null

    wanted = item.mapToScene(rect).boundingRect()
    bounds = scene.sceneRect()
    if not bounds.isEmpty():
        wanted = wanted.intersected(bounds)
    src = QRectF(wanted.toAlignedRect())
    if src.width() < 1 or src.height() < 1:
        return null

    pix = QPixmap(int(src.width()), int(src.height()))
    pix.fill(Qt.GlobalColor.transparent)

    # Paint the items by hand rather than calling scene.render(): a nested
    # scene.render() issued from inside paint() (as happens during export,
    # which itself uses scene.render) produces an empty pixmap.
    global _CHROME_SUPPRESSED
    p = QPainter(pix)
    _GRAB_STACK.append(item)
    _CHROME_SUPPRESSED += 1
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        base = QTransform.fromTranslate(-src.x(), -src.y())
        # (scene.items(rect) is unreliable while an outer scene.render() is
        # running, so take everything and cull by hand.)
        for other in scene.items(Qt.SortOrder.AscendingOrder):
            if (
                not other.isVisible()
                or not other.sceneBoundingRect().intersects(src)
                or other.flags() & QGraphicsItem.GraphicsItemFlag.ItemHasNoContents
                or _hidden_from_grab(other)
            ):
                continue
            p.save()
            p.setWorldTransform(other.sceneTransform() * base)
            p.setOpacity(other.effectiveOpacity())
            opt = QStyleOptionGraphicsItem()
            opt.exposedRect = other.boundingRect()
            other.paint(p, opt, None)
            p.restore()
    finally:
        _CHROME_SUPPRESSED -= 1
        _GRAB_STACK.pop()
        p.end()

    return pix, src


# ═══════════════════════════════════════════════════════════════════════════
#  ResizeHandle / HandleGroup
# ═══════════════════════════════════════════════════════════════════════════

HANDLE_SIZE = 8.0
HANDLE_COLOR = QColor("#0078D4")
HANDLE_BORDER_COLOR = QColor("#FFFFFF")
SELECTION_DASH_COLOR = QColor("#00BFFF")

# Index → (x-factor, y-factor) over the parent's logical rect.
_HANDLE_FACTORS: List[Tuple[float, float]] = [
    (0.0, 0.0),   # 0 = top-left
    (0.5, 0.0),   # 1 = top-center
    (1.0, 0.0),   # 2 = top-right
    (1.0, 0.5),   # 3 = middle-right
    (1.0, 1.0),   # 4 = bottom-right
    (0.5, 1.0),   # 5 = bottom-center
    (0.0, 1.0),   # 6 = bottom-left
    (0.0, 0.5),   # 7 = middle-left
]

# Index → Qt cursor for each handle position.
_HANDLE_CURSORS: Dict[int, Qt.CursorShape] = {
    0: Qt.CursorShape.SizeFDiagCursor,   # TL
    1: Qt.CursorShape.SizeVerCursor,     # TC
    2: Qt.CursorShape.SizeBDiagCursor,   # TR
    3: Qt.CursorShape.SizeHorCursor,     # MR
    4: Qt.CursorShape.SizeFDiagCursor,   # BR
    5: Qt.CursorShape.SizeVerCursor,     # BC
    6: Qt.CursorShape.SizeBDiagCursor,   # BL
    7: Qt.CursorShape.SizeHorCursor,     # ML
}


class ResizeHandle(QGraphicsRectItem):
    """A single resize handle — small square child item with hover cursor.

    The handle is a child of the item it controls.  It intercepts mouse
    drags and forwards resize deltas to the parent via the `HandleGroup`.
    """

    def __init__(
        self,
        index: int,
        parent_item: QGraphicsItem,
        group: "HandleGroup",
    ) -> None:
        super().__init__(-HANDLE_SIZE / 2, -HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE, parent_item)
        self._index = index
        self._group = group
        self._dragging = False
        self._drag_origin = QPointF()

        self.setPen(QPen(HANDLE_BORDER_COLOR, 1.0))
        self.setBrush(QBrush(HANDLE_COLOR))
        self.setZValue(10000)
        self.setAcceptHoverEvents(True)

        # Handles should NOT be selectable/movable on their own.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        # Keep a constant on-screen size at any zoom; the handle stays anchored
        # at its (parent-space) position.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        if _chrome_hidden():
            return
        super().paint(painter, option, widget)

    # -- Hover cursors -----------------------------------------------------

    def _cursor_shape(self) -> Qt.CursorShape:
        return _HANDLE_CURSORS.get(self._index, Qt.CursorShape.ArrowCursor)

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setCursor(QCursor(self._cursor_shape()))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    # -- Drag-to-resize ----------------------------------------------------

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_origin = event.scenePos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            delta_scene = event.scenePos() - self._drag_origin
            self._group.handle_dragged(self._index, delta_scene)
            self._drag_origin = event.scenePos()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)


def _resize_rect_by_handle(rect: QRectF, handle_idx: int, delta: QPointF) -> QRectF:
    """Return a new QRectF after dragging handle *handle_idx* by *delta*."""
    r = QRectF(rect)
    dx, dy = delta.x(), delta.y()
    if handle_idx == 0:
        r.setTopLeft(r.topLeft() + delta)
    elif handle_idx == 1:
        r.setTop(r.top() + dy)
    elif handle_idx == 2:
        r.setTopRight(r.topRight() + delta)
    elif handle_idx == 3:
        r.setRight(r.right() + dx)
    elif handle_idx == 4:
        r.setBottomRight(r.bottomRight() + delta)
    elif handle_idx == 5:
        r.setBottom(r.bottom() + dy)
    elif handle_idx == 6:
        r.setBottomLeft(r.bottomLeft() + delta)
    elif handle_idx == 7:
        r.setLeft(r.left() + dx)
    return r.normalized()


class HandleGroup:
    """Creates and manages 8 ResizeHandle children for a parent item.

    Subclasses or users must call ``update_positions(rect)`` whenever the
    parent's logical rectangle changes, and ``set_visible(bool)`` on
    selection changes.
    """

    def __init__(self, parent_item: QGraphicsItem, resize_callback) -> None:
        self._parent = parent_item
        self._resize_callback = resize_callback  # fn(index, delta_scene) -> None
        self._handles: List[ResizeHandle] = []
        for i in range(8):
            h = ResizeHandle(i, parent_item, self)
            h.setVisible(False)
            self._handles.append(h)

    def handle_dragged(self, index: int, delta_scene: QPointF) -> None:
        """Called by a ResizeHandle when it is dragged."""
        self._resize_callback(index, delta_scene)

    def update_positions(self, rect: QRectF) -> None:
        """Reposition all 8 handles around *rect* (in parent coords)."""
        for i, (xf, yf) in enumerate(_HANDLE_FACTORS):
            cx = rect.x() + rect.width() * xf
            cy = rect.y() + rect.height() * yf
            self._handles[i].setPos(cx, cy)

    def set_visible(self, visible: bool) -> None:
        for h in self._handles:
            h.setVisible(visible)


# ═══════════════════════════════════════════════════════════════════════════
#  Helper: paint selection dashes
# ═══════════════════════════════════════════════════════════════════════════

def _paint_selection_rect(painter: QPainter, rect: QRectF) -> None:
    """Draw a dashed selection rectangle (constant 1px on screen at any zoom)."""
    if _chrome_hidden():
        return
    pen = QPen(SELECTION_DASH_COLOR, 1.0, Qt.PenStyle.DashLine)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)


# ═══════════════════════════════════════════════════════════════════════════
#  ArrowItem
# ═══════════════════════════════════════════════════════════════════════════

class ArrowItem(QGraphicsPathItem):
    """Vector arrow with resize handles for endpoint adjustment."""

    SHAFT_WIDTH = 3.0
    HEAD_LENGTH = 18.0
    HEAD_WIDTH = 14.0

    def __init__(
        self,
        start: QPointF,
        end: QPointF,
        color: QColor = QColor("#FF4444"),
        parent: Optional[QGraphicsItem] = None,
        shaft_width: Optional[float] = None,
        head_length: Optional[float] = None,
        head_width: Optional[float] = None,
    ) -> None:
        super().__init__(parent)
        self._start = start
        self._end = end
        self._color = color
        # Per-instance geometry (tool settings); class constants are the defaults.
        self._shaft_width = float(shaft_width if shaft_width is not None else self.SHAFT_WIDTH)
        self._head_length = float(head_length if head_length is not None else self.HEAD_LENGTH)
        self._head_width = float(head_width if head_width is not None else self.HEAD_WIDTH)
        self._rebuild()

        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(self._color))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

        self._handles = HandleGroup(self, self._on_handle_drag)

    def _logical_rect(self) -> QRectF:
        return super().boundingRect()

    def _rebuild(self) -> None:
        path = QPainterPath()
        dx = self._end.x() - self._start.x()
        dy = self._end.y() - self._start.y()
        length = math.hypot(dx, dy)
        if length < 1e-3:
            self.setPath(path)
            return

        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        head_base_len = max(length - self._head_length, 0)
        shaft_half = self._shaft_width / 2.0
        head_half = self._head_width / 2.0

        p1 = QPointF(self._start.x() + px * shaft_half, self._start.y() + py * shaft_half)
        p2 = QPointF(self._start.x() + ux * head_base_len + px * shaft_half, self._start.y() + uy * head_base_len + py * shaft_half)
        p3 = QPointF(self._start.x() + ux * head_base_len + px * head_half, self._start.y() + uy * head_base_len + py * head_half)
        p4 = self._end
        p5 = QPointF(self._start.x() + ux * head_base_len - px * head_half, self._start.y() + uy * head_base_len - py * head_half)
        p6 = QPointF(self._start.x() + ux * head_base_len - px * shaft_half, self._start.y() + uy * head_base_len - py * shaft_half)
        p7 = QPointF(self._start.x() - px * shaft_half, self._start.y() - py * shaft_half)

        poly = QPolygonF([p1, p2, p3, p4, p5, p6, p7, p1])
        path.addPolygon(poly)
        path.closeSubpath()
        self.setPath(path)

    def set_end(self, end: QPointF) -> None:
        self._end = end
        self._rebuild()

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        delta = self.mapFromScene(self.mapToScene(QPointF()) + delta_scene) - QPointF()
        br = self._logical_rect()
        new_br = _resize_rect_by_handle(br, index, delta)
        if new_br.width() < 5 or new_br.height() < 5:
            return
        old_br = br
        sx = new_br.width() / max(old_br.width(), 1)
        sy = new_br.height() / max(old_br.height(), 1)
        self._start = QPointF(
            new_br.x() + (self._start.x() - old_br.x()) * sx,
            new_br.y() + (self._start.y() - old_br.y()) * sy,
        )
        self._end = QPointF(
            new_br.x() + (self._end.x() - old_br.x()) * sx,
            new_br.y() + (self._end.y() - old_br.y()) * sy,
        )
        self.prepareGeometryChange()
        self._rebuild()
        self._handles.update_positions(self._logical_rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self._logical_rect())
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        # Clear the default selection visual (dotted rect from Qt).
        option.state &= ~QStyle.StateFlag.State_Selected  # type: ignore[assignment]
        super().paint(painter, option, widget)
        if self.isSelected():
            _paint_selection_rect(painter, self._logical_rect())


# ═══════════════════════════════════════════════════════════════════════════
#  EditableTextItem
# ═══════════════════════════════════════════════════════════════════════════

class EditableTextItem(QGraphicsTextItem):
    """Movable, in-place-editable text with resize handles (font scaling)."""

    def __init__(
        self,
        text: str = "Text",
        pos: QPointF = QPointF(0, 0),
        color: QColor = QColor("#FFFFFF"),
        font_size: int = 16,
        parent: Optional[QGraphicsItem] = None,
        handles: bool = True,
        bold: bool = True,
        family: str = "Sans Serif",
    ) -> None:
        super().__init__(text, parent)
        self.setPos(pos)
        self.setDefaultTextColor(color)
        self._base_font_size: float = float(font_size)
        font = QFont(family or "Sans Serif", int(font_size))
        font.setBold(bool(bold))
        self.setFont(font)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        # Nested text (e.g. inside a balloon) has no handles of its own.
        self._handles: Optional[HandleGroup] = (
            HandleGroup(self, self._on_handle_drag) if handles else None
        )

    def _logical_rect(self) -> QRectF:
        return super().boundingRect()

    def mouseDoubleClickEvent(self, event) -> None:
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event) -> None:
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        super().focusOutEvent(event)

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        dy = delta_scene.y()
        dx = delta_scene.x()
        if index in (4, 5, 6):
            scale_delta = dy * 0.3
        elif index in (0, 1, 2):
            scale_delta = -dy * 0.3
        else:
            scale_delta = abs(dx) * 0.2 * (1 if dx > 0 else -1)
        new_size = max(6.0, self._base_font_size + scale_delta)
        font = self.font()
        font.setPointSizeF(new_size)
        self.setFont(font)
        self._base_font_size = new_size
        if self._handles is not None:
            self._handles.update_positions(self._logical_rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and self._handles is not None:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self._logical_rect())
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        if self.isSelected():
            _paint_selection_rect(painter, self._logical_rect())
        # Clear default Qt selection visual before painting text.
        option.state &= ~QStyle.StateFlag.State_Selected  # type: ignore[assignment]
        super().paint(painter, option, widget)


# ═══════════════════════════════════════════════════════════════════════════
#  MagnifierItem — Dynamic live-paint magnifier
# ═══════════════════════════════════════════════════════════════════════════

class MagnifierItem(QGraphicsItem):
    """Circular magnifier that dynamically renders from the background.

    Instead of holding a static pixmap, ``paint()`` grabs the bg region
    under its current position, scales it up, clips to an ellipse, and
    draws the result.  Moving or resizing the magnifier refreshes the
    view in real-time.
    """

    BORDER_WIDTH = 3.0
    BORDER_COLOR = QColor("#00BFFF")
    DEFAULT_ZOOM = 2.0

    def __init__(
        self,
        center: QPointF,
        radius: float = 80.0,
        zoom_factor: float = 2.0,
        parent: Optional[QGraphicsItem] = None,
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._zoom = zoom_factor

        self.setPos(center.x() - radius, center.y() - radius)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

        self._handles = HandleGroup(self, self._on_handle_drag)

    def _diameter(self) -> float:
        return self._radius * 2.0

    # -- QGraphicsItem interface -------------------------------------------

    def boundingRect(self) -> QRectF:
        d = self._diameter()
        margin = self.BORDER_WIDTH + 1
        return QRectF(-margin, -margin, d + margin * 2, d + margin * 2)

    def _content_rect(self) -> QRectF:
        d = self._diameter()
        return QRectF(0, 0, d, d)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        d = self._diameter()
        content = self._content_rect()

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # --- Grab scene content under our current position ---
        src_radius = self._radius / self._zoom
        src_rect_local = QRectF(self._radius - src_radius, self._radius - src_radius, src_radius * 2, src_radius * 2)

        pix, src_scene = _grab_scene_content(self, src_rect_local)
        if pix.isNull():
            # Draw empty circle
            painter.setPen(QPen(self.BORDER_COLOR, self.BORDER_WIDTH))
            painter.setBrush(QBrush(QColor(40, 40, 40, 180)))
            painter.drawEllipse(content)
            return

        # The grabbed pixmap covers an integer-aligned scene rect that is a
        # touch larger than the ideal source; map it into the circle exactly.
        tl = self.mapFromScene(src_scene.topLeft())
        target = QRectF(
            (tl.x() - self._radius) * self._zoom + self._radius,
            (tl.y() - self._radius) * self._zoom + self._radius,
            src_scene.width() * self._zoom,
            src_scene.height() * self._zoom,
        )

        # Clip to circle
        clip = QPainterPath()
        clip.addEllipse(content)
        painter.setClipPath(clip)
        painter.drawPixmap(target, pix, QRectF(pix.rect()))

        # Border ring
        painter.setClipping(False)
        painter.setPen(QPen(self.BORDER_COLOR, self.BORDER_WIDTH))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = self.BORDER_WIDTH / 2.0
        painter.drawEllipse(QRectF(half, half, d - self.BORDER_WIDTH, d - self.BORDER_WIDTH))

        # Selection rect
        if self.isSelected():
            _paint_selection_rect(painter, content)

    # -- Resize via handles ------------------------------------------------

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        d = max(abs(delta_scene.x()), abs(delta_scene.y()))
        # Determine sign from handle position.
        if index in (0, 1, 2, 7):
            sign = -1 if (delta_scene.x() < 0 or delta_scene.y() < 0) else 1
        else:
            sign = 1 if (delta_scene.x() > 0 or delta_scene.y() > 0) else -1
        new_radius = max(20.0, self._radius + sign * d * 0.5)
        if abs(new_radius - self._radius) > 0.5:
            self.prepareGeometryChange()
            self._radius = new_radius
            self._handles.update_positions(self._content_rect())
            self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self._content_rect())
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            # Trigger repaint when moved so magnified area updates.
            self.update()
        return super().itemChange(change, value)


# ═══════════════════════════════════════════════════════════════════════════
#  SpeechBalloonItem
# ═══════════════════════════════════════════════════════════════════════════

class SpeechBalloonItem(QGraphicsPathItem):
    """Comic-style speech bubble with embedded editable text."""

    BODY_RADIUS = 14.0
    TAIL_WIDTH = 20.0
    TAIL_HEIGHT = 30.0
    PADDING = 12.0

    def __init__(
        self,
        text: str = "Hello!",
        pos: QPointF = QPointF(0, 0),
        color: QColor = QColor("#FFFFFF"),
        parent: Optional[QGraphicsItem] = None,
        font_size: int = 13,
        text_color: Optional[QColor] = None,
    ) -> None:
        super().__init__(parent)
        self.setPos(pos)
        self._color = color

        # The text is a handle-less child; the balloon's handles control
        # everything.  No flags => it can't be selected, moved or deleted on
        # its own (presses fall through to the balloon); double-click still
        # starts in-place editing.
        self._text_item = EditableTextItem(
            text, QPointF(self.PADDING, self.PADDING),
            QColor(text_color) if text_color is not None else QColor("#1E1E1E"),
            int(font_size), self, handles=False,
        )
        self._text_item.setFlags(QGraphicsItem.GraphicsItemFlag(0))

        self._rebuild()

        self.setPen(QPen(QColor("#333333"), 2))
        self.setBrush(QBrush(self._color))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

        self._handles = HandleGroup(self, self._on_handle_drag)

        # Reflow the bubble whenever the text is edited.
        self._text_item.document().contentsChanged.connect(self._on_text_changed)

    def _logical_rect(self) -> QRectF:
        return super().boundingRect()

    def _on_text_changed(self) -> None:
        self.prepareGeometryChange()
        self._rebuild()
        self._handles.update_positions(self._logical_rect())
        self.update()

    def _rebuild(self) -> None:
        # document().size() forces layout, so it is current even while the
        # text item's cached boundingRect has not caught up with an edit.
        doc_size = self._text_item.document().size()
        bw = doc_size.width() + self.PADDING * 2
        bh = doc_size.height() + self.PADDING * 2

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, bw, bh), self.BODY_RADIUS, self.BODY_RADIUS)

        tail = QPainterPath()
        tx = bw * 0.25
        tail.moveTo(tx, bh)
        tail.lineTo(tx - self.TAIL_WIDTH / 2, bh + self.TAIL_HEIGHT)
        tail.lineTo(tx + self.TAIL_WIDTH, bh)
        tail.closeSubpath()

        path = path.united(tail)
        self.setPath(path)

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        dy = delta_scene.y()
        dx = delta_scene.x()
        if index in (4, 5, 6):
            scale_delta = dy * 0.3
        elif index in (0, 1, 2):
            scale_delta = -dy * 0.3
        else:
            scale_delta = abs(dx) * 0.2 * (1 if dx > 0 else -1)
        new_size = max(6.0, self._text_item._base_font_size + scale_delta)
        font = self._text_item.font()
        font.setPointSizeF(new_size)
        self._text_item.setFont(font)
        self._text_item._base_font_size = new_size
        self.prepareGeometryChange()
        self._rebuild()
        self._handles.update_positions(self._logical_rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self._logical_rect())
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        option.state &= ~QStyle.StateFlag.State_Selected  # type: ignore[assignment]
        super().paint(painter, option, widget)
        if self.isSelected():
            _paint_selection_rect(painter, self._logical_rect())


# ═══════════════════════════════════════════════════════════════════════════
#  ResizableRectItem
# ═══════════════════════════════════════════════════════════════════════════

class _RectHandleMixin:
    """Resize handles + selection box for rect/ellipse shaped graphics items.

    Mix in *before* QGraphicsRectItem / QGraphicsEllipseItem; the host class
    creates ``self._handles = HandleGroup(self, self._on_handle_drag)``.
    """

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        delta = self.mapFromScene(self.mapToScene(QPointF()) + delta_scene) - QPointF()
        new_rect = _resize_rect_by_handle(self.rect(), index, delta)
        if new_rect.width() > 5 and new_rect.height() > 5:
            self.prepareGeometryChange()
            self.setRect(new_rect)
            self._handles.update_positions(self.rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self.rect())
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        option.state &= ~QStyle.StateFlag.State_Selected  # type: ignore[assignment]
        super().paint(painter, option, widget)
        if self.isSelected():
            _paint_selection_rect(painter, self.rect())


class ResizableRectItem(_RectHandleMixin, QGraphicsRectItem):
    """QGraphicsRectItem with proper child resize handles."""

    def __init__(self, rect: QRectF, parent: Optional[QGraphicsItem] = None) -> None:
        super().__init__(rect, parent)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._handles = HandleGroup(self, self._on_handle_drag)


# ═══════════════════════════════════════════════════════════════════════════
#  PixelateItem & BlurItem — Dynamic live-paint effects
# ═══════════════════════════════════════════════════════════════════════════

class _LiveEffectItem(ResizableRectItem):
    """Base for rectangles that repaint the scene beneath them with an effect.

    Z-order comes from the LayerManager (no setZValue here); the effect only
    sees items below it, so stacked effects compose.
    """

    SMOOTH = False

    def __init__(self, rect: QRectF, intensity: int = 10, parent: Optional[QGraphicsItem] = None) -> None:
        super().__init__(rect, parent)
        self._intensity = intensity

    def set_intensity(self, val: int) -> None:
        self._intensity = max(1, val)
        self.update()

    def _apply(self, pix: QPixmap) -> QPixmap:
        raise NotImplementedError

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        r = self.rect()
        pix, src_scene = _grab_scene_content(self, r)
        if not pix.isNull():
            painter.save()
            painter.setClipRect(r, Qt.ClipOperation.IntersectClip)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.SMOOTH)
            painter.drawPixmap(self.mapFromScene(src_scene.topLeft()), self._apply(pix))
            painter.restore()

        if self.isSelected():
            _paint_selection_rect(painter, r)


class PixelateItem(_LiveEffectItem):
    """Dynamic rectangle that pixelates any items beneath it."""

    def _apply(self, pix: QPixmap) -> QPixmap:
        block = max(1, self._intensity)
        w, h = pix.width(), pix.height()
        small = pix.scaled(max(1, w // block), max(1, h // block), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)


class BlurItem(_LiveEffectItem):
    """Dynamic rectangle that blurs any items beneath it."""

    SMOOTH = True

    def _apply(self, pix: QPixmap) -> QPixmap:
        scale = max(1, self._intensity)
        w, h = pix.width(), pix.height()
        small = pix.scaled(max(1, w // scale), max(1, h // scale), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


# ═══════════════════════════════════════════════════════════════════════════
#  EllipseItem
# ═══════════════════════════════════════════════════════════════════════════

class EllipseItem(_RectHandleMixin, QGraphicsEllipseItem):
    """Resizable ellipse / circle outline (or fill) with the usual 8 handles."""

    def __init__(self, rect: QRectF, parent: Optional[QGraphicsItem] = None) -> None:
        super().__init__(rect, parent)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._handles = HandleGroup(self, self._on_handle_drag)


# ═══════════════════════════════════════════════════════════════════════════
#  Stroked-path helpers shared by LineItem / FreehandItem
# ═══════════════════════════════════════════════════════════════════════════

MIN_HIT_WIDTH = 12.0   # thin strokes stay easy to click


def _stroke_shape(path: QPainterPath, pen: QPen) -> QPainterPath:
    stroker = QPainterPathStroker()
    stroker.setWidth(max(pen.widthF(), MIN_HIT_WIDTH))
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def _round_pen(color: QColor, width: float, round_caps: bool = True) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap if round_caps else Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


# ═══════════════════════════════════════════════════════════════════════════
#  LineItem
# ═══════════════════════════════════════════════════════════════════════════

class _EndpointHandle(ResizeHandle):
    def _cursor_shape(self) -> Qt.CursorShape:
        return Qt.CursorShape.SizeAllCursor


class _EndpointHandles:
    """Two endpoint handles; same interface as :class:`HandleGroup`."""

    def __init__(self, parent_item: QGraphicsItem, callback) -> None:
        self._callback = callback  # fn(index, delta_scene)
        self._handles = [_EndpointHandle(i, parent_item, self) for i in range(2)]
        for h in self._handles:
            h.setVisible(False)

    def handle_dragged(self, index: int, delta_scene: QPointF) -> None:
        self._callback(index, delta_scene)

    def update_positions(self, points) -> None:
        for h, pt in zip(self._handles, points):
            h.setPos(pt)

    def set_visible(self, visible: bool) -> None:
        for h in self._handles:
            h.setVisible(visible)


class LineItem(QGraphicsPathItem):
    """Straight line with round caps and two draggable endpoints."""

    WIDTH = 3.0

    def __init__(
        self,
        start: QPointF,
        end: QPointF,
        color: QColor = QColor("#FF4444"),
        width: float = WIDTH,
        parent: Optional[QGraphicsItem] = None,
        round_caps: bool = True,
    ) -> None:
        super().__init__(parent)
        self._start = QPointF(start)
        self._end = QPointF(end)
        self._color = QColor(color)
        self.setPen(_round_pen(self._color, width, round_caps))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._rebuild()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._handles = _EndpointHandles(self, self._on_handle_drag)

    def length(self) -> float:
        return math.hypot(self._end.x() - self._start.x(), self._end.y() - self._start.y())

    def _logical_rect(self) -> QRectF:
        return QRectF(self._start, self._end).normalized()

    def _rebuild(self) -> None:
        path = QPainterPath()
        path.moveTo(self._start)
        path.lineTo(self._end)
        self.setPath(path)

    def set_end(self, end: QPointF) -> None:
        self._end = QPointF(end)
        self._rebuild()

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        pen = self.pen()
        pen.setColor(self._color)
        self.setPen(pen)
        self.update()

    def shape(self) -> QPainterPath:
        return _stroke_shape(self.path(), self.pen())

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        delta = self.mapFromScene(self.mapToScene(QPointF()) + delta_scene) - QPointF()
        self.prepareGeometryChange()
        if index == 0:
            self._start = self._start + delta
        else:
            self._end = self._end + delta
        self._rebuild()
        self._handles.update_positions([self._start, self._end])

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions([self._start, self._end])
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        option.state &= ~QStyle.StateFlag.State_Selected  # type: ignore[assignment]
        super().paint(painter, option, widget)
        if self.isSelected():
            half = self.pen().widthF() / 2.0
            _paint_selection_rect(painter, self._logical_rect().adjusted(-half, -half, half, half))


# ═══════════════════════════════════════════════════════════════════════════
#  FreehandItem
# ═══════════════════════════════════════════════════════════════════════════

class FreehandItem(QGraphicsPathItem):
    """Freehand pen stroke, smoothed with quadratic midpoint curves."""

    WIDTH = 3.0
    MIN_POINT_DISTANCE = 1.0   # px; closer samples are dropped while drawing

    def __init__(
        self,
        start: QPointF,
        color: QColor = QColor("#FF4444"),
        width: float = WIDTH,
        parent: Optional[QGraphicsItem] = None,
        smoothing: bool = True,
    ) -> None:
        super().__init__(parent)
        self._points: List[QPointF] = [QPointF(start)]
        self._color = QColor(color)
        self._smoothing = bool(smoothing)
        self.setPen(_round_pen(self._color, width))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._rebuild()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._handles = HandleGroup(self, self._on_handle_drag)

    # -- geometry ---------------------------------------------------------

    def add_point(self, pt: QPointF) -> None:
        last = self._points[-1]
        if math.hypot(pt.x() - last.x(), pt.y() - last.y()) < self.MIN_POINT_DISTANCE:
            return
        self._points.append(QPointF(pt))
        self._rebuild()

    def _points_rect(self) -> QRectF:
        xs = [p.x() for p in self._points]
        ys = [p.y() for p in self._points]
        return QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys)))

    def extent(self) -> float:
        """Larger side of the stroke's point bounds (for stray-click checks)."""
        r = self._points_rect()
        return max(r.width(), r.height())

    def _rebuild(self) -> None:
        pts = self._points
        path = QPainterPath()
        path.moveTo(pts[0])
        n = len(pts)
        if n == 1:
            path.lineTo(pts[0] + QPointF(0.01, 0.0))   # round cap draws a dot
        elif n == 2 or not self._smoothing:
            for p in pts[1:]:
                path.lineTo(p)
        else:
            for i in range(1, n - 1):
                mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2.0, (pts[i].y() + pts[i + 1].y()) / 2.0)
                path.quadTo(pts[i], mid)
            path.lineTo(pts[-1])
        self.setPath(path)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        pen = self.pen()
        pen.setColor(self._color)
        self.setPen(pen)
        self.update()

    def shape(self) -> QPainterPath:
        return _stroke_shape(self.path(), self.pen())

    # -- resize (scales the points into the dragged bounding rect) ---------

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        delta = self.mapFromScene(self.mapToScene(QPointF()) + delta_scene) - QPointF()
        old = self._points_rect()
        new = _resize_rect_by_handle(old, index, delta)
        if new.width() < 1 or new.height() < 1:
            return
        sx = new.width() / max(old.width(), 1.0)
        sy = new.height() / max(old.height(), 1.0)
        self.prepareGeometryChange()
        self._points = [
            QPointF(new.x() + (p.x() - old.x()) * sx, new.y() + (p.y() - old.y()) * sy)
            for p in self._points
        ]
        self._rebuild()
        self._handles.update_positions(self._points_rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self._points_rect())
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        option.state &= ~QStyle.StateFlag.State_Selected  # type: ignore[assignment]
        super().paint(painter, option, widget)
        if self.isSelected():
            half = self.pen().widthF() / 2.0
            _paint_selection_rect(painter, self._points_rect().adjusted(-half, -half, half, half))


# ═══════════════════════════════════════════════════════════════════════════
#  StepItem — numbered marker
# ═══════════════════════════════════════════════════════════════════════════

def _contrast_color(color: QColor) -> QColor:
    """Black or white, whichever reads better on *color*."""
    lum = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
    return QColor("#1E1E1E") if lum > 160 else QColor("#FFFFFF")


class StepItem(QGraphicsItem):
    """Filled circle with a bold number — for "step 1, 2, 3" call-outs."""

    DIAMETER = 28.0
    MIN_DIAMETER = 16.0

    def __init__(
        self,
        number: int,
        center: QPointF,
        color: QColor = QColor("#FF4444"),
        diameter: float = DIAMETER,
        parent: Optional[QGraphicsItem] = None,
    ) -> None:
        super().__init__(parent)
        self._number = int(number)
        self._color = QColor(color)
        self._diameter = float(diameter)
        self.setPos(center.x() - diameter / 2.0, center.y() - diameter / 2.0)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._handles = HandleGroup(self, self._on_handle_drag)

    @property
    def number(self) -> int:
        return self._number

    def set_number(self, number: int) -> None:
        self._number = int(number)
        self.update()

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def _content_rect(self) -> QRectF:
        return QRectF(0, 0, self._diameter, self._diameter)

    def boundingRect(self) -> QRectF:
        return self._content_rect().adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self._content_rect())
        return path

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self._content_rect()

        ring = QColor(255, 255, 255, self._color.alpha())
        painter.setPen(QPen(ring, 2.0))
        painter.setBrush(QBrush(self._color))
        painter.drawEllipse(rect.adjusted(1, 1, -1, -1))

        text = str(self._number)
        scale = 0.55 if len(text) <= 2 else 0.42
        font = QFont("Sans Serif")
        font.setBold(True)
        font.setPixelSize(max(6, int(self._diameter * scale)))
        painter.setFont(font)
        painter.setPen(_contrast_color(self._color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        if self.isSelected():
            _paint_selection_rect(painter, rect)

    def mouseDoubleClickEvent(self, event) -> None:
        value, ok = QInputDialog.getInt(
            event.widget(), "Step number", "Number:", self._number, 0, 9999,
        )
        if ok:
            self.set_number(value)
        event.accept()

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        d = max(abs(delta_scene.x()), abs(delta_scene.y()))
        if index in (0, 1, 2, 7):
            sign = -1 if (delta_scene.x() < 0 or delta_scene.y() < 0) else 1
        else:
            sign = 1 if (delta_scene.x() > 0 or delta_scene.y() > 0) else -1
        new_d = max(self.MIN_DIAMETER, self._diameter + sign * d)
        if abs(new_d - self._diameter) < 0.5:
            return
        grow = new_d - self._diameter
        self.prepareGeometryChange()
        self.setPos(self.pos() - QPointF(grow / 2.0, grow / 2.0))   # keep the centre
        self._diameter = new_d
        self._handles.update_positions(self._content_rect())
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self._content_rect())
        return super().itemChange(change, value)


# ═══════════════════════════════════════════════════════════════════════════
#  SpotlightItem — dims everything except the selected area
# ═══════════════════════════════════════════════════════════════════════════

class SpotlightItem(ResizableRectItem):
    """Dims the whole image except a movable / resizable "hole".

    ``rect()`` is the hole (rectangle or ellipse).  The dimmed area is computed
    from the scene rect at paint time, so it follows crops and replaced images.
    Because it paints at its layer's Z-value it dims everything *below* that
    layer; annotations on higher layers stay un-dimmed.  With several
    spotlights the top-most visible one paints a single dimming with all the
    holes cut out (its ``dim_alpha`` wins).
    """

    DEFAULT_DIM_ALPHA = 160
    _HUGE = 1.0e5           # bounding rect covering any realistic scene

    def __init__(
        self,
        rect: QRectF,
        shape: str = "rect",
        dim_alpha: int = DEFAULT_DIM_ALPHA,
        parent: Optional[QGraphicsItem] = None,
    ) -> None:
        super().__init__(rect, parent)
        self._hole_shape = shape if shape in ("rect", "ellipse") else "rect"
        self._dim_alpha = max(0, min(255, int(dim_alpha)))
        self.setPen(QPen(Qt.PenStyle.NoPen))

    # -- options -----------------------------------------------------------

    def set_shape(self, shape: str) -> None:
        if shape in ("rect", "ellipse") and shape != self._hole_shape:
            self.prepareGeometryChange()
            self._hole_shape = shape
            self.update()

    def hole_shape(self) -> str:
        return self._hole_shape

    def set_dim_alpha(self, alpha: int) -> None:
        self._dim_alpha = max(0, min(255, int(alpha)))
        self.update()

    def dim_alpha(self) -> int:
        return self._dim_alpha

    # -- geometry ----------------------------------------------------------

    def _hole_path(self) -> QPainterPath:
        path = QPainterPath()
        if self._hole_shape == "ellipse":
            path.addEllipse(self.rect())
        else:
            path.addRect(self.rect())
        return path

    def boundingRect(self) -> QRectF:
        h = self._HUGE
        return QRectF(-h, -h, 2 * h, 2 * h)

    def shape(self) -> QPainterPath:      # only the hole is clickable
        return self._hole_path()

    def hole_scene_rect(self) -> QRectF:
        return self.mapRectToScene(self.rect())

    def _visible_peers(self) -> List["SpotlightItem"]:
        scene = self.scene()
        if scene is None:
            return [self]
        return [it for it in scene.items() if isinstance(it, SpotlightItem) and it.isVisible()]

    def _owns_dimming(self, peers: List["SpotlightItem"]) -> bool:
        """Only the top-most visible spotlight paints the shared dimming."""
        mine = (self.zValue(), id(self))
        return all((p.zValue(), id(p)) <= mine for p in peers)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        # Several spotlights cooperate: the top-most one dims everything below
        # it *once*, with every spotlight's hole cut out, so each hole stays
        # clear and overlapping dimming never stacks up.
        scene = self.scene()
        if scene is not None:
            peers = self._visible_peers()
            if self._owns_dimming(peers):
                dimmed = QPainterPath()
                dimmed.addRect(self.mapRectFromScene(scene.sceneRect()))
                for peer in peers:
                    hole = self._hole_path() if peer is self else self.mapFromItem(peer, peer._hole_path())
                    dimmed = dimmed.subtracted(hole)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 0, 0, self._dim_alpha))
                painter.drawPath(dimmed)
        if self.isSelected():
            _paint_selection_rect(painter, self.rect())


# ═══════════════════════════════════════════════════════════════════════════
#  StickerItem — emoji / glyph
# ═══════════════════════════════════════════════════════════════════════════

class StickerItem(QGraphicsSimpleTextItem):
    """A big emoji (or any short glyph string) that can be moved and scaled."""

    DEFAULT_SIZE = 48     # px
    MIN_SIZE = 12.0
    EMOJI_FAMILIES = ["Noto Color Emoji", "Segoe UI Emoji", "Apple Color Emoji", "Twemoji Mozilla"]

    def __init__(
        self,
        text: str = "😀",
        center: QPointF = QPointF(0, 0),
        size: float = DEFAULT_SIZE,
        parent: Optional[QGraphicsItem] = None,
    ) -> None:
        super().__init__(text, parent)
        self._size = float(size)
        self._apply_font()
        br = self.boundingRect()
        self.setPos(center.x() - br.width() / 2.0, center.y() - br.height() / 2.0)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._handles = HandleGroup(self, self._on_handle_drag)

    def _apply_font(self) -> None:
        font = QFont()
        font.setFamilies(self.EMOJI_FAMILIES)
        font.setPixelSize(max(6, int(round(self._size))))
        self.setFont(font)

    def _logical_rect(self) -> QRectF:
        return super().boundingRect()

    def _on_handle_drag(self, index: int, delta_scene: QPointF) -> None:
        dy = delta_scene.y()
        dx = delta_scene.x()
        if index in (4, 5, 6):
            delta = dy
        elif index in (0, 1, 2):
            delta = -dy
        else:
            delta = abs(dx) * (1 if dx > 0 else -1)
        new_size = max(self.MIN_SIZE, self._size + delta * 0.5)
        if abs(new_size - self._size) < 0.5:
            return
        self.prepareGeometryChange()
        self._size = new_size
        self._apply_font()
        self._handles.update_positions(self._logical_rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self._handles.set_visible(selected)
            if selected:
                self._handles.update_positions(self._logical_rect())
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        option.state &= ~QStyle.StateFlag.State_Selected  # type: ignore[assignment]
        super().paint(painter, option, widget)
        if self.isSelected():
            _paint_selection_rect(painter, self._logical_rect())
