#!/usr/bin/env python3
"""
capture/portal_helper.py — ask the desktop portal for a silent screenshot, on behalf of a real window.

Why this exists
---------------
On GNOME (Wayland) the portal only takes a *silent* screenshot for an app it can tie to a focused window:
otherwise it tries to show its "allow screenshot?" dialog, GNOME answers "Only the focused app is allowed to
show a system access dialog", and the request fails at once with response 2.  A shortcut-started Crabture has
no window at that moment, and a native-Wayland Qt window cannot hand the portal a parent handle.

An XWayland (xcb) window can: its ``x11:<xid>`` handle is a valid ``parent_window``.  So this tiny program
runs under ``QT_QPA_PLATFORM=xcb``, shows a 1x1 invisible window, waits until that window has focus, makes the
portal call with its handle and prints the result.  It is started by ``CaptureService`` (backend
"portal-focus"); if it fails, the service carries on with the plain portal call and the other backends.

Output (one line on stdout)::

    OK <absolute path of the screenshot file>
    ERR <code> <message>

Deliberately standalone (imports PyQt6 only), so it starts fast and works from the installed package.
"""

from __future__ import annotations

import sys
import time
import uuid

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusMessage
from PyQt6.QtWidgets import QApplication, QWidget

SERVICE = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"

FOCUS_WAIT_MS = 1500          # how long to wait for the window to get focus before asking anyway
OVERALL_TIMEOUT_MS = 60000    # the user may have to click "Allow" on the first run


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


class _Receiver(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__(app)

    @pyqtSlot(QDBusMessage)
    def on_message(self, msg: QDBusMessage) -> None:
        args = msg.arguments()
        code = int(args[0]) if args else 2
        results = args[1] if len(args) > 1 else {}
        if code != 0:
            _say(f"ERR {code} the portal refused the screenshot")
            QApplication.exit(1)
            return
        uri = results.get("uri") if isinstance(results, dict) else None
        if hasattr(uri, "variant"):
            uri = uri.variant()
        uri = str(uri or "")
        path = QUrl(uri).toLocalFile() if uri.startswith("file:") else uri
        if path:
            _say(f"OK {path}")
            QApplication.exit(0)
        else:
            _say("ERR 2 the portal returned no file")
            QApplication.exit(1)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Crabture")
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        _say("ERR 2 no D-Bus session bus")
        return 1

    win = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                  | Qt.WindowType.WindowStaysOnTopHint)
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
    win.resize(1, 1)
    win.setWindowOpacity(0.0)              # focusable, but never visible in the picture
    win.show()
    win.activateWindow()
    win.raise_()

    receiver = _Receiver(app)
    started = time.monotonic()
    state = {"sent": False}

    def ask() -> None:
        if state["sent"]:
            return
        state["sent"] = True
        token = "crabture_" + uuid.uuid4().hex[:12]
        sender = bus.baseService()[1:].replace(".", "_")
        bus.connect(SERVICE, f"{PATH}/request/{sender}/{token}", REQUEST_IFACE, "Response", receiver.on_message)
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, SCREENSHOT_IFACE, "Screenshot")
        msg.setArguments([f"x11:{int(win.winId()):x}", {"handle_token": token, "interactive": False}])
        bus.asyncCall(msg)

    def poll() -> None:
        if app.activeWindow() is win or (time.monotonic() - started) * 1000 >= FOCUS_WAIT_MS:
            ask()
        else:
            QTimer.singleShot(40, poll)

    QTimer.singleShot(40, poll)
    QTimer.singleShot(OVERALL_TIMEOUT_MS, lambda: (_say("ERR 2 timed out"), QApplication.exit(1)))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
