"""
capture/tray.py — the notification-area icon of Crabture's background instance (Windows).

The background instance exists so capture hot-keys work without a window being open.  The tray
icon is its only visible part: a menu to take a shot, open the editor, or quit.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from crabture.capture.config import MODE_LABELS


class CrabtureTray(QSystemTrayIcon):
    def __init__(self, icon: QIcon, capture: Callable[[str], None], open_editor: Callable[[], None],
                 quit_app: Callable[[], None], parent=None) -> None:
        super().__init__(icon, parent)
        self.setToolTip("Crabture")
        self._menu = QMenu()
        for mode in ("region", "fullscreen", "window", "monitor", "scroll"):
            act = QAction(f"Capture: {MODE_LABELS[mode]}", self._menu)
            act.triggered.connect(lambda _checked=False, m=mode: capture(m))
            self._menu.addAction(act)
        self._menu.addSeparator()
        edit = QAction("Open Crabture", self._menu)
        edit.triggered.connect(lambda _checked=False: open_editor())
        self._menu.addAction(edit)
        self._menu.addSeparator()
        bye = QAction("Quit Crabture", self._menu)
        bye.triggered.connect(lambda _checked=False: quit_app())
        self._menu.addAction(bye)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        self._open_editor = open_editor

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._open_editor()


def available() -> bool:
    return QApplication.instance() is not None and QSystemTrayIcon.isSystemTrayAvailable()
