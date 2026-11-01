"""
capture/service.py — Screen capture for Crabture (X11, Wayland and Windows).

Every mode first grabs the whole virtual desktop into one pixmap
(``DesktopGrab``) and then crops it:

    region      frozen-screen overlay, drag a rectangle
    fullscreen  the whole virtual desktop
    window      active window (X11 via xdotool, Windows via Win32) / portal picker (Wayland)
    monitor     the monitor under the cursor (or ``monitor_index``)
    scroll      select a region, scroll the content, frames are stitched

Backends are chosen from the *session* type (XDG_SESSION_TYPE /
WAYLAND_DISPLAY), never from Qt's platform plugin — under XWayland
``QScreen.grabWindow`` returns black.

    Wayland : xdg-desktop-portal Screenshot  →  gnome-screenshot  →  grim
    X11     : Qt grabWindow                  →  maim → scrot → ImageMagick import
    Windows : Qt grabWindow  (the session is "windows"; Crabture's own selector is the default engine)

Public API::

    CaptureService.capture(mode, delay_ms=0, hide_window=None, monitor_index=None, confirm=None)
        returns False (and does nothing) while another capture runs; otherwise
        emits exactly one of captured(QPixmap, mode) / failed(str) / cancelled()
    backend_info(), save_capture(), stitch_vertical(), CAPTURE_MODES

Saving, the clipboard and notifications are NOT done here — see capture/controller.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from PyQt6.QtCore import (
    QObject,
    QPoint,
    QProcess,
    QProcessEnvironment,
    QRect,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QCursor, QGuiApplication, QImage, QPainter, QPixmap

try:                                   # the Windows wheels have no QtDBus; only the Wayland portal needs it
    from PyQt6.QtDBus import (
        QDBusConnection,
        QDBusMessage,
        QDBusPendingCallWatcher,
        QDBusPendingReply,
    )
    HAVE_DBUS = True
except ImportError:                    # pragma: no cover - depends on the platform
    QDBusConnection = QDBusMessage = QDBusPendingCallWatcher = QDBusPendingReply = None
    HAVE_DBUS = False

from crabture import osinfo
from crabture.capture import config, winapi
from crabture.capture.config import CAPTURE_MODES
from crabture.capture.log import log
from crabture.capture.overlay import (
    CountdownOverlay,
    RegionOutline,
    ScrollCaptureStrip,
    logical_to_physical,
    start_monitor_picking,
    start_region_selection,
    start_window_picking,
)

# Time to let the compositor repaint after we hide our own windows.
SETTLE_MS = {"wayland": 400, "x11": 250, "windows": 200, "unknown": 250}
SCROLL_INTERVAL_MS = 600
SCROLL_MAX_FRAMES = 120

# A portal request that ends in "error" after the picker had been on screen at least this
# long means the user closed it (portals use code 1 *or* 2 for that, depending on the desktop).
INTERACTION_SECONDS = 1.2

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
PORTAL_REQUEST_IFACE = "org.freedesktop.portal.Request"


# =========================================================================
#  Session / backend selection (pure — unit-testable)
# =========================================================================

def session_type(env=None) -> str:
    """'windows', 'wayland', 'x11' or 'unknown', from the *session* environment."""
    if env is None and osinfo.IS_WINDOWS:
        return "windows"
    env = os.environ if env is None else env
    t = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if t in ("wayland", "x11"):
        return t
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "unknown"


def backend_chain(session: str, which: Callable[[str], Optional[str]] = shutil.which) -> List[str]:
    """Ordered backend names to try for *session*.

    Names: 'portal-focus', 'portal', 'portal-interactive', 'qt', or an executable name.
    """
    if session == "wayland":
        chain = ["portal-focus", "portal", "portal-interactive"]
        chain += [t for t in ("gnome-screenshot", "grim") if which(t)]
    elif session == "windows":
        chain = ["qt"]
    else:
        chain = ["qt"] + [t for t in ("maim", "scrot", "import") if which(t)]
    return chain


def backend_info(env=None, which: Callable[[str], Optional[str]] = shutil.which) -> str:
    """Short human-readable description of the capture backend in use."""
    s = session_type(env)
    chain = backend_chain(s, which)
    extras = [n for n in chain if n not in ("portal-focus", "portal", "portal-interactive", "qt")]
    if s == "wayland":
        base = "Wayland / xdg-desktop-portal"
    elif s == "x11":
        base = "X11 / Qt grabWindow"
    elif s == "windows":
        base = "Windows / Qt grabWindow"
    else:
        base = "Unknown session (trying X11 backends) / Qt grabWindow"
    tool = system_tool_description(env, which)
    return (f"Default engine: {tool}. Crabture's own grab: " + base
            + (f" (fallbacks: {', '.join(extras)})" if extras else ""))


def _install_hint(session: str) -> str:
    if session == "windows":
        return ("Windows refused the screen grab — check that no security software blocks screen capture "
                "and see the log at " + log_path_hint() + ".")
    if session == "wayland":
        return ("Install a screenshot portal backend "
                "(sudo apt install xdg-desktop-portal xdg-desktop-portal-gnome — or "
                "xdg-desktop-portal-wlr / grim on wlroots compositors).")
    return "Install a capture tool (sudo apt install maim) or check that DISPLAY is set."


def _process_argv(name: str, out_path: str) -> List[str]:
    if name == "gnome-screenshot":
        return ["gnome-screenshot", "-f", out_path]
    if name == "grim":
        return ["grim", out_path]
    if name == "maim":
        return ["maim", out_path]
    if name == "scrot":
        return ["scrot", out_path]
    if name == "import":
        return ["import", "-window", "root", out_path]
    raise ValueError(f"unknown capture tool: {name}")


def log_path_hint() -> str:
    from crabture.capture import log as _log
    return _log.path()


# =========================================================================
#  The system's own screenshot tool (the default engine)
# =========================================================================

# Tools that show their own region / window picker and write the result to a file.
SYSTEM_TOOLS = ("xfce4-screenshooter", "gnome-screenshot")
# Modes where the tool lets the user choose something (so "no file" means "cancelled").
_PICKING_MODES = ("region", "window")


def _sys_argv(tool: str, mode: str, out_path: str) -> List[str]:
    """Command line that makes *tool* take a *mode* screenshot into *out_path*."""
    if tool == "xfce4-screenshooter":
        flag = {"region": "-r", "window": "-w"}.get(mode, "-f")
        return ["xfce4-screenshooter", flag, "-s", out_path]
    if tool == "gnome-screenshot":
        flag = {"region": "-a", "window": "-w"}.get(mode)
        return ["gnome-screenshot"] + ([flag] if flag else []) + ["-f", out_path]
    raise ValueError(f"unknown screenshot tool: {tool}")


def desktop_name(env=None) -> str:
    env = os.environ if env is None else env
    return (env.get("XDG_CURRENT_DESKTOP") or "").lower()


def system_chain(mode: str, session: str, env=None,
                 which: Callable[[str], Optional[str]] = shutil.which) -> List[str]:
    """Backends of the machine's default screenshot tool for *mode*; [] when there is none
    (the caller then uses Crabture's own selector).

    Wayland  : GNOME's built-in screenshot UI through the portal (what the Print key shows);
               a whole-screen shot is taken instantly.
    X11      : ``xfce4-screenshooter`` (Kali / Xfce) or ``gnome-screenshot`` (Debian GNOME).
    Scrolling capture and "monitor" on X11 have no system equivalent.
    """
    if mode == "scroll" or session == "windows":
        return []                             # Windows: Crabture's own selector (Snipping Tool has no CLI)
    if session == "wayland":
        chain = ["portal-focus", "portal"] if mode == "fullscreen" else []
        chain.append("portal-interactive")
        if which("gnome-screenshot"):
            chain.append("sys:gnome-screenshot")
        return chain
    if mode == "monitor":
        return []
    tools = list(SYSTEM_TOOLS)
    if "xfce" not in desktop_name(env):
        tools.reverse()                       # GNOME & co: gnome-screenshot first
    return [f"sys:{t}" for t in tools if which(t)]


def system_tool_description(env=None, which: Callable[[str], Optional[str]] = shutil.which) -> str:
    """One line for the settings page: which tool 'System screenshot tool' means here."""
    session = session_type(env)
    if session == "windows":
        return "Crabture's own selector (Windows has no command-line screenshot tool)"
    if session == "wayland":
        return "GNOME's built-in screenshot tool (through the desktop portal)"
    found = [t for t in system_chain("region", session, env, which)]
    if found:
        return found[0][4:]
    return "none found — Crabture's own selector will be used"


# =========================================================================
#  Portal helpers (pure parts)
# =========================================================================

def uri_to_path(uri: str) -> str:
    if uri.startswith("file:"):
        return QUrl(uri).toLocalFile()
    return uri


def parse_portal_response(code: int, results) -> Tuple[str, str]:
    """Interpret org.freedesktop.portal.Request.Response.

    Returns ('ok', path) | ('cancelled', '') | ('error', message).
    """
    if code == 0:
        uri = results.get("uri") if isinstance(results, dict) else None
        if hasattr(uri, "variant"):          # QDBusVariant
            uri = uri.variant()
        if isinstance(uri, str) and uri:
            return "ok", uri_to_path(uri)
        return "error", "the portal returned no image"
    if code == 1:
        return "cancelled", ""
    return "error", f"the portal refused or failed (response {code})"


def parse_xdotool_geometry(text: str) -> Optional[QRect]:
    """Parse ``xdotool getwindowgeometry --shell`` output."""
    vals = {}
    for line in text.splitlines():
        m = re.match(r"\s*(X|Y|WIDTH|HEIGHT)\s*=\s*(-?\d+)\s*$", line)
        if m:
            vals[m.group(1)] = int(m.group(2))
    if all(k in vals for k in ("X", "Y", "WIDTH", "HEIGHT")) and vals["WIDTH"] > 0 and vals["HEIGHT"] > 0:
        return QRect(vals["X"], vals["Y"], vals["WIDTH"], vals["HEIGHT"])
    return None


def parse_frame_extents(text: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse ``xprop _NET_FRAME_EXTENTS`` output into (left, right, top, bottom)."""
    m = re.search(r"=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text or "")
    return tuple(int(g) for g in m.groups()) if m else None      # type: ignore[return-value]


def _run_quiet(argv: Sequence[str], timeout: float = 2.0) -> Optional[str]:
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def x11_active_window_rect() -> Optional[QRect]:
    """Rectangle (X pixels) of the focused window INCLUDING its title bar and borders.

    Needs ``xdotool``; ``xprop`` adds the window-manager frame when present.  None
    when nothing can be determined (e.g. the focused window is a native Wayland one).
    """
    if not shutil.which("xdotool"):
        return None
    wid = (_run_quiet(["xdotool", "getactivewindow"]) or "").strip()
    if not wid.isdigit():
        return None
    rect = parse_xdotool_geometry(_run_quiet(["xdotool", "getwindowgeometry", "--shell", wid]) or "")
    if rect is None:
        return None
    if shutil.which("xprop"):
        ext = parse_frame_extents(_run_quiet(["xprop", "-id", wid, "_NET_FRAME_EXTENTS"]) or "")
        if ext:
            left, right, top, bottom = ext
            rect = QRect(rect.x() - left, rect.y() - top, rect.width() + left + right, rect.height() + top + bottom)
    return rect


def _screen_table() -> List[Tuple[QRect, float]]:
    return [(s.geometry(), s.devicePixelRatio()) for s in QGuiApplication.screens()]


def windows_active_window_rect() -> Optional[QRect]:
    """Focused window (Windows) in Qt logical coordinates, or None when it cannot be determined."""
    native = winapi.active_window_rect()
    return None if native is None else winapi.native_to_logical(native, _screen_table())


# =========================================================================
#  Saving (thin wrappers; the real work is in capture/config.py)
# =========================================================================

def save_capture(pixmap: QPixmap, directory: Optional[str] = None) -> Optional[str]:
    """Write *pixmap* with the saved name pattern/format; return the path or None."""
    return config.save_pixmap(pixmap, directory)[0]


# =========================================================================
#  Desktop grab model
# =========================================================================

@dataclass
class DesktopGrab:
    """The whole virtual desktop in physical pixels.

    ``physical = (logical - origin) * scale``.
    """
    pixmap: QPixmap
    origin: QPoint
    scale: float
    # True when the user already chose what to capture (interactive portal / picker):
    # the pixmap is then the final result, not the whole desktop.
    interactive: bool = False
    source: str = ""                  # 'portal', 'portal-interactive', 'qt', a tool name …

    def to_physical(self, logical: QRect) -> QRect:
        return logical_to_physical(logical, self.origin, self.scale, self.pixmap.size())

    def crop(self, logical: QRect) -> QPixmap:
        out = self.pixmap.copy(self.to_physical(logical))
        out.setDevicePixelRatio(1.0)
        return out

    def crop_physical(self, rect: QRect) -> QPixmap:
        """Crop by pixels of the grab itself (clamped to the picture)."""
        out = self.pixmap.copy(rect.intersected(QRect(0, 0, self.pixmap.width(), self.pixmap.height())))
        out.setDevicePixelRatio(1.0)
        return out


def virtual_geometry(rects: Sequence[QRect]) -> QRect:
    out = QRect()
    for r in rects:
        out = out.united(r)
    return out


def grab_from_pixmap(pix: QPixmap) -> DesktopGrab:
    """Wrap an externally produced full-desktop image (portal / CLI tool)."""
    pix.setDevicePixelRatio(1.0)
    virt = virtual_geometry([s.geometry() for s in QGuiApplication.screens()])
    if virt.isNull() or virt.width() <= 0:
        return DesktopGrab(pix, QPoint(0, 0), 1.0)
    return DesktopGrab(pix, virt.topLeft(), pix.width() / virt.width())


def _looks_blank(pix: QPixmap) -> bool:
    """True if a coarse sample of *pix* is entirely black/transparent."""
    img = pix.toImage()
    if img.isNull():
        return True
    w, h = img.width(), img.height()
    for yi in range(8):
        for xi in range(8):
            c = img.pixelColor(min(w - 1, xi * w // 8 + w // 16), min(h - 1, yi * h // 8 + h // 16))
            if c.red() or c.green() or c.blue():
                return False
    return True


def grab_qt_desktop() -> Optional[DesktopGrab]:
    """X11: grab every screen with Qt and compose them by geometry."""
    screens = QGuiApplication.screens()
    if not screens:
        return None
    virt = virtual_geometry([s.geometry() for s in screens])
    scale = max(s.devicePixelRatio() for s in screens)
    target = QPixmap(max(1, round(virt.width() * scale)), max(1, round(virt.height() * scale)))
    target.setDevicePixelRatio(1.0)
    target.fill(Qt.GlobalColor.black)
    painter = QPainter(target)
    try:
        for s in screens:
            pm = s.grabWindow(0)
            if pm.isNull():
                return None
            g = s.geometry()
            dest = QRect(
                round((g.x() - virt.x()) * scale), round((g.y() - virt.y()) * scale),
                round(g.width() * scale), round(g.height() * scale),
            )
            painter.drawPixmap(dest, pm)
    finally:
        painter.end()
    return DesktopGrab(target, virt.topLeft(), scale)


# =========================================================================
#  Asynchronous grab with backend fallback
# =========================================================================

class _PortalReceiver(QObject):
    """Receives org.freedesktop.portal.Request.Response."""

    response = pyqtSignal(int, object)

    if HAVE_DBUS:
        @pyqtSlot(QDBusMessage)
        def on_message(self, msg: QDBusMessage) -> None:
            args = msg.arguments()
            code = int(args[0]) if args else 2
            results = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            self.response.emit(code, results)


class _GrabJob(QObject):
    """Try the backends in *chain* one after another until one yields an image.

    Emits ``finished(DesktopGrab | None, error_text)`` or ``cancelled()``
    (user dismissed the portal dialog).  Nothing here blocks the GUI thread.
    """

    finished = pyqtSignal(object, str)
    cancelled = pyqtSignal()

    def __init__(self, chain: Sequence[str], session: str, parent=None, mode: str = "") -> None:
        super().__init__(parent)
        self._chain = list(chain)
        self._session = session
        self._mode = mode
        self._errors: List[str] = []
        # An all-black picture is suspicious (some backends return one when they are not
        # allowed to see the screen) but also what a really black screen looks like: keep the
        # first one and use it only if no backend delivers anything better.
        self._blank_fallback: Optional[DesktopGrab] = None
        self._tmpdir: Optional[str] = None
        self._attempt = 0
        self._current = ""
        self._dead = False
        self._proc: Optional[QProcess] = None
        self._watcher: Optional[QDBusPendingCallWatcher] = None
        self._receiver: Optional[_PortalReceiver] = None
        self._portal_paths: List[str] = []
        self._portal_started = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    # -- control -----------------------------------------------------------

    def start(self) -> None:
        QTimer.singleShot(0, self._next)

    def abort(self) -> None:
        self._dead = True
        self._teardown_attempt()
        self._cleanup()

    def _cleanup(self) -> None:
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def _tmp(self, name: str) -> str:
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="crabture-grab-")
        return os.path.join(self._tmpdir, name)

    def _finish(self, grab: Optional[DesktopGrab], err: str) -> None:
        if self._dead:
            return
        self._dead = True
        self._teardown_attempt()
        self._cleanup()
        self.finished.emit(grab, err)

    def _teardown_attempt(self) -> None:
        self._timer.stop()
        if self._proc is not None:
            proc, self._proc = self._proc, None
            try:
                proc.finished.disconnect()
                proc.errorOccurred.disconnect()
            except TypeError:
                pass
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
            proc.deleteLater()
        if self._portal_paths and HAVE_DBUS:
            bus = QDBusConnection.sessionBus()
            for path in self._portal_paths:
                if self._receiver is not None:
                    bus.disconnect(PORTAL_SERVICE, path, PORTAL_REQUEST_IFACE, "Response",
                                   self._receiver.on_message)
            self._portal_paths = []
        if self._receiver is not None:
            self._receiver.deleteLater()
            self._receiver = None
        if self._watcher is not None:
            self._watcher.deleteLater()
            self._watcher = None

    # -- chain -------------------------------------------------------------

    def _next(self) -> None:
        if self._dead:
            return
        self._teardown_attempt()
        if not self._chain and self._blank_fallback is not None:
            self._finish(self._blank_fallback, "")
            return
        if not self._chain:
            detail = "; ".join(self._errors) if self._errors else "no capture backend available"
            self._finish(None, f"Screen capture failed ({detail}). {_install_hint(self._session)}")
            return
        self._current = self._chain.pop(0)
        self._attempt += 1
        log(f"trying backend {self._current} (mode={self._mode})")
        name = self._current
        try:
            if name == "qt":
                QTimer.singleShot(0, lambda a=self._attempt: self._run_qt(a))
            elif name == "portal-focus":
                self._run_portal_focus()
            elif name in ("portal", "portal-interactive"):
                self._run_portal(interactive=(name == "portal-interactive"))
            else:
                self._run_process(name)
        except Exception as e:    # defensive: never let a backend kill the chain
            self._fail(self._attempt, str(e))

    def _fail(self, attempt: int, msg: str) -> None:
        if self._dead or attempt != self._attempt:
            return
        log(f"backend {self._current} failed: {msg}")
        self._errors.append(f"{self._current}: {msg}")
        QTimer.singleShot(0, self._next)

    def _succeed(self, attempt: int, pix: QPixmap, check_blank: bool, interactive: bool = False,
                 source: str = "") -> None:
        if self._dead or attempt != self._attempt:
            return
        if pix.isNull():
            self._fail(attempt, "could not decode the captured image")
        elif check_blank and _looks_blank(pix):
            if self._blank_fallback is None:
                self._blank_fallback = grab_from_pixmap(pix)
            self._fail(attempt, "captured image is blank (black)")
        else:
            grab = grab_from_pixmap(pix)
            grab.interactive = interactive
            grab.source = source or self._current
            log(f"grab ok via {grab.source} ({pix.width()}x{pix.height()}, interactive={interactive})")
            self._finish(grab, "")

    def _on_timeout(self) -> None:
        if self._current == "portal-interactive" and not self._dead:
            self._cancel("the screenshot picker was left open for too long")
            return
        self._fail(self._attempt, "timed out")

    def _cancel(self, why: str) -> None:
        """The user dismissed the picker: stop everything, capture nothing, do NOT fall back."""
        if self._dead:
            return
        log(f"cancelled ({why})")
        self._dead = True
        self._teardown_attempt()
        self._cleanup()
        self.cancelled.emit()

    # -- Qt (X11) ----------------------------------------------------------

    def _run_qt(self, attempt: int) -> None:
        if self._dead or attempt != self._attempt:
            return
        grab = grab_qt_desktop()
        if grab is None:
            self._fail(attempt, "Qt grabWindow returned no usable image")
        elif _looks_blank(grab.pixmap):
            if self._blank_fallback is None:
                self._blank_fallback = grab
            self._fail(attempt, "Qt grabWindow returned a blank (black) image")
        else:
            self._finish(grab, "")

    # -- external tools ----------------------------------------------------

    def _run_process(self, name: str) -> None:
        system = name.startswith("sys:")
        tool = name[4:] if system else name
        out = self._tmp(f"{tool}.png")
        argv = _sys_argv(tool, self._mode, out) if system else _process_argv(tool, out)
        attempt = self._attempt
        proc = QProcess(self)
        self._proc = proc
        proc.setProgram(argv[0])
        proc.setArguments(argv[1:])
        proc.finished.connect(lambda code, _st, a=attempt, o=out, sy=system: self._on_proc_finished(a, code, o, sy))
        proc.errorOccurred.connect(lambda err, a=attempt: self._on_proc_error(a, err))
        proc.start()
        picking = system and self._mode in _PICKING_MODES
        self._timer.start(180000 if picking else 20000)

    def _on_proc_error(self, attempt: int, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self._fail(attempt, "failed to start")

    def _on_proc_finished(self, attempt: int, code: int, out: str, system: bool = False) -> None:
        if self._dead or attempt != self._attempt:
            return
        if not os.path.exists(out):
            if system and self._mode in _PICKING_MODES and code in (0, 1):
                # The user dismissed the tool's own picker: that is a cancel, not a failure.
                self._dead = True
                self._teardown_attempt()
                self._cleanup()
                self.cancelled.emit()
                return
            self._fail(attempt, f"exited with status {code}")
            return
        if code != 0 and not system:
            self._fail(attempt, f"exited with status {code}")
            return
        # A system tool's picture is already what the user chose: no blank check, final result.
        self._succeed(attempt, QPixmap(out), check_blank=not system, interactive=system)

    # -- xdg-desktop-portal ------------------------------------------------

    def _run_portal_focus(self) -> None:
        """Silent portal screenshot taken on behalf of a focused XWayland window (see portal_helper.py)."""
        attempt = self._attempt
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portal_helper.py")
        if getattr(sys, "frozen", False) or not os.path.isfile(helper):
            self._fail(attempt, "the portal helper is not available in this build")
            return
        if not os.environ.get("DISPLAY"):
            self._fail(attempt, "XWayland is not available (DISPLAY is not set)")
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("QT_QPA_PLATFORM", "xcb")
        proc = QProcess(self)
        self._proc = proc
        proc.setProcessEnvironment(env)
        proc.setProgram(sys.executable)
        proc.setArguments([helper])
        proc.finished.connect(lambda code, _st, a=attempt, p=proc: self._on_helper_finished(a, code, p))
        proc.errorOccurred.connect(lambda err, a=attempt: self._on_proc_error(a, err))
        self._portal_started = time.monotonic()
        proc.start()
        self._timer.start(75000)

    def _on_helper_finished(self, attempt: int, code: int, proc: QProcess) -> None:
        if self._dead or attempt != self._attempt:
            return
        lines = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").strip().splitlines()
        line = lines[-1] if lines else ""
        log(f"portal helper: {line or 'exit ' + str(code)} after {self._portal_elapsed():.1f}s")
        if not line.startswith("OK "):
            self._fail(attempt, line[4:] if line.startswith("ERR ") else f"the portal helper exited with status {code}")
            return
        path = line[3:].strip()
        pix = QPixmap(path)
        if not pix.isNull():
            _remove_portal_file(path)         # GNOME saved it in ~/Pictures; we are about to show it ourselves
        self._succeed(attempt, pix, check_blank=False, source="portal")

    def _run_portal(self, interactive: bool) -> None:
        attempt = self._attempt
        if not HAVE_DBUS:
            self._fail(attempt, "QtDBus is not available on this platform")
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            self._fail(attempt, "no D-Bus session bus")
            return
        token = "crabture_" + uuid.uuid4().hex[:12]
        sender = bus.baseService()[1:].replace(".", "_")
        expected = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        self._receiver = _PortalReceiver(self)
        self._receiver.response.connect(
            lambda code, res, a=attempt, i=interactive: self._on_portal_response(a, i, code, res)
        )
        # Subscribe BEFORE calling so the Response can't be missed.
        bus.connect(PORTAL_SERVICE, expected, PORTAL_REQUEST_IFACE, "Response", self._receiver.on_message)
        self._portal_paths.append(expected)

        msg = QDBusMessage.createMethodCall(
            PORTAL_SERVICE, PORTAL_PATH, PORTAL_SCREENSHOT_IFACE, "Screenshot"
        )
        msg.setArguments(["", {"handle_token": token, "interactive": interactive}])
        call = bus.asyncCall(msg)
        self._watcher = QDBusPendingCallWatcher(call, self)
        self._watcher.finished.connect(
            lambda w, a=attempt, e=expected: self._on_portal_call_done(a, e, w)
        )
        self._portal_started = time.monotonic()
        self._timer.start(180000 if interactive else 45000)

    def _portal_elapsed(self) -> float:
        return time.monotonic() - self._portal_started if self._portal_started else 0.0

    def _on_portal_call_done(self, attempt: int, expected: str, watcher) -> None:
        if self._dead or attempt != self._attempt:
            return
        reply = QDBusPendingReply(watcher)
        if reply.isError():
            msg = reply.error().message() or reply.error().name()
            if self._current == "portal-interactive" and self._portal_elapsed() >= INTERACTION_SECONDS:
                self._cancel(f"the picker ended with a D-Bus error after {self._portal_elapsed():.1f}s: {msg}")
            else:
                self._fail(attempt, msg)
            return
        # Older portals may return a different request path than we predicted.
        try:
            handle = str(reply.argumentAt(0))
        except Exception:
            handle = ""
        if handle.startswith("/") and handle != expected and self._receiver is not None:
            QDBusConnection.sessionBus().connect(
                PORTAL_SERVICE, handle, PORTAL_REQUEST_IFACE, "Response", self._receiver.on_message
            )
            self._portal_paths.append(handle)

    def _on_portal_response(self, attempt: int, interactive: bool, code: int, results) -> None:
        if self._dead or attempt != self._attempt:
            return
        kind, value = parse_portal_response(code, results)
        elapsed = self._portal_elapsed()
        log(f"portal response: code={code} kind={kind} interactive={interactive} after {elapsed:.1f}s")
        if kind == "error" and interactive and elapsed >= INTERACTION_SECONDS:
            kind = "cancelled"                         # the picker was shown for a while: the user closed it
        if kind == "cancelled":
            self._cancel("the user closed the screenshot picker")
        elif kind == "error":
            self._fail(attempt, value)
        else:
            pix = QPixmap(value)
            if not pix.isNull() and not interactive:
                # Only our own silent grab is tidied away.  A picker shot is the desktop's own file
                # (its "Screenshot captured" notification points at it), so it is left alone.
                _remove_portal_file(value)
            self._succeed(attempt, pix, check_blank=False, interactive=interactive,
                          source="portal-interactive" if interactive else "portal")


def _remove_portal_file(path: str) -> None:
    """Delete the file the portal just created for OUR request (once loaded).

    GNOME writes it into ~/Pictures; leaving it would clutter the user's
    folder with a duplicate of what we are about to show.  Only files freshly
    created (last minute) are removed.
    """
    try:
        import time
        if os.path.isfile(path) and time.time() - os.path.getmtime(path) < 60:
            os.remove(path)
    except OSError:
        pass


# =========================================================================
#  Stitching (scrolling capture)
# =========================================================================

def _row_signatures(img: QImage, ignore_right: int) -> List[int]:
    """One hash per scanline, ignoring the rightmost *ignore_right* pixels
    (so a moving scrollbar does not break matching)."""
    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    data = bytes(ptr)
    bpl = img.bytesPerLine()
    keep = max(1, w - ignore_right) * 4
    return [hash(data[y * bpl: y * bpl + keep]) for y in range(h)]


def _common_prefix(a: Sequence[int], b: Sequence[int]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _find_overlap(acc: Sequence[int], new: Sequence[int], min_overlap: int) -> int:
    """Largest k with ``acc[-k:] == new[:k]`` (k >= min_overlap), else 0.

    Overlaps made only of identical rows (blank space) are ambiguous and are
    ignored unless they cover all of *new*.
    """
    if not new or not acc:
        return 0
    first = new[0]
    n_acc, n_new = len(acc), len(new)
    lo = max(0, n_acc - n_new)
    for p in range(lo, n_acc):          # ascending p == descending k
        k = n_acc - p
        if k < min_overlap:
            break
        if acc[p] != first:
            continue
        if list(acc[p:]) == list(new[:k]):
            if len(set(new[:k])) == 1 and k != n_new:
                continue
            return k
    return 0


def stitch_vertical(frames, ignore_right: Optional[int] = None, min_overlap: Optional[int] = None) -> QImage:
    """Stitch scroll-captured *frames* (top to bottom) into one tall image.

    * The vertical overlap of consecutive frames is found by comparing
      scanline hashes.
    * Rows identical at the top / bottom of every frame (sticky header /
      footer) are kept once instead of being repeated.
    * Duplicate frames add nothing; if no overlap is found the frame is
      simply appended.
    * The rightmost ``ignore_right`` pixels (default min(24, width/10)) are
      ignored when matching, so a moving scrollbar is harmless.
    """
    imgs = [f.convertToFormat(QImage.Format.Format_RGB32) for f in frames if f is not None and not f.isNull()]
    if not imgs:
        return QImage()
    w = imgs[0].width()
    imgs = [i for i in imgs if i.width() == w]
    if len(imgs) == 1:
        return imgs[0].copy()

    margin = min(24, w // 10) if ignore_right is None else max(0, ignore_right)
    rows = [_row_signatures(i, margin) for i in imgs]
    heights = {i.height() for i in imgs}
    h = imgs[0].height()

    # Sticky header / footer: rows common to every pair of consecutive frames.
    top = bottom = 0
    if len(heights) == 1:
        tops, bottoms = [], []
        for a, b in zip(rows, rows[1:]):
            if a == b:
                continue                      # duplicate pair says nothing
            tops.append(_common_prefix(a, b))
            bottoms.append(_common_prefix(a[::-1], b[::-1]))
        if tops:
            top, bottom = min(tops), min(bottoms)
        cap = int(h * 0.4)
        top, bottom = min(top, cap), min(bottom, cap)
        if h - top - bottom < max(8, h // 5):
            top = bottom = 0

    min_k = min_overlap if min_overlap is not None else max(4, h // 25)
    # segments: (frame index, y0, y1)
    segments: List[Tuple[int, int, int]] = [(0, 0, h - bottom)]
    acc: List[int] = list(rows[0][0: h - bottom])
    last_idx, last_hi = 0, h - bottom
    for j in range(1, len(imgs)):
        hj = imgs[j].height()
        lo, hi = top, hj - bottom
        content = rows[j][lo:hi]
        k = _find_overlap(acc, content, min_k)
        if k and k == len(content):
            continue                          # nothing new in this frame
        start = lo + k
        segments.append((j, start, hi))
        acc.extend(rows[j][start:hi])
        last_idx, last_hi = j, hi
    if bottom:
        segments.append((last_idx, last_hi, imgs[last_idx].height()))

    total = sum(y1 - y0 for _, y0, y1 in segments)
    out = QImage(w, total, QImage.Format.Format_RGB32)
    out.fill(Qt.GlobalColor.white)
    painter = QPainter(out)
    y = 0
    for idx, y0, y1 in segments:
        painter.drawImage(QPoint(0, y), imgs[idx], QRect(0, y0, w, y1 - y0))
        y += y1 - y0
    painter.end()
    return out


# =========================================================================
#  CaptureService
# =========================================================================

class CaptureService(QObject):
    """Asynchronous screen capture.  One request at a time.

    ``capture()`` returns immediately; exactly one of ``captured``,
    ``failed`` or ``cancelled`` is emitted later (always from the event loop).
    """

    captured = pyqtSignal(QPixmap, str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._busy = False
        self._session = session_type()
        self._mode = ""
        self._monitor_index: Optional[int] = None
        self._hidden_windows: List = []
        self._had_delay = False
        self._confirm = "button"
        self._countdown: Optional[CountdownOverlay] = None
        self._job: Optional[_GrabJob] = None
        self._selection = None
        self._picker = None
        self.last_source = ""            # which backend produced the last captured picture
        self._system_try = False
        self._grab_origin = QPoint(0, 0)
        self._grab_scale = 1.0
        # scrolling-capture state
        self._strip: Optional[ScrollCaptureStrip] = None
        self._outline: Optional[RegionOutline] = None
        self._scroll_timer: Optional[QTimer] = None
        self._frames: List[QImage] = []
        self._region = QRect()
        self._scroll_busy = False
        self._strip_hidden = False
        self._dup_count = 0
        self._err_count = 0
        self._xdotool: Optional[QProcess] = None

    # -- public ------------------------------------------------------------

    def is_busy(self) -> bool:
        return self._busy

    def capture(
        self,
        mode: str,
        delay_ms: int = 0,
        hide_window=None,
        monitor_index: Optional[int] = None,
        confirm: Optional[str] = None,
    ) -> bool:
        """Start a capture.  *hide_window* is a widget (or an iterable of widgets) to
        hide while grabbing.  Returns False — without emitting anything — if a
        capture is already running (e.g. a shortcut pressed twice)."""
        if mode not in CAPTURE_MODES:
            self._emit_later(self.failed, f"Unknown capture mode '{mode}'. Use one of: {', '.join(CAPTURE_MODES)}.")
            return False
        if self._busy:
            return False
        self._busy = True
        self._session = session_type()
        self._mode = mode
        self._monitor_index = monitor_index
        self._had_delay = delay_ms > 0
        self._confirm = confirm or config.confirm_mode()

        # Hide our own windows FIRST, so that during a countdown the user can
        # arrange whatever they want to photograph.
        if hide_window is None:
            windows = []
        elif hasattr(hide_window, "isVisible"):
            windows = [hide_window]
        else:
            windows = list(hide_window)
        for win in windows:
            if win.isVisible() and not win.isMinimized():
                win.hide()
                self._hidden_windows.append(win)

        if delay_ms > 0:
            self._countdown = CountdownOverlay()
            self._countdown.finished.connect(self._begin)
            self._countdown.start(int(delay_ms))
        else:
            self._begin()
        return True

    def cancel(self) -> None:
        """Abort an in-flight capture (emits ``cancelled``)."""
        if self._busy:
            self._finish_cancelled()

    # -- flow --------------------------------------------------------------

    def _emit_later(self, signal, *args) -> None:
        QTimer.singleShot(0, lambda: signal.emit(*args))

    def _begin(self) -> None:
        if not self._busy:
            return
        if self._countdown is not None:
            self._countdown.deleteLater()
            self._countdown = None
        # Give the compositor time to repaint without our windows / the countdown.
        if self._had_delay:
            settle = 150
        elif self._hidden_windows:
            settle = SETTLE_MS.get(self._session, 250)
        else:
            settle = 60
        QTimer.singleShot(settle, self._start_grab)

    def _start_grab(self, own: bool = False) -> None:
        """Grab with the system screenshot tool (default) or, if *own* / none works, with
        Crabture's own full-desktop grab followed by its own selector."""
        if not self._busy:
            return
        chain = [] if own or config.engine() != "system" else system_chain(self._mode, self._session)
        self._system_try = bool(chain)
        if not chain:
            chain = backend_chain(self._session)
        self._job = _GrabJob(chain, self._session, self, mode=self._mode)
        self._job.finished.connect(self._on_grab)
        self._job.cancelled.connect(self._finish_cancelled)
        self._job.start()

    def _on_grab(self, grab: Optional[DesktopGrab], err: str) -> None:
        self._job = None
        if not self._busy:
            return
        if grab is None:
            if self._system_try:                      # the system tool failed: use our own grab + selector
                self._start_grab(own=True)
                return
            self._finish_failed(err)
            return
        mode = self._mode
        self._grab_origin, self._grab_scale = grab.origin, grab.scale
        self._source = grab.source
        if grab.interactive:
            # The system's own picker already let the user choose what to capture.
            if mode == "scroll":
                self._finish_failed("Scrolling capture needs the whole screen, but the system screenshot "
                                    "dialog was used instead. Allow Crabture to take screenshots and try again.")
            else:
                self._finish_ok(grab.pixmap)
            return
        if mode == "fullscreen":
            self._finish_ok(grab.pixmap)
        elif mode == "monitor":
            self._capture_monitor(grab)
        elif mode == "window":
            self._capture_window(grab)
        elif mode in ("region", "scroll"):
            self._select_region(grab)

    # -- monitor / window --------------------------------------------------

    def _capture_monitor(self, grab: DesktopGrab) -> None:
        screens = QGuiApplication.screens()
        screen = None
        if self._monitor_index is not None and 0 <= self._monitor_index < len(screens):
            screen = screens[self._monitor_index]
        elif len(screens) == 1:
            screen = screens[0]
        elif self._session in ("x11", "windows"):
            screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is not None:
            self._finish_ok(grab.crop(screen.geometry()))
            return
        # Several monitors and no reliable pointer position (Wayland): let the user click one.
        self._picker = start_monitor_picking(grab.pixmap, grab.origin, grab.scale, self)
        self._picker.selected.connect(lambda r, g=grab: self._on_monitor_picked(g, r))
        self._picker.cancelled.connect(self._finish_cancelled)

    def _on_monitor_picked(self, grab: DesktopGrab, rect: QRect) -> None:
        self._picker = None
        if self._busy:
            self._finish_ok(grab.crop(rect))

    def _on_window_manual(self, grab: DesktopGrab) -> None:
        self._picker = None
        if self._busy:
            self._select_region(grab)

    def _capture_window(self, grab: DesktopGrab) -> None:
        if self._session == "wayland":
            # The compositor will not say where windows are: outline the one under the pointer from the picture.
            self._picker = start_window_picking(grab.pixmap, grab.origin, grab.scale, self)
            self._picker.selected.connect(lambda r, g=grab: self._on_monitor_picked(g, r))
            self._picker.manual.connect(lambda g=grab: self._on_window_manual(g))
            self._picker.cancelled.connect(self._finish_cancelled)
            return
        if self._session == "windows":
            logical = windows_active_window_rect()
            pix = grab.crop(logical) if logical is not None else None
        else:
            # X11 / XWayland: the focused window, including its title bar and borders.
            rect = x11_active_window_rect()
            pix = grab.crop_physical(rect) if rect is not None else None
        if pix is None or pix.isNull() or pix.width() < 4 or pix.height() < 4:
            self._select_region(grab)             # could not detect it: drag it yourself
            return
        self._finish_ok(pix)

    # -- region / scroll ---------------------------------------------------

    def _select_region(self, grab: DesktopGrab) -> None:
        self._selection = start_region_selection(grab.pixmap, grab.origin, grab.scale, self,
                                                 confirm=self._confirm)
        self._selection.selected.connect(lambda r, g=grab: self._on_region_selected(g, r))
        self._selection.cancelled.connect(self._finish_cancelled)

    def _on_region_selected(self, grab: DesktopGrab, rect: QRect) -> None:
        self._selection = None
        if not self._busy:
            return
        if self._mode == "scroll":
            # Let the overlays disappear before the strip and re-grabs begin.
            QTimer.singleShot(SETTLE_MS.get(self._session, 250), lambda: self._start_scroll(grab, rect))
        else:
            pix = grab.crop(rect)
            if pix.isNull():
                self._finish_failed("The selected region is empty.")
            else:
                self._finish_ok(pix)

    # -- scrolling capture -------------------------------------------------

    def _start_scroll(self, grab: DesktopGrab, rect: QRect) -> None:
        if not self._busy:
            return
        self._region = QRect(rect)
        self._frames = [grab.crop(rect).toImage()]
        self._dup_count = self._err_count = 0
        self._scroll_busy = False
        wayland = self._session == "wayland"
        can_autoscroll = (not wayland) and (self._session == "windows" or shutil.which("xdotool") is not None)

        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        strip = ScrollCaptureStrip(
            allow_autoscroll=can_autoscroll,
            auto_default=not wayland,           # portal grabs flash the screen: manual by default
            always_hide_for_grab=wayland,       # compositor decides where the strip goes
        )
        self._strip = strip
        strip.set_frame_count(1)
        if screen is not None:
            strip.place_near(rect, screen.geometry())
        strip.frame_requested.connect(self._scroll_grab)
        strip.done.connect(self._scroll_done)
        strip.cancelled.connect(self._finish_cancelled)
        strip.show()
        strip.activateWindow()
        if not wayland:
            self._outline = RegionOutline(rect)
            self._outline.show()

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(SCROLL_INTERVAL_MS)
        self._scroll_timer.timeout.connect(self._scroll_tick)
        self._scroll_timer.start()

    def _scroll_tick(self) -> None:
        strip = self._strip
        if strip is None or self._scroll_busy or not strip.auto_capture_enabled():
            return
        if strip.autoscroll_enabled() and self._session == "windows":
            self._scroll_busy = True
            target = winapi.logical_to_native(self._region.center(), _screen_table())
            winapi.scroll_down_at(target.x(), target.y(), 3)
            QTimer.singleShot(250, self._after_win_scroll)
        elif strip.autoscroll_enabled():
            self._scroll_busy = True
            c = self._region.center()
            px = round((c.x() - self._grab_origin.x()) * self._grab_scale)     # X pixels of the region centre
            py = round((c.y() - self._grab_origin.y()) * self._grab_scale)
            proc = QProcess(self)
            self._xdotool = proc
            proc.finished.connect(lambda *_a, p=proc: self._after_autoscroll(p))
            proc.errorOccurred.connect(lambda *_a, p=proc: self._after_autoscroll(p))
            proc.start("xdotool", ["mousemove", str(px), str(py),
                                   "click", "--repeat", "3", "--delay", "30", "5"])
        else:
            self._scroll_grab()

    def _after_win_scroll(self) -> None:
        if not self._busy:
            return
        self._scroll_busy = False
        QTimer.singleShot(250, self._scroll_grab)

    def _after_autoscroll(self, proc: QProcess) -> None:
        if proc is not self._xdotool:
            return
        self._xdotool = None
        proc.deleteLater()
        self._scroll_busy = False
        QTimer.singleShot(250, self._scroll_grab)

    def _scroll_grab(self) -> None:
        if not self._busy or self._strip is None or self._scroll_busy:
            return
        self._scroll_busy = True
        strip = self._strip
        settle = 30
        self._strip_hidden = False
        if strip.needs_hide_for_grab(self._region):
            strip.hide()
            if self._outline is not None:
                self._outline.close()
                self._outline = None
            self._strip_hidden = True
            settle = SETTLE_MS.get(self._session, 250)
        QTimer.singleShot(settle, self._scroll_do_grab)

    def _scroll_do_grab(self) -> None:
        if not self._busy:
            return
        self._job = _GrabJob(backend_chain(self._session), self._session, self)
        self._job.finished.connect(self._on_scroll_frame)
        self._job.cancelled.connect(lambda: self._on_scroll_frame(None, "cancelled"))
        self._job.start()

    def _on_scroll_frame(self, grab: Optional[DesktopGrab], err: str) -> None:
        self._job = None
        if not self._busy or self._strip is None:
            return
        self._scroll_busy = False
        if self._strip_hidden:
            self._strip.show()
            self._strip_hidden = False
        strip = self._strip
        if grab is None:
            self._err_count += 1
            strip.set_status(f"Grab failed ({self._err_count}) — Frames: {len(self._frames)}")
            if self._err_count >= 3:
                self._finish_failed(err)
            return
        self._err_count = 0
        img = grab.crop(self._region).toImage()
        if img == self._frames[-1]:
            self._dup_count += 1
            if strip.autoscroll_enabled() and self._dup_count >= 2:
                self._scroll_done()               # reached the end of the content
            return
        self._dup_count = 0
        self._frames.append(img)
        strip.set_frame_count(len(self._frames))
        if len(self._frames) >= SCROLL_MAX_FRAMES:
            self._scroll_done()

    def _scroll_done(self) -> None:
        if not self._busy:
            return
        frames = list(self._frames)
        if self._job is not None:
            self._job.abort()
            self._job = None
        self._stop_scroll_ui()
        result = stitch_vertical(frames)
        if result.isNull():
            self._finish_failed("Nothing was captured.")
        else:
            self._finish_ok(QPixmap.fromImage(result))

    def _stop_scroll_ui(self) -> None:
        if self._scroll_timer is not None:
            self._scroll_timer.stop()
            self._scroll_timer.deleteLater()
            self._scroll_timer = None
        if self._strip is not None:
            self._strip.hide()
            self._strip.deleteLater()
            self._strip = None
        if self._outline is not None:
            self._outline.close()
            self._outline = None
        if self._xdotool is not None:
            self._xdotool.kill()
            self._xdotool.deleteLater()
            self._xdotool = None
        self._scroll_busy = False

    # -- completion --------------------------------------------------------

    def _teardown(self) -> None:
        self._busy = False
        if self._countdown is not None:
            self._countdown.cancel()
            self._countdown.deleteLater()
            self._countdown = None
        if self._job is not None:
            self._job.abort()
            self._job = None
        if self._selection is not None:
            self._selection.close()
            self._selection = None
        if self._picker is not None:
            self._picker.close()
            self._picker = None
        self._stop_scroll_ui()
        self._frames = []
        windows, self._hidden_windows = self._hidden_windows, []
        for win in windows:
            try:
                win.show()
            except RuntimeError:                  # the window was closed meanwhile
                pass
        # Deliberately NOT raised / activated: a shortcut that only saves a screenshot must not
        # pull Crabture in front of whatever you were doing.

    def _finish_ok(self, pixmap: QPixmap) -> None:
        mode = self._mode
        self.last_source = getattr(self, "_source", "")
        self._teardown()
        self._emit_later(self.captured, pixmap, mode)

    def _finish_failed(self, message: str) -> None:
        self._teardown()
        self._emit_later(self.failed, message)

    def _finish_cancelled(self) -> None:
        self._teardown()
        self._emit_later(self.cancelled)
