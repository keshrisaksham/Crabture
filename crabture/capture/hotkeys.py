"""
capture/hotkeys.py — hand Crabture's capture shortcuts to the desktop.

Wayland offers applications no global hotkeys, and X11 grabs are fragile, so the
supported route is a desktop-level shortcut that runs ``crabture --capture <mode>``.
If Crabture is already running that command is forwarded to the running instance
(see ``crabture.ipc``) and is instant; otherwise a small background process takes
the shot.

Supported desktops: GNOME (gsettings custom keybindings), Xfce (xfconf) and Windows.
Anywhere else :func:`apply_shortcuts` returns the exact commands to bind by hand.

Windows has no per-user shortcut database to write to, but it lets a program register global
hot-keys itself (``RegisterHotKey``).  So "applying" there means: start Crabture's small
background instance (tray icon, see ``winhotkeys.py``) at every login.  It reads the shortcuts
from Configure ▸ Capture, so edits take effect at once and no per-shortcut state is stored.

The functions are NON-DESTRUCTIVE:

  • other shortcuts are never touched;
  • an accelerator already used by something else is skipped and reported;
  • everything we create is recognisable (GNOME: ``…/custom-keybindings/crabture-<id>/``,
    Xfce: a command containing ``--capture <mode>``) so :func:`remove_shortcuts`
    removes only ours.

The pure helpers (accelerator conversion, command building) need no display.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtGui import QKeySequence

from crabture import osinfo
from crabture.capture.config import CaptureShortcut, MODE_LABELS

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = MEDIA_KEYS + ".custom-keybinding"
LIST_KEY = "custom-keybindings"
BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
OUR_PREFIX = "crabture-"

XFCE_CHANNEL = "xfce4-keyboard-shortcuts"
XFCE_PREFIX = "/commands/custom/"

# Other schemas whose accelerators we must not clash with.
CONFLICT_SCHEMAS = (MEDIA_KEYS, "org.gnome.shell.keybindings", "org.gnome.desktop.wm.keybindings",
                    "org.gnome.mutter.keybindings", "org.gnome.mutter.wayland.keybindings")

Runner = Callable[[Sequence[str]], Tuple[int, str]]


# =========================================================================
#  Accelerator conversion (pure)
# =========================================================================

_MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "primary": "ctrl", "ctl": "ctrl",
    "alt": "alt", "mod1": "alt",
    "shift": "shift",
    "meta": "super", "super": "super", "mod4": "super", "win": "super", "windows": "super",
}
_MOD_ORDER = ("ctrl", "alt", "shift", "super")

# Qt portable key name -> X keysym name (single letters / digits are handled separately).
_QT_KEY_TO_KEYSYM = {
    "print": "Print", "sysreq": "Sys_Req", "pause": "Pause", "scrolllock": "Scroll_Lock",
    "esc": "Escape", "escape": "Escape", "del": "Delete", "delete": "Delete", "ins": "Insert",
    "insert": "Insert", "pgup": "Page_Up", "pgdown": "Page_Down", "home": "Home", "end": "End",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down", "backspace": "BackSpace",
    "tab": "Tab", "return": "Return", "enter": "KP_Enter", "space": "space", "menu": "Menu",
    "-": "minus", "=": "equal", ",": "comma", ".": "period", "/": "slash", ";": "semicolon",
    "'": "apostrophe", "[": "bracketleft", "]": "bracketright", "\\": "backslash", "`": "grave",
    "+": "plus",
}
_BARE_OK = {"Print", "Pause", "Scroll_Lock", "Sys_Req"}      # allowed without a modifier (besides F-keys)
_FKEY = re.compile(r"^F([1-9]|[12][0-9]|3[0-5])$")


def split_accelerator(text: str) -> Tuple[List[str], str]:
    """``'Ctrl+Shift+Print'`` -> ``(['ctrl', 'shift'], 'Print')``.

    Also understands GTK/X style (``'<Control><Shift>Print'``).  Returns ``([], '')``
    when *text* is empty.  Modifier names are canonical (ctrl/alt/shift/super).
    """
    text = (text or "").strip()
    if not text:
        return [], ""
    mods: List[str] = []
    if "<" in text:
        for m in re.finditer(r"<([^>]+)>", text):
            canon = _MOD_ALIASES.get(m.group(1).strip().lower())
            if canon and canon not in mods:
                mods.append(canon)
        key = text.rsplit(">", 1)[-1].strip()
        return mods, key
    rest = text
    while "+" in rest:
        head, tail = rest.split("+", 1)
        canon = _MOD_ALIASES.get(head.strip().lower())
        if canon is None:
            break
        if canon not in mods:
            mods.append(canon)
        rest = tail
    return mods, rest.strip()


def _keysym(key: str) -> Optional[str]:
    """Key name (Qt or X) -> X keysym name, or None if we cannot express it."""
    if not key:
        return None
    if len(key) == 1 and key.isalpha():
        return key.lower()
    if len(key) == 1 and key.isdigit():
        return key
    if _FKEY.match(key.upper()):
        return key.upper()
    mapped = _QT_KEY_TO_KEYSYM.get(key.lower())
    if mapped:
        return mapped
    # Already an X keysym such as "Page_Up" / "KP_Enter" / "minus"
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
        return key
    return None


def canon_accel(text: str) -> str:
    """Canonical comparison form of an accelerator in either notation (``''`` if unusable)."""
    mods, key = split_accelerator(text)
    sym = _keysym(key)
    if sym is None:
        return ""
    ordered = [m for m in _MOD_ORDER if m in mods]
    return "+".join(ordered + [sym.lower()])


_QT_MOD_NAMES = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Meta"}


def _as_qt(text: str) -> str:
    """Rewrite *text* with Qt's modifier names (Super/Win -> Meta, <Control> -> Ctrl, …)."""
    mods, key = split_accelerator(text)
    return "+".join([_QT_MOD_NAMES[m] for m in _MOD_ORDER if m in mods] + [key]) if key else ""


def validate_accelerator(text: str) -> Optional[str]:
    """None if *text* can be a desktop capture shortcut, else a human-readable reason."""
    text = (text or "").strip()
    if not text:
        return None
    text = _as_qt(text) or text
    seq = QKeySequence(text, QKeySequence.SequenceFormat.PortableText)
    if seq.count() == 0 or int(seq[0].key().value) in (0, 0x01FFFFFF):
        return f"“{text}” is not a valid key combination."
    if seq.count() > 1:
        return "Use a single key combination (no chords like Ctrl+K, Ctrl+C)."
    portable = seq.toString(QKeySequence.SequenceFormat.PortableText)
    mods, key = split_accelerator(portable)
    sym = _keysym(key)
    if sym is None:
        return f"The key “{key}” cannot be used as a desktop shortcut."
    if not mods and sym not in _BARE_OK and not _FKEY.match(sym):
        return "Add Ctrl, Alt, Shift or Super — a bare key would be stolen from every other application."
    if key.lower() in ("shift", "ctrl", "control", "alt", "meta", "super"):
        return "A shortcut needs a real key besides the modifiers."
    return None


def normalise_accelerator(text: str) -> str:
    """Portable Qt text in canonical spelling ('' for empty/invalid)."""
    text = (text or "").strip()
    if not text or validate_accelerator(text):
        return ""
    return QKeySequence(_as_qt(text), QKeySequence.SequenceFormat.PortableText).toString(
        QKeySequence.SequenceFormat.PortableText)


def to_gtk(text: str) -> Optional[str]:
    """``'Ctrl+Shift+Print'`` -> ``'<Control><Shift>Print'`` (GNOME), or None."""
    if validate_accelerator(text) or not text.strip():
        return None
    mods, key = split_accelerator(normalise_accelerator(text))
    sym = _keysym(key)
    if sym is None:
        return None
    names = {"ctrl": "<Control>", "alt": "<Alt>", "shift": "<Shift>", "super": "<Super>"}
    return "".join(names[m] for m in _MOD_ORDER if m in mods) + sym


# Qt portable key name -> Windows virtual-key code (letters, digits and F-keys are computed).
_VK = {
    "print": 0x2C, "pause": 0x13, "scrolllock": 0x91, "esc": 0x1B, "escape": 0x1B, "del": 0x2E,
    "delete": 0x2E, "ins": 0x2D, "insert": 0x2D, "pgup": 0x21, "pgdown": 0x22, "home": 0x24, "end": 0x23,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "backspace": 0x08, "tab": 0x09,
    "return": 0x0D, "enter": 0x0D, "space": 0x20, "menu": 0x5D,
    "-": 0xBD, "=": 0xBB, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB,
    "]": 0xDD, "\\": 0xDC, "`": 0xC0,
}
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
_WIN_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "shift": MOD_SHIFT, "super": MOD_WIN}


def to_win32(text: str) -> Optional[Tuple[int, int]]:
    """``'Ctrl+Shift+Print'`` -> ``(modifiers, virtual-key)`` for ``RegisterHotKey``, or None."""
    if validate_accelerator(text) or not text.strip():
        return None
    mods, key = split_accelerator(normalise_accelerator(text))
    low = key.lower()
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", low):
        vk = 0x70 + int(low[1:]) - 1
    else:
        vk = _VK.get(low)
    if vk is None:
        return None
    return sum(_WIN_MODS[m] for m in mods), vk


def to_xfce(text: str) -> Optional[str]:
    """``'Ctrl+Shift+Print'`` -> ``'<Primary><Shift>Print'`` (Xfce), or None."""
    gtk = to_gtk(text)
    return gtk.replace("<Control>", "<Primary>") if gtk else None


# =========================================================================
#  Commands
# =========================================================================

def _q(text: str) -> str:
    """Quote one command-line token for the shell of this platform."""
    return subprocess.list2cmdline([text]) if osinfo.IS_WINDOWS else shlex.quote(text)


def launcher_command(launcher: Optional[str] = None) -> str:
    """The command prefix a shortcut runs, e.g. ``/usr/bin/crabture``."""
    if launcher:
        return _q(launcher) if os.path.exists(launcher) else launcher
    if getattr(sys, "frozen", False):                         # PyInstaller build
        return _q(sys.executable)
    found = shutil.which("crabture")
    if found:
        return _q(found)
    local = os.path.expanduser("~/.local/bin/crabture")
    if os.path.exists(local):
        return _q(local)
    # this file is <root>/crabture/capture/hotkeys.py and main.py sits in <root>
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return f"{_q(sys.executable or 'python3')} {_q(os.path.join(root, 'main.py'))}"


def command_for(sc: CaptureShortcut, launcher: Optional[str] = None) -> str:
    """The full command line one shortcut runs."""
    parts = [launcher_command(launcher), "--capture", sc.mode]
    if sc.delay is not None:
        d = float(sc.delay)
        parts += ["--delay", str(int(d)) if d == int(d) else f"{d:g}"]
    if sc.folder:
        parts += ["--folder", _q(os.path.expanduser(sc.folder))]
    if sc.after in ("editor", "save"):
        parts += ["--after", sc.after]
    return " ".join(parts)


def shortcut_title(sc: CaptureShortcut) -> str:
    return f"Crabture: {MODE_LABELS.get(sc.mode, sc.mode)}"


# =========================================================================
#  Desktop detection / process helper
# =========================================================================

def desktop_kind(env=None) -> str:
    """'gnome', 'xfce', 'windows' or 'other'."""
    if env is None and osinfo.IS_WINDOWS:
        return "windows"
    env = os.environ if env is None else env
    names = [n.strip() for n in (env.get("XDG_CURRENT_DESKTOP") or "").lower().split(":")]
    if "gnome" in names:                    # also "ubuntu:GNOME", "pop:GNOME"
        return "gnome"
    if "xfce" in names:
        return "xfce"
    return "other"


def _is_gnome(env=None) -> bool:      # kept for callers of the old API
    return desktop_kind(env) == "gnome"


def desktop_name(env=None) -> str:
    kind = desktop_kind(env)
    return {"gnome": "GNOME", "xfce": "Xfce", "windows": "Windows"}.get(kind, (os.environ if env is None else env)
                                                    .get("XDG_CURRENT_DESKTOP") or "your desktop")


def supported(env=None, which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    kind = desktop_kind(env)
    return (kind == "windows" or (kind == "gnome" and bool(which("gsettings")))
            or (kind == "xfce" and bool(which("xfconf-query"))))


AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "Crabture"


def autostart_enabled() -> bool:
    """Windows: is the background instance registered to start at login?"""
    try:
        import winreg                                    # type: ignore[import-not-found]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
        return True
    except (ImportError, OSError):
        return False


def set_autostart(on: bool) -> bool:
    """Windows: add/remove the login entry that starts ``crabture --background``.  True on success."""
    try:
        import winreg                                    # type: ignore[import-not-found]
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, osinfo.autostart_command() or "")
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except (ImportError, OSError):
        return False


def _default_runner(args: Sequence[str]) -> Tuple[int, str]:
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


@dataclass
class Report:
    """Outcome of :func:`apply_shortcuts` / :func:`remove_shortcuts`."""
    lines: List[str] = field(default_factory=list)
    status: Dict[str, str] = field(default_factory=dict)      # shortcut id -> 'ok' | message
    applied: int = 0
    skipped: int = 0
    ok: bool = True
    supported: bool = True

    def text(self) -> str:
        return "\n".join(self.lines)


# =========================================================================
#  GVariant text helpers (GNOME)
# =========================================================================

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


def _is_ours(path: str) -> bool:
    return os.path.basename(path.rstrip("/")).startswith(OUR_PREFIX)


def _entry_path(sc_id: str) -> str:
    return f"{BASE_PATH}{OUR_PREFIX}{sc_id}/"


def _entry_schema(path: str) -> str:
    return f"{CUSTOM_SCHEMA}:{path}"


def _gnome_list(run: Runner) -> Optional[List[str]]:
    rc, out = run(["gsettings", "get", MEDIA_KEYS, LIST_KEY])
    return _parse_gv_list(out) if rc == 0 else None


def _gnome_used(run: Runner, entries: Sequence[str]) -> Dict[str, str]:
    """canonical accelerator -> description of what already uses it (ours excluded)."""
    used: Dict[str, str] = {}
    for path in entries:
        if _is_ours(path):
            continue
        rc, out = run(["gsettings", "get", _entry_schema(path), "binding"])
        accel = _parse_gv_str(out) if rc == 0 else ""
        canon = canon_accel(accel)
        if canon:
            _rc, name = run(["gsettings", "get", _entry_schema(path), "name"])
            used.setdefault(canon, f"the custom shortcut “{_parse_gv_str(name) or path}”")
    for schema in CONFLICT_SCHEMAS:
        rc, out = run(["gsettings", "list-recursively", schema])
        if rc != 0:
            continue
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            for accel in _quoted_strings(parts[2]):
                canon = canon_accel(accel)
                if canon:
                    used.setdefault(canon, f"{parts[1]} ({schema.rsplit('.', 1)[-1]})")
    return used


# =========================================================================
#  Public API
# =========================================================================

def _assign(shortcuts: Sequence[CaptureShortcut], report: Report) -> List[Tuple[CaptureShortcut, str, str]]:
    """Drop shortcuts without a usable key / with a duplicate key.

    Returns ``(shortcut, canonical, portable)`` for those worth registering."""
    out: List[Tuple[CaptureShortcut, str, str]] = []
    seen: Dict[str, CaptureShortcut] = {}
    for sc in shortcuts:
        if not sc.key.strip():
            continue                                    # unassigned: nothing to register
        err = validate_accelerator(sc.key)
        if err:
            report.status[sc.id] = err
            report.lines.append(f"Skipped {sc.key} ({sc.describe()}): {err}")
            report.skipped += 1
            continue
        canon = canon_accel(sc.key)
        if canon in seen:
            msg = f"already used by your “{seen[canon].describe()}” capture shortcut"
            report.status[sc.id] = msg
            report.lines.append(f"Skipped {sc.key} ({sc.describe()}): {msg}.")
            report.skipped += 1
            continue
        seen[canon] = sc
        out.append((sc, canon, normalise_accelerator(sc.key)))
    return out


def apply_shortcuts(
    shortcuts: Sequence[CaptureShortcut],
    launcher: Optional[str] = None,
    runner: Optional[Runner] = None,
    env=None,
) -> Report:
    """Make the desktop's capture shortcuts match *shortcuts* exactly.

    Entries Crabture created earlier but that are no longer wanted are removed;
    nothing else is changed.
    """
    report = Report()
    kind = desktop_kind(env)
    run = runner or _default_runner
    if kind == "windows":
        return _apply_windows(shortcuts, report)
    if kind == "gnome":
        if runner is None and not shutil.which("gsettings"):
            report.ok = report.supported = False
            report.lines.append("gsettings was not found — cannot register shortcuts automatically.")
            return report
        return _apply_gnome(shortcuts, launcher, run, report)
    if kind == "xfce":
        if runner is None and not shutil.which("xfconf-query"):
            report.ok = report.supported = False
            report.lines.append("xfconf-query was not found — cannot register shortcuts automatically.")
            return report
        return _apply_xfce(shortcuts, launcher, run, report)
    report.ok = report.supported = False
    report.lines.append("Automatic registration is not available on this desktop. "
                        "Add these commands in your keyboard settings:")
    for sc in shortcuts:
        if sc.key.strip():
            report.lines.append(f"  {sc.key:<20} {command_for(sc, launcher)}")
    return report


def _apply_windows(shortcuts, report: Report) -> Report:
    from crabture.capture import config
    keys: List[str] = []
    for sc, _canon, portable in _assign(shortcuts, report):
        if to_win32(portable) is None:
            report.status[sc.id] = "that key cannot be registered"
            report.lines.append(f"Skipped {portable} ({sc.describe()}): that key cannot be registered.")
            report.skipped += 1
            continue
        keys.append(portable)
        report.status[sc.id] = "ok"
        report.applied += 1
        report.lines.append(f"Registered {portable} → {sc.describe()}")
    if not set_autostart(bool(keys)):
        report.ok = False
        report.lines.append("Could not change the Windows start-up entry.")
        return report
    config.set_applied_keys(keys)
    if keys:
        report.lines.append("Crabture now keeps running in the notification area so these keys work "
                            "everywhere, and starts that way when you sign in. A key another program "
                            "already owns is reported by Crabture when it starts.")
    return report


def remove_shortcuts(runner: Optional[Runner] = None, env=None) -> Report:
    """Remove only the shortcuts Crabture created."""
    if desktop_kind(env) == "other":
        return Report(lines=["Nothing to remove: Crabture only manages shortcuts on GNOME and Xfce."],
                      ok=True, supported=False)
    report = apply_shortcuts([], runner=runner, env=env)
    if report.supported and not report.lines:
        report.lines.append("No Crabture shortcuts were registered.")
    return report


def _apply_gnome(shortcuts, launcher, run: Runner, report: Report) -> Report:
    entries = _gnome_list(run)
    if entries is None:
        report.ok = False
        report.lines.append("Could not read the GNOME custom keybinding list; nothing was changed.")
        return report
    wanted = _assign(shortcuts, report)
    used = _gnome_used(run, entries)

    keep = [p for p in entries if not _is_ours(p)]
    ours_before = [p for p in entries if _is_ours(p)]
    new_ours: List[str] = []
    for sc, canon, portable in wanted:
        gtk = to_gtk(portable)
        conflict = used.get(canon)
        if conflict or gtk is None:
            msg = f"already used by {conflict}" if conflict else "that key cannot be registered"
            report.status[sc.id] = msg
            report.lines.append(f"Skipped {portable} ({sc.describe()}): {msg}.")
            report.skipped += 1
            continue
        path = _entry_path(sc.id)
        schema = _entry_schema(path)
        ok = all(
            run(["gsettings", "set", schema, key, val])[0] == 0
            for key, val in (("name", _gv_str(shortcut_title(sc))),
                             ("command", _gv_str(command_for(sc, launcher))),
                             ("binding", _gv_str(gtk)))
        )
        if not ok:
            report.status[sc.id] = "could not be written to GNOME settings"
            report.lines.append(f"Failed to write the shortcut for {sc.describe()}.")
            report.skipped += 1
            report.ok = False
            continue
        new_ours.append(path)
        report.status[sc.id] = "ok"
        report.applied += 1
        report.lines.append(f"{'Updated' if path in ours_before else 'Registered'} {portable} → {sc.describe()}")

    for path in ours_before:
        if path not in new_ours:
            run(["gsettings", "reset-recursively", _entry_schema(path)])
            if path.split("/")[-2][len(OUR_PREFIX):] not in {sc.id for sc, _c, _p in wanted}:
                report.lines.append(f"Removed shortcut {os.path.basename(path.rstrip('/'))}")

    final = keep + new_ours
    if final != entries:
        rc, _ = run(["gsettings", "set", MEDIA_KEYS, LIST_KEY, _gv_list(final)])
        if rc != 0:
            report.ok = False
            report.lines.append("Failed to update the custom keybinding list.")
    return report


def registered_accels(runner: Optional[Runner] = None, env=None) -> set:
    """Canonical accelerators Crabture currently has registered with the desktop."""
    run = runner or _default_runner
    kind = desktop_kind(env)
    out: set = set()
    if kind == "windows":
        from crabture.capture import config
        return {c for c in (canon_accel(k) for k in config.applied_keys()) if c}
    if kind == "gnome":
        if runner is None and not shutil.which("gsettings"):
            return out
        for path in _gnome_list(run) or []:
            if _is_ours(path):
                rc, val = run(["gsettings", "get", _entry_schema(path), "binding"])
                canon = canon_accel(_parse_gv_str(val)) if rc == 0 else ""
                if canon:
                    out.add(canon)
    elif kind == "xfce":
        if runner is None and not shutil.which("xfconf-query"):
            return out
        for accel, cmd in _xfce_entries(run).items():
            if _xfce_is_ours(cmd) and canon_accel(accel):
                out.add(canon_accel(accel))
    return out


# -- Xfce ------------------------------------------------------------------

_XFCE_OURS = re.compile(r"(crabture|main\.py).*--capture\s+(region|fullscreen|window|monitor|scroll)\b")


def _xfce_is_ours(command: str) -> bool:
    return bool(_XFCE_OURS.search(command))


def _xfce_entries(run: Runner) -> Dict[str, str]:
    """accelerator (as stored) -> command."""
    rc, out = run(["xfconf-query", "-c", XFCE_CHANNEL, "-l", "-v"])
    entries: Dict[str, str] = {}
    if rc != 0:
        return entries
    for line in out.splitlines():
        line = line.rstrip()
        if not line.startswith(XFCE_PREFIX):
            continue
        prop, _sp, cmd = line[len(XFCE_PREFIX):].partition(" ")
        cmd = cmd.strip()
        if prop and prop != "override" and cmd:
            entries[prop] = cmd
    return entries


def _apply_xfce(shortcuts, launcher, run: Runner, report: Report) -> Report:
    existing = _xfce_entries(run)
    wanted = _assign(shortcuts, report)
    used: Dict[str, str] = {}
    for accel, cmd in existing.items():
        if not _xfce_is_ours(cmd):
            canon = canon_accel(accel)
            if canon:
                used.setdefault(canon, f"“{cmd}”")

    new_props: List[str] = []
    for sc, canon, portable in wanted:
        accel = to_xfce(portable)
        conflict = used.get(canon)
        if conflict or accel is None:
            msg = f"already used by {conflict}" if conflict else "that key cannot be registered"
            report.status[sc.id] = msg
            report.lines.append(f"Skipped {portable} ({sc.describe()}): {msg}.")
            report.skipped += 1
            continue
        rc, _ = run(["xfconf-query", "-c", XFCE_CHANNEL, "-p", XFCE_PREFIX + accel, "-n",
                     "-t", "string", "-s", command_for(sc, launcher)])
        if rc != 0:
            report.status[sc.id] = "could not be written to Xfce settings"
            report.lines.append(f"Failed to write the shortcut for {sc.describe()}.")
            report.skipped += 1
            report.ok = False
            continue
        new_props.append(accel)
        report.status[sc.id] = "ok"
        report.applied += 1
        report.lines.append(f"{'Updated' if accel in existing else 'Registered'} {portable} → {sc.describe()}")

    for accel, cmd in existing.items():
        if _xfce_is_ours(cmd) and accel not in new_props:
            run(["xfconf-query", "-c", XFCE_CHANNEL, "-p", XFCE_PREFIX + accel, "-r"])
            report.lines.append(f"Removed shortcut {accel}")
    if new_props:
        run(["xfconf-query", "-c", XFCE_CHANNEL, "-p", XFCE_PREFIX + "override", "-n", "-t", "bool", "-s", "true"])
    return report


# -- compatibility with the previous API (used by older callers) -------------

def register_gnome_shortcuts(launcher: Optional[str] = None, runner: Optional[Runner] = None,
                             env=None) -> List[str]:
    from crabture.capture import config
    return apply_shortcuts(config.shortcuts(), launcher, runner, env).lines


def unregister_gnome_shortcuts(runner: Optional[Runner] = None, env=None) -> List[str]:
    return remove_shortcuts(runner, env).lines
