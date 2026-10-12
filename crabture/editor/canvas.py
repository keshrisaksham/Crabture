"""
canvas.py — EditorContainer (QGraphicsView) for Crabture.

Owns the QGraphicsScene, tool state-machine, drawing/annotation functions,
history (undo/redo), zoom, and export helpers.

V2 additions:
  • Active color state
  • Shift-modifier constraints (square rect, axis-locked arrow)
  • Magnifier tool
  • Speech balloon tool
  • Interactive crop workflow (start → confirm / cancel)
  • zoom_changed signal for slider sync
"""

from __future__ import annotations

import math
import os
from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QInputDialog,
    QMessageBox,
)

from items import ArrowItem, EditableTextItem, MagnifierItem, ResizableRectItem, SpeechBalloonItem
from models import ActionHistory, LayerManager


# Available tool names (V2 — added magnifier, balloon).
TOOLS = (
    "select", "arrow", "rectangle", "highlight",
    "pixelate", "text", "crop", "magnifier", "balloon",
)


class EditorContainer(QGraphicsView):
    """Core editing canvas.

    Manages the scene, the active tool, zoom states, all drawing/annotation
    functions, undo/redo history, and export.
    """

    # Emitted whenever the layer list changes so the UI panel can refresh.
    layers_changed = pyqtSignal()
    tool_changed = pyqtSignal(str)
    # Emitted after any zoom op with the current zoom percentage (100 = 1:1).
    zoom_changed = pyqtSignal(int)

    ZOOM_FACTOR = 1.2
    MIN_ZOOM_PCT = 10
    MAX_ZOOM_PCT = 400

    # ------------------------------------------------------------------
    # Checkerboard background (drawn once into a tiled pixmap)
    # ------------------------------------------------------------------
    @staticmethod
    def _make_checker_brush(cell: int = 16) -> QBrush:
        """Return a tiled QBrush with a soft light-mode checkerboard pattern."""
        size = cell * 2
        pm = QPixmap(size, size)
        pm.fill(QColor("#FFFFFF"))
        p = QPainter(pm)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E8EDF5"))          # very soft blue-grey square
        p.drawRect(0, 0, cell, cell)
        p.drawRect(cell, cell, cell, cell)
        p.end()
        brush = QBrush()
        brush.setTexture(pm)
        return brush

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # -- Scene ---------------------------------------------------------
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(self._make_checker_brush())
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # -- State ---------------------------------------------------------
        self._bg_pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._current_filepath: Optional[str] = None
        self._active_tool: str = "select"
        self._active_color: QColor = QColor("#FF4444")
        self._active_alpha: int = 255
        self._drawing = False
        self._draw_start: Optional[QPointF] = None
        self._temp_item: Optional[QGraphicsItem] = None

        # -- Interactive crop state ----------------------------------------
        self._crop_active = False
        self._crop_rect_item: Optional[QGraphicsRectItem] = None
        self._crop_overlay_items: List[QGraphicsItem] = []
        self._crop_confirm_item: Optional[QGraphicsRectItem] = None
        self._crop_cancel_item: Optional[QGraphicsRectItem] = None

        # -- Models --------------------------------------------------------
        self.layer_manager = LayerManager()
        self.history = ActionHistory()

    # =====================================================================
    #  Image loading
    # =====================================================================

    def load_image(self, filepath: str) -> bool:
        """Load *filepath* as the background pixmap. Returns False on error."""
        pixmap = QPixmap(filepath)
        if pixmap.isNull():
            return False

        # Cancel any pending crop.
        self._cancel_crop_silently()

        # Reset state.
        self._scene.clear()
        self.history.clear()
        self.layer_manager.clear()
        self.layer_manager.add_layer("Layer 1")

        self._current_filepath = filepath
        self._bg_pixmap_item = QGraphicsPixmapItem(pixmap)
        self._bg_pixmap_item.setZValue(0)
        self._scene.addItem(self._bg_pixmap_item)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.reset_zoom()
        self.layers_changed.emit()
        return True

    # =====================================================================
    #  Color management (V2)
    # =====================================================================

    def set_active_color(self, color: QColor) -> None:
        """Set the color used for all newly drawn items.

        Also updates any currently selected items on the canvas.
        Merges the current ``_active_alpha`` into the colour.
        """
        color.setAlpha(self._active_alpha)
        self._active_color = color
        # Post-placement color editing: update selected items immediately.
        self._apply_color_to_selection()

    def set_active_alpha(self, alpha: int) -> None:
        """Set the opacity (0–255) for the active colour.

        If items are selected, update their brush/pen alpha live.
        """
        self._active_alpha = max(0, min(255, alpha))
        self._active_color.setAlpha(self._active_alpha)
        self._apply_color_to_selection()

    def _apply_color_to_selection(self) -> None:
        """Push current color+alpha to every selected item."""
        color = QColor(self._active_color)
        for item in self._scene.selectedItems():
            if item is self._bg_pixmap_item:
                continue
            if isinstance(item, ArrowItem):
                item._color = color
                item.setBrush(QBrush(color))
                item.update()
            elif isinstance(item, EditableTextItem):
                item.setDefaultTextColor(color)
            elif isinstance(item, SpeechBalloonItem):
                item._color = color
                item.setBrush(QBrush(color))
                item.update()
            elif isinstance(item, (ResizableRectItem, QGraphicsRectItem)):
                pen = item.pen()
                pen.setColor(color)
                item.setPen(pen)
                brush = item.brush()
                if brush.style() != Qt.BrushStyle.NoBrush:
                    bc = brush.color()
                    bc.setAlpha(color.alpha())
                    brush.setColor(bc)
                    item.setBrush(brush)
                item.update()

    def active_color(self) -> QColor:
        return QColor(self._active_color)

    def active_alpha(self) -> int:
        return self._active_alpha

    # =====================================================================
    #  Tool management
    # =====================================================================

    def set_tool(self, tool_name: str) -> None:
        if tool_name not in TOOLS:
            return
        # If switching away from crop while a crop is pending, cancel it.
        if self._crop_active and tool_name != "crop":
            self.cancel_crop()
        self._active_tool = tool_name
        if tool_name == "select":
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit(tool_name)

    def active_tool(self) -> str:
        return self._active_tool

    # =====================================================================
    #  Zoom / navigation
    # =====================================================================

    def _current_zoom_pct(self) -> int:
        return max(1, int(self.transform().m11() * 100))

    def _emit_zoom(self) -> None:
        self.zoom_changed.emit(self._current_zoom_pct())

    def zoom_in(self) -> None:
        if self._current_zoom_pct() < self.MAX_ZOOM_PCT:
            self.scale(self.ZOOM_FACTOR, self.ZOOM_FACTOR)
            self._emit_zoom()

    def zoom_out(self) -> None:
        if self._current_zoom_pct() > self.MIN_ZOOM_PCT:
            factor = 1.0 / self.ZOOM_FACTOR
            self.scale(factor, factor)
            self._emit_zoom()

    def reset_zoom(self) -> None:
        self.resetTransform()
        if self._bg_pixmap_item:
            self.fitInView(self._bg_pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._emit_zoom()

    def fit_to_screen(self) -> None:
        """Alias kept for API parity with the UI button label."""
        self.reset_zoom()

    def update_zoom_slider(self, value: int) -> None:
        """Called from UI slider. *value* is a percentage (10–400)."""
        value = max(self.MIN_ZOOM_PCT, min(self.MAX_ZOOM_PCT, value))
        desired = value / 100.0
        current = self.transform().m11()
        if abs(current) < 1e-6:
            return
        factor = desired / current
        self.scale(factor, factor)
        self._emit_zoom()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Ctrl+Wheel → zoom.  Plain wheel → scroll."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    # =====================================================================
    #  Mouse event routing (V2 — with Shift constraints)
    # =====================================================================

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # If a crop is pending, check if user clicked a crop button.
        if self._crop_active:
            if event.button() == Qt.MouseButton.LeftButton:
                scene_pos = self.mapToScene(event.pos())
                hit = self._scene.itemAt(scene_pos, self.transform())
                if hit is self._crop_confirm_item:
                    event.accept()
                    QTimer.singleShot(0, self.confirm_crop)
                    return
                if hit is self._crop_cancel_item:
                    event.accept()
                    QTimer.singleShot(0, self.cancel_crop)
                    return
                # Also check if the hit is a child text label of the buttons.
                if hit is not None and hit.parentItem() in (self._crop_confirm_item, self._crop_cancel_item):
                    parent = hit.parentItem()
                    event.accept()
                    if parent is self._crop_confirm_item:
                        QTimer.singleShot(0, self.confirm_crop)
                    else:
                        QTimer.singleShot(0, self.cancel_crop)
                    return
            super().mousePressEvent(event)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        scene_pos = self.mapToScene(event.pos())
        tool = self._active_tool

        if tool == "select":
            super().mousePressEvent(event)
            return

        self._drawing = True
        self._draw_start = scene_pos

        if tool == "arrow":
            self._temp_item = ArrowItem(scene_pos, scene_pos, color=self._active_color)
            self._scene.addItem(self._temp_item)

        elif tool in ("rectangle", "highlight", "pixelate", "crop"):
            pen_map = {
                "rectangle": QPen(QColor(self._active_color), 3),
                "highlight": QPen(Qt.PenStyle.NoPen),
                "pixelate": QPen(QColor("#AAAAAA"), 1, Qt.PenStyle.DashLine),
                "crop": QPen(QColor("#00FF00"), 2, Qt.PenStyle.DashDotLine),
            }
            hl_color = QColor(255, 255, 0, self._active_alpha if self._active_alpha < 255 else 80)
            brush_map = {
                "rectangle": QBrush(Qt.BrushStyle.NoBrush),
                "highlight": QBrush(hl_color),
                "pixelate": QBrush(QColor(128, 128, 128, 40)),
                "crop": QBrush(QColor(0, 255, 0, 30)),
            }
            rect_item = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
            rect_item.setPen(pen_map[tool])
            rect_item.setBrush(brush_map[tool])
            self._temp_item = rect_item
            self._scene.addItem(rect_item)

        elif tool == "text":
            self._drawing = False
            self.add_text(scene_pos)

        elif tool == "magnifier":
            self._drawing = False
            self.apply_magnifier(scene_pos)

        elif tool == "balloon":
            self._drawing = False
            self.add_speech_balloon(scene_pos)

        else:
            self._drawing = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._drawing or self._draw_start is None:
            super().mouseMoveEvent(event)
            return

        scene_pos = self.mapToScene(event.pos())
        tool = self._active_tool
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if tool == "arrow" and isinstance(self._temp_item, ArrowItem):
            end = scene_pos
            if shift:
                # Lock to horizontal or vertical based on primary drag direction.
                dx = abs(end.x() - self._draw_start.x())
                dy = abs(end.y() - self._draw_start.y())
                if dx >= dy:
                    end = QPointF(end.x(), self._draw_start.y())
                else:
                    end = QPointF(self._draw_start.x(), end.y())
            self._temp_item.set_end(end)

        elif tool in ("rectangle", "highlight", "pixelate", "crop") and isinstance(self._temp_item, QGraphicsRectItem):
            r = QRectF(self._draw_start, scene_pos).normalized()
            if shift and tool in ("rectangle", "crop"):
                # Force 1:1 aspect ratio (perfect square).
                side = max(r.width(), r.height())
                r.setWidth(side)
                r.setHeight(side)
            self._temp_item.setRect(r)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._drawing:
            super().mouseReleaseEvent(event)
            return

        scene_pos = self.mapToScene(event.pos())
        tool = self._active_tool
        self._drawing = False

        if tool == "arrow" and isinstance(self._temp_item, ArrowItem):
            self._finalize_arrow(self._temp_item)

        elif tool == "rectangle" and isinstance(self._temp_item, QGraphicsRectItem):
            self._finalize_rectangle(self._temp_item)

        elif tool == "highlight" and isinstance(self._temp_item, QGraphicsRectItem):
            self._finalize_highlight(self._temp_item)

        elif tool == "pixelate" and isinstance(self._temp_item, QGraphicsRectItem):
            self._finalize_pixelate(self._temp_item)

        elif tool == "crop" and isinstance(self._temp_item, QGraphicsRectItem):
            self._start_interactive_crop(self._temp_item)

        self._temp_item = None
        self._draw_start = None

    # =====================================================================
    #  Key events (Enter → confirm crop, Esc → cancel crop)
    # =====================================================================

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._crop_active:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.confirm_crop()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Escape:
                self.cancel_crop()
                event.accept()
                return

        # ── ShareX-style single-key tool shortcuts ────────────────────
        # Only activate when no text editor has focus.
        if not self._is_text_editing():
            _SHORTCUT_MAP = {
                Qt.Key.Key_R: "rectangle",
                Qt.Key.Key_A: "arrow",
                Qt.Key.Key_T: "text",
                Qt.Key.Key_H: "highlight",
                Qt.Key.Key_M: "magnifier",
                Qt.Key.Key_O: "pixelate",
                Qt.Key.Key_C: "crop",
                Qt.Key.Key_S: "select",
            }
            tool = _SHORTCUT_MAP.get(event.key())
            if tool is not None and not event.modifiers():
                self.set_tool(tool)
                event.accept()
                return

        super().keyPressEvent(event)

    def _is_text_editing(self) -> bool:
        """Return True if any QGraphicsTextItem currently has editor focus."""
        focus_item = self._scene.focusItem()
        if focus_item is None:
            return False
        from PyQt6.QtWidgets import QGraphicsTextItem
        return isinstance(focus_item, QGraphicsTextItem)

    # =====================================================================
    #  Drawing & annotation helpers
    # =====================================================================

    # -- Auto-layer helper -------------------------------------------------

    _tool_counters: dict = {}   # class-level counter per tool type

    def _add_item_to_new_layer(self, item, label: str) -> str:
        """Create a new uniquely-named layer, add *item* to it, record history.

        This is called by every _finalize_* method so that each drawn element
        automatically lives on its own dedicated layer.  Returns the new lid.
        """
        # Increment per-tool counter so layers are named e.g. "Arrow 1", "Arrow 2"
        count = EditorContainer._tool_counters.get(label, 0) + 1
        EditorContainer._tool_counters[label] = count
        layer_name = f"{label} {count}"

        lid = self.layer_manager.add_layer(layer_name)
        self.layer_manager.add_item_to_layer(lid, item)
        self.history.record_action({"type": "add_item", "item": item, "layer": lid})
        self.layers_changed.emit()
        return lid

    # -- Arrow -------------------------------------------------------------

    def draw_arrow(self, start: QPointF, end: QPointF) -> ArrowItem:
        item = ArrowItem(start, end, color=self._active_color)
        self._scene.addItem(item)
        return item

    def _finalize_arrow(self, item: ArrowItem) -> None:
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._add_item_to_new_layer(item, "Arrow")

    # -- Rectangle ---------------------------------------------------------

    def draw_rectangle(self, rect: QRectF) -> QGraphicsRectItem:
        item = ResizableRectItem(rect)
        item.setPen(QPen(QColor(self._active_color), 3))
        item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(item)
        return item

    def _finalize_rectangle(self, item: QGraphicsRectItem) -> None:
        # Replace plain QGraphicsRectItem with ResizableRectItem.
        rect = item.rect()
        pen = item.pen()
        brush = item.brush()
        self._scene.removeItem(item)

        resizable = ResizableRectItem(rect)
        resizable.setPen(pen)
        resizable.setBrush(brush)
        resizable.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(resizable)
        self._add_item_to_new_layer(resizable, "Rectangle")

    # -- Highlight ---------------------------------------------------------

    def apply_highlight(self, rect: QRectF) -> QGraphicsRectItem:
        alpha = self._active_alpha if self._active_alpha < 255 else 80
        item = ResizableRectItem(rect)
        item.setPen(QPen(Qt.PenStyle.NoPen))
        item.setBrush(QBrush(QColor(255, 255, 0, alpha)))
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(item)
        return item

    def _finalize_highlight(self, item: QGraphicsRectItem) -> None:
        # Replace plain QGraphicsRectItem with ResizableRectItem.
        rect = item.rect()
        pen = item.pen()
        brush = item.brush()
        self._scene.removeItem(item)

        resizable = ResizableRectItem(rect)
        resizable.setPen(pen)
        resizable.setBrush(brush)
        resizable.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(resizable)
        self._add_item_to_new_layer(resizable, "Highlight")

    # -- Pixelate ----------------------------------------------------------

    def apply_pixelate(self, rect: QRectF) -> Optional[QGraphicsPixmapItem]:
        """Grab the pixels under *rect*, pixelate them, and place as item."""
        if self._bg_pixmap_item is None:
            return None
        bg = self._bg_pixmap_item.pixmap()
        src = rect.intersected(QRectF(bg.rect()))
        if src.isEmpty():
            return None

        sub = bg.copy(src.toRect())
        if sub.isNull():
            return None

        block_size = 10
        w, h = max(1, sub.width()), max(1, sub.height())
        small = sub.scaled(
            max(1, w // block_size),
            max(1, h // block_size),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        pixelated = small.scaled(
            w, h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

        item = QGraphicsPixmapItem(pixelated)
        item.setPos(src.topLeft())
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(item)
        return item

    def _finalize_pixelate(self, preview_rect: QGraphicsRectItem) -> None:
        rect = preview_rect.rect()
        self._scene.removeItem(preview_rect)

        item = self.apply_pixelate(rect)
        if item is None:
            return

        self._add_item_to_new_layer(item, "Pixelate")

    # -- Text --------------------------------------------------------------

    def add_text(self, pos: QPointF, text_string: str | None = None) -> Optional[EditableTextItem]:
        if text_string is None:
            text_string, ok = QInputDialog.getText(
                self, "Add Text", "Enter text:", text="Annotation"
            )
            if not ok or not text_string:
                return None

        item = EditableTextItem(text_string, pos, color=self._active_color)
        self._scene.addItem(item)
        self._add_item_to_new_layer(item, "Text")
        return item

    # -- Magnifier (V3 — dynamic live paint) ---------------------------------

    def apply_magnifier(
        self,
        pos: QPointF,
        radius: float = 80.0,
        zoom_factor: float = 2.0,
    ) -> Optional[MagnifierItem]:
        """Create a dynamic circular magnifier at *pos*.

        The MagnifierItem renders from the background pixmap in its
        ``paint()`` override, so dragging/resizing updates live.
        """
        if self._bg_pixmap_item is None:
            return None

        item = MagnifierItem(self._bg_pixmap_item, pos, radius, zoom_factor)
        self._scene.addItem(item)

        self._add_item_to_new_layer(item, "Magnifier")
        return item

    # -- Speech Balloon (V2) -----------------------------------------------

    def add_speech_balloon(
        self, pos: QPointF, text_string: str | None = None,
    ) -> Optional[SpeechBalloonItem]:
        """Create a comic-style speech bubble at *pos*."""
        if text_string is None:
            text_string, ok = QInputDialog.getText(
                self, "Speech Balloon", "Enter text:", text="Hello!"
            )
            if not ok or not text_string:
                return None

        item = SpeechBalloonItem(text_string, pos, color=self._active_color)
        self._scene.addItem(item)

        self._add_item_to_new_layer(item, "Balloon")
        return item

    # =====================================================================
    #  Interactive crop (V3 — pure QGraphicsItem, no proxy widgets)
    # =====================================================================

    @staticmethod
    def _make_crop_button(text: str, bg_color: str, x: float, y: float) -> QGraphicsRectItem:
        """Create a simple painted rectangle with centred text label."""
        btn_w, btn_h = 36.0, 28.0
        btn = QGraphicsRectItem(QRectF(0, 0, btn_w, btn_h))
        btn.setPen(QPen(Qt.PenStyle.NoPen))
        btn.setBrush(QBrush(QColor(bg_color)))
        btn.setZValue(10000)
        btn.setPos(x, y)
        # Text label as child item (inherits parent's scene membership).
        lbl = QGraphicsSimpleTextItem(text, btn)
        lbl.setBrush(QBrush(QColor("#FFFFFF")))
        font = QFont("Sans Serif", 14)
        font.setBold(True)
        lbl.setFont(font)
        lbl_br = lbl.boundingRect()
        lbl.setPos((btn_w - lbl_br.width()) / 2, (btn_h - lbl_br.height()) / 2)
        return btn

    def _start_interactive_crop(self, selection_rect: QGraphicsRectItem) -> None:
        """Enter the interactive crop state.  Darkens outside the selection."""
        self._crop_active = True
        self._crop_rect_item = selection_rect

        crop_r = selection_rect.rect()
        scene_r = self._scene.sceneRect()
        dark_brush = QBrush(QColor(0, 0, 0, 160))
        no_pen = QPen(Qt.PenStyle.NoPen)

        # Four rects covering everything outside the crop selection.
        top = QGraphicsRectItem(QRectF(scene_r.left(), scene_r.top(), scene_r.width(), crop_r.top() - scene_r.top()))
        bot = QGraphicsRectItem(QRectF(scene_r.left(), crop_r.bottom(), scene_r.width(), scene_r.bottom() - crop_r.bottom()))
        left = QGraphicsRectItem(QRectF(scene_r.left(), crop_r.top(), crop_r.left() - scene_r.left(), crop_r.height()))
        right = QGraphicsRectItem(QRectF(crop_r.right(), crop_r.top(), scene_r.right() - crop_r.right(), crop_r.height()))

        for overlay in (top, bot, left, right):
            overlay.setPen(no_pen)
            overlay.setBrush(dark_brush)
            overlay.setZValue(9998)
            self._scene.addItem(overlay)
            self._crop_overlay_items.append(overlay)

        # Bring crop rect to top.
        selection_rect.setZValue(9999)

        # ── Pure QGraphicsItem buttons (no proxy widgets) ───────────────
        btn_w = 36.0
        spacing = 6.0
        bx = crop_r.right() - (btn_w * 2 + spacing)
        by = crop_r.bottom() + 6

        self._crop_confirm_item = self._make_crop_button("✔", "#00C853", bx, by)
        self._scene.addItem(self._crop_confirm_item)
        self._crop_overlay_items.append(self._crop_confirm_item)

        self._crop_cancel_item = self._make_crop_button("✖", "#E53935", bx + btn_w + spacing, by)
        self._scene.addItem(self._crop_cancel_item)
        self._crop_overlay_items.append(self._crop_cancel_item)

        # Grab focus so key events (Enter / Escape) reach us.
        self.setFocus()

    def confirm_crop(self) -> None:
        """Execute the crop and clean up overlays.

        Snapshots the rect *before* removing anything from the scene.
        """
        if not self._crop_active or self._crop_rect_item is None:
            return
        rect = QRectF(self._crop_rect_item.rect())
        self._cleanup_crop_overlays()
        self.crop_image(rect)

    def cancel_crop(self) -> None:
        """Abort crop: remove darkened overlay and selection rect."""
        if not self._crop_active:
            return
        self._cleanup_crop_overlays()

    def _cancel_crop_silently(self) -> None:
        """Cancel without requiring _crop_active guard (used on image load)."""
        self._cleanup_crop_overlays()

    def _cleanup_crop_overlays(self) -> None:
        """Remove all crop-related scene items safely.

        Because we only use plain QGraphicsItems (no proxy widgets),
        we just null out Python refs and remove from scene.
        """
        # Null out refs FIRST so no code re-enters with stale pointers.
        self._crop_confirm_item = None
        self._crop_cancel_item = None
        self._crop_active = False

        crop_rect = self._crop_rect_item
        self._crop_rect_item = None

        overlays = self._crop_overlay_items[:]
        self._crop_overlay_items.clear()

        # Remove from scene AFTER all Python refs are cleared.
        if crop_rect is not None:
            try:
                self._scene.removeItem(crop_rect)
            except RuntimeError:
                pass
        for overlay in overlays:
            try:
                self._scene.removeItem(overlay)
            except RuntimeError:
                pass

    def crop_image(self, rect: QRectF) -> None:
        """Crop the background pixmap to *rect* and reposition everything.

        Takes a snapshot of scene items before iterating to avoid issues
        with deleted C++ objects from the crop overlay cleanup.
        """
        if self._bg_pixmap_item is None:
            return
        bg = self._bg_pixmap_item.pixmap()
        cropped_rect = rect.intersected(QRectF(bg.rect())).toRect()
        if cropped_rect.isEmpty():
            return

        cropped = bg.copy(cropped_rect)
        if cropped.isNull():
            return

        self.history.record_action({
            "type": "crop",
            "old_pixmap": bg,
            "new_pixmap": cropped,
            "offset": QPointF(cropped_rect.topLeft()),
        })

        self._bg_pixmap_item.setPixmap(cropped)
        self._bg_pixmap_item.setPos(0, 0)
        self._scene.setSceneRect(QRectF(cropped.rect()))

        ox = cropped_rect.x()
        oy = cropped_rect.y()
        new_scene_rect = QRectF(cropped.rect())
        # Snapshot the item list to avoid iterating over stale C++ refs.
        items_snapshot = list(self._scene.items())
        to_remove = []
        for scene_item in items_snapshot:
            if scene_item is self._bg_pixmap_item:
                continue
            # Skip child items (e.g. button text labels — they move with parent).
            if scene_item.parentItem() is not None:
                continue
            try:
                scene_item.moveBy(-ox, -oy)
                # Check if the item falls completely outside the new canvas.
                item_br = scene_item.sceneBoundingRect()
                if not item_br.intersects(new_scene_rect):
                    to_remove.append(scene_item)
            except RuntimeError:
                # C++ object already deleted — skip.
                pass

        for dead_item in to_remove:
            try:
                self._scene.removeItem(dead_item)
                self.layer_manager.remove_item(dead_item)
            except RuntimeError:
                pass

        self.reset_zoom()

    # =====================================================================
    #  Undo / Redo
    # =====================================================================

    def undo(self) -> None:
        action = self.history.get_undo_action()
        if action is None:
            return
        atype = action.get("type")

        if atype == "add_item":
            item = action["item"]
            self._scene.removeItem(item)
            self.layer_manager.remove_item(item)

        elif atype == "remove_item":
            item = action["item"]
            self._scene.addItem(item)
            lid = action.get("layer")
            if lid:
                self.layer_manager.add_item_to_layer(lid, item)

        elif atype == "crop":
            old_pix = action["old_pixmap"]
            offset = action["offset"]
            if self._bg_pixmap_item:
                self._bg_pixmap_item.setPixmap(old_pix)
                self._bg_pixmap_item.setPos(0, 0)
                self._scene.setSceneRect(QRectF(old_pix.rect()))
                ox, oy = offset.x(), offset.y()
                for scene_item in list(self._scene.items()):
                    if scene_item is self._bg_pixmap_item:
                        continue
                    try:
                        scene_item.moveBy(ox, oy)
                    except RuntimeError:
                        pass
                self.reset_zoom()

        self.layers_changed.emit()

    def redo(self) -> None:
        action = self.history.get_redo_action()
        if action is None:
            return
        atype = action.get("type")

        if atype == "add_item":
            item = action["item"]
            self._scene.addItem(item)
            lid = action.get("layer")
            if lid:
                self.layer_manager.add_item_to_layer(lid, item)

        elif atype == "remove_item":
            item = action["item"]
            self._scene.removeItem(item)
            self.layer_manager.remove_item(item)

        elif atype == "crop":
            new_pix = action["new_pixmap"]
            offset = action["offset"]
            if self._bg_pixmap_item:
                self._bg_pixmap_item.setPixmap(new_pix)
                self._bg_pixmap_item.setPos(0, 0)
                self._scene.setSceneRect(QRectF(new_pix.rect()))
                ox, oy = offset.x(), offset.y()
                for scene_item in list(self._scene.items()):
                    if scene_item is self._bg_pixmap_item:
                        continue
                    try:
                        scene_item.moveBy(-ox, -oy)
                    except RuntimeError:
                        pass
                self.reset_zoom()

        self.layers_changed.emit()

    # =====================================================================
    #  Export
    # =====================================================================

    def _render_to_pixmap(self) -> QPixmap:
        """Flatten the entire visible scene into a QPixmap."""
        rect = self._scene.sceneRect()
        pixmap = QPixmap(int(rect.width()), int(rect.height()))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._scene.render(painter, QRectF(pixmap.rect()), rect)
        painter.end()
        return pixmap

    def save_overwrite(self) -> bool:
        """Overwrite the original file."""
        if not self._current_filepath:
            return self.save_as()
        pixmap = self._render_to_pixmap()
        ok = pixmap.save(self._current_filepath)
        if ok:
            self.load_image(self._current_filepath)
        return ok

    def save_as(self, filepath: str | None = None) -> bool:
        if filepath is None:
            filepath, _ = QFileDialog.getSaveFileName(
                self,
                "Save Image As",
                os.path.expanduser("~/Downloads/Crabshots/edited.png"),
                "Images (*.png *.jpg *.bmp *.webp)",
            )
        if not filepath:
            return False
        pixmap = self._render_to_pixmap()
        return pixmap.save(filepath)

    def copy_to_clipboard(self) -> None:
        pixmap = self._render_to_pixmap()
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setPixmap(pixmap)

    # =====================================================================
    #  Delete selected items
    # =====================================================================

    def delete_selected(self) -> None:
        for item in self._scene.selectedItems():
            if item is self._bg_pixmap_item:
                continue
            lid = None
            for layer_id, _, _ in self.layer_manager.layer_names():
                if item in self.layer_manager.get_items_for_layer(layer_id):
                    lid = layer_id
                    break
            self._scene.removeItem(item)
            self.layer_manager.remove_item(item)
            if lid and not self.layer_manager.get_items_for_layer(lid):
                self.layer_manager.delete_layer(lid)
            self.history.record_action({"type": "remove_item", "item": item, "layer": lid})
        self.layers_changed.emit()
