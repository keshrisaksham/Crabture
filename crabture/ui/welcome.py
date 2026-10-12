"""
ui/welcome.py — the empty-state page shown until an image is loaded.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from crabture.ui import icons
from crabture.ui import theme
from crabture.ui.common import make_thumbnail, set_role

RECENT_COUNT = 8
THUMB = (128, 80)


class WelcomePage(QFrame):
    """Big capture / open actions plus a strip of the newest captures."""

    capture_requested = pyqtSignal(str)
    open_image_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()
    image_chosen = pyqtSignal(str)
    shortcuts_setup_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("welcome")
        self._thumbs: Dict[tuple, QIcon] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        column = QWidget()
        column.setMaximumWidth(920)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        outer.addWidget(column, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        # -- Hero ----------------------------------------------------------
        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._logo)
        title = QLabel("Crabture")
        set_role(title, "h1")
        title.setStyleSheet("font-size: 32px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(title)
        tag = QLabel("Capture, annotate, share.")
        set_role(tag, "muted")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setStyleSheet("font-size: 15px;")
        col.addWidget(tag)
        col.addSpacing(20)

        # -- Action tiles --------------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch(1)
        tiles = [
            ("capture_region", "Capture region", "primary",
             lambda: self.capture_requested.emit("region")),
            ("capture_fullscreen", "Fullscreen", "secondary",
             lambda: self.capture_requested.emit("fullscreen")),
            ("capture_window", "Window", "secondary",
             lambda: self.capture_requested.emit("window")),
            ("open", "Open image…", "secondary", self.open_image_requested.emit),
            ("folder", "Open folder…", "secondary", self.open_folder_requested.emit),
        ]
        self.tiles: Dict[str, QToolButton] = {}
        for icon_name, label, role, slot in tiles:
            b = QToolButton()
            b.setText(label)
            b.setProperty("role", role)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setFixedSize(138, 92)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            icons.bind_icon(b, icon_name, 28, "accent_text" if role == "primary" else None)
            b.setIconSize(QSize(28, 28))
            b.clicked.connect(lambda _=False, s=slot: s())
            row.addWidget(b)
            self.tiles[icon_name] = b
        row.addStretch(1)
        col.addLayout(row)
        col.addSpacing(26)

        # -- Recent captures ----------------------------------------------
        head = QHBoxLayout()
        h2 = QLabel("Recent captures")
        set_role(h2, "h2")
        self._recent_caption = QLabel("")
        set_role(self._recent_caption, "caption")
        head.addWidget(h2)
        head.addSpacing(8)
        head.addWidget(self._recent_caption)
        head.addStretch(1)
        col.addLayout(head)

        self._recent = QListWidget()
        self._recent.setObjectName("gallery")
        self._recent.setViewMode(QListView.ViewMode.IconMode)
        self._recent.setFlow(QListView.Flow.LeftToRight)
        self._recent.setWrapping(False)
        self._recent.setMovement(QListView.Movement.Static)
        self._recent.setIconSize(QSize(*THUMB))
        self._recent.setGridSize(QSize(THUMB[0] + 24, THUMB[1] + 52))
        self._recent.setSpacing(4)
        self._recent.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._recent.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent.setFixedHeight(THUMB[1] + 52 + 22)
        self._recent.setFrameShape(QFrame.Shape.NoFrame)
        self._recent.itemClicked.connect(
            lambda it: self.image_chosen.emit(it.data(Qt.ItemDataRole.UserRole))
        )
        col.addWidget(self._recent)

        self._empty = QLabel("No captures yet — press Capture region to take your first screenshot.")
        set_role(self._empty, "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._empty)
        col.addSpacing(14)

        # -- Hint ----------------------------------------------------------
        self._hint = QWidget()
        hl = QHBoxLayout(self._hint)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hint = QLabel("Take screenshots with a key press, from anywhere.")
        set_role(hint, "caption")
        hl.addWidget(hint)
        self._setup_btn = QPushButton("Set up GNOME shortcuts")
        set_role(self._setup_btn, "ghost")
        self._setup_btn.clicked.connect(self.shortcuts_setup_requested.emit)
        hl.addWidget(self._setup_btn)
        hl.addStretch(1)
        col.addWidget(self._hint)
        self._hint.hide()

        self._retint()
        theme.signals.changed.connect(self._retint)
        self.set_recent([], "")

    def _retint(self, *_args) -> None:
        self._logo.setPixmap(icons.pixmap("logo_mark", 72, "accent_fg"))

    def set_hotkey_hint_visible(self, visible: bool) -> None:
        self._hint.setVisible(visible)

    def set_recent(self, paths: List[str], folder_label: str) -> None:
        """Show the newest *RECENT_COUNT* images of *paths* (newest first)."""
        self._recent.clear()
        shown = 0
        for path in paths:
            if shown >= RECENT_COUNT:
                break
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue
            key = (path, mtime)
            icon = self._thumbs.get(key)
            if icon is None:
                made = make_thumbnail(path, *THUMB)
                if made is None:
                    continue
                icon = made[0]
                self._thumbs[key] = icon
            item = QListWidgetItem(icon, Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            item.setSizeHint(QSize(THUMB[0] + 20, THUMB[1] + 48))
            self._recent.addItem(item)
            shown += 1
        have = shown > 0
        self._recent.setVisible(have)
        self._empty.setVisible(not have)
        self._recent_caption.setText(f"from {folder_label}" if have and folder_label else "")
        if len(self._thumbs) > 64:                       # keep the cache bounded
            self._thumbs.clear()
