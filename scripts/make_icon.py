#!/usr/bin/env python3
"""Render assets/logo.png into assets/logo.ico (Windows icon) using Qt — no Pillow needed.

Qt's ICO writer stores one image per file, so we write the largest size Windows icons use (256x256);
Windows scales it down for the taskbar, title bar and Explorer.

    python scripts/make_icon.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    _app = QGuiApplication(sys.argv)
    src = os.path.join(ROOT, "assets", "logo.png")
    out = os.path.join(ROOT, "assets", "logo.ico")
    img = QImage(src)
    if img.isNull():
        print(f"cannot read {src}", file=sys.stderr)
        return 1
    img = img.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if not img.save(out, "ICO"):
        print("this Qt build cannot write .ico files", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
