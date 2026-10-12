"""
capture/hotkeys.py — Register Crabture capture shortcuts as GNOME custom keybindings.

Wayland offers no global hotkeys to applications, so the supported route is a
desktop-level shortcut that simply runs ``crabture --capture <mode>``.  On
GNOME these live in gsettings (org.gnome.settings-daemon.plugins.media-keys).

The functions are NON-DESTRUCTIVE:

  • existing custom shortcuts are preserved (we only append to the list);
  • an accelerator already used by another shortcut is skipped and reported;
  • every entry we create lives at ``…/custom-keybindings/crabture-<mode>/`` so
    ``unregister_gnome_shortcuts`` removes only ours.
"""

from __future__ import annotations

import ast
import os
import shlex
import shutil
import subprocess
from typing import Callable, Dict, List, Optional, Sequence, Tuple

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = MEDIA_KEYS + ".custom-keybinding"
LIST_KEY = "custom-keybindings"
BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
OUR_PREFIX = "crabture-"

# Other schemas whose accelerators we must not clash with.
CONFLICT_SCHEMAS = (MEDIA_KEYS, "org.gnome.shell.keybindings")

# mode -> (accelerator, shortcut name)
DEFAULT_BINDINGS: Dict[str, Tuple[str, str]] = {
    "fullscreen": ("<Ctrl>Print", "Crabture: capture full screen"),
    "region": ("<Ctrl><Shift>Print", "Crabture: capture region"),
    "window": ("<Ctrl><Alt>Print", "Crabture: capture window"),
}

Runner = Callable[[Sequence[str]], Tuple[int, str]]


def _default_runner(args: Sequence[str]) -> Tuple[int, str]:
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


# -- GVariant text helpers -------------------------------------------------

def _gv_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _gv_list(items: Sequence[str]) -> str:
    return "[" + ", ".join(_gv_str(i) for i in items) + "]" if items else "@as []"


def _parse_gv_list(text: str) -> List[str]:
    text = text.strip()
    if text.startswith("@as"):
        text = text[3:].strip()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def _parse_gv_str(text: str) -> str:
    text = text.strip()
    try:
        value = ast.literal_eval(text)
        return value if isinstance(value, str) else ""
    except (ValueError, SyntaxError):
        return ""


def _is_ours(path: str) -> bool:
    return os.path.basename(path.rstrip("/")).startswith(OUR_PREFIX)


def _entry_schema(path: str) -> str:
    return f"{CUSTOM_SCHEMA}:{path}"


def _is_gnome(env=None) -> bool:
    env = os.environ if env is None else env
    return "gnome" in (env.get("XDG_CURRENT_DESKTOP") or "").lower()


def default_launcher_command(launcher: Optional[str] = None) -> str:
    """Command prefix used in the shortcut (e.g. ``/home/u/.local/bin/crabture``)."""
    if launcher:
        return shlex.quote(launcher) if os.path.exists(launcher) else launcher
    found = shutil.which("crabture")
    if found:
        return shlex.quote(found)
    local = os.path.expanduser("~/.local/bin/crabture")
    if os.path.exists(local):
        return shlex.quote(local)
    # this file is <root>/crabture/capture/hotkeys.py and main.py sits in <root>
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main_py = os.path.join(root, "main.py")
    return f"python3 {shlex.quote(main_py)}"


def _read_list(run: Runner) -> Optional[List[str]]:
    rc, out = run(["gsettings", "get", MEDIA_KEYS, LIST_KEY])
    return _parse_gv_list(out) if rc == 0 else None


def _used_accelerators(run: Runner, entries: Sequence[str]) -> Dict[str, str]:
    """accelerator (lower-case) -> description of what already uses it.

    Entries owned by Crabture are excluded (re-registering is fine).
    """
    used: Dict[str, str] = {}
    for path in entries:
        if _is_ours(path):
            continue
        rc, out = run(["gsettings", "get", _entry_schema(path), "binding"])
        if rc == 0:
            accel = _parse_gv_str(out)
            if accel:
                rc2, name = run(["gsettings", "get", _entry_schema(path), "name"])
                used[accel.lower()] = f"custom shortcut {_parse_gv_str(name) or path}"
    for schema in CONFLICT_SCHEMAS:
        rc, out = run(["gsettings", "list-recursively", schema])
        if rc != 0:
            continue
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            for accel in _quoted_strings(parts[2]):
                used.setdefault(accel.lower(), f"{parts[0]} {parts[1]}")
    return used


def _quoted_strings(value: str) -> List[str]:
    try:
        v = ast.literal_eval(value.strip().removeprefix("@as").strip())
    except (ValueError, SyntaxError):
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str)]
    return []


def register_gnome_shortcuts(
    launcher: Optional[str] = None,
    runner: Optional[Runner] = None,
    env=None,
) -> List[str]:
    """Add Crabture's capture shortcuts to GNOME; return human-readable lines."""
    run = runner or _default_runner
    lines: List[str] = []
    if runner is None and not shutil.which("gsettings"):
        return ["gsettings not found — cannot register shortcuts automatically."]
    if not _is_gnome(env):
        return ["GNOME desktop not detected (XDG_CURRENT_DESKTOP); bind these commands "
                "in your desktop's keyboard settings instead:"] + [
            f"  {accel:<20} {default_launcher_command(launcher)} --capture {mode}"
            for mode, (accel, _n) in DEFAULT_BINDINGS.items()
        ]

    entries = _read_list(run)
    if entries is None:
        return ["Could not read the GNOME custom keybinding list; nothing was changed."]
    used = _used_accelerators(run, entries)
    cmd_base = default_launcher_command(launcher)

    new_entries = list(entries)
    for mode, (accel, name) in DEFAULT_BINDINGS.items():
        path = f"{BASE_PATH}{OUR_PREFIX}{mode}/"
        conflict = used.get(accel.lower())
        if conflict:
            lines.append(f"Skipped {accel} ({mode}): already used by {conflict}.")
            continue
        schema = _entry_schema(path)
        ok = all(
            run(["gsettings", "set", schema, key, val])[0] == 0
            for key, val in (
                ("name", _gv_str(name)),
                ("command", _gv_str(f"{cmd_base} --capture {mode}")),
                ("binding", _gv_str(accel)),
            )
        )
        if not ok:
            lines.append(f"Failed to write shortcut for {mode}.")
            continue
        verb = "Updated" if path in new_entries else "Registered"
        if path not in new_entries:
            new_entries.append(path)
        lines.append(f"{verb} {accel} → crabture --capture {mode}")

    if new_entries != entries:
        rc, _ = run(["gsettings", "set", MEDIA_KEYS, LIST_KEY, _gv_list(new_entries)])
        if rc != 0:
            lines.append("Failed to update the custom keybinding list.")
    return lines


def unregister_gnome_shortcuts(runner: Optional[Runner] = None, env=None) -> List[str]:
    """Remove only the shortcuts created by :func:`register_gnome_shortcuts`."""
    run = runner or _default_runner
    if runner is None and not shutil.which("gsettings"):
        return ["gsettings not found — nothing to remove."]
    if not _is_gnome(env):
        return ["GNOME desktop not detected; nothing to remove."]
    entries = _read_list(run)
    if entries is None:
        return ["Could not read the GNOME custom keybinding list; nothing was changed."]
    ours = [p for p in entries if _is_ours(p)]
    if not ours:
        return ["No Crabture shortcuts were registered."]
    lines: List[str] = []
    for path in ours:
        run(["gsettings", "reset-recursively", _entry_schema(path)])
        lines.append(f"Removed shortcut {os.path.basename(path.rstrip('/'))}")
    rest = [p for p in entries if not _is_ours(p)]
    run(["gsettings", "set", MEDIA_KEYS, LIST_KEY, _gv_list(rest)])
    return lines
