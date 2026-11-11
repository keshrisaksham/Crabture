"""
ipc.py — single-instance command channel.

A desktop shortcut runs ``crabture --capture region``.  If Crabture is already
running, that command is handed to the running instance instead of starting a
second application (which used to open a second window and take seconds).

Transport: a QLocalServer named ``crabture-<uid>`` (a unix socket, a named pipe on Windows).  Messages are
one JSON object per line; the server answers ``ok`` once the command has been
queued.  Nothing here touches the UI.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from crabture import osinfo
from crabture.capture.log import log

from PyQt6.QtCore import QLockFile, QObject, pyqtSignal

try:                                   # QtNetwork is a separate package on a few distributions
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
    AVAILABLE = True
except ImportError:                    # pragma: no cover - depends on the installation
    QLocalServer = QLocalSocket = None
    AVAILABLE = False


_STAMP: Optional[str] = None


def code_stamp() -> str:
    """Identifies the code this process runs (version + newest source file time).

    A running instance keeps the code it started with.  After an update, a shortcut must
    not be handed to such a stale instance (it would behave like the old version), so the
    client only trusts an instance whose stamp matches its own."""
    global _STAMP
    if _STAMP is None:
        newest = 0
        root = os.path.dirname(os.path.abspath(__file__))
        try:
            for folder, _dirs, files in os.walk(root):
                for name in files:
                    if name.endswith(".py"):
                        newest = max(newest, int(os.path.getmtime(os.path.join(folder, name))))
        except OSError:
            pass
        try:
            from crabture.version import __version__ as version
        except Exception:                              # noqa: BLE001
            version = "?"
        _STAMP = f"{version}-{newest}"
    return _STAMP


def server_name() -> str:
    return f"crabture-{osinfo.user_id()}"


def send_command(command: dict, timeout_ms: int = 1500, name: Optional[str] = None) -> bool:
    """Deliver *command* to a running instance.  True when it was accepted."""
    if not AVAILABLE:
        return False
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(400):
        return False
    try:
        sock.write(json.dumps(command).encode("utf-8") + b"\n")
        if not sock.waitForBytesWritten(timeout_ms):
            return False
        if not sock.waitForReadyRead(timeout_ms):
            return False
        reply = bytes(sock.readAll()).decode("utf-8", "replace").split()
        if not reply or reply[0] != "ok":
            return False
        if len(reply) < 2 or reply[1] != code_stamp():
            log(f"ipc: running instance has different code ({reply[1:] or 'old'} vs {code_stamp()}); not using it")
            return False
        return True
    finally:
        sock.disconnectFromServer()
        sock.close()


class StartupLock:
    """Held by the process that is *starting* the first Crabture.

    A file manager may launch ``crabture`` once per selected file, all at the same moment.  None of
    those processes can see a running instance yet, so each would open its own window.  The first one
    takes this lock until its command server is up; the others wait for it and hand their files over.
    """

    def __init__(self) -> None:
        import tempfile
        self._lock = QLockFile(os.path.join(tempfile.gettempdir(), f"crabture-start-{osinfo.user_id()}.lock"))
        self._lock.setStaleLockTime(10000)
        self.held = False

    def acquire(self) -> bool:
        self.held = self._lock.tryLock(0)
        return self.held

    def release(self) -> None:
        if self.held:
            self._lock.unlock()
            self.held = False


class CaptureLock:
    """Makes terminal captures take turns, across processes.

    The desktop's screenshot service answers one request at a time; when several scripts (or one
    script running jobs in parallel) call ``crabture --capture`` together, all but one would wait for
    a long time-out.  A one-shot capture holds this lock while it grabs, the others queue behind it.
    The lock of a process that died is detected and ignored (there is no time limit on a live holder:
    an on-screen region selection may take as long as the user needs).
    """

    def __init__(self) -> None:
        import tempfile
        self._lock = QLockFile(os.path.join(tempfile.gettempdir(), f"crabture-capture-{osinfo.user_id()}.lock"))
        self._lock.setStaleLockTime(0)
        self.held = False

    def acquire(self, timeout_s: float) -> bool:
        self.held = self._lock.tryLock(int(timeout_s * 1000))
        return self.held

    def release(self) -> None:
        if self.held:
            self._lock.unlock()
            self.held = False


class CommandServer(QObject):
    """Listens for commands from other ``crabture`` processes."""

    command_received = pyqtSignal(dict)

    def __init__(self, parent=None, name: Optional[str] = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server = QLocalServer(self) if AVAILABLE else None
        if self._server is not None:
            self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            self._server.newConnection.connect(self._on_connection)
        self._buffers: dict = {}

    def start(self) -> bool:
        if not AVAILABLE:
            return False
        if self._server.isListening():
            return True
        if self._server.listen(self._name):
            return True
        # Either another instance is alive (leave it alone) or a crashed one left its
        # socket file behind (clear it and retry once).
        probe = QLocalSocket()
        probe.connectToServer(self._name)
        if probe.waitForConnected(300):
            probe.abort()
            return False
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))

    def _drop(self, sock) -> None:
        self._buffers.pop(sock, None)
        sock.deleteLater()

    def _on_ready(self, sock) -> None:
        buf = self._buffers.get(sock, b"") + bytes(sock.readAll())
        if len(buf) > 65536:                                 # nonsense: drop the peer
            sock.disconnectFromServer()
            return
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                command = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                command = None
            if isinstance(command, dict):
                sock.write(f"ok {code_stamp()}\n".encode())
                sock.flush()
                self.command_received.emit(command)
            else:
                sock.write(b"error\n")
                sock.flush()
        self._buffers[sock] = buf
