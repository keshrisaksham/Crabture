"""
resources.py — Locate bundled resource files (logo etc.).

Assets live in ``<project root>/assets``:

* source tree / installed package:  <root>/assets/<name>, where <root> is the
  folder that contains both ``main.py`` and the ``crabture`` package
  (e.g. ``/usr/lib/crabture`` for the .deb);
* PyInstaller one-file build:       <_MEIPASS>/assets/<name>.
"""

from __future__ import annotations

import os
import sys

ASSETS_DIRNAME = "assets"


def project_root() -> str:
    """Folder holding main.py, the ``crabture`` package and ``assets/``."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    # this file is <root>/crabture/resources.py
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource(relative_path: str) -> str:
    """Absolute path of a bundled resource (``get_resource("logo.png")``)."""
    root = project_root()
    candidate = os.path.join(root, ASSETS_DIRNAME, relative_path)
    if os.path.exists(candidate):
        return candidate
    return os.path.join(root, relative_path)     # legacy flat layout
