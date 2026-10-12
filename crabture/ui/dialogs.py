"""
ui/dialogs.py — Keyboard-shortcut reference and About dialogs.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from crabture.ui import icons
from crabture.settings import keymap
from crabture.ui import theme
from crabture.ui.common import set_role

# Static (not configurable) hints shown next to the live keymap.
DRAWING_HINTS = [
    ("Hold Shift", "Arrow/line snap to 0°/45°/90°; rectangle, ellipse, spotlight and crop become square"),
    ("Enter / Esc", "Confirm / cancel a pending crop"),
    ("Double-click", "Edit a text, balloon or step number"),
    ("Ctrl + mouse wheel", "Zoom at the pointer"),
]


def _shown(seq: str) -> str:
    return seq if seq else "—"


def current_shortcut_tables():
    """(left column, right column) built from the CURRENT keymap each call."""
    by_group: dict[str, list] = {}
    for d, cur in keymap.all():
        by_group.setdefault(d.group, []).append((d, cur))
    left = []
    for group in ("File", "Edit", "Capture", "View", "Help", "Configure"):
        rows = [(_shown(cur), d.label) for d, cur in by_group.get(group, [])]
        if rows:
            left.append((group, rows))
    tools = [(_shown(cur), d.label) for d, cur in by_group.get("Tools", [])]
    right = [("Tools (canvas focused)", tools), ("While drawing", DRAWING_HINTS)]
    return left, right


def _header(title: str, subtitle: str) -> QFrame:
    header = QFrame()
    header.setObjectName("dialog_header")
    lay = QVBoxLayout(header)
    lay.setContentsMargins(24, 18, 24, 14)
    lay.setSpacing(2)
    h1 = QLabel(title)
    set_role(h1, "h1")
    sub = QLabel(subtitle)
    set_role(sub, "muted")
    lay.addWidget(h1)
    lay.addWidget(sub)
    return header


def _section_card(title: str, rows) -> QFrame:
    card = QFrame()
    card.setProperty("role", "card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    h2 = QLabel(title)
    set_role(h2, "h2")
    lay.addWidget(h2)
    grid = QGridLayout()
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(6)
    for r, (keys, what) in enumerate(rows):
        key = QLabel(keys)
        set_role(key, "badge")
        key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc = QLabel(what)
        desc.setWordWrap(True)
        grid.addWidget(key, r, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(desc, r, 1)
    grid.setColumnStretch(1, 1)
    lay.addLayout(grid)
    return card


class ShortcutsDialog(QDialog):
    """Two-column reference of every keyboard shortcut (the current keymap)."""

    customise_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(960, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(_header("Keyboard shortcuts", "Everything you can do without leaving the keyboard."))

        body = QWidget()
        cols = QHBoxLayout(body)
        cols.setContentsMargins(24, 16, 24, 16)
        cols.setSpacing(16)
        for table in current_shortcut_tables():
            col = QVBoxLayout()
            col.setSpacing(12)
            for title, rows in table:
                col.addWidget(_section_card(title, rows))
            col.addStretch(1)
            cols.addLayout(col, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(24, 10, 24, 16)
        hint = QLabel("A dash means the action has no shortcut. You can change any of them.")
        set_role(hint, "muted")
        foot.addWidget(hint)
        foot.addStretch(1)
        custom = QPushButton("Customise…")
        custom.setToolTip("Open Configure ▸ Keyboard Shortcuts")
        custom.clicked.connect(self.customise_requested.emit)
        foot.addWidget(custom)
        close = QPushButton("Close")
        set_role(close, "primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        root.addLayout(foot)


class AboutDialog(QDialog):
    """Version, backend and project info."""

    def __init__(self, version: str, backend: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Crabture")
        self.setFixedWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 20)
        root.setSpacing(6)
        root.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._logo)
        title = QLabel("Crabture")
        set_role(title, "h1")
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        ver = QLabel(f"Version {version}")
        set_role(ver, "badge")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(ver, 0, Qt.AlignmentFlag.AlignHCenter)
        tag = QLabel("Capture, annotate, share.\nA ShareX-style screenshot tool for Linux.")
        set_role(tag, "muted")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setWordWrap(True)
        root.addWidget(tag)

        card = QFrame()
        card.setProperty("role", "card")
        cl = QVBoxLayout(card)
        cap = QLabel("Capture backend")
        set_role(cap, "caption")
        val = QLabel(backend or "unknown")
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(cap)
        cl.addWidget(val)
        root.addSpacing(10)
        root.addWidget(card)

        foot = QLabel("Part of the YawnByte project suite.")
        set_role(foot, "caption")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addSpacing(6)
        root.addWidget(foot)

        close = QPushButton("Close")
        set_role(close, "primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        root.addSpacing(8)
        root.addWidget(close, 0, Qt.AlignmentFlag.AlignHCenter)

        self._retint()
        theme.signals.changed.connect(self._retint)

    def _retint(self, *_args) -> None:
        self._logo.setPixmap(icons.pixmap("logo_mark", 72, "accent_fg"))
