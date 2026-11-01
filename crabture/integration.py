"""
integration.py — the right-click menu entries for the file manager (Linux).

Nautilus ("Files") has no system-wide place for script entries: they live in
``~/.local/share/nautilus/scripts`` and only the user can own them.  So

* ``scripts/builder.sh --install`` copies them there for a source checkout, and
* the ``.deb`` ships them in ``/usr/share/crabture/nautilus-scripts`` and Crabture copies them
  into the user's folder the first time it starts (``ensure_first_run``), or whenever asked with
  ``crabture --install-integration`` / ``--remove-integration``.

The scripts delete themselves if Crabture has been uninstalled, so ``apt remove`` leaves no dead entries.
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional, Tuple

from PyQt6.QtCore import QSettings

from crabture import osinfo

SCRIPT_NAMES = ("Edit with Crabture", "Open Directory with Crabture")
SYSTEM_SCRIPTS = "/usr/share/crabture/nautilus-scripts"
_DONE_KEY = "integration/nautilus_done"


def nautilus_scripts_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "nautilus", "scripts")


def scripts_source() -> Optional[str]:
    """Folder holding the script templates: the installed package's, else the source tree's."""
    tree = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging", "nautilus")
    for path in (SYSTEM_SCRIPTS, tree):
        if all(os.path.isfile(os.path.join(path, n)) for n in SCRIPT_NAMES):
            return path
    return None


def nautilus_available() -> bool:
    return bool(shutil.which("nautilus")) or os.path.isdir(os.path.dirname(nautilus_scripts_dir()))


def install_nautilus_scripts(overwrite: bool = True) -> Tuple[List[str], str]:
    """Copy the scripts to the user's Nautilus folder.  Returns ``(installed paths, message)``."""
    if osinfo.IS_WINDOWS:
        return [], "The right-click scripts are for Linux file managers only."
    source = scripts_source()
    if source is None:
        return [], "Could not find the Crabture context-menu scripts."
    target = nautilus_scripts_dir()
    done: List[str] = []
    try:
        os.makedirs(target, exist_ok=True)
        for name in SCRIPT_NAMES:
            dest = os.path.join(target, name)
            if os.path.exists(dest) and not overwrite:
                continue
            shutil.copyfile(os.path.join(source, name), dest)
            os.chmod(dest, 0o755)
            done.append(dest)
    except OSError as e:
        return done, f"Could not install the scripts into {target}: {e.strerror or e}"
    return done, f"Installed {len(done)} right-click script(s) in {target} (Files ▸ right-click ▸ Scripts)."


def remove_nautilus_scripts() -> Tuple[List[str], str]:
    target = nautilus_scripts_dir()
    removed: List[str] = []
    for name in SCRIPT_NAMES:
        path = os.path.join(target, name)
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed, f"Removed {len(removed)} right-click script(s) from {target}."


def ensure_first_run() -> None:
    """Packaged install only: offer the right-click entries once, without ever overwriting.

    Never raises: a missing file manager or read-only home must not stop Crabture starting.
    """
    try:
        if osinfo.IS_WINDOWS or osinfo.IS_MAC or scripts_source() != SYSTEM_SCRIPTS:
            return
        qs = QSettings("YawnByte", "Crabture")
        if str(qs.value(_DONE_KEY, "false")).lower() == "true":
            return
        if nautilus_available():
            install_nautilus_scripts(overwrite=False)
        qs.setValue(_DONE_KEY, "true")
        qs.sync()
    except Exception:  # noqa: BLE001
        pass
