"""
items.py — Custom QGraphicsItem subclasses for Crabture.

V3 rewrite:
  • ResizeHandle / HandleGroup — proper child-item handles with hover cursors.
  • ArrowItem, EditableTextItem, SpeechBalloonItem — annotation items.
  • MagnifierItem — dynamic live-paint magnifier (grabs from bg pixmap).
  • ResizableRectItem — rectangles / highlights with drag-to-resize.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)


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
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)

    # -- Hover cursors -----------------------------------------------------

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.setCursor(QCursor(_HANDLE_CURSORS.get(self._index, Qt.CursorShape.ArrowCursor)))
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
    """Draw a dashed selection rectangle."""
    painter.setPen(QPen(SELECTION_DASH_COLOR, 1.0, Qt.PenStyle.DashLine))
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
    ) -> None:
        super().__init__(parent)
        self._start = start
        self._end = end
        self._color = color
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
        head_base_len = max(length - self.HEAD_LENGTH, 0)
        shaft_half = self.SHAFT_WIDTH / 2.0
        head_half = self.HEAD_WIDTH / 2.0

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
    ) -> None:
        super().__init__(text, parent)
        self.setPos(pos)
        self.setDefaultTextColor(color)
        self._base_font_size: float = float(font_size)
        font = QFont("Sans Serif", font_size)
        font.setBold(True)
        self.setFont(font)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        self._handles = HandleGroup(self, self._on_handle_drag)

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
        self._handles.update_positions(self._logical_rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
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
        bg_pixmap_item: QGraphicsPixmapItem,
        center: QPointF,
        radius: float = 80.0,
        zoom_factor: float = 2.0,
        parent: Optional[QGraphicsItem] = None,
    ) -> None:
        super().__init__(parent)
        self._bg_item = bg_pixmap_item
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

        # --- Grab background under our current scene position ---
        try:
            bg_pix = self._bg_item.pixmap()
        except RuntimeError:
            return
        if bg_pix.isNull():
            return

        # Our scene-center
        scene_center = self.mapToScene(QPointF(self._radius, self._radius))
        src_radius = self._radius / self._zoom
        src_rect = QRectF(
            scene_center.x() - src_radius,
            scene_center.y() - src_radius,
            src_radius * 2, src_radius * 2,
        )
        # Clamp to bg bounds
        bg_rect = QRectF(bg_pix.rect())
        src_rect = src_rect.intersected(bg_rect)
        if src_rect.isEmpty():
            # Draw empty circle
            painter.setPen(QPen(self.BORDER_COLOR, self.BORDER_WIDTH))
            painter.setBrush(QBrush(QColor(40, 40, 40, 180)))
            painter.drawEllipse(content)
            return

        sub = bg_pix.copy(src_rect.toRect())
        if sub.isNull():
            return

        scaled = sub.scaled(
            int(d), int(d),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        # Clip to circle
        clip = QPainterPath()
        clip.addEllipse(content)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, scaled)

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
    ) -> None:
        super().__init__(parent)
        self.setPos(pos)
        self._color = color

        self._text_item = EditableTextItem(text, QPointF(self.PADDING, self.PADDING), QColor("#1E1E1E"), 13, self)
        self._text_item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        # Hide the text-item's own handles (balloon's handles control everything).
        self._text_item._handles.set_visible(False)

        self._rebuild()

        self.setPen(QPen(QColor("#333333"), 2))
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
        tr = self._text_item.boundingRect()
        bw = tr.width() + self.PADDING * 2
        bh = tr.height() + self.PADDING * 2

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

class ResizableRectItem(QGraphicsRectItem):
    """QGraphicsRectItem with proper child resize handles."""

    def __init__(self, rect: QRectF, parent: Optional[QGraphicsItem] = None) -> None:
        super().__init__(rect, parent)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._handles = HandleGroup(self, self._on_handle_drag)

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
