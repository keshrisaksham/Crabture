"""
ui/stickers.py — the emoji sticker picker popup.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QGridLayout,
    QInputDialog,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
    QWidgetAction,
)

STICKERS = [
    "😀", "😂", "😍", "😎", "🤔", "😱", "👍", "👎",
    "👉", "👈", "👆", "👇", "✅", "❌", "⚠️", "❓",
    "❗", "⭐", "🔥", "💡", "🐛", "🔒", "🔑", "🚀",
]


def pick_sticker(editor, anchor: QWidget) -> None:
    """Pop up a grid of emoji; the chosen one becomes the active sticker."""
    menu = QMenu(anchor)
    holder = QWidget(menu)
    grid = QGridLayout(holder)
    grid.setContentsMargins(8, 8, 8, 8)
    grid.setSpacing(2)

    def choose(text: str) -> None:
        editor.set_active_sticker(text)
        menu.close()

    for i, glyph in enumerate(STICKERS):
        b = QToolButton()
        b.setText(glyph)
        b.setAutoRaise(True)
        b.setStyleSheet("font-size: 22px;")
        b.setToolTip(glyph)
        b.clicked.connect(lambda _=False, g=glyph: choose(g))
        grid.addWidget(b, i // 8, i % 8)

    custom = QPushButton("Custom…")

    def ask_custom() -> None:
        menu.close()
        text, ok = QInputDialog.getText(
            anchor, "Sticker", "Emoji or short text:", text=editor.active_sticker()
        )
        if ok and text.strip():
            editor.set_active_sticker(text.strip())

    custom.clicked.connect(ask_custom)
    grid.addWidget(custom, len(STICKERS) // 8 + 1, 0, 1, 8)
    action = QWidgetAction(menu)
    action.setDefaultWidget(holder)
    menu.addAction(action)
    menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
