#!/usr/bin/env python3
"""
main.py — Entry point for Crabture.

Creates the QApplication, applies the default (light) theme, instantiates
CrabtureMainWindow, and enters the event loop.

Themes live in ui/theme.py and resource lookup in resources.py; both are
re-exported here for backwards compatibility.

Capture flags (see capture/):
    crabture --capture region|fullscreen|window|monitor|scroll
             [--delay S] [--folder DIR] [--after editor|save] [--clipboard] [--no-hide]
             [--output FILE|DIR] [--name NAME] [--copy-to DIR ...] [--print-path]
    crabture --register-hotkeys | --unregister-hotkeys   (desktop shortcuts, from Configure ▸ Capture)
    crabture --background                                (Windows: tray icon + global capture hot-keys)
    crabture --install-integration | --remove-integration (right-click entries in the file manager)
    crabture --backend-info
    crabture [--directory DIR] [IMAGE ...]               (several images open as one session)

If Crabture is already running, ``--capture`` is handed to that instance (ipc.py).
Otherwise a capture that only saves the shot runs without opening any window.

Terminal use: ``--output`` / ``--print-path`` make the command a self-contained one-shot.  It is not
handed to a running instance, prints the saved path(s) on stdout, shows no notification and exits
with 0 (saved), 1 (failed) or 2 (cancelled).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from crabture import osinfo
from crabture.capture import config as capture_config
from crabture.capture.config import AFTER_CHOICES, CAPTURE_MODES, CaptureRequest
from crabture.resources import get_resource
from crabture.ui.theme import apply_theme, initial_theme

__all__ = ["main", "build_parser", "get_resource", "apply_theme"]

# A terminal capture waits at most this long for other terminal captures ahead of it.
CAPTURE_QUEUE_SECONDS = 180

# A background instance that only saves shots goes away after this long without work.
IDLE_QUIT_MS = 15 * 60 * 1000
# "Open" commands arriving within this many seconds of the previous one belong to the same
# selection (a file manager launching Crabture once per file) and are merged into one session.
OPEN_BURST_SECONDS = 2.5


def _app_version() -> str:
    try:
        from crabture.version import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crabture",
        description="Crabture — Image Editor & screen capture",
    )
    parser.add_argument(
        "images",
        nargs="*",
        default=[],
        metavar="IMAGE",
        help="Image file(s) to open on launch (used by the 'Edit with Crabture' context menu). "
             "Several files open as one session: the gallery lists just those files.",
    )
    parser.add_argument(
        "--directory",
        "-d",
        default=None,
        metavar="DIR",
        help="Directory to populate the gallery with on launch "
             "(used by 'Open Directory with Crabture' context menu).",
    )
    cap = parser.add_argument_group("screen capture")
    cap.add_argument(
        "--capture",
        choices=CAPTURE_MODES,
        default=None,
        metavar="MODE",
        help="Take a screenshot on launch: " + ", ".join(CAPTURE_MODES) + ".",
    )
    cap.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Wait this many seconds before capturing (default: the delay saved in Configure).",
    )
    cap.add_argument(
        "--folder",
        default=None,
        metavar="DIR",
        help="Save the screenshot in DIR (default: the capture folder saved in Configure).",
    )
    cap.add_argument(
        "--after",
        choices=AFTER_CHOICES,
        default=None,
        help="What to do with the shot: 'editor' opens it in Crabture, 'save' only saves it "
             "(default: as saved in Configure).",
    )
    cap.add_argument(
        "--clipboard",
        action="store_true",
        help="Also copy the screenshot to the clipboard.",
    )
    cap.add_argument(
        "--output", "-o",
        default=None,
        metavar="PATH",
        help="Save the screenshot to exactly this file (.png .jpg .webp .bmp; no suffix = the saved format), "
             "or into this folder if PATH is a directory / ends in a slash. Implies --print-path.",
    )
    cap.add_argument(
        "--name", "-n",
        default=None,
        metavar="NAME",
        help="File name for this screenshot, without a folder (strftime codes like %%Y-%%m-%%d and {mode} work; "
             "a .png/.jpg/.webp/.bmp ending also picks the format). Use with --folder or --output DIR.",
    )
    cap.add_argument(
        "--copy-to",
        action="append",
        default=[],
        metavar="DIR",
        help="Also save a copy in DIR (repeat for several folders; added to the folders saved in Configure).",
    )
    cap.add_argument(
        "--print-path", "-p",
        action="store_true",
        help="Wait for the capture, print the path(s) of the saved file(s) on stdout and exit with "
             "0 (saved), 1 (failed) or 2 (cancelled). Defaults --after to 'save'. For scripts and the terminal.",
    )
    cap.add_argument(
        "--no-hide",
        action="store_true",
        help="Do not hide the Crabture window while capturing.",
    )
    cap.add_argument(
        "--register-hotkeys",
        action="store_true",
        help="Give the capture shortcuts saved in Configure ▸ Capture to the desktop "
             "(GNOME / Xfce / Windows) so they work from anywhere, then exit.",
    )
    cap.add_argument(
        "--unregister-hotkeys",
        action="store_true",
        help="Remove the shortcuts added by --register-hotkeys, then exit.",
    )
    cap.add_argument(
        "--background",
        action="store_true",
        help="Run without a window, with a notification-area icon, and serve the capture shortcuts "
             "applied in Configure ▸ Capture (Windows start-up mode).",
    )
    cap.add_argument(
        "--install-integration",
        action="store_true",
        help="Add the right-click 'Edit with Crabture' entries to your file manager (Nautilus), then exit.",
    )
    cap.add_argument(
        "--remove-integration",
        action="store_true",
        help="Remove the right-click entries added by --install-integration, then exit.",
    )
    cap.add_argument(
        "--backend-info",
        action="store_true",
        help="Print which screen-capture backend would be used, then exit.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_app_version()}",
    )
    return parser


def is_oneshot(args) -> bool:
    """A terminal capture: wait for the result, print the path, exit."""
    return bool(args.capture and (args.output or args.print_path))


def request_from_args(args) -> Optional[CaptureRequest]:
    """Build the request for ``--capture``.  Raises ``ValueError`` for an unusable ``--output``."""
    if not args.capture:
        return None
    folder, output = args.folder, None
    if args.output:
        out_folder, output, error = capture_config.resolve_output(args.output)
        if error:
            raise ValueError(f"--output: {error}")
        folder = out_folder or folder
    if args.name:
        if "/" in args.name or "\\" in args.name:
            raise ValueError("--name: give only a file name; use --folder or --output DIR for the folder")
        if output:
            raise ValueError("--name cannot be combined with --output FILE (the file name is already in it); "
                             "use --output DIR/ or --folder with --name")
    oneshot = is_oneshot(args)
    return CaptureRequest(
        mode=args.capture,
        delay=None if args.delay is None else max(0.0, args.delay),
        folder=folder,
        after=args.after or ("save" if oneshot else None),
        hide=False if args.no_hide else None,
        clipboard=True if args.clipboard else None,
        source="cli",
        output=output,
        name=args.name or None,
        copies=list(args.copy_to or []) or None,
        quiet=oneshot,
    )


class _Host(QObject):
    """Owns the capture controller, the command channel and (lazily) the main window.

    Shortcuts that only save the shot never create the window at all.
    """

    def __init__(self, app: QApplication, args, request: Optional[CaptureRequest], startup_lock=None,
                 capture_lock=None) -> None:
        super().__init__(app)
        from crabture import ipc
        from crabture.capture import controller

        self._app = app
        self._args = args
        self.window = None
        self._exit_code = 0
        self._oneshot = bool(request is not None and request.quiet)   # terminal capture: print, then exit
        self._clip_conn = False
        self._clip_owned = False         # we put a screenshot on the clipboard and still own it
        self.startup_lock = startup_lock
        self.capture_lock = capture_lock         # held by a terminal one-shot until its capture ends
        self._opened: list = []          # files of the current "open" session
        self._opened_at = time.monotonic()
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._idle_quit)

        self.controller = controller.shared()
        self.controller.finished.connect(self._on_finished)
        self.controller.failed.connect(self._on_failed)
        self.controller.cancelled.connect(self._on_cancelled)

        self.server = ipc.CommandServer(self)
        self.server.command_received.connect(self._on_command)
        self.server.start()
        if self.startup_lock is not None:
            self.startup_lock.release()

        # A resident instance (Windows start-up mode) never goes away on its own: it owns the hot-keys.
        self._resident = bool(getattr(args, "background", False))
        self.hotkeys = None
        self.tray = None
        if osinfo.IS_WINDOWS:
            self._start_windows_services()

        wants_window = (request is None and not self._resident) or (
            request is not None and request.resolved().after == "editor")
        if wants_window:
            self.ensure_window(show=request is None or not request.resolved().hide)
        else:
            app.setQuitOnLastWindowClosed(False)     # no window: only quit() ends the process
        if request is not None:
            QTimer.singleShot(0, lambda: self.capture(request))

    # -- Windows: global hot-keys + tray -----------------------------------

    def _start_windows_services(self) -> None:
        from crabture.capture import notify, tray, winhotkeys
        if self._resident and tray.available():
            self.tray = tray.CrabtureTray(
                self._app.windowIcon(),
                capture=lambda mode: self.capture(CaptureRequest(mode=mode, source="tray")),
                open_editor=lambda: self.ensure_window(show=True),
                quit_app=self._app.quit,
                parent=self,
            )
            self.tray.show()
            notify.set_tray(self.tray)
        if self._resident or capture_config.desktop_applied():
            self.hotkeys = winhotkeys.WinHotkeys(self)
            self.hotkeys.triggered.connect(lambda sc: self.capture(capture_config.request_for(sc)))
            failures = self.hotkeys.start()
            if failures and self._resident:
                names = {sc.id: sc for sc in capture_config.shortcuts()}
                lines = [f"{names[i].describe()}: {why}" for i, why in failures.items() if i in names]
                notify.notify("Some Crabture shortcuts are unavailable", "\n".join(lines), parent=self)

    # -- window ------------------------------------------------------------

    def ensure_window(self, show: bool = True):
        if self.window is None:
            from crabture.ui.window import CrabtureMainWindow
            self._app.setQuitOnLastWindowClosed(not self._resident)
            images = [os.path.abspath(p) for p in (self._args.images or [])]
            self._opened = list(images)
            self._opened_at = time.monotonic()
            # The gallery shows the folder the user asked for (--directory), else the folder the
            # file came from (a file manager's "Open With" passes only the file), else the capture folder.
            self.window = CrabtureMainWindow(initial_image=images[0] if images else None,
                                             initial_directory=self._args.directory)
            if len(images) > 1:
                self.window.open_files(images)           # several files: one session, only those files
            elif images:
                self.window.load_image_to_editor(images[0])
        if show:
            self.window.showMaximized()
            if self._resident:
                self.window.raise_()
                self.window.activateWindow()
        return self.window

    # -- commands ----------------------------------------------------------

    def capture(self, request: CaptureRequest) -> None:
        self._idle.stop()
        if self.window is None and request.resolved().after == "editor":
            self.ensure_window(show=False)           # shown once the shot is taken
        self.controller.request(request)             # silently ignored if one is running

    def _on_command(self, cmd: dict) -> None:
        kind = cmd.get("cmd")
        if kind == "capture":
            req = CaptureRequest.from_dict(cmd.get("request") or {})
            if req is not None:
                self.capture(req)
        elif kind == "raise":
            self.ensure_window(show=True)
        elif kind == "open":
            self.open_from_command(cmd.get("files") or [], cmd.get("directory"))

    def open_from_command(self, files, directory) -> None:
        """Files / a folder sent by another ``crabture`` process: show them in THIS window.

        Several commands in quick succession (one per selected file) form one session.
        """
        files = [os.path.abspath(str(f)) for f in files if f]
        directory = os.path.abspath(str(directory)) if directory else None
        self._idle.stop()
        if self.window is None:
            self._args.images = files
            self._args.directory = directory
            self.ensure_window(show=True)
            return
        if files:
            now = time.monotonic()
            burst = self._opened and now - self._opened_at < OPEN_BURST_SECONDS
            self._opened = list(dict.fromkeys((self._opened + files) if burst else files))
            self._opened_at = now
            self.window.open_files(self._opened)
        elif directory:
            self.window.watch_directory(directory)
        self.window.showMaximized()
        self.window.raise_()
        self.window.activateWindow()

    # -- results -----------------------------------------------------------

    def _print_result(self, result) -> None:
        """Terminal one-shot: the saved path(s) go to stdout (one per line), notes to stderr."""
        try:
            print(result.path)
            for extra in result.copies:
                print(extra)
            sys.stdout.flush()
            if result.note:
                print(f"crabture: {result.note}", file=sys.stderr)
        except (OSError, ValueError):                 # closed pipe: the file is saved anyway
            pass

    def _release_capture_lock(self) -> None:
        if self.capture_lock is not None:
            self.capture_lock.release()

    def _on_finished(self, result) -> None:
        self._release_capture_lock()
        if self._oneshot:
            self._print_result(result)
        if self.window is None and result.request.after == "editor":
            self.ensure_window(show=False)
        if self.window is not None:
            self.window.show_capture_result(result)
            return
        if self._oneshot and not result.request.clipboard:
            self._app.exit(self._exit_code)           # nothing left to wait for
            return
        if self._oneshot:
            print("crabture: staying alive so the clipboard keeps the image "
                  "(until something else is copied; Ctrl+C to stop).", file=sys.stderr)
        # Background mode: the notification is the only feedback.
        note = result.notification
        if note is not None:
            note.done.connect(lambda _ok: self._background_done(result.request.clipboard))
        else:
            self._background_done(result.request.clipboard)

    def _background_done(self, clipboard: bool) -> None:
        if clipboard:
            # A clipboard owned by this process disappears when it exits (there is no
            # clipboard manager on every desktop): stay quietly until something else is copied.
            self._clip_owned = True
            if not self._clip_conn:
                self._clip_conn = True
                clip = QApplication.clipboard()
                if clip is not None:
                    clip.dataChanged.connect(self._clipboard_taken)
        # Stay ready for the next shortcut a moment (much longer while we own the clipboard).
        self._idle.start(IDLE_QUIT_MS if self._clip_owned else 2500)

    def _clipboard_taken(self) -> None:
        clip = QApplication.clipboard()
        if clip is not None and clip.ownsClipboard():
            return                           # that was our own copy
        self._clip_owned = False
        if self.window is None and not self.controller.is_busy() and not self._resident:
            self._app.quit()

    def _idle_quit(self) -> None:
        if self._resident:
            return
        if self.window is None and not self.controller.is_busy():
            self._app.exit(self._exit_code)
        else:
            self._idle.start(IDLE_QUIT_MS)

    def _on_failed(self, message: str, request) -> None:
        self._release_capture_lock()
        if self.window is not None:
            self.window.show_capture_failure(message)
            if not self.window.isVisible() and not self._resident:   # launched only for this capture
                self._app.exit(1)
            return
        print(f"crabture: {message}", file=sys.stderr)
        self._exit_code = 1
        if self._oneshot:
            self._app.exit(1)
            return
        from crabture.capture.notify import notify
        notify("Screenshot failed", message, parent=self, on_done=lambda _ok: self._idle.start(300))

    def _on_cancelled(self, request) -> None:
        self._release_capture_lock()
        if self.window is not None:
            self.window.show_capture_cancelled()
            if not self.window.isVisible() and not self._resident:   # launched only for this capture
                self._app.quit()
        elif self._oneshot:
            print("crabture: capture cancelled.", file=sys.stderr)
            self._app.exit(2)
        else:
            self._idle.start(300)


def _exit_cleanly_on_signals(app: QApplication, host: "_Host") -> None:
    """Terminal one-shot: Ctrl+C / ``kill`` ends the capture quietly (status 128+signal).

    Without this, Ctrl+C raises KeyboardInterrupt inside a Qt callback and PyQt aborts the process
    (traceback, core dump, status -6) with the capture lock still held.  The timer only exists so the
    interpreter gets control often enough to notice the signal while Qt waits for events.
    """
    import signal

    def handler(signum, _frame) -> None:
        host._release_capture_lock()
        app.exit(128 + int(signum))

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            if name == "SIGINT" and signal.getsignal(sig) == signal.SIG_IGN:
                continue                          # started with Ctrl+C ignored (nohup, a background job): keep that
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    tick = QTimer(host)
    tick.timeout.connect(lambda: None)
    tick.start(200)


def main(argv: list[str] | None = None) -> None:
    # --- Parse CLI arguments (used by .desktop right-click integration) ---
    parser = build_parser()
    # argparse must not consume Qt's own flags; parse only known args.
    args, _qt_args = parser.parse_known_args(argv)

    # Flags that must work without a display / QApplication.
    if args.register_hotkeys or args.unregister_hotkeys:
        from crabture.capture import config, hotkeys
        if args.register_hotkeys:
            report = hotkeys.apply_shortcuts(config.shortcuts())
            config.set_desktop_applied(report.supported and report.applied > 0)
        else:
            report = hotkeys.remove_shortcuts()
            config.set_desktop_applied(False)
        print(report.text())
        sys.exit(0 if report.ok else 1)
    if args.install_integration or args.remove_integration:
        from crabture import integration
        _paths, message = (integration.install_nautilus_scripts() if args.install_integration
                           else integration.remove_nautilus_scripts())
        print(message)
        sys.exit(0)
    if args.backend_info:
        from crabture.capture.service import backend_info
        print(backend_info())
        sys.exit(0)

    if args.background:
        from crabture import ipc
        if ipc.send_command({"cmd": "ping"}):
            sys.exit(0)                     # a Crabture is already serving the shortcuts

    if (args.output or args.print_path or args.copy_to or args.name) and not args.capture:
        parser.error("--output, --name, --print-path and --copy-to need --capture MODE")
    try:
        request = request_from_args(args)
    except ValueError as e:
        parser.error(str(e))
    if request is not None and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                                    or osinfo.IS_WINDOWS or sys.platform == "darwin"):
        print("crabture: taking a screenshot needs a graphical session "
              "(DISPLAY / WAYLAND_DISPLAY is not set).", file=sys.stderr)
        sys.exit(1)
    if request is not None and not request.quiet:
        # A running Crabture takes the job: instant, and no second window.
        # (A terminal one-shot --output / --print-path never does: it must report its own result.)
        from crabture import ipc
        if ipc.send_command({"cmd": "capture", "request": request.to_dict()}):
            sys.exit(0)

    # Files / a folder: one Crabture window does it all.  A running instance takes them over, and
    # when a file manager starts several processes at once, only the first one becomes the window.
    startup_lock = None
    if (args.images or args.directory) and request is None and not args.background:
        from crabture import ipc
        command = {"cmd": "open",
                   "files": [os.path.abspath(p) for p in args.images],
                   "directory": os.path.abspath(args.directory) if args.directory else None}
        if ipc.send_command(command):
            sys.exit(0)
        startup_lock = ipc.StartupLock()
        deadline = time.monotonic() + 5.0
        while True:
            if startup_lock.acquire():
                # Nobody was starting.  Or the starter just finished and its server is up now: ask once more.
                if ipc.send_command(command):
                    startup_lock.release()
                    sys.exit(0)
                break
            if time.monotonic() > deadline:
                break                        # lock still busy (a stale one): carry on as our own instance
            time.sleep(0.1)
            if ipc.send_command(command):
                sys.exit(0)

    capture_lock = None
    if request is not None and request.quiet:
        # Terminal captures take turns (the desktop can only serve one screenshot at a time).
        from crabture import ipc
        capture_lock = ipc.CaptureLock()
        if not capture_lock.acquire(CAPTURE_QUEUE_SECONDS):
            print(f"crabture: another screenshot is still in progress (waited {CAPTURE_QUEUE_SECONDS}s).",
                  file=sys.stderr)
            sys.exit(1)

    osinfo.set_app_user_model_id("YawnByte.Crabture")      # Windows: our icon on the taskbar / in toasts
    app = QApplication(sys.argv)
    app.setApplicationName("Crabture")
    app.setOrganizationName("YawnByte")

    app.setWindowIcon(QIcon(get_resource("logo.png")))

    apply_theme(app, initial_theme())

    from crabture import integration
    integration.ensure_first_run()         # packaged install: add the right-click entries once

    host = _Host(app, args, request, startup_lock, capture_lock)       # noqa: F841 - parented to the application
    if host._oneshot:
        _exit_cleanly_on_signals(app, host)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
