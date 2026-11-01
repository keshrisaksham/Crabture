"""
capture/log.py — a tiny diagnostic log of what the capture machinery did.

Written to ``$XDG_CACHE_HOME/crabture/capture.log`` (default ``~/.cache/crabture``; on Windows
``%LOCALAPPDATA%\\Crabture\\capture.log``), trimmed
to ~200 KB.  It exists because capture runs from a desktop shortcut, where there is no
terminal to read errors from.  Never raises.
"""

from __future__ import annotations

import os
import time

from crabture import osinfo

_MAX = 200_000


def path() -> str:
    return os.path.join(osinfo.cache_dir(), "capture.log")


def log(message: str) -> None:
    try:
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            if os.path.getsize(p) > _MAX:
                with open(p, "rb") as fh:
                    fh.seek(-_MAX // 2, os.SEEK_END)
                    tail = fh.read()
                with open(p, "wb") as fh:
                    fh.write(tail[tail.find(b"\n") + 1:])
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} [{os.getpid()}] {message}\n")
    except Exception:                                  # noqa: BLE001 - logging must never break a capture
        pass
