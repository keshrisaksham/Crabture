"""
ui/panels.py — the right-hand side panel: Properties, Layers and Gallery.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import QDate, QDateTime, QFileSystemWatcher, QPoint, QRect, QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from crabture.ui import icons
from crabture.ui import theme
from crabture.editor.items import SpotlightItem
from crabture.ui.common import human_size, make_thumbnail, set_role
from crabture.ui.stickers import pick_sticker

SWATCH_COLORS = [
    "#FF4444", "#FF9800", "#FFEB3B", "#4CAF50",
    "#2196F3", "#9C27B0", "#FFFFFF", "#1E1E1E",
]

TOOL_LABELS = {
    "select": "Select", "arrow": "Arrow", "rectangle": "Rectangle", "highlight": "Highlight",
    "pixelate": "Pixelate", "blur": "Blur", "text": "Text", "crop": "Crop",
    "magnifier": "Magnifier", "balloon": "Speech balloon", "ellipse": "Ellipse", "line": "Line",
    "pen": "Freehand pen", "step": "Numbered step", "spotlight": "Spotlight",
    "sticker": "Sticker", "eyedropper": "Eyedropper",
}


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setProperty("role", "card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(8)
    cap = QLabel(title.upper())
    set_role(cap, "caption")
    lay.addWidget(cap)
    return card, lay


def _icon_button(icon_name: str, tip: str, role: str = "toolbar", size: int = 18) -> QToolButton:
    b = QToolButton()
    b.setProperty("role", role)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    icons.bind_icon(b, icon_name, size)
    b.setIconSize(QSize(size, size))
    return b


# =========================================================================
#  Colour swatch
# =========================================================================

class Swatch(QAbstractButton):
    """Round colour button with a selection ring, painted from theme tokens."""

    def __init__(self, color: QColor, size: int = 26, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._selected = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self._color.name().upper())
        self.setAccessibleName(f"Colour {self._color.name().upper()}")

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def setSelected(self, on: bool) -> None:
        if on != self._selected:
            self._selected = on
            self.update()

    def isSelected(self) -> bool:
        return self._selected

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRect(self.rect()).adjusted(3, 3, -3, -3)
        if self._selected or self.underMouse():
            ring = QColor(t["accent"] if self._selected else t["border_strong"])
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        p.setPen(QPen(QColor(t["border_strong"]), 1))
        p.setBrush(self._color)
        p.drawEllipse(r)
        p.end()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)


# =========================================================================
#  Properties
# =========================================================================

class PropertiesPanel(QWidget):
    """Colour / opacity plus the options of the active tool or selection."""

    # Emitted (via the event loop) with the height the visible sections need.
    content_height_changed = pyqtSignal(int)

    def __init__(self, editor, parent=None) -> None:
        super().__init__(parent)
        self._editor = editor

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("props_scroll")
        # Scoped by object name so the rule does not cascade to child widgets.
        scroll.setStyleSheet("QScrollArea#props_scroll { background: transparent; border: none; }"
                             "QWidget#props_body { background: transparent; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("props_body")
        self._body = body
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        # -- Title ---------------------------------------------------------
        head = QHBoxLayout()
        title = QLabel("Properties")
        set_role(title, "h2")
        self._tool_badge = QLabel("Select")
        set_role(self._tool_badge, "badge")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._tool_badge)
        lay.addLayout(head)

        # -- Colour card ---------------------------------------------------
        self.color_card, cl = _card("Colour")
        sw_row = QHBoxLayout()
        sw_row.setSpacing(2)
        self._swatches: List[Swatch] = []
        for hex_color in SWATCH_COLORS:
            sw = Swatch(QColor(hex_color))
            sw.clicked.connect(lambda _=False, c=hex_color: self._pick(c))
            self._swatches.append(sw)
            sw_row.addWidget(sw)
        sw_row.addStretch(1)
        cl.addLayout(sw_row)

        cur = QHBoxLayout()
        self._chip = Swatch(QColor("#FF4444"), 28)
        self._chip.setEnabled(False)
        self._chip.setToolTip("Current colour")
        self._hex = QLabel("#FF4444")
        self._hex.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        custom = _icon_button("palette", "Custom colour…", "toolbar", 18)
        custom.clicked.connect(self._custom)
        self._eyedrop = _icon_button("eyedropper", "Pick a colour from the image (I)", "toolbar", 18)
        self._eyedrop.clicked.connect(lambda: self._editor.set_tool("eyedropper"))
        cur.addWidget(self._chip)
        cur.addWidget(self._hex)
        cur.addStretch(1)
        cur.addWidget(self._eyedrop)
        cur.addWidget(custom)
        cl.addLayout(cur)

        self._alpha, self._alpha_val = self._slider_row(cl, "Opacity", 0, 255, 255)
        self._alpha.valueChanged.connect(self._on_alpha)
        lay.addWidget(self.color_card)

        # -- Intensity card (blur / pixelate) ------------------------------
        self.intensity_card, il = _card("Effect strength")
        self._intensity, self._intensity_val = self._slider_row(il, "Intensity", 2, 50, 10)
        self._intensity.valueChanged.connect(self._on_intensity)
        lay.addWidget(self.intensity_card)

        # -- Spotlight card ------------------------------------------------
        self.spot_card, sl = _card("Spotlight")
        self._dim, self._dim_val = self._slider_row(sl, "Dim strength", 0, 255, 160)
        self._dim.valueChanged.connect(self._on_dim)
        lay.addWidget(self.spot_card)

        # -- Sticker card --------------------------------------------------
        self.sticker_card, kl = _card("Sticker")
        row = QHBoxLayout()
        self._glyph = QLabel(editor.active_sticker())
        self._glyph.setStyleSheet("font-size: 30px;")
        change = QPushButton("Change…")
        change.clicked.connect(lambda: pick_sticker(self._editor, change))
        row.addWidget(self._glyph)
        row.addStretch(1)
        row.addWidget(change)
        kl.addLayout(row)
        lay.addWidget(self.sticker_card)

        # -- Step card -----------------------------------------------------
        self.step_card, tl = _card("Numbered steps")
        row = QHBoxLayout()
        self._step_label = QLabel("Next number: 1")
        reset = QPushButton("Reset")
        reset.setToolTip("Start numbering from 1 again")
        reset.clicked.connect(self._reset_steps)
        row.addWidget(self._step_label)
        row.addStretch(1)
        row.addWidget(reset)
        tl.addLayout(row)
        lay.addWidget(self.step_card)

        lay.addStretch(1)

        self._editor.tool_changed.connect(lambda _t: self.update_sections())
        self._editor.layers_changed.connect(self._update_step_label)
        self._editor.color_picked.connect(lambda _c: self.sync_color())
        self._editor.scene().selectionChanged.connect(self.update_sections)
        self.sync_color()
        self.update_sections()

    # -- builders ----------------------------------------------------------

    @staticmethod
    def _slider_row(layout: QVBoxLayout, label: str, lo: int, hi: int, value: int):
        row = QHBoxLayout()
        name = QLabel(label)
        name.setMinimumWidth(66)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(value)
        val = QLabel("")
        val.setMinimumWidth(40)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        set_role(val, "muted")
        row.addWidget(name)
        row.addWidget(slider, 1)
        row.addWidget(val)
        layout.addLayout(row)
        return slider, val

    # -- colour ------------------------------------------------------------

    def _pick(self, hex_color: str) -> None:
        self._editor.set_active_color(QColor(hex_color))
        self.sync_color()

    def _custom(self) -> None:
        color = QColorDialog.getColor(self._editor.active_color(), self, "Pick colour")
        if color.isValid():
            self._editor.set_active_color(color)
            self.sync_color()

    def _on_alpha(self, value: int) -> None:
        self._editor.set_active_alpha(value)
        self._alpha_val.setText(f"{round(value * 100 / 255)}%")

    def sync_color(self) -> None:
        """Mirror the editor's active colour/opacity into the chip, ring and slider."""
        color = self._editor.active_color()
        solid = QColor(color)
        solid.setAlpha(255)
        self._chip.setColor(solid)
        self._hex.setText(solid.name().upper())
        name = solid.name().lower()
        for sw in self._swatches:
            sw.setSelected(sw.color().name().lower() == name)
        self._alpha.blockSignals(True)
        self._alpha.setValue(self._editor.active_alpha())
        self._alpha.blockSignals(False)
        self._alpha_val.setText(f"{round(self._editor.active_alpha() * 100 / 255)}%")

    # -- tool options ------------------------------------------------------

    def _on_intensity(self, value: int) -> None:
        self._editor.set_active_intensity(value)
        self._intensity_val.setText(str(value))

    def _on_dim(self, value: int) -> None:
        if hasattr(self._editor, "set_spotlight_dim"):
            self._editor.set_spotlight_dim(value)
        self._dim_val.setText(f"{round(value * 100 / 255)}%")

    def _reset_steps(self) -> None:
        self._editor.reset_step_counter()
        self._update_step_label()

    def _update_step_label(self) -> None:
        nxt = int(getattr(self._editor, "_step_counter", 0)) + 1
        self._step_label.setText(f"Next number: {nxt}")

    def update_sections(self) -> None:
        tool = self._editor.active_tool()
        try:
            selected = self._editor.scene().selectedItems()
        except RuntimeError:
            selected = []
        has_intensity_item = any(hasattr(i, "set_intensity") for i in selected)
        has_spot_item = any(isinstance(i, SpotlightItem) for i in selected)

        self._tool_badge.setText(TOOL_LABELS.get(tool, tool.title()))
        no_colour_tools = {"blur", "pixelate", "crop", "magnifier", "eyedropper"}
        show_colour = tool not in no_colour_tools or (bool(selected) and not has_intensity_item)
        self.color_card.setVisible(show_colour)
        self.intensity_card.setVisible(tool in ("blur", "pixelate") or has_intensity_item)
        self.spot_card.setVisible(tool == "spotlight" or has_spot_item)
        self.sticker_card.setVisible(tool == "sticker")
        self.step_card.setVisible(tool == "step")

        # Refresh values from the editor each time a section opens.
        for slider, label, value, fmt in (
            (self._intensity, self._intensity_val, self._editor.active_intensity(), str),
            (self._dim, self._dim_val,
             self._editor.spotlight_dim() if hasattr(self._editor, "spotlight_dim") else 160,
             lambda v: f"{round(v * 100 / 255)}%"),
        ):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
            label.setText(fmt(value))
        self._glyph.setText(self._editor.active_sticker())
        self._update_step_label()
        QTimer.singleShot(0, self._emit_height)

    def refresh_from_editor(self) -> None:
        """Re-read colour, opacity, intensity, dim, sticker... from the editor.

        Used when a tool's saved defaults were applied or changed in Configure."""
        self.sync_color()
        self.update_sections()
        try:
            from crabture.settings import keymap
            key = keymap.get("tool.eyedropper")
        except Exception:
            key = "I"
        self._eyedrop.setToolTip("Pick a colour from the image" + (f" ({key})" if key else ""))

    def _emit_height(self) -> None:
        self._body.layout().activate()
        self.content_height_changed.emit(self._body.sizeHint().height() + 4)


# =========================================================================
#  Layers
# =========================================================================

class _LayerList(QListWidget):
    """List whose leading eye icon toggles visibility without selecting the row."""

    eye_clicked = pyqtSignal(str)
    EYE_ZONE = 34

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                rect = self.visualRect(idx)
                if event.position().toPoint().x() - rect.left() < self.EYE_ZONE:
                    lid = idx.data(Qt.ItemDataRole.UserRole)
                    if lid:
                        self.eye_clicked.emit(lid)
                        event.accept()
                        return
        super().mousePressEvent(event)


class LayersPanel(QWidget):
    def __init__(self, editor, parent=None) -> None:
        super().__init__(parent)
        self._editor = editor
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.btn_add = _icon_button("plus", "Add empty layer")
        self.btn_del = _icon_button("trash", "Delete selected layers")
        self.btn_up = _icon_button("up", "Move layer up")
        self.btn_down = _icon_button("down", "Move layer down")
        self.btn_vis = _icon_button("eye", "Toggle visibility of the selected layer")
        for b in (self.btn_add, self.btn_del, self.btn_up, self.btn_down, self.btn_vis):
            bar.addWidget(b)
        bar.addStretch(1)
        self._count = QLabel("")
        set_role(self._count, "caption")
        bar.addWidget(self._count)
        lay.addLayout(bar)

        self._list = _LayerList()
        self._list.setObjectName("layer_list")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setIconSize(QSize(18, 18))
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.eye_clicked.connect(self._toggle_lid)
        lay.addWidget(self._list, 1)

        self.btn_add.clicked.connect(self._add_layer)
        self.btn_del.clicked.connect(self._del_layer)
        self.btn_up.clicked.connect(self._move_up)
        self.btn_down.clicked.connect(self._move_down)
        self.btn_vis.clicked.connect(self._toggle_vis)
        self._editor.layers_changed.connect(self.refresh)
        theme.signals.changed.connect(lambda _n: self.refresh())
        self.refresh()

    @property
    def list_widget(self) -> QListWidget:
        return self._list

    def refresh(self) -> None:
        keep = set(self._selected_lids())
        self._list.clear()
        names = self._editor.layer_manager.layer_names()
        t = theme.tokens()
        for lid, name, visible in names:
            item = QListWidgetItem(icons.icon("eye" if visible else "eye_off", 18), name)
            item.setData(Qt.ItemDataRole.UserRole, lid)
            item.setSizeHint(QSize(0, 34))
            if not visible:
                item.setForeground(QColor(t["text_muted"]))
            self._list.addItem(item)
            if lid in keep:
                item.setSelected(True)
        n = len(names)
        self._count.setText(f"{n} layer{'s' if n != 1 else ''}")

    def _selected_lid(self) -> Optional[str]:
        cur = self._list.currentItem()
        return cur.data(Qt.ItemDataRole.UserRole) if cur else None

    def _selected_lids(self) -> list[str]:
        return [i.data(Qt.ItemDataRole.UserRole) for i in self._list.selectedItems()]

    def _add_layer(self) -> None:
        idx = self._list.count() + 1
        self._editor.layer_manager.add_layer(f"Layer {idx}")
        self._editor.layers_changed.emit()

    def _del_layer(self) -> None:
        lids = self._selected_lids()
        if lids:
            self._editor.delete_layers(lids)

    def _move_up(self) -> None:
        lid = self._selected_lid()
        if lid:
            self._editor.layer_manager.move_layer_up(lid)
            self._editor.layers_changed.emit()

    def _move_down(self) -> None:
        lid = self._selected_lid()
        if lid:
            self._editor.layer_manager.move_layer_down(lid)
            self._editor.layers_changed.emit()

    def _toggle_vis(self) -> None:
        for lid in self._selected_lids() or ([self._selected_lid()] if self._selected_lid() else []):
            self._editor.layer_manager.toggle_visibility(lid)
        self._editor.layers_changed.emit()

    def _toggle_lid(self, lid: str) -> None:
        self._editor.layer_manager.toggle_visibility(lid)
        self._editor.layers_changed.emit()


# =========================================================================
#  Gallery
# =========================================================================

_SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 1
_MTIME_ROLE = Qt.ItemDataRole.UserRole + 2
_SIZE_ROLE = Qt.ItemDataRole.UserRole + 3
_DIMS_ROLE = Qt.ItemDataRole.UserRole + 4

VIEW_MODES = ("grid", "list", "details", "compact")
SORT_KEYS = (("name", "Name"), ("modified", "Date modified"), ("size", "Size"), ("dimensions", "Dimensions"))

_ROW_PAD = 8          # horizontal padding inside a details/compact row
_DETAILS_THUMB = 24
_COMPACT_THUMB = 20


def _natural_key(text: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def _fmt_mtime(ts) -> str:
    try:
        dt = QDateTime.fromSecsSinceEpoch(int(ts))
    except (TypeError, ValueError):
        return ""
    if dt.date().year() == QDate.currentDate().year():
        return dt.toString("d MMM, HH:mm")
    return dt.toString("d MMM yyyy")


def _detail_columns(row_width: int) -> list[tuple[str, str, int]]:
    """Columns after the name, left to right: (key, label, width)."""
    cols: list[tuple[str, str, int]] = []
    if row_width >= 470:
        cols.append(("dimensions", "Dimensions", 86))
    cols.append(("size", "Size", 60))
    cols.append(("modified", "Modified", 82))
    return cols


def details_layout(row_width: int) -> tuple[int, int, list[tuple[str, str, int, int]]]:
    """Shared by the delegate and the header so they line up.

    Returns (name_x, name_right, [(key, label, x, width)...]) with x relative
    to the left edge of the row."""
    name_x = _ROW_PAD + _DETAILS_THUMB + 8
    x = row_width - _ROW_PAD
    placed: list[tuple[str, str, int, int]] = []
    for key, label, w in reversed(_detail_columns(row_width)):
        x -= w
        placed.append((key, label, x, w))
    placed.reverse()
    name_right = (placed[0][2] if placed else row_width - _ROW_PAD) - 8
    return name_x, name_right, placed


class _GalleryList(QListWidget):
    """QListWidget that tells the panel when its viewport changes size."""

    resized = pyqtSignal()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.resized.emit()


class _DetailsHeader(QWidget):
    """Clickable column header shown above the list in Details view."""

    sort_requested = pyqtSignal(str)

    def __init__(self, panel: "GalleryPanel") -> None:
        super().__init__(panel)
        self._panel = panel
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hit: list[tuple[str, int, int]] = []

    def _row_width(self) -> int:
        return max(120, self._panel.list.viewport().width() - 6)

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self._row_width()
        name_x, name_right, cols = details_layout(w)
        font = QFont(p.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        fm = QFontMetrics(font)
        arrow = " ▼" if self._panel.sort_desc() else " ▲"
        self._hit = []
        items = [("name", "Name", name_x, max(20, name_right - name_x))] + list(cols)
        # Where a row starts, in header coordinates: the list viewport's left edge
        # (the list may have padding) plus the spacing QListView puts before each row.
        lst = self._panel.list
        origin = lst.viewport().mapTo(self._panel, QPoint(0, 0)).x() - self.x() + lst.spacing()
        for key, label, x, cw in items:
            x += origin
            active = key == self._panel.sort_key()
            p.setPen(QColor(t["accent_fg"] if active else t["text_muted"]))
            text = label + (arrow if active else "")
            p.drawText(QRect(x, 0, cw, self.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, cw))
            self._hit.append((key, x, x + cw))
        p.setPen(QPen(QColor(t["border"])))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        x = int(event.position().x())
        for key, x0, x1 in self._hit:
            if x0 <= x <= x1 + 6:
                self.sort_requested.emit(key)
                return


class GalleryDelegate(QStyledItemDelegate):
    """Draws thumbnail cards (grid, list) or single-line rows (details, compact)."""

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._view = view

    def _mode(self) -> str:
        return str(self._view.property("galleryMode") or "list")

    def _grid(self) -> bool:
        return self._mode() == "grid"

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        mode = self._mode()
        icon = self._view.iconSize()
        if mode == "grid":
            g = self._view.gridSize()
            return g if g.isValid() else QSize(icon.width() + 24, icon.height() + 62)
        w = max(120, self._view.viewport().width() - 6)
        if mode == "compact":
            return QSize(w, 30)
        if mode == "details":
            return QSize(w, 38)
        return QSize(w, icon.height() + 20)

    def _paint_row(self, painter: QPainter, opt, name: str, icon: QIcon, index, mode: str) -> None:
        t = theme.tokens()
        side = _COMPACT_THUMB if mode == "compact" else _DETAILS_THUMB
        rect = opt.rect
        box = QRect(rect.left() + _ROW_PAD, rect.top() + (rect.height() - side) // 2, side, side)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t["surface3"]))
        painter.drawRoundedRect(box, 4, 4)
        if not icon.isNull():
            pm = icon.pixmap(box.size())
            if not pm.isNull():
                ratio = pm.devicePixelRatio() or 1.0
                w, h = pm.width() / ratio, pm.height() / ratio
                painter.drawPixmap(int(box.left() + (box.width() - w) / 2), int(box.top() + (box.height() - h) / 2), pm)

        font = QFont(painter.font())
        font.setPixelSize(12)
        small = QFont(painter.font())
        small.setPixelSize(11)
        fm, sm = QFontMetrics(font), QFontMetrics(small)
        if mode == "compact":
            name_x, name_right, cols = _ROW_PAD + side + 10, rect.width() - _ROW_PAD, []
        else:
            name_x, name_right, cols = details_layout(rect.width())
        painter.setFont(font)
        painter.setPen(QColor(t["text"]))
        nrect = QRect(rect.left() + name_x, rect.top(), max(20, name_right - name_x), rect.height())
        painter.drawText(nrect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                         fm.elidedText(name, Qt.TextElideMode.ElideMiddle, nrect.width()))
        if not cols:
            return
        painter.setFont(small)
        painter.setPen(QColor(t["text_muted"]))
        dims = index.data(_DIMS_ROLE)
        for key, _label, x, cw in cols:
            if key == "dimensions":
                text = f"{dims.width()}×{dims.height()}" if dims is not None and dims.isValid() else "…"
            elif key == "size":
                size = index.data(_SIZE_ROLE)
                text = human_size(size) if size is not None else ""
            else:
                text = _fmt_mtime(index.data(_MTIME_ROLE))
            painter.drawText(QRect(rect.left() + x, rect.top(), cw - 4, rect.height()),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             sm.elidedText(text, Qt.TextElideMode.ElideRight, cw - 4))

    def paint(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name, sub = opt.text, index.data(_SUBTITLE_ROLE) or ""
        icon = QIcon(index.data(Qt.ItemDataRole.DecorationRole) or QIcon())   # own copy: opt.icon is a live reference
        opt.text = ""
        opt.icon = QIcon()
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        painter.save()
        if self._grid():
            # Qt leaves any width that doesn't fit another column on the right;
            # shift the cards by half of it so both gutters are equal.
            cell = self._view.gridSize().width()
            if cell > 0:
                vp = self._view.viewport().width()
                bar = self._view.verticalScrollBar()
                bar_w = bar.width() if bar.isVisible() else 0     # the bar also eats the right gutter
                painter.translate(((vp % cell) + bar_w) // 2, 0)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        t = theme.tokens()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        mode = self._mode()
        if mode in ("details", "compact"):
            self._paint_row(painter, opt, name, icon, index, mode)
            painter.restore()
            return
        r = opt.rect.adjusted(8, 8, -8, -8)
        isize = self._view.iconSize()
        if self._grid():
            box = QRect(r.left() + (r.width() - isize.width()) // 2, r.top(), isize.width(), isize.height())
            text_rect = QRect(r.left(), box.bottom() + 6, r.width(), r.bottom() - box.bottom() - 6)
            align = Qt.AlignmentFlag.AlignHCenter
        else:
            box = QRect(r.left(), r.top() + (r.height() - isize.height()) // 2, isize.width(), isize.height())
            text_rect = QRect(box.right() + 12, r.top(), r.right() - box.right() - 12, r.height())
            align = Qt.AlignmentFlag.AlignLeft

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t["surface3"]))
        painter.drawRoundedRect(box, 6, 6)
        if not icon.isNull():
            pm = icon.pixmap(isize)
            if not pm.isNull():
                ratio = pm.devicePixelRatio() or 1.0
                w, h = pm.width() / ratio, pm.height() / ratio
                painter.drawPixmap(
                    int(box.left() + (box.width() - w) / 2),
                    int(box.top() + (box.height() - h) / 2),
                    pm,
                )

        name_font = QFont(painter.font())
        name_font.setPixelSize(12)
        name_font.setWeight(QFont.Weight.DemiBold)
        sub_font = QFont(painter.font())
        sub_font.setPixelSize(11)
        nm, sm = QFontMetrics(name_font), QFontMetrics(sub_font)
        name_h, sub_h = nm.height(), sm.height()
        block_h = name_h + (sub_h + 2 if sub else 0)
        y = text_rect.top() + (max(0, text_rect.height() - block_h) // 2 if not self._grid() else 0)
        flags = int(align | Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(name_font)
        painter.setPen(QColor(t["text"]))
        painter.drawText(QRect(text_rect.left(), y, text_rect.width(), name_h), flags,
                         nm.elidedText(name, Qt.TextElideMode.ElideMiddle, text_rect.width()))
        if sub:
            painter.setFont(sub_font)
            painter.setPen(QColor(t["text_muted"]))
            painter.drawText(QRect(text_rect.left(), y + name_h + 2, text_rect.width(), sub_h), flags,
                             sm.elidedText(sub, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()


class GalleryPanel(QWidget):
    """Folder gallery: filter box, sort menu, size slider, four view modes, watcher."""

    open_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(list)           # list of QListWidgetItem
    change_folder_requested = pyqtSignal()
    listing_changed = pyqtSignal(list)            # all image paths, newest first

    def __init__(self, ext_getter: Callable[[], set], default_dir: str, parent=None) -> None:
        super().__init__(parent)
        self._ext_getter = ext_getter
        self.directory: str = default_dir
        self._thumb_size = 120
        self._is_list_view = True
        self._view_mode = "list"
        self._sort_key = "modified"
        self._sort_desc = True
        self._in_grid_update = False
        self._watcher: Optional[QFileSystemWatcher] = None
        # (path, thumb width) -> (mtime, icon, original size)
        self._thumb_cache: dict[tuple[str, int], tuple[float, object, object]] = {}
        # Thumbnails still to decode: (item, path, mtime), drained in small batches.
        self._thumb_queue: list[tuple[QListWidgetItem, str, float]] = []
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(0)
        self._thumb_timer.timeout.connect(self._load_thumb_batch)
        self._rescale_timer = QTimer(self)
        self._rescale_timer.setSingleShot(True)
        self._rescale_timer.setInterval(150)
        self._rescale_timer.timeout.connect(self.refresh)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # Folder header
        head = QHBoxLayout()
        head.setSpacing(6)
        folder_icon = QToolButton()
        folder_icon.setAutoRaise(True)
        folder_icon.setEnabled(False)
        icons.bind_icon(folder_icon, "folder", 18)
        self._folder_label = QLabel("")
        set_role(self._folder_label, "h2")
        self._folder_label.setMinimumWidth(10)
        change = QPushButton("Change…")
        set_role(change, "ghost")
        change.clicked.connect(self.change_folder_requested.emit)
        head.addWidget(folder_icon)
        head.addWidget(self._folder_label, 1)
        head.addWidget(change)
        lay.addLayout(head)

        # Filter box
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter by name…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        frow = QHBoxLayout()
        frow.setSpacing(6)
        frow.addWidget(self._filter, 1)
        self._sort_btn = QToolButton()
        self._sort_btn.setProperty("role", "toolbar")
        self._sort_btn.setToolTip("Sort files")
        self._sort_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        icons.bind_icon(self._sort_btn, "sort", 18)
        self._sort_btn.setIconSize(QSize(18, 18))
        self._sort_btn.setMenu(self._build_sort_menu())
        frow.addWidget(self._sort_btn)
        lay.addLayout(frow)

        # Size slider + list/grid chips
        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        size_lbl = QLabel("Size")
        set_role(size_lbl, "caption")
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(40, 200)
        self._size_slider.setValue(self._thumb_size)
        self._size_slider.setToolTip("Thumbnail size")
        self._size_slider.valueChanged.connect(self._on_thumb_scale)
        self._mode_buttons: dict[str, QToolButton] = {}
        for mode, icon_name, tip in (
            ("grid", "grid", "Grid — large thumbnails"),
            ("list", "list", "List — thumbnail and file info"),
            ("details", "view_details", "Details — name, size, date (click a column to sort)"),
            ("compact", "view_compact", "Compact list — names only"),
        ):
            b = QToolButton()
            b.setProperty("role", "chip")
            b.setCheckable(True)
            b.setToolTip(tip)
            icons.bind_icon(b, icon_name, 16)
            b.setIconSize(QSize(16, 16))
            b.clicked.connect(lambda _=False, m=mode: self.set_view(m))
            self._mode_buttons[mode] = b
        self._btn_list = self._mode_buttons["list"]
        self._btn_grid = self._mode_buttons["grid"]
        ctl.addWidget(size_lbl)
        ctl.addWidget(self._size_slider, 1)
        for mode in VIEW_MODES:
            ctl.addWidget(self._mode_buttons[mode])
        lay.addLayout(ctl)

        # List
        self._header = _DetailsHeader(self)
        self._header.sort_requested.connect(self._header_sort)
        self._header.hide()
        lay.addWidget(self._header)

        self.list = _GalleryList()
        self.list.resized.connect(self._on_list_resized)
        self.list.setObjectName("gallery")
        self.list.setViewMode(QListView.ViewMode.ListMode)
        self.list.setIconSize(QSize(self._thumb_size, int(self._thumb_size * 0.75)))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setSpacing(4)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.setItemDelegate(GalleryDelegate(self.list))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemClicked.connect(self._on_click)
        lay.addWidget(self.list, 1)

        self._empty = QLabel("No images in this folder yet.")
        set_role(self._empty, "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._empty)
        self._empty.hide()

        qs = QSettings()
        saved_sort = str(qs.value("gallery/sort", "modified"))
        self._sort_key = saved_sort if saved_sort in dict(SORT_KEYS) else "modified"
        self._sort_desc = str(qs.value("gallery/sort_desc", "true")).lower() != "false"
        self._sync_sort_menu()
        saved_view = str(qs.value("gallery/view", "list"))
        self.set_view(saved_view if saved_view in VIEW_MODES else "list")

    # -- public API --------------------------------------------------------

    def watch(self, directory: str) -> None:
        self.directory = directory
        Path(directory).mkdir(parents=True, exist_ok=True)
        short = directory.replace(str(Path.home()), "~")
        self._folder_label.setText(os.path.basename(directory.rstrip("/")) or directory)
        self._folder_label.setToolTip(short)
        if self._watcher is not None:
            self._watcher.removePaths(self._watcher.directories())
        else:
            self._watcher = QFileSystemWatcher(self)
            self._watcher.directoryChanged.connect(self.refresh)
        self._watcher.addPath(directory)
        self.refresh()

    def set_view(self, mode) -> None:
        """Switch view style: 'grid' | 'list' | 'details' | 'compact'.
        (True/False are accepted for backwards compatibility: list/grid.)"""
        if mode is True:
            mode = "list"
        elif mode is False:
            mode = "grid"
        if mode not in VIEW_MODES:
            mode = "list"
        self._view_mode = mode
        self._is_list_view = mode != "grid"
        for m, btn in self._mode_buttons.items():
            btn.setChecked(m == mode)
        self.list.setProperty("galleryMode", mode)
        self._header.setVisible(mode == "details")
        self._size_slider.setEnabled(mode in ("grid", "list"))
        self._size_slider.setToolTip("Thumbnail size" if mode in ("grid", "list")
                                     else "Thumbnail size (grid and list views)")
        if mode == "grid":
            self.list.setViewMode(QListView.ViewMode.IconMode)
            self.list.setWrapping(True)
            self.list.setResizeMode(QListView.ResizeMode.Adjust)
            self.list.setMovement(QListView.Movement.Static)
            self.list.setSpacing(0)
            self.list.setUniformItemSizes(True)
            self._update_grid()
        else:
            self.list.setViewMode(QListView.ViewMode.ListMode)
            self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.list.setWrapping(False)
            self.list.setGridSize(QSize())
            self.list.setUniformItemSizes(False)
            self.list.setSpacing(4 if mode == "list" else 1)
        self.list.doItemsLayout()
        self.list.viewport().update()
        self._header.update()
        QSettings().setValue("gallery/view", mode)

    def view_mode(self) -> str:
        return self._view_mode

    def _update_grid(self) -> None:
        """Fill each row evenly with cells so the left and right gutters match.

        Qt's icon view reserves a scrollbar-sized gutter on the right whenever
        the scrollbar policy is 'as needed', which made the spacing lopsided.
        So we decide up front (from the item count) whether scrolling is
        needed: if not, the scrollbar is switched off and cells use the full
        width; if so, the bar's width is reserved and cells fill the rest."""
        if self._view_mode != "grid" or self._in_grid_update:
            return
        self._in_grid_update = True
        try:
            lst = self.list
            # Qt keeps the scrollbar's extent plus the frame/padding clear of the icons.
            ext = (lst.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, lst.verticalScrollBar()) or 10) \
                + lst.frameWidth()
            full = lst.maximumViewportSize().width()
            icon = lst.iconSize()
            min_cell = icon.width() + 30
            cell_h = icon.height() + 66
            count = sum(1 for i in range(lst.count()) if not lst.item(i).isHidden())
            view_h = lst.viewport().height()

            def layout(avail: int) -> tuple[int, int]:
                cols = max(1, avail // max(1, min_cell))
                return cols, max(1, avail // cols)

            cols, _w = layout(full)
            rows = -(-count // cols) if count else 0
            if rows * cell_h > view_h:
                lst.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                cols, cell_w = layout(max(1, full - ext))
            else:
                lst.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                cols, cell_w = layout(full - 2)     # Qt's layout width is a hair under the viewport
            lst.setGridSize(QSize(cell_w, cell_h))
        finally:
            self._in_grid_update = False

    def _on_list_resized(self) -> None:
        self._update_grid()
        self._header.update()

    # -- sorting -----------------------------------------------------------

    def sort_key(self) -> str:
        return self._sort_key

    def sort_desc(self) -> bool:
        return self._sort_desc

    def set_sort(self, key: str, desc: bool | None = None) -> None:
        if key not in dict(SORT_KEYS):
            return
        if desc is None:
            desc = key != "name"       # names ascending; dates, sizes largest/newest first
        self._sort_key, self._sort_desc = key, bool(desc)
        qs = QSettings()
        qs.setValue("gallery/sort", key)
        qs.setValue("gallery/sort_desc", "true" if desc else "false")
        self._sync_sort_menu()
        self._header.update()
        self.refresh()

    def _header_sort(self, key: str) -> None:
        if key == self._sort_key:
            self.set_sort(key, not self._sort_desc)
        else:
            self.set_sort(key)

    def _build_sort_menu(self) -> QMenu:
        menu = QMenu(self)
        self._sort_actions: dict[str, QAction] = {}
        key_group = QActionGroup(menu)
        key_group.setExclusive(True)
        for key, label in SORT_KEYS:
            act = QAction(label, menu, checkable=True)
            act.triggered.connect(lambda _=False, k=key: self.set_sort(k))
            key_group.addAction(act)
            menu.addAction(act)
            self._sort_actions[key] = act
        menu.addSeparator()
        dir_group = QActionGroup(menu)
        dir_group.setExclusive(True)
        self._act_asc = QAction("Ascending", menu, checkable=True)
        self._act_desc = QAction("Descending", menu, checkable=True)
        self._act_asc.triggered.connect(lambda: self.set_sort(self._sort_key, False))
        self._act_desc.triggered.connect(lambda: self.set_sort(self._sort_key, True))
        for a in (self._act_asc, self._act_desc):
            dir_group.addAction(a)
            menu.addAction(a)
        return menu

    def _sync_sort_menu(self) -> None:
        for key, act in self._sort_actions.items():
            act.setChecked(key == self._sort_key)
        self._act_asc.setChecked(not self._sort_desc)
        self._act_desc.setChecked(self._sort_desc)
        label = dict(SORT_KEYS).get(self._sort_key, "")
        self._sort_btn.setToolTip(f"Sort: {label} ({'descending' if self._sort_desc else 'ascending'})")

    def selected_items(self) -> list:
        return sorted(self.list.selectedItems(), key=self.list.row)

    def filter_text(self) -> str:
        return self._filter.text()

    def set_filter(self, text: str) -> None:
        self._filter.setText(text)

    # -- listing -----------------------------------------------------------

    def refresh(self) -> None:
        self._thumb_timer.stop()
        self._thumb_queue.clear()
        th_w = self._thumb_size

        entries: list[tuple[float, Path, int]] = []
        try:
            exts = self._ext_getter()
            for path in Path(self.directory).iterdir():
                if path.suffix.lower() not in exts:
                    continue
                try:
                    st = path.stat()
                    entries.append((st.st_mtime, path, st.st_size))
                except OSError:
                    continue  # vanished between iterdir() and stat()
        except OSError:
            return
        entries.sort(key=self._sort_func(), reverse=self._sort_desc)

        selected = {i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()}
        scroll = self.list.verticalScrollBar().value()

        live = {(str(p), th_w) for _, p, _s in entries}
        self._thumb_cache = {k: v for k, v in self._thumb_cache.items() if k in live}

        self.list.clear()
        for mtime, path, fsize in entries:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setData(_MTIME_ROLE, mtime)
            item.setData(_SIZE_ROLE, fsize)
            item.setToolTip(str(path))
            cached = self._thumb_cache.get((str(path), th_w))
            if cached and cached[0] == mtime:
                item.setIcon(cached[1])
                item.setData(_DIMS_ROLE, cached[2])
                item.setData(_SUBTITLE_ROLE, self._info_line(str(path), cached[2]))
            else:
                item.setData(_SUBTITLE_ROLE, self._info_line(str(path), None))
                self._thumb_queue.append((item, str(path), mtime))
            self.list.addItem(item)
            if str(path) in selected:
                item.setSelected(True)

        self.list.verticalScrollBar().setValue(scroll)
        self._apply_filter()
        self._update_grid()
        if self._thumb_queue:
            self._thumb_timer.start()
        self._empty.setVisible(not entries)
        self.list.setVisible(bool(entries))
        self.listing_changed.emit([str(p) for _, p, _s in entries])

    def _sort_func(self):
        key = self._sort_key
        if key == "name":
            return lambda e: _natural_key(e[1].name)
        if key == "size":
            return lambda e: e[2]
        if key == "dimensions":
            th_w = self._thumb_size

            def by_dims(e):
                hit = self._thumb_cache.get((str(e[1]), th_w))
                d = hit[2] if hit else None
                return d.width() * d.height() if d is not None and d.isValid() else 0
            return by_dims
        return lambda e: e[0]

    @staticmethod
    def _info_line(path: str, dims) -> str:
        try:
            size = human_size(os.stat(path).st_size)
        except OSError:
            size = ""
        if dims is not None and dims.isValid():
            return f"{dims.width()}×{dims.height()} · {size}"
        return size

    def _load_thumb_batch(self) -> None:
        th_w = self._thumb_size
        th_h = int(th_w * 0.75)
        deadline = time.monotonic() + 0.015
        while self._thumb_queue and time.monotonic() < deadline:
            item, path, mtime = self._thumb_queue.pop(0)
            made = make_thumbnail(path, th_w, th_h)
            if made is None:
                row = self.list.row(item)
                if row >= 0:
                    self.list.takeItem(row)
                continue
            icon, dims = made
            self._thumb_cache[(path, th_w)] = (mtime, icon, dims)
            item.setIcon(icon)
            item.setData(_DIMS_ROLE, dims)
            item.setData(_SUBTITLE_ROLE, self._info_line(path, dims))
        if not self._thumb_queue:
            self._thumb_timer.stop()

    # -- controls ----------------------------------------------------------

    def _apply_filter(self, *_args) -> None:
        needle = self._filter.text().strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(needle) and needle not in it.text().lower())
        self._update_grid()

    def _on_thumb_scale(self, value: int) -> None:
        self._thumb_size = value
        self.list.setIconSize(QSize(value, int(value * 0.75)))
        self._update_grid()
        self.list.doItemsLayout()
        self._rescale_timer.start()

    def _on_click(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.open_requested.emit(path)

    def _context_menu(self, pos: QPoint) -> None:
        items = self.selected_items()
        if not items:
            return
        menu = QMenu(self.list)
        menu.setObjectName("context_menu")
        act_open = menu.addAction(icons.icon("open", 16), "Open")
        act_open.triggered.connect(lambda: self._on_click(items[0]))
        act_rename = menu.addAction(icons.icon("rename", 16), f"Power Rename {len(items)} item(s)…")
        act_rename.triggered.connect(lambda: self.rename_requested.emit(items))
        menu.addSeparator()
        act_fm = menu.addAction(icons.icon("folder", 16), "Open in file manager")
        act_fm.triggered.connect(lambda: self.reveal(items))
        act_copy = menu.addAction(icons.icon("copy", 16), "Copy path")
        act_copy.triggered.connect(lambda: self.copy_path(items))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    @staticmethod
    def reveal(items) -> None:
        if not items:
            return
        import subprocess
        path = items[0].data(Qt.ItemDataRole.UserRole)
        subprocess.Popen(["xdg-open", os.path.dirname(path)])

    @staticmethod
    def copy_path(items) -> None:
        if not items:
            return
        clip = QApplication.clipboard()
        if clip:
            clip.setText("\n".join(i.data(Qt.ItemDataRole.UserRole) for i in items))
