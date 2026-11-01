"""
osinfo.py — the few places where Crabture has to care which operating system it runs on.

Everything else in the code base is plain Qt.  Keeping the platform differences in one module
means the rest of the code never tests ``sys.platform`` for paths, processes or file managers.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from typing import Optional

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

APP_DIRNAME = "Crabture" if IS_WINDOWS else "crabture"


def _windows_base() -> str:
    return os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")


def cache_dir() -> str:
    """Where throw-away files (logs) go.  ``$XDG_CACHE_HOME/crabture`` / ``%LOCALAPPDATA%\\Crabture``."""
    if IS_WINDOWS:
        return os.path.join(_windows_base(), APP_DIRNAME)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, APP_DIRNAME)


def state_dir() -> str:
    """Where data that should survive (the rename journal) goes."""
    if IS_WINDOWS:
        return os.path.join(_windows_base(), APP_DIRNAME)
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, APP_DIRNAME)


def user_id() -> str:
    """A stable per-user token for names that must not collide between users of one machine."""
    if hasattr(os, "getuid"):
        return str(os.getuid())
    try:
        name = getpass.getuser()
    except Exception:                                   # noqa: BLE001 - no username in odd environments
        name = os.environ.get("USERNAME") or "user"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "user"


def subprocess_kwargs() -> dict:
    """Extra ``subprocess`` arguments: no console window on Windows, own process group elsewhere."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {"start_new_session": True}


def kill_process_tree(proc: "subprocess.Popen") -> None:
    """Kill *proc* (and, where the platform allows it, its children)."""
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           timeout=5, **{"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)})
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(proc.pid, 9)
    except (OSError, ProcessLookupError):
        proc.kill()


def reveal_in_file_manager(path: str) -> None:
    """Show *path* in the system file manager (selecting it where that is supported)."""
    path = os.path.abspath(path)
    try:
        if IS_WINDOWS:
            if os.path.isfile(path):
                # explorer needs the argument as ONE token: /select,"C:\a b\c.png"
                subprocess.Popen(f'explorer /select,"{path}"')
            else:
                os.startfile(path)                      # type: ignore[attr-defined]  # noqa: S606
        elif IS_MAC:
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
    except OSError:
        pass


def set_app_user_model_id(app_id: str) -> None:
    """Windows: group the taskbar button / toast notifications under our own identity (and icon)
    instead of the Python interpreter's."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)   # type: ignore[attr-defined]
    except Exception:                                   # noqa: BLE001 - cosmetic only
        pass


def autostart_command() -> Optional[str]:
    """Windows: the command line that starts Crabture's background (hot-key) instance at login."""
    if not IS_WINDOWS:
        return None
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--background"])
    exe = sys.executable or "python"
    windowless = os.path.join(os.path.dirname(exe), "pythonw.exe")      # no console window
    if os.path.isfile(windowless):
        exe = windowless
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.list2cmdline([exe, os.path.join(root, "main.py"), "--background"])
