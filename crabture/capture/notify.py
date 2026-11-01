"""
capture/notify.py — desktop notifications.

Used when a capture finished without the editor opening (shortcut → "just save"),
so the user still gets confirmation of what happened and where the file went.

Two transports, both best effort (a missing notification service is never an error):

  • Linux: org.freedesktop.Notifications over D-Bus;
  • everywhere else (Windows, or Linux without QtDBus): a system-tray balloon / toast.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

try:                                   # QtDBus does not exist in the Windows wheels
    from PyQt6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply
    HAVE_DBUS = True
except ImportError:                    # pragma: no cover - depends on the platform
    QDBusConnection = QDBusMessage = QDBusPendingCallWatcher = QDBusPendingReply = None
    HAVE_DBUS = False

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
IFACE = "org.freedesktop.Notifications"

# A balloon vanishes with the tray icon that showed it, so a short-lived process keeps the
# icon around for this long (ms) before it reports "done".
BALLOON_LINGER_MS = 4500

_shared_tray = None                     # a QSystemTrayIcon that lives as long as the process (background mode)


def set_tray(tray) -> None:
    """Use *tray* (a long-lived QSystemTrayIcon) for balloon notifications."""
    global _shared_tray
    _shared_tray = tray


class Notification(QObject):
    """One fire-and-forget notification.  ``done`` is emitted when the daemon has
    answered (or after a short timeout / when there is no daemon)."""

    done = pyqtSignal(bool)                 # True = the daemon accepted it

    def __init__(self, summary: str, body: str = "", icon: str = "", timeout_ms: int = 6000,
                 parent=None) -> None:
        super().__init__(parent)
        self._summary, self._body, self._icon, self._timeout = summary, body, icon, timeout_ms
        self._finished = False
        self._watcher = None
        self._temp_tray = None

    def send(self) -> None:
        if HAVE_DBUS and self._send_dbus():
            return
        self._send_balloon()

    # -- D-Bus ---------------------------------------------------------------

    def _send_dbus(self) -> bool:
        """True when the request was handed to the bus (``done`` follows asynchronously)."""
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, "Notify")
        icon = self._icon if self._icon and os.path.isfile(self._icon) else "camera-photo"
        msg.setArguments([
            "Crabture", 0, icon, self._summary, self._body, [], {"category": "transfer.complete"},
            int(self._timeout),
        ])
        call = bus.asyncCall(msg, 2000)
        self._watcher = QDBusPendingCallWatcher(call, self)
        self._watcher.finished.connect(lambda w: self._finish(not QDBusPendingReply(w).isError()))
        QTimer.singleShot(2500, lambda: self._finish(False))
        return True

    # -- tray balloon ---------------------------------------------------------

    def _send_balloon(self) -> None:
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
        if QApplication.instance() is None or not QSystemTrayIcon.isSystemTrayAvailable():
            self._finish(False)
            return
        tray = _shared_tray
        linger = 400
        if tray is None:
            icon = QIcon(self._icon) if self._icon and os.path.isfile(self._icon) else QApplication.windowIcon()
            tray = self._temp_tray = QSystemTrayIcon(icon)
            tray.show()
            linger = BALLOON_LINGER_MS
        tray.showMessage(self._summary, self._body, QSystemTrayIcon.MessageIcon.Information, int(self._timeout))
        QTimer.singleShot(linger, lambda: self._finish(True))

    def _finish(self, ok: bool) -> None:
        if self._finished:
            return
        self._finished = True
        if self._temp_tray is not None:
            self._temp_tray.hide()
            self._temp_tray.deleteLater()
            self._temp_tray = None
        self.done.emit(ok)


def notify(summary: str, body: str = "", icon: str = "", parent: Optional[QObject] = None,
           on_done: Optional[Callable[[bool], None]] = None) -> Notification:
    n = Notification(summary, body, icon, parent=parent)
    if on_done is not None:
        n.done.connect(on_done)
    n.done.connect(lambda _ok, o=n: o.deleteLater())
    n.send()
    return n
