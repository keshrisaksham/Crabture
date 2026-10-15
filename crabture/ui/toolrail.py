"""
ui/toolrail.py — the vertical tool rail on the left of the editor.

Two columns of 40 px checkable buttons (17 tools do not fit in a single
column on a 720 px-high window), grouped by purpose with thin dividers.
"""

from __future__ import annotations

from typing import Dict

from PyQt6.QtCore import QSize, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from crabture.ui import icons
from crabture.settings import keymap
from crabture.ui.common import divider

# (tool id, icon name, label, shortcut key or "")
TOOL_GROUPS = [
    [("select", "select", "Select", "S"), ("hand", "hand", "Hand", "Space")],
    [("arrow", "arrow", "Arrow", "A"), ("line", "line", "Line", "L"), ("pen", "pen", "Freehand pen", "D")],
    [("rectangle", "rectangle", "Rectangle", "R"), ("ellipse", "ellipse", "Ellipse", "E"),
     ("highlight", "highlight", "Highlight", "H")],
    [("text", "text", "Text", "T"), ("balloon", "balloon", "Speech balloon", ""),
     ("step", "step", "Numbered step", "N"), ("sticker", "sticker", "Sticker", "Y")],
    [("pixelate", "pixelate", "Pixelate", "P"), ("blur", "blur", "Blur", "B"),
     ("spotlight", "spotlight", "Spotlight", "O"), ("magnifier", "magnifier", "Magnifier", "M")],
    [("crop", "crop", "Crop", "C"), ("eyedropper", "eyedropper", "Eyedropper", "I")],
]

COLUMNS = 2
BUTTON = 40


class ToolRail(QFrame):
    """Exclusive column of tool buttons kept in sync with the editor."""

    # Emitted when the already-active sticker tool is clicked again.
    sticker_reclicked = pyqtSignal(QWidget)

    def __init__(self, editor, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tool_rail")
        self._editor = editor
        self._buttons: Dict[str, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        inner = QWidget()
        inner.setObjectName("rail_inner")
        grid = QGridLayout(inner)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        row = 0
        for gi, group in enumerate(TOOL_GROUPS):
            if gi:
                grid.addWidget(divider(), row, 0, 1, COLUMNS)
                grid.setRowMinimumHeight(row, 9)
                row += 1
            col = 0
            for tool_id, icon_name, label, key in group:
                btn = QToolButton()
                btn.setProperty("role", "tool")
                btn.setCheckable(True)
                btn.setAutoRaise(False)
                btn.setFixedSize(BUTTON, BUTTON)
                btn.setAccessibleName(label)
                btn.setObjectName(f"tool_{tool_id}")
                icons.bind_icon(btn, icon_name, 22)
                btn.setIconSize(QSize(22, 22))
                btn.clicked.connect(lambda _=False, t=tool_id: self._on_tool(t))
                self._group.addButton(btn)
                grid.addWidget(btn, row, col)
                self._buttons[tool_id] = btn
                col += 1
                if col >= COLUMNS:
                    col = 0
                    row += 1
            if col:
                row += 1
        grid.setRowStretch(row, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        scroll.viewport().setAutoFillBackground(False)
        inner.setAutoFillBackground(False)
        scroll.setObjectName("rail_scroll")
        # Scoped by object name: a bare 'background: transparent' would cascade to the buttons.
        scroll.setStyleSheet("QScrollArea#rail_scroll { background: transparent; border: none; }"
                             "QWidget#rail_inner { background: transparent; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setFixedWidth(COLUMNS * BUTTON + (COLUMNS - 1) * 4 + 16 + 4)

        self._labels = {t: label for g in TOOL_GROUPS for t, _i, label, _k in g}
        self.refresh_shortcuts()
        keymap.signals.changed.connect(lambda _id: self.refresh_shortcuts())

        self._buttons["select"].setChecked(True)
        self._editor.tool_changed.connect(self._sync)

    def refresh_shortcuts(self) -> None:
        """Tooltips show the CURRENT key of each tool (it can be remapped)."""
        for tool_id, btn in self._buttons.items():
            key = keymap.key_text_for_tool(tool_id)
            label = self._labels.get(tool_id, tool_id.title())
            btn.setToolTip(f"{label} ({key})" if key else label)

    def button(self, tool_id: str) -> QToolButton:
        return self._buttons[tool_id]

    def tool_ids(self) -> list[str]:
        return list(self._buttons)

    def _on_tool(self, tool_id: str) -> None:
        already = self._editor.active_tool() == tool_id
        self._editor.set_tool(tool_id)
        if tool_id == "sticker" and already:
            self.sticker_reclicked.emit(self._buttons["sticker"])

    @pyqtSlot(str)
    def _sync(self, tool_id: str) -> None:
        btn = self._buttons.get(tool_id)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
