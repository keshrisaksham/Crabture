"""
capture/controller.py — one capture request, start to finish.

    request ──► CaptureService (grab / select) ──► save ──► clipboard ──► notification

The controller has no UI of its own (apart from the overlays the service shows), so the
same code serves the main window and the "no window at all" mode used by shortcuts that
just save the shot.  It emits exactly one of ``finished`` / ``failed`` / ``cancelled``
per accepted request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from crabture.capture import config
from crabture.capture.config import CaptureRequest, MODE_LABELS
from crabture.capture.log import log
from crabture.capture.service import CaptureService


@dataclass
class CaptureResult:
    request: CaptureRequest           # fully resolved (no None fields)
    pixmap: QPixmap
    path: str
    note: str = ""                    # e.g. "folder not writable — saved to the default folder"
    notification: Optional[QObject] = None   # pending desktop notification, if one was sent


def visible_windows() -> List:
    """Our own top-level windows that are on screen (to hide while grabbing)."""
    out = []
    for w in QApplication.topLevelWidgets():
        try:
            if (w.isVisible() and w.isWindow() and not w.isMinimized()
                    and w.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog)):
                out.append(w)
        except RuntimeError:          # widget deleted underneath us
            continue
    return out


class CaptureController(QObject):
    finished = pyqtSignal(object)     # CaptureResult
    failed = pyqtSignal(str, object)  # message, CaptureRequest
    cancelled = pyqtSignal(object)    # CaptureRequest

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._service = CaptureService(self)
        self._service.captured.connect(self._on_captured)
        self._service.failed.connect(self._on_failed)
        self._service.cancelled.connect(self._on_cancelled)
        self._current: Optional[CaptureRequest] = None

    def is_busy(self) -> bool:
        return self._service.is_busy()

    def cancel(self) -> None:
        self._service.cancel()

    def request(self, req: CaptureRequest) -> bool:
        """Start *req*.  False if a capture is already running (nothing is emitted)."""
        if self._service.is_busy():
            return False
        resolved = req.resolved()
        log(f"request {resolved.mode} source={resolved.source} after={resolved.after} "
            f"folder={resolved.folder} delay={resolved.delay} engine={config.engine()}")
        # Crabture's own window: a capture started from one of its buttons or menus hides it (the setting
        # "Hide the Crabture window while capturing"), otherwise it would be in every picture.  A shortcut,
        # the command line or the tray never hides it, so Crabture itself can be screenshotted, and it does not
        # pop up again afterwards.  An explicit request (hide=True) always wins.
        wants_hide = resolved.hide if resolved.source == "menu" else bool(req.hide)
        hide = visible_windows() if wants_hide else None
        self._current = resolved
        if not self._service.capture(resolved.mode, int(resolved.delay * 1000), hide_window=hide):
            self._current = None
            return False
        return True

    # -- service callbacks ---------------------------------------------------

    def _on_captured(self, pixmap: QPixmap, mode: str) -> None:
        req, self._current = self._current or CaptureRequest(mode=mode).resolved(), None
        if req.clipboard:
            clip = QApplication.clipboard()
            if clip is not None:
                clip.setPixmap(pixmap)
        path, note = config.save_pixmap(pixmap, req.folder, mode)
        log(f"saved -> {path} {note}".strip())
        if not path:
            extra = " It was copied to the clipboard." if req.clipboard else ""
            self.failed.emit(f"The screenshot was taken but could not be saved ({note}).{extra}", req)
            return
        result = CaptureResult(request=req, pixmap=pixmap, path=path, note=note)
        # GNOME's own picker already announces its screenshot; don't add a second notification.
        own_notice = self._service.last_source != "portal-interactive"
        if req.after == "save" and config.notify() and own_notice:
            from crabture.capture.notify import notify
            where = os.path.dirname(path).replace(os.path.expanduser("~"), "~")
            body = f"{os.path.basename(path)}\nSaved in {where}"
            if req.clipboard:
                body += "\nCopied to the clipboard"
            if note:
                body += f"\n({note})"
            result.notification = notify(f"{MODE_LABELS.get(mode, mode)} captured", body, icon=path, parent=self)
        self.finished.emit(result)

    def _on_failed(self, message: str) -> None:
        req, self._current = self._current or CaptureRequest().resolved(), None
        self.failed.emit(message, req)

    def _on_cancelled(self) -> None:
        req, self._current = self._current or CaptureRequest().resolved(), None
        self.cancelled.emit(req)


_shared: Optional[CaptureController] = None


def shared() -> CaptureController:
    """The one controller of this process (windows and shortcuts all use it, so
    "a capture is already running" is detected across every entry point)."""
    global _shared
    if _shared is None:
        _shared = CaptureController(QApplication.instance())
    return _shared
