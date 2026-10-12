#!/usr/bin/env python3
"""
main.py — Entry point for Crabture.

Creates the QApplication, applies the default (light) theme, instantiates
CrabtureMainWindow, and enters the event loop.

Themes live in ui/theme.py and resource lookup in resources.py; both are
re-exported here for backwards compatibility.

Capture flags (see capture/service.py):
    crabture --capture region|fullscreen|window|monitor|scroll [--delay S] [--no-hide]
    crabture --register-hotkeys | --unregister-hotkeys   (GNOME shortcuts)
    crabture --backend-info
"""

from __future__ import annotations

import argparse
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from crabture.resources import get_resource
from crabture.ui.theme import apply_theme, initial_theme
from crabture.ui.window import CrabtureMainWindow

__all__ = ["main", "build_parser", "get_resource", "apply_theme"]

CAPTURE_MODES = ("region", "fullscreen", "window", "monitor", "scroll")


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
        "image",
        nargs="?",
        default=None,
        help="Image file to open on launch (used by 'Edit with Crabture' context menu).",
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
        default=0.0,
        metavar="SECONDS",
        help="Wait this many seconds before capturing (default: 0).",
    )
    cap.add_argument(
        "--no-hide",
        action="store_true",
        help="Do not hide the Crabture window while capturing.",
    )
    cap.add_argument(
        "--register-hotkeys",
        action="store_true",
        help="Register GNOME keyboard shortcuts (Ctrl+Print, Ctrl+Shift+Print, "
             "Ctrl+Alt+Print) that run 'crabture --capture …', then exit.",
    )
    cap.add_argument(
        "--unregister-hotkeys",
        action="store_true",
        help="Remove the shortcuts added by --register-hotkeys, then exit.",
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


def _run_capture_when_ready(window, args) -> None:
    """Start the CLI-requested capture once the event loop is running."""
    if not hasattr(window, "run_capture"):
        print("crabture: this build of the main window has no run_capture(); "
              "--capture is not available.", file=sys.stderr)
        return
    window.run_capture(args.capture, max(0.0, args.delay), hide=not args.no_hide, quit_on_cancel=True)


def main(argv: list[str] | None = None) -> None:
    # --- Parse CLI arguments (used by .desktop right-click integration) ---
    parser = build_parser()
    # argparse must not consume Qt's own flags; parse only known args.
    args, _qt_args = parser.parse_known_args(argv)

    # Flags that must work without a display / QApplication.
    if args.register_hotkeys or args.unregister_hotkeys:
        from crabture.capture import hotkeys
        lines = (hotkeys.register_gnome_shortcuts() if args.register_hotkeys
                 else hotkeys.unregister_gnome_shortcuts())
        print("\n".join(lines))
        sys.exit(0)
    if args.backend_info:
        from crabture.capture.service import backend_info
        print(backend_info())
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("Crabture")
    app.setOrganizationName("YawnByte")

    app.setWindowIcon(QIcon(get_resource("logo.png")))

    apply_theme(app, initial_theme())

    window = CrabtureMainWindow(initial_image=args.image, initial_directory=args.directory)
    # Crabture always starts maximized. A CLI capture that hides Crabture shouldn't flash the window first; it
    # is shown once the screenshot has been taken.
    if not (args.capture and not args.no_hide):
        window.showMaximized()

    # Load the image after the event loop is ready.
    if args.image:
        window.load_image_to_editor(args.image)

    if args.capture:
        QTimer.singleShot(0, lambda: _run_capture_when_ready(window, args))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
