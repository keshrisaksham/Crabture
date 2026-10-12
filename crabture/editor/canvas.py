"""
editor/canvas.py — EditorContainer (QGraphicsView) for Crabture.

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
from contextlib import contextmanager
from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QImageWriter,
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

from crabture.editor.items import (
    ArrowItem,
    BlurItem,
    EditableTextItem,
    EllipseItem,
    FreehandItem,
    LineItem,
    MagnifierItem,
    PixelateItem,
    ResizableRectItem,
    SpeechBalloonItem,
    SpotlightItem,
    StepItem,
    StickerItem,
    chrome_suppressed,
)
from crabture.editor.models import ActionHistory, LayerManager
from crabture.settings import keymap
from crabture.settings import tool_config


# Available tool names (V2 — added magnifier, balloon; V4 — ellipse … eyedropper).
TOOLS = (
    "select", "arrow", "rectangle", "highlight",
    "pixelate", "blur", "text", "crop", "magnifier", "balloon",
    "ellipse", "line", "pen", "step", "spotlight", "sticker", "eyedropper",
)


class EditorContainer(QGraphicsView):
    """Core editing canvas.

    Manages the scene, the active tool, zoom states, all drawing/annotation
    functions, undo/redo history, and export.
    """

    # Emitted whenever the layer list changes so the UI panel can refresh.
    layers_changed = pyqtSignal()
    tool_changed = pyqtSignal(str)
    # Emitted after a tool's saved defaults (colour, opacity, intensity, spotlight
    # dim/shape, sticker glyph) were loaded into the active state, so panels can
    # re-read active_color()/active_alpha()/active_intensity()/spotlight_*().
    tool_defaults_applied = pyqtSignal()
    # Emitted after any zoom op with the current zoom percentage (100 = 1:1).
    zoom_changed = pyqtSignal(int)
    # Emitted when the "unsaved changes" state flips (True = modified).
    modified_changed = pyqtSignal(bool)
    # Emitted by the eyedropper tool with the sampled (opaque) colour.
    color_picked = pyqtSignal(QColor)

    ZOOM_FACTOR = 1.2
    MIN_ZOOM_PCT = 10
    MAX_ZOOM_PCT = 400

    # ------------------------------------------------------------------
    # Checkerboard background (drawn once into a tiled pixmap)
    # ------------------------------------------------------------------
    @staticmethod
    def _make_checker_brush(cell: int = 16, color_a: str = "#FFFFFF", color_b: str = "#E8EDF5") -> QBrush:
        """Return a tiled QBrush with a soft checkerboard pattern."""
        size = cell * 2
        pm = QPixmap(size, size)
        pm.fill(QColor(color_a))
        p = QPainter(pm)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color_b))
        p.drawRect(0, 0, cell, cell)
        p.drawRect(cell, cell, cell, cell)
        p.end()
        brush = QBrush()
        brush.setTexture(pm)
        return brush

    def apply_theme_colors(self) -> None:
        """Pick up the current theme: solid surround, checkerboard behind the image.

        Connected to ``theme.signals.changed`` by the main window.
        """
        try:
            from crabture.ui import theme
            t = theme.tokens()
        except Exception:
            t = {}
        self._canvas_bg = QColor(t.get("canvas_bg", "#E3E6EE"))
        self._checker_brush = self._make_checker_brush(
            16, t.get("checker_a", "#FFFFFF"), t.get("checker_b", "#E8EDF5")
        )
        self.setBackgroundBrush(QBrush(self._canvas_bg))
        self.viewport().update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        """Solid surround, with the transparency checkerboard only under the image."""
        super().drawBackground(painter, rect)
        scene_rect = self._scene.sceneRect()
        if self._bg_pixmap_item is not None and not scene_rect.isEmpty():
            painter.fillRect(scene_rect.intersected(rect), self._checker_brush)

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
        self.apply_theme_colors()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # -- State ---------------------------------------------------------
        self._bg_pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._current_filepath: Optional[str] = None
        self._fit_mode: bool = True
        # Eyedropper: magnifier overlay, press-and-slide picking, tool to return to on Esc.
        self._loupe = None
        self._eyedrop_down: bool = False
        self._tool_before_eyedropper: str = "select"
        self._active_tool: str = "select"
        self._active_color: QColor = QColor("#FF4444")
        self._active_alpha: int = 255
        self._active_intensity: int = 10
        self._active_sticker: str = "😀"
        self._spotlight_shape: str = "rect"
        self._spotlight_dim: int = SpotlightItem.DEFAULT_DIM_ALPHA
        # Colour/opacity the user chose in the palette; a tool with a fixed
        # colour overrides the *active* values temporarily and they are
        # restored when switching to a tool that uses the palette colour.
        self._user_color: QColor = QColor(self._active_color)
        self._user_alpha: int = self._active_alpha
        # Tools whose saved intensity / spotlight defaults still have to be
        # loaded the next time they are selected (everything, initially).
        self._defaults_dirty: set = set(TOOLS)
        self._active_sticker = str(tool_config.get("sticker", "glyph") or self._active_sticker)
        tool_config.signals.changed.connect(self._on_tool_config_changed)
        self._step_counter: int = 0
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
        self._modified = False
        # Per-tool counters so layers are named "Arrow 1", "Arrow 2", …
        self._tool_counters: dict = {}

    # =====================================================================
    #  Modified-state tracking
    # =====================================================================

    def is_modified(self) -> bool:
        """True if the history changed since the last load / save."""
        return self._modified

    def mark_clean(self) -> None:
        self._set_modified(False)

    def _set_modified(self, value: bool) -> None:
        if self._modified != value:
            self._modified = value
            self.modified_changed.emit(value)

    def _record(self, action: dict) -> None:
        """Record *action* in the history and flag the document as modified."""
        self.history.record_action(action)
        self._set_modified(True)

    # =====================================================================
    #  Image loading
    # =====================================================================

    def load_image(self, filepath: str) -> bool:
        """Load *filepath* as the background pixmap. Returns False on error."""
        return self._load_pixmap(QPixmap(filepath), filepath)

    def load_pixmap(self, pixmap: QPixmap, filepath: Optional[str] = None) -> bool:
        """Like :meth:`load_image` but from memory (e.g. a fresh capture).

        With ``filepath=None`` the document has no file yet, so Save goes to
        "Save As".  The document starts out clean.
        """
        return self._load_pixmap(pixmap, filepath)

    def _load_pixmap(self, pixmap: QPixmap, filepath: Optional[str]) -> bool:
        if pixmap is None or pixmap.isNull():
            return False

        # Cancel any pending crop.
        self._cancel_crop_silently()

        # Reset state.
        self._scene.clear()
        self.history.clear()
        self.layer_manager.clear()
        self.layer_manager.add_layer("Layer 1")
        self._tool_counters = {}
        self._step_counter = 0
        self._set_modified(False)

        self._current_filepath = filepath
        self._bg_pixmap_item = QGraphicsPixmapItem(pixmap)
        self._bg_pixmap_item.setZValue(0)
        self._scene.addItem(self._bg_pixmap_item)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.reset_zoom()
        self.layers_changed.emit()
        return True

    def clear_image(self) -> None:
        """Drop the current image and all annotations (used by File > Close Image)."""
        self._cancel_crop_silently()
        self._scene.clear()
        self._bg_pixmap_item = None
        self._current_filepath = None
        self.history.clear()
        self.layer_manager.clear()
        self.layer_manager.add_layer("Layer 1")
        self._tool_counters = {}
        self._step_counter = 0
        self._scene.setSceneRect(QRectF())
        self._set_modified(False)
        self.resetTransform()
        self._emit_zoom()
        self.layers_changed.emit()

    def current_filepath(self) -> Optional[str]:
        """Path of the file the document came from (None for unsaved captures)."""
        return self._current_filepath

    def background_pixmap(self) -> Optional[QPixmap]:
        """The current background image without any annotations (or None)."""
        if self._bg_pixmap_item is None:
            return None
        return self._bg_pixmap_item.pixmap()

    def replace_image(self, pixmap: QPixmap, label: str = "Effects") -> None:
        """Replace the background with *pixmap* (an already-flattened result).

        All annotation items are removed (they are assumed to be baked into
        *pixmap*).  Undoable: undo restores the old background together with
        every item and layer.
        """
        if pixmap is None or pixmap.isNull():
            return
        if self._bg_pixmap_item is None:
            self.load_pixmap(pixmap)
            return

        self._cancel_crop_silently()
        old_pixmap = self._bg_pixmap_item.pixmap()
        old_counter = self._step_counter

        removed = []
        for scene_item in list(self._scene.items()):
            if scene_item is self._bg_pixmap_item or scene_item.parentItem() is not None:
                continue
            try:
                removed.append(self._detach_item(scene_item))
            except RuntimeError:
                pass

        self._apply_background(pixmap)
        self._step_counter = 0
        self._record({
            "type": "replace_image",
            "label": label,
            "old_pixmap": old_pixmap,
            "new_pixmap": pixmap,
            "removed": removed,
            "old_step_counter": old_counter,
        })
        self.layers_changed.emit()

    def _apply_background(self, pixmap: QPixmap) -> None:
        self._bg_pixmap_item.setPixmap(pixmap)
        self._bg_pixmap_item.setPos(0, 0)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.reset_zoom()

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
        self._user_color = QColor(color)
        # Post-placement color editing: update selected items immediately.
        self._apply_color_to_selection()

    def set_active_alpha(self, alpha: int) -> None:
        """Set the opacity (0–255) for the active colour.

        If items are selected, update their brush/pen alpha live.
        """
        self._active_alpha = max(0, min(255, alpha))
        self._user_alpha = self._active_alpha
        self._active_color.setAlpha(self._active_alpha)
        self._user_color.setAlpha(self._active_alpha)
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
            elif isinstance(item, (LineItem, FreehandItem, StepItem)):
                item.set_color(color)
            elif isinstance(item, (SpotlightItem, StickerItem)):
                continue   # colour is meaningless for these
            elif isinstance(item, EllipseItem):
                pen = item.pen()
                pen.setColor(color)
                item.setPen(pen)
                if item.brush().style() != Qt.BrushStyle.NoBrush:
                    bc = QColor(color)
                    bc.setAlpha(getattr(item, "_fill_alpha", color.alpha()))
                    item.setBrush(QBrush(bc))
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
                    if hasattr(item, "_fill_alpha"):
                        # Filled shape: fill follows the new colour at its own opacity.
                        bc = QColor(color)
                        bc.setAlpha(item._fill_alpha)
                    else:
                        bc = brush.color()
                        bc.setAlpha(color.alpha())
                    brush.setColor(bc)
                    item.setBrush(brush)
                item.update()

    def active_color(self) -> QColor:
        return QColor(self._active_color)

    def active_alpha(self) -> int:
        return self._active_alpha

    def set_active_intensity(self, val: int) -> None:
        self._active_intensity = max(1, min(100, val))
        self._apply_intensity_to_selection()

    def _apply_intensity_to_selection(self) -> None:
        """Push current intensity to every selected dynamic item."""
        for item in self._scene.selectedItems():
            if hasattr(item, "set_intensity"):
                item.set_intensity(self._active_intensity)

    def active_intensity(self) -> int:
        return self._active_intensity

    # -- Sticker / spotlight / step settings (V4) -------------------------

    def set_active_sticker(self, text: str) -> None:
        """Glyph / emoji placed by the sticker tool."""
        if text and text.strip():
            self._active_sticker = text.strip()

    def active_sticker(self) -> str:
        return self._active_sticker

    def set_spotlight_shape(self, shape: str) -> None:
        """'rect' or 'ellipse'; applies to new and currently selected spotlights."""
        if shape not in ("rect", "ellipse"):
            return
        self._spotlight_shape = shape
        for item in self._scene.selectedItems():
            if isinstance(item, SpotlightItem):
                item.set_shape(shape)

    def spotlight_shape(self) -> str:
        return self._spotlight_shape

    def set_spotlight_dim(self, alpha: int) -> None:
        """Darkness (0-255 alpha) outside spotlights; new and selected ones."""
        self._spotlight_dim = max(0, min(255, int(alpha)))
        for item in self._scene.selectedItems():
            if isinstance(item, SpotlightItem):
                item.set_dim_alpha(self._spotlight_dim)

    def spotlight_dim(self) -> int:
        return self._spotlight_dim

    def reset_step_counter(self) -> None:
        """Next step marker will be numbered 1 again."""
        self._step_counter = 0

    # =====================================================================
    #  Tool management
    # =====================================================================

    def set_tool(self, tool_name: str) -> None:
        if tool_name not in TOOLS:
            return
        # If switching away from crop while a crop is pending, cancel it.
        if self._crop_active and tool_name != "crop":
            self.cancel_crop()
        if tool_name == "eyedropper" and self._active_tool != "eyedropper":
            self._tool_before_eyedropper = self._active_tool if self._active_tool in TOOLS else "select"
        self._active_tool = tool_name
        if tool_name == "select":
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif tool_name == "eyedropper":
            from crabture.editor.eyedropper_loupe import make_eyedropper_cursor
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(make_eyedropper_cursor())
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        self._set_eyedropper_active(tool_name == "eyedropper")
        self._apply_tool_defaults(tool_name)
        self.tool_changed.emit(tool_name)

    def active_tool(self) -> str:
        return self._active_tool

    # -- Per-tool saved defaults (tool_config) ------------------------------

    def _apply_tool_defaults(self, tool: str, force: bool = False) -> None:
        """Load *tool*'s saved defaults into the active drawing state.

        Never touches the current selection (the state is set directly, not
        through ``set_active_color`` & co.).  A fixed colour/opacity is applied
        every time; intensity and spotlight settings only the first time the
        tool is selected after they changed, so a slider nudged by hand is not
        reset on every tool switch.
        """
        opts = {o.key: o for o in tool_config.TOOL_OPTIONS.get(tool, ())}
        vals = tool_config.values(tool)

        # Colour: a 'color_opt' option with a value is a fixed colour for this tool.
        fixed = ""
        for key in ("color", "fill"):
            if key in opts and opts[key].kind == "color_opt":
                fixed = vals.get(key) or ""
        if fixed:
            alpha = int(vals["alpha"]) if "alpha" in vals else self._user_alpha
            col = QColor(fixed)
            col.setAlpha(alpha)
            self._active_alpha = alpha
            self._active_color = col
        else:
            self._active_alpha = self._user_alpha
            self._active_color = QColor(self._user_color)
            self._active_color.setAlpha(self._active_alpha)

        if force or tool in self._defaults_dirty:
            self._defaults_dirty.discard(tool)
            if tool in ("blur", "pixelate") and "intensity" in vals:
                self._active_intensity = max(1, min(100, int(vals["intensity"])))
            elif tool == "spotlight":
                self._spotlight_dim = max(0, min(255, int(vals["dim"])))
                if vals["shape"] in ("rect", "ellipse"):
                    self._spotlight_shape = vals["shape"]
        self.tool_defaults_applied.emit()

    def _on_tool_config_changed(self, tool_id: str) -> None:
        """A saved tool setting changed: reload it if it affects the current state."""
        if tool_id in ("", "sticker"):
            glyph = str(tool_config.get("sticker", "glyph") or "").strip()
            if glyph:
                self._active_sticker = glyph
        if tool_id == "":
            self._defaults_dirty = set(TOOLS)
            self._apply_tool_defaults(self._active_tool, force=True)
            return
        self._defaults_dirty.add(tool_id)
        if tool_id == self._active_tool:
            self._apply_tool_defaults(tool_id, force=True)

    # =====================================================================
    #  Zoom / navigation
    # =====================================================================

    def _current_zoom_pct(self) -> int:
        return max(1, int(self.transform().m11() * 100))

    def _emit_zoom(self) -> None:
        self.zoom_changed.emit(self._current_zoom_pct())

    def zoom_in(self) -> None:
        if self._current_zoom_pct() < self.MAX_ZOOM_PCT:
            self._fit_mode = False
            self.scale(self.ZOOM_FACTOR, self.ZOOM_FACTOR)
            self._emit_zoom()

    def zoom_out(self) -> None:
        if self._current_zoom_pct() > self.MIN_ZOOM_PCT:
            self._fit_mode = False
            factor = 1.0 / self.ZOOM_FACTOR
            self.scale(factor, factor)
            self._emit_zoom()

    def reset_zoom(self) -> None:
        """Fit the image to the view and stay fitted while the view is resized
        (until the user zooms manually)."""
        self._fit_mode = True
        self._refit()

    def _refit(self) -> None:
        self.resetTransform()
        if self._bg_pixmap_item:
            self.fitInView(self._bg_pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._emit_zoom()

    def is_fit_mode(self) -> bool:
        return self._fit_mode

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # The first fit often happens before the view has its final size
        # (e.g. the editor page was hidden, or panels were still appearing).
        if self._fit_mode and self._bg_pixmap_item is not None:
            self._refit()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._fit_mode and self._bg_pixmap_item is not None:
            QTimer.singleShot(0, lambda: self._fit_mode and self._bg_pixmap_item is not None and self._refit())

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
        if abs(desired - current) > 1e-3:
            self._fit_mode = False
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
            cfg = tool_config.values("arrow")
            self._temp_item = ArrowItem(
                scene_pos, scene_pos, color=self._active_color,
                shaft_width=cfg["shaft_width"], head_length=cfg["head_length"],
                head_width=cfg["head_width"],
            )
            self._scene.addItem(self._temp_item)

        elif tool == "ellipse":
            ellipse = QGraphicsEllipseItem(QRectF(scene_pos, scene_pos))
            ellipse.setPen(self._shape_pen("ellipse"))
            ellipse.setBrush(self._shape_brush("ellipse"))
            self._temp_item = ellipse
            self._scene.addItem(ellipse)

        elif tool == "line":
            cfg = tool_config.values("line")
            self._temp_item = LineItem(
                scene_pos, scene_pos, color=self._active_color,
                width=cfg["width"], round_caps=cfg["round_caps"],
            )
            self._scene.addItem(self._temp_item)

        elif tool == "pen":
            cfg = tool_config.values("pen")
            self._temp_item = FreehandItem(
                scene_pos, color=self._active_color,
                width=cfg["width"], smoothing=cfg["smoothing"],
            )
            self._scene.addItem(self._temp_item)

        elif tool == "step":
            self._drawing = False
            self.add_step(scene_pos)

        elif tool == "sticker":
            self._drawing = False
            self.add_sticker(scene_pos)

        elif tool == "eyedropper":
            self._drawing = False
            self._eyedrop_down = True            # keep picking while the button is held
            self.pick_color_at(scene_pos)

        elif tool in ("rectangle", "highlight", "pixelate", "blur", "crop", "spotlight"):
            hl = tool_config.values("highlight")
            pen_map = {
                "rectangle": self._shape_pen("rectangle"),
                "highlight": QPen(Qt.PenStyle.NoPen),
                "pixelate": QPen(QColor("#AAAAAA"), 1, Qt.PenStyle.DashLine),
                "blur": QPen(QColor("#4A6CF7"), 1, Qt.PenStyle.DashLine),
                "crop": QPen(QColor("#00FF00"), 2, Qt.PenStyle.DashDotLine),
                "spotlight": QPen(QColor("#FFFFFF"), 1, Qt.PenStyle.DashLine),
            }
            hl_color = QColor(hl["color"])
            hl_color.setAlpha(self._active_alpha if self._active_alpha < 255 else hl["alpha"])
            brush_map = {
                "rectangle": self._shape_brush("rectangle"),
                "highlight": QBrush(hl_color),
                "pixelate": QBrush(QColor(128, 128, 128, 40)),
                "blur": QBrush(QColor(74, 108, 247, 40)),
                "crop": QBrush(QColor(0, 255, 0, 30)),
                "spotlight": QBrush(QColor(0, 0, 0, 40)),
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
        if self._active_tool == "eyedropper":
            self._eyedropper_move(event.position().toPoint())
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

        elif tool == "ellipse" and isinstance(self._temp_item, QGraphicsEllipseItem):
            self._temp_item.setRect(self._drag_rect(self._draw_start, scene_pos, shift))

        elif tool == "line" and isinstance(self._temp_item, LineItem):
            end = self._snap_angle(self._draw_start, scene_pos) if shift else scene_pos
            self._temp_item.set_end(end)

        elif tool == "pen" and isinstance(self._temp_item, FreehandItem):
            self._temp_item.add_point(scene_pos)

        elif tool == "spotlight" and isinstance(self._temp_item, QGraphicsRectItem):
            self._temp_item.setRect(self._drag_rect(self._draw_start, scene_pos, shift))

        elif tool in ("rectangle", "highlight", "pixelate", "blur", "crop") and isinstance(self._temp_item, QGraphicsRectItem):
            r = QRectF(self._draw_start, scene_pos).normalized()
            if shift and tool in ("rectangle", "crop"):
                # Force 1:1 aspect ratio (perfect square).
                side = max(r.width(), r.height())
                r.setWidth(side)
                r.setHeight(side)
            self._temp_item.setRect(r)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._eyedrop_down = False
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

        elif tool == "blur" and isinstance(self._temp_item, QGraphicsRectItem):
            self._finalize_blur(self._temp_item)

        elif tool == "crop" and isinstance(self._temp_item, QGraphicsRectItem):
            self._start_interactive_crop(self._temp_item)

        elif tool == "ellipse" and isinstance(self._temp_item, QGraphicsEllipseItem):
            self._finalize_ellipse(self._temp_item)

        elif tool == "line" and isinstance(self._temp_item, LineItem):
            self._finalize_line(self._temp_item)

        elif tool == "pen" and isinstance(self._temp_item, FreehandItem):
            self._finalize_pen(self._temp_item)

        elif tool == "spotlight" and isinstance(self._temp_item, QGraphicsRectItem):
            self._finalize_spotlight(self._temp_item)

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

        if self._active_tool == "eyedropper":
            if event.key() == Qt.Key.Key_Escape:
                back = self._tool_before_eyedropper
                self.set_tool(back if back in TOOLS and back != "eyedropper" else "select")
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                from PyQt6.QtGui import QCursor
                local = self.viewport().mapFromGlobal(QCursor.pos())
                if self.viewport().rect().contains(local):
                    self.pick_color_at(self.mapToScene(local))
                event.accept()
                return

        # ── ShareX-style single-key tool shortcuts ────────────────────
        # Only activate when no text editor has focus.
        if not self._is_text_editing():
            # Key -> tool comes from settings/keymap.py (user-configurable); it returns
            # None for any key with a modifier held.
            tool = keymap.tool_for_key(event, event.modifiers())
            if tool is not None:
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

    MIN_ITEM_SIZE = 5.0   # drags smaller than this (px) are treated as stray clicks

    def _add_item_to_new_layer(self, item, label: str, name: Optional[str] = None) -> str:
        """Create a new uniquely-named layer, add *item* to it, record history.

        This is called by every _finalize_* method so that each drawn element
        automatically lives on its own dedicated layer.  Returns the new lid.
        """
        # Increment per-tool counter so layers are named e.g. "Arrow 1", "Arrow 2"
        if name is None:
            count = self._tool_counters.get(label, 0) + 1
            self._tool_counters[label] = count
            name = f"{label} {count}"
        layer_name = name

        lid = self.layer_manager.add_layer(layer_name)
        self.layer_manager.add_item_to_layer(lid, item)
        info = self.layer_manager.layer_info(lid)
        self._record({
            "type": "add_item", "item": item, "layer": lid,
            "layer_name": layer_name,
            "layer_index": info[1] if info else None,
            "layer_visible": True,
        })
        self.layers_changed.emit()
        return lid

    # -- Layer-aware attach / detach (shared by delete, undo, redo, crop) --

    def _detach_item(self, item) -> dict:
        """Remove *item* from the scene and its layer; drop the layer if it
        becomes empty.  Returns a dict describing where it lived so that
        ``_attach_item`` can put it back exactly (layer name / position)."""
        lid = self.layer_manager.layer_id_of(item)
        info = self.layer_manager.layer_info(lid)
        if item.scene() is self._scene:
            self._scene.removeItem(item)
        self.layer_manager.remove_item(item)
        if lid and self.layer_manager.is_layer_empty(lid):
            self.layer_manager.delete_layer(lid)
        return {
            "item": item,
            "layer": lid,
            "layer_name": info[0] if info else None,
            "layer_index": info[1] if info else None,
            "layer_visible": info[2] if info else True,
        }

    def _attach_item(self, entry: dict) -> None:
        """Inverse of ``_detach_item``: re-create the layer if needed, add
        the item back to the scene and to its layer."""
        item = entry["item"]
        lid = entry.get("layer")
        if lid and self.layer_manager.layer_info(lid) is None:
            self.layer_manager.restore_layer(
                lid,
                entry.get("layer_name") or "Layer",
                entry.get("layer_index"),
                entry.get("layer_visible", True),
            )
        if item.scene() is not self._scene:
            self._scene.addItem(item)
        if lid:
            self.layer_manager.add_item_to_layer(lid, item)

    def _discard_if_tiny(self, item, width: float, height: float) -> bool:
        """Remove *item* from the scene and return True for a stray click."""
        if width < self.MIN_ITEM_SIZE or height < self.MIN_ITEM_SIZE:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
            return True
        return False

    def _shape_pen(self, tool: str) -> QPen:
        """Outline pen for a rectangle / ellipse from the active colour and tool settings."""
        return QPen(QColor(self._active_color), tool_config.get(tool, "stroke_width"))

    def _shape_brush(self, tool: str) -> QBrush:
        """Fill brush for a rectangle / ellipse (none unless 'fill' is enabled)."""
        if not tool_config.get(tool, "fill"):
            return QBrush(Qt.BrushStyle.NoBrush)
        fill = QColor(self._active_color)
        fill.setAlpha(int(tool_config.get(tool, "fill_opacity")))
        return QBrush(fill)

    @staticmethod
    def _drag_rect(start: QPointF, pos: QPointF, square: bool) -> QRectF:
        """Rect spanned by a drag; with *square* it stays square towards the cursor."""
        dx = pos.x() - start.x()
        dy = pos.y() - start.y()
        if square:
            side = max(abs(dx), abs(dy))
            dx = math.copysign(side, dx if dx else 1.0)
            dy = math.copysign(side, dy if dy else 1.0)
        return QRectF(start, QPointF(start.x() + dx, start.y() + dy)).normalized()

    @staticmethod
    def _snap_angle(start: QPointF, end: QPointF) -> QPointF:
        """Snap the start->end direction to the nearest multiple of 45 degrees."""
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return QPointF(end)
        step = math.pi / 4.0
        angle = round(math.atan2(dy, dx) / step) * step
        return QPointF(start.x() + length * math.cos(angle), start.y() + length * math.sin(angle))

    @staticmethod
    def _item_extent(item) -> QRectF:
        """Scene rect that decides whether a crop leaves *item* outside the image."""
        if isinstance(item, SpotlightItem):
            return item.hole_scene_rect()
        return item.sceneBoundingRect()

    # -- Arrow -------------------------------------------------------------

    def draw_arrow(self, start: QPointF, end: QPointF) -> ArrowItem:
        item = ArrowItem(start, end, color=self._active_color)
        self._scene.addItem(item)
        return item

    def _finalize_arrow(self, item: ArrowItem) -> None:
        length = math.hypot(item._end.x() - item._start.x(), item._end.y() - item._start.y())
        if self._discard_if_tiny(item, length, length):
            return
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._add_item_to_new_layer(item, "Arrow")

    # -- Rectangle ---------------------------------------------------------

    def draw_rectangle(self, rect: QRectF) -> QGraphicsRectItem:
        item = ResizableRectItem(rect)
        item.setPen(self._shape_pen("rectangle"))
        item.setBrush(self._shape_brush("rectangle"))
        if item.brush().style() != Qt.BrushStyle.NoBrush:
            item._fill_alpha = item.brush().color().alpha()
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(item)
        return item

    def _finalize_rectangle(self, item: QGraphicsRectItem) -> None:
        # Replace plain QGraphicsRectItem with ResizableRectItem.
        rect = item.rect()
        if self._discard_if_tiny(item, rect.width(), rect.height()):
            return
        pen = item.pen()
        brush = item.brush()
        self._scene.removeItem(item)

        resizable = ResizableRectItem(rect)
        resizable.setPen(pen)
        resizable.setBrush(brush)
        if brush.style() != Qt.BrushStyle.NoBrush:
            resizable._fill_alpha = brush.color().alpha()   # fill keeps its own opacity on recolour
        resizable.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(resizable)
        self._add_item_to_new_layer(resizable, "Rectangle")

    # -- Highlight ---------------------------------------------------------

    def apply_highlight(self, rect: QRectF) -> QGraphicsRectItem:
        hl = tool_config.values("highlight")
        color = QColor(hl["color"])
        color.setAlpha(self._active_alpha if self._active_alpha < 255 else hl["alpha"])
        item = ResizableRectItem(rect)
        item.setPen(QPen(Qt.PenStyle.NoPen))
        item.setBrush(QBrush(color))
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(item)
        return item

    def _finalize_highlight(self, item: QGraphicsRectItem) -> None:
        # Replace plain QGraphicsRectItem with ResizableRectItem.
        rect = item.rect()
        if self._discard_if_tiny(item, rect.width(), rect.height()):
            return
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

    def _finalize_pixelate(self, preview_rect: QGraphicsRectItem) -> None:
        rect = preview_rect.rect()
        if self._discard_if_tiny(preview_rect, rect.width(), rect.height()):
            return
        self._scene.removeItem(preview_rect)

        item = PixelateItem(rect, intensity=self._active_intensity)
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(item)
        self._add_item_to_new_layer(item, "Pixelate")

    # -- Blur --------------------------------------------------------------

    def _finalize_blur(self, preview_rect: QGraphicsRectItem) -> None:
        rect = preview_rect.rect()
        if self._discard_if_tiny(preview_rect, rect.width(), rect.height()):
            return
        self._scene.removeItem(preview_rect)

        item = BlurItem(rect, intensity=self._active_intensity)
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self._scene.addItem(item)
        self._add_item_to_new_layer(item, "Blur")

    # -- Ellipse / Line / Pen (V4) -----------------------------------------

    def _finalize_ellipse(self, preview: QGraphicsEllipseItem) -> None:
        rect = preview.rect()
        if self._discard_if_tiny(preview, rect.width(), rect.height()):
            return
        pen = preview.pen()
        brush = preview.brush()
        self._scene.removeItem(preview)

        item = EllipseItem(rect)
        item.setPen(pen)
        item.setBrush(brush)
        if brush.style() != Qt.BrushStyle.NoBrush:
            item._fill_alpha = brush.color().alpha()
        self._scene.addItem(item)
        self._add_item_to_new_layer(item, "Ellipse")

    def _finalize_line(self, item: LineItem) -> None:
        if self._discard_if_tiny(item, item.length(), item.length()):
            return
        self._add_item_to_new_layer(item, "Line")

    def _finalize_pen(self, item: FreehandItem) -> None:
        extent = item.extent()
        if self._discard_if_tiny(item, extent, extent):
            return
        self._add_item_to_new_layer(item, "Pen")

    # -- Spotlight (V4) ----------------------------------------------------

    def _finalize_spotlight(self, preview_rect: QGraphicsRectItem) -> None:
        rect = preview_rect.rect()
        if self._discard_if_tiny(preview_rect, rect.width(), rect.height()):
            return
        self._scene.removeItem(preview_rect)

        item = SpotlightItem(rect, shape=self._spotlight_shape, dim_alpha=self._spotlight_dim)
        self._scene.addItem(item)
        self._add_item_to_new_layer(item, "Spotlight")

    # -- Step marker (V4) --------------------------------------------------

    def add_step(self, pos: QPointF) -> StepItem:
        """Place the next numbered marker centred on *pos*."""
        number = self._step_counter + 1
        item = StepItem(number, pos, color=self._active_color,
                        diameter=float(tool_config.get("step", "diameter")))
        self._scene.addItem(item)
        self._step_counter = number
        self._add_item_to_new_layer(item, "Step", name=f"Step {number}")
        return item

    def _step_added_undone(self, item) -> None:
        """Undoing the newest marker gives its number back."""
        if isinstance(item, StepItem) and item.number == self._step_counter:
            self._step_counter = max(0, self._step_counter - 1)

    def _step_added_redone(self, item) -> None:
        if isinstance(item, StepItem):
            self._step_counter = max(self._step_counter, item.number)

    # -- Sticker (V4) ------------------------------------------------------

    def add_sticker(self, pos: QPointF, text: Optional[str] = None) -> Optional[StickerItem]:
        glyph = text if text else self._active_sticker
        if not glyph:
            return None
        item = StickerItem(glyph, pos, size=float(tool_config.get("sticker", "size")))
        self._scene.addItem(item)
        self._add_item_to_new_layer(item, "Sticker")
        return item

    # -- Eyedropper (V4) ---------------------------------------------------

    def pick_color_at(self, scene_pos: QPointF) -> Optional[QColor]:
        """Sample the flattened image (no selection chrome) at *scene_pos*.

        Sets the active colour (keeping the current alpha setting) and emits
        :attr:`color_picked`.  Returns the opaque sampled colour or None if the
        point is outside the image / fully transparent.  Creates no items and
        no history.
        """
        x = math.floor(scene_pos.x())
        y = math.floor(scene_pos.y())
        if not self._scene.sceneRect().contains(QPointF(x + 0.5, y + 0.5)):
            return None
        img = QImage(1, 1, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        try:
            with self._hidden_crop_overlays(), chrome_suppressed():
                self._scene.render(painter, QRectF(0, 0, 1, 1), QRectF(x, y, 1, 1))
        finally:
            painter.end()
        picked = img.pixelColor(0, 0)
        if picked.alpha() == 0:
            return None
        picked.setAlpha(255)
        self.set_active_color(QColor(picked))
        self.color_picked.emit(QColor(picked))
        return picked

    # -- Eyedropper loupe -----------------------------------------------------

    def _set_eyedropper_active(self, on: bool) -> None:
        """Mouse tracking lets the loupe follow the pointer without a button held."""
        self.viewport().setMouseTracking(on)
        if not on:
            self._eyedrop_down = False
            if self._loupe is not None:
                self._loupe.hide()

    def _sample_neighborhood(self, scene_pos: QPointF) -> QImage:
        """The image pixels around *scene_pos* (chrome excluded), centre = the picked pixel."""
        from crabture.editor.eyedropper_loupe import CELLS, sample_radius
        r = sample_radius()
        x, y = math.floor(scene_pos.x()), math.floor(scene_pos.y())
        img = QImage(CELLS, CELLS, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        try:
            with self._hidden_crop_overlays(), chrome_suppressed():
                self._scene.render(painter, QRectF(0, 0, CELLS, CELLS), QRectF(x - r, y - r, CELLS, CELLS))
        finally:
            painter.end()
        return img

    def _eyedropper_move(self, pos) -> None:
        if self._bg_pixmap_item is None:
            return
        from crabture.editor.eyedropper_loupe import EyedropperLoupe
        if self._loupe is None:
            self._loupe = EyedropperLoupe(self.viewport())
        scene_pos = self.mapToScene(pos)
        self._loupe.set_sample(self._sample_neighborhood(scene_pos))
        self._loupe.place(pos, self.viewport().size())
        if self._eyedrop_down:
            self.pick_color_at(scene_pos)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._loupe is not None:
            self._loupe.hide()
        super().leaveEvent(event)

    # -- User-triggered redaction (V4) -------------------------------------

    def add_blur_regions(self, rects: List[QRectF], kind: str = "blur",
                         intensity: Optional[int] = None) -> int:
        """Cover each of *rects* (scene/image coordinates) with a blur or
        pixelate item on its own undoable layer.  Returns how many were added.
        *intensity* overrides the active intensity (None = use the active one).

        Only ever called from an explicit user action (the redaction dialog) —
        nothing in Crabture redacts automatically.
        """
        if kind not in ("blur", "pixelate"):
            raise ValueError(f"unknown redaction kind: {kind!r}")
        bounds = self._scene.sceneRect()
        strength = self._active_intensity if intensity is None else max(1, min(100, int(intensity)))
        added = 0
        for rect in rects:
            r = QRectF(rect).normalized().intersected(bounds)
            if r.width() < 2 or r.height() < 2:
                continue
            if kind == "pixelate":
                item = PixelateItem(r, intensity=strength)
                label = "Pixelate"
            else:
                item = BlurItem(r, intensity=strength)
                label = "Blur"
            self._scene.addItem(item)
            self._add_item_to_new_layer(item, label)
            added += 1
        return added

    # -- Text --------------------------------------------------------------

    def add_text(self, pos: QPointF, text_string: str | None = None) -> Optional[EditableTextItem]:
        if text_string is None:
            text_string, ok = QInputDialog.getText(
                self, "Add Text", "Enter text:", text="Annotation"
            )
            if not ok or not text_string:
                return None

        cfg = tool_config.values("text")
        item = EditableTextItem(
            text_string, pos, color=self._active_color,
            font_size=cfg["font_size"], bold=cfg["bold"], family=cfg["font_family"],
        )
        self._scene.addItem(item)
        self._add_item_to_new_layer(item, "Text")
        return item

    # -- Magnifier (V3 — dynamic live paint) ---------------------------------

    def apply_magnifier(
        self,
        pos: QPointF,
        radius: Optional[float] = None,
        zoom_factor: Optional[float] = None,
    ) -> Optional[MagnifierItem]:
        """Create a dynamic circular magnifier at *pos*.

        The MagnifierItem renders from the background pixmap in its
        ``paint()`` override, so dragging/resizing updates live.
        """
        if self._bg_pixmap_item is None:
            return None
        if radius is None:
            radius = float(tool_config.get("magnifier", "radius"))
        if zoom_factor is None:
            zoom_factor = float(tool_config.get("magnifier", "zoom"))

        item = MagnifierItem(pos, radius, zoom_factor)
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

        cfg = tool_config.values("balloon")
        item = SpeechBalloonItem(
            text_string, pos, color=self._active_color,
            font_size=cfg["font_size"], text_color=QColor(cfg["text_color"]),
        )
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
        sel = selection_rect.rect()
        if self._discard_if_tiny(selection_rect, sel.width(), sel.height()):
            return
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
                item_br = self._item_extent(scene_item)
                if not item_br.intersects(new_scene_rect):
                    to_remove.append(scene_item)
            except RuntimeError:
                # C++ object already deleted — skip.
                pass

        # Remember what the crop discarded (and which layer it lived on) so
        # undo can bring it back.
        removed = []
        for dead_item in to_remove:
            try:
                removed.append(self._detach_item(dead_item))
            except RuntimeError:
                pass

        self._record({
            "type": "crop",
            "old_pixmap": bg,
            "new_pixmap": cropped,
            "offset": QPointF(cropped_rect.topLeft()),
            "removed": removed,
        })
        self.layers_changed.emit()
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
            # Detach (and drop the now-empty layer); refresh the recorded
            # layer position so redo can re-create it in the same place.
            action.update(self._detach_item(action["item"]))
            self._step_added_undone(action["item"])

        elif atype == "remove_item":
            self._attach_item(action)

        elif atype == "replace_image":
            if self._bg_pixmap_item:
                self._apply_background(action["old_pixmap"])
                for entry in reversed(action.get("removed", [])):
                    self._attach_item(entry)
                self._step_counter = action.get("old_step_counter", 0)

        elif atype == "crop":
            old_pix = action["old_pixmap"]
            offset = action["offset"]
            if self._bg_pixmap_item:
                self._bg_pixmap_item.setPixmap(old_pix)
                self._bg_pixmap_item.setPos(0, 0)
                self._scene.setSceneRect(QRectF(old_pix.rect()))
                # Bring back items the crop discarded (last removed first so
                # layer positions are restored correctly), then shift all.
                for entry in reversed(action.get("removed", [])):
                    self._attach_item(entry)
                self._shift_top_level_items(offset.x(), offset.y())
                self.reset_zoom()

        self._set_modified(True)
        self.layers_changed.emit()

    def redo(self) -> None:
        action = self.history.get_redo_action()
        if action is None:
            return
        atype = action.get("type")

        if atype == "add_item":
            self._attach_item(action)
            self._step_added_redone(action["item"])

        elif atype == "remove_item":
            action.update(self._detach_item(action["item"]))

        elif atype == "replace_image":
            if self._bg_pixmap_item:
                for entry in action.get("removed", []):
                    entry.update(self._detach_item(entry["item"]))
                self._apply_background(action["new_pixmap"])
                self._step_counter = 0

        elif atype == "crop":
            new_pix = action["new_pixmap"]
            offset = action["offset"]
            if self._bg_pixmap_item:
                self._bg_pixmap_item.setPixmap(new_pix)
                self._bg_pixmap_item.setPos(0, 0)
                self._scene.setSceneRect(QRectF(new_pix.rect()))
                # Shift everything first (discarded items are back in the
                # scene after undo), then drop the ones the crop removed.
                self._shift_top_level_items(-offset.x(), -offset.y())
                for entry in action.get("removed", []):
                    entry.update(self._detach_item(entry["item"]))
                self.reset_zoom()

        self._set_modified(True)
        self.layers_changed.emit()

    def _shift_top_level_items(self, dx: float, dy: float) -> None:
        """Move every top-level annotation by (dx, dy).

        Child items (handles, balloon text, crop buttons' labels) are skipped
        because they follow their parent.
        """
        for scene_item in list(self._scene.items()):
            if scene_item is self._bg_pixmap_item:
                continue
            if scene_item.parentItem() is not None:
                continue
            try:
                scene_item.moveBy(dx, dy)
            except RuntimeError:
                pass

    # =====================================================================
    #  Export
    # =====================================================================

    # Formats that cannot carry an alpha channel — flattened onto white.
    _OPAQUE_FORMATS = {"jpg", "jpeg", "bmp"}

    @staticmethod
    def _writable_formats() -> set:
        return {bytes(f).decode().lower() for f in QImageWriter.supportedImageFormats()}

    @staticmethod
    def _extension(filepath: str) -> str:
        return os.path.splitext(filepath)[1].lstrip(".").lower()

    @contextmanager
    def _hidden_crop_overlays(self):
        """Temporarily hide the pending-crop overlay (dimming + buttons)."""
        hidden = [i for i in self._crop_overlay_items if i.isVisible()]
        if self._crop_rect_item is not None and self._crop_rect_item.isVisible():
            hidden.append(self._crop_rect_item)
        for item in hidden:
            item.setVisible(False)
        try:
            yield
        finally:
            for item in hidden:
                item.setVisible(True)

    def render_flattened(self, background: Optional[QColor] = None) -> QPixmap:
        """The whole image with all annotations baked in (no editor chrome)."""
        return self._render_to_pixmap(background)

    def _render_to_pixmap(self, background: Optional[QColor] = None) -> QPixmap:
        """Flatten the entire visible scene into a QPixmap.

        Selection boxes, resize handles and any pending crop overlay are
        hidden for the duration of the render so they never leak into
        exports.  *background* (if given) fills the pixmap first, otherwise
        it starts transparent.
        """
        rect = self._scene.sceneRect()
        pixmap = QPixmap(max(1, int(rect.width())), max(1, int(rect.height())))
        pixmap.fill(background if background is not None else Qt.GlobalColor.transparent)

        selected = self._scene.selectedItems()
        self._scene.clearSelection()
        try:
            with self._hidden_crop_overlays():
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                with chrome_suppressed():
                    self._scene.render(painter, QRectF(pixmap.rect()), rect)
                painter.end()
        finally:
            for item in selected:
                item.setSelected(True)
        return pixmap

    def _write_pixmap(self, filepath: str) -> bool:
        """Render and write to *filepath*; False if Qt cannot write the format."""
        ext = self._extension(filepath)
        if ext not in self._writable_formats():
            return False
        background = QColor("#FFFFFF") if ext in self._OPAQUE_FORMATS else None
        return self._render_to_pixmap(background).save(filepath)

    def _is_current_file(self, filepath: str) -> bool:
        return bool(self._current_filepath) and (
            os.path.abspath(filepath) == os.path.abspath(self._current_filepath)
        )

    def _save_to(self, filepath: str) -> bool:
        ok = self._write_pixmap(filepath)
        if ok and self._is_current_file(filepath):
            self.mark_clean()
        return ok

    def save_overwrite(self) -> bool:
        """Overwrite the original file.

        If Qt cannot write the original's format (e.g. GIF) this falls back
        to "Save As" with a PNG suggestion instead of failing.
        """
        if not self._current_filepath:
            return self.save_as()
        if self._extension(self._current_filepath) not in self._writable_formats():
            suggested = os.path.splitext(self._current_filepath)[0] + ".png"
            return self._save_as_dialog(suggested)
        return self._save_to(self._current_filepath)

    def save_as(self, filepath: str | None = None) -> bool:
        if filepath is None:
            return self._save_as_dialog(os.path.expanduser("~/Downloads/Crabshots/edited.png"))
        return self._save_to(filepath)

    def _save_as_dialog(self, suggested: str) -> bool:
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save Image As",
            suggested,
            "Images (*.png *.jpg *.bmp *.webp)",
        )
        if not filepath:
            return False
        if not self._extension(filepath):
            filepath += ".png"
        return self._save_to(filepath)

    def copy_to_clipboard(self) -> None:
        pixmap = self._render_to_pixmap()
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setPixmap(pixmap)

    # =====================================================================
    #  Delete selected items
    # =====================================================================

    def delete_layers(self, layer_ids) -> None:
        """Delete whole layers (and their items) as undoable removals."""
        for lid in layer_ids:
            for item in self.layer_manager.get_items_for_layer(lid):
                entry = self._detach_item(item)
                self._record({"type": "remove_item", **entry})
            # Empty layers have no items to record; just drop them.
            if self.layer_manager.layer_info(lid) is not None:
                self.layer_manager.delete_layer(lid)
        self.layers_changed.emit()

    def notify_file_renamed(self, old_path: str, new_path: str) -> None:
        """Keep the editor pointed at its file after it was renamed on disk."""
        if self._current_filepath and os.path.abspath(self._current_filepath) == os.path.abspath(old_path):
            self._current_filepath = new_path

    def delete_selected(self) -> None:
        for item in self._scene.selectedItems():
            if item is self._bg_pixmap_item:
                continue
            # Children (e.g. a balloon's text) are removed with their parent.
            if item.parentItem() is not None:
                continue
            entry = self._detach_item(item)
            self._record({"type": "remove_item", **entry})
        self.layers_changed.emit()
