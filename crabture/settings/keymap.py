"""
settings/keymap.py — configurable keyboard shortcuts for Crabture.

Single source of truth for every shortcut:

* ``kind == 'tool'``   – a single key with no modifiers that switches the
  active drawing tool while the canvas has focus (``S`` = Select, ``R`` =
  Rectangle, …).  ``''`` means unassigned.
* ``kind == 'action'`` – a menu/toolbar action shortcut, any valid
  ``QKeySequence`` text (``Ctrl+Shift+S``, a chord such as ``Ctrl+K, Ctrl+C``
  …).  It must contain Ctrl/Alt/Meta or be a function key.  ``''`` means
  unassigned.
* ``kind == 'fixed'``  – shown in the settings UI but not editable.

User choices are stored with QSettings('YawnByte', 'Crabture') under the
group ``shortcuts``.  Every mutation emits ``signals.changed(id)`` (``''``
when several ids changed at once) so menus and the canvas can refresh.
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence


# ---------------------------------------------------------------------------
#  Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShortcutDef:
    id: str
    label: str
    group: str
    default: str
    kind: str          # 'tool' | 'action' | 'fixed'


def _tool(tool: str, label: str, key: str) -> ShortcutDef:
    return ShortcutDef(f"tool.{tool}", label, "Tools", key, "tool")


def _act(name: str, label: str, group: str, seq: str = "", kind: str = "action") -> ShortcutDef:
    return ShortcutDef(f"action.{name}", label, group, seq, kind)


DEFS: List[ShortcutDef] = [
    # -- tools (single key, canvas focused) --------------------------------
    _tool("select", "Select", "S"),
    _tool("arrow", "Arrow", "A"),
    _tool("line", "Line", "L"),
    _tool("pen", "Freehand Pen", "D"),
    _tool("rectangle", "Rectangle", "R"),
    _tool("ellipse", "Ellipse", "E"),
    _tool("highlight", "Highlight", "H"),
    _tool("text", "Text", "T"),
    _tool("balloon", "Speech Balloon", "K"),
    _tool("step", "Numbered Step", "N"),
    _tool("sticker", "Sticker", "Y"),
    _tool("pixelate", "Pixelate", "P"),
    _tool("blur", "Blur", "B"),
    _tool("spotlight", "Spotlight", "O"),
    _tool("magnifier", "Magnifier", "M"),
    _tool("crop", "Crop", "C"),
    _tool("eyedropper", "Eyedropper", "I"),
    # -- File ---------------------------------------------------------------
    _act("open", "Open Image…", "File", "Ctrl+O"),
    _act("open_folder", "Open Folder…", "File", "Ctrl+Shift+O"),
    _act("close_image", "Close Image", "File", "Ctrl+W"),
    _act("save", "Save (Overwrite)", "File", "Ctrl+S"),
    _act("save_as", "Save As…", "File", "Ctrl+Shift+S"),
    _act("copy", "Copy to Clipboard", "File", "Ctrl+Shift+C"),
    _act("quit", "Quit", "File", "Ctrl+Q"),
    # -- Edit ---------------------------------------------------------------
    _act("undo", "Undo", "Edit", "Ctrl+Z"),
    _act("redo", "Redo", "Edit", "Ctrl+Y"),
    _act("delete", "Delete Selected", "Edit", "Delete", kind="fixed"),
    _act("effects", "Image Effects…", "Edit", "Ctrl+E"),
    _act("sensitive_run", "Run Sensitive-Data Scan", "Edit", "Ctrl+Shift+F"),
    _act("sensitive_configure", "Configure Sensitive-Data Scan…", "Edit", ""),
    _act("rename", "Power Rename…", "Edit", "Ctrl+R"),
    _act("undo_rename", "Undo Last Rename", "Edit", ""),
    _act("choose_sticker", "Choose Sticker…", "Edit", ""),
    _act("reset_steps", "Reset Step Counter", "Edit", ""),
    # -- Capture ------------------------------------------------------------
    _act("capture_region", "Capture Region", "Capture", ""),
    _act("capture_fullscreen", "Capture Fullscreen", "Capture", ""),
    _act("capture_window", "Capture Active Window", "Capture", ""),
    _act("capture_monitor", "Capture Monitor", "Capture", ""),
    _act("capture_scroll", "Scrolling Capture", "Capture", ""),
    # -- View ---------------------------------------------------------------
    _act("zoom_in", "Zoom In", "View", "Ctrl+="),
    _act("zoom_out", "Zoom Out", "View", "Ctrl+-"),
    _act("fit", "Fit to Window", "View", "Ctrl+0"),
    _act("actual_size", "Actual Size (100%)", "View", "Ctrl+1"),
    _act("toggle_panel", "Toggle Side Panel", "View", "F9"),
    # -- Help / Configure ---------------------------------------------------
    _act("shortcuts_help", "Keyboard Shortcuts", "Help", "F1"),
    _act("configure", "Configure…", "Configure", "Ctrl+,"),
]

_BY_ID: Dict[str, ShortcutDef] = {d.id: d for d in DEFS}

GROUPS: Tuple[str, ...] = ("Tools", "File", "Edit", "Capture", "View", "Help", "Configure")


# ---------------------------------------------------------------------------
#  Signals / storage
# ---------------------------------------------------------------------------

class _Signals(QObject):
    changed = pyqtSignal(str)       # shortcut id, or '' when many changed


signals = _Signals()

_UNASSIGNED = "<none>"              # stored marker for an explicitly empty shortcut
_tool_key_cache: Optional[Dict[int, str]] = None


def _settings() -> QSettings:
    """Factory for the settings store (tests may monkeypatch this)."""
    return QSettings("YawnByte", "Crabture")


def _key(id: str) -> str:
    return f"shortcuts/{id}"


def _def(id: str) -> ShortcutDef:
    try:
        return _BY_ID[id]
    except KeyError:
        raise KeyError(f"unknown shortcut id: {id!r}") from None


def _invalidate(*_args) -> None:
    global _tool_key_cache
    _tool_key_cache = None


signals.changed.connect(_invalidate)


# ---------------------------------------------------------------------------
#  Key helpers
# ---------------------------------------------------------------------------

_UNKNOWN_KEY = 0x01FFFFFF

_ALLOWED_TOOL_KEYS = (
    {int(getattr(Qt.Key, f"Key_{c}").value) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
    | {int(getattr(Qt.Key, f"Key_F{i}").value) for i in range(1, 13)}
    | {int(k.value) for k in (
        Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight, Qt.Key.Key_Comma, Qt.Key.Key_Period,
        Qt.Key.Key_Slash, Qt.Key.Key_Semicolon, Qt.Key.Key_Apostrophe, Qt.Key.Key_Minus,
        Qt.Key.Key_Equal, Qt.Key.Key_Backslash, Qt.Key.Key_QuoteLeft,
    )}
)
_BLOCKED_BARE_KEYS = {int(k.value) for k in (
    Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Delete,
    Qt.Key.Key_Backspace, Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Space,
)}
_FKEYS = {int(getattr(Qt.Key, f"Key_F{i}").value) for i in range(1, 36)}
_MOD_MASK = (
    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
)


def _combos(text: str) -> list:
    """The key combinations of *text* ([] if unparsable)."""
    seq = QKeySequence(text, QKeySequence.SequenceFormat.PortableText)
    return [seq[i] for i in range(seq.count())]


def _normalise(text: str) -> str:
    """Canonical portable text ('' for empty / unparsable)."""
    text = (text or "").strip()
    if not text:
        return ""
    return QKeySequence(text, QKeySequence.SequenceFormat.PortableText).toString(
        QKeySequence.SequenceFormat.PortableText
    )


def _combo_key(combo) -> int:
    return int(combo.key().value)


def _combo_mods(combo):
    return combo.keyboardModifiers()


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def get(id: str) -> str:
    """Current portable shortcut text for *id* ('' if unassigned)."""
    d = _def(id)
    if d.kind == "fixed":
        return d.default
    s = _settings()
    k = _key(id)
    if not s.contains(k):
        return d.default
    value = s.value(k, d.default)
    if value is None:
        return d.default
    value = str(value)
    if value == _UNASSIGNED:
        return ""
    return _normalise(value) if validate(id, value) is None else d.default


def validate(id: str, text: str) -> Optional[str]:
    """None if *text* is acceptable for *id*, otherwise a human-readable error."""
    try:
        d = _def(id)
    except KeyError:
        return f"Unknown shortcut '{id}'."
    if d.kind == "fixed":
        return "This shortcut is fixed and cannot be changed."
    text = (text or "").strip()
    if not text:
        return None                                   # unassigned is always fine
    combos = _combos(text)
    if not combos:
        return f"'{text}' is not a valid key sequence."
    if any(_combo_key(c) in (0, _UNKNOWN_KEY) for c in combos):
        return f"'{text}' is not a valid key sequence."

    if d.kind == "tool":
        if len(combos) != 1:
            return "A tool shortcut must be a single key (no chords)."
        c = combos[0]
        if _combo_mods(c) != Qt.KeyboardModifier.NoModifier:
            return "A tool shortcut must be a single key without Ctrl, Alt, Shift or Meta."
        key = _combo_key(c)
        if key in _BLOCKED_BARE_KEYS:
            return "That key is reserved (Esc, Enter, Delete, Backspace, Tab and Space can't be tool shortcuts)."
        if key not in _ALLOWED_TOOL_KEYS:
            return "Use a letter, digit, F1–F12 or one of [ ] , . / ; ' - = \\ ` as a tool shortcut."
        return None

    # action
    first = combos[0]
    mods = _combo_mods(first)
    key = _combo_key(first)
    has_mod = bool(mods & _MOD_MASK)
    if not has_mod and key in _BLOCKED_BARE_KEYS:
        return "That key is reserved and needs Ctrl, Alt or Meta with it."
    if not has_mod and key not in _FKEYS:
        return "An action shortcut needs Ctrl, Alt or Meta (or must be a function key)."
    return None


def _prefix_conflict(a: list, b: list) -> bool:
    n = min(len(a), len(b))
    return n > 0 and builtins.all(
        _combo_key(x) == _combo_key(y) and _combo_mods(x) == _combo_mods(y)
        for x, y in zip(a[:n], b[:n])
    )


def conflicts(id: str, text: str) -> List[ShortcutDef]:
    """Other definitions whose current shortcut collides with *text*.

    Tools and actions are checked against each other; chords also collide when
    one is a prefix of the other (Qt would never fire the shorter one).
    """
    mine = _combos((text or "").strip())
    if not mine:
        return []
    out: List[ShortcutDef] = []
    for d in DEFS:
        if d.id == id:
            continue
        other = _combos(get(d.id))
        if other and _prefix_conflict(mine, other):
            out.append(d)
    return out


def set(id: str, text: str, unassign_conflicts: bool = False) -> None:   # noqa: A001
    """Validate and persist the shortcut for *id*.

    Raises ValueError if *text* is invalid.  With *unassign_conflicts* any other
    definition using the same sequence is cleared first (the UI's "take it over").
    """
    err = validate(id, text)
    if err:
        raise ValueError(err)
    norm = _normalise(text)
    s = _settings()
    changed_many = False
    if unassign_conflicts and norm:
        for other in conflicts(id, norm):
            if other.kind != "fixed":
                s.setValue(_key(other.id), _UNASSIGNED)
                changed_many = True
    s.setValue(_key(id), norm if norm else _UNASSIGNED)
    s.sync()
    _invalidate()
    signals.changed.emit("" if changed_many else id)


def reset(id: str) -> None:
    d = _def(id)
    if d.kind == "fixed":
        return
    s = _settings()
    s.remove(_key(id))
    s.sync()
    _invalidate()
    signals.changed.emit(id)


def reset_all() -> None:
    s = _settings()
    s.remove("shortcuts")
    s.sync()
    _invalidate()
    signals.changed.emit("")


def all() -> List[Tuple[ShortcutDef, str]]:   # noqa: A001
    """Every definition paired with its current shortcut text."""
    return [(d, get(d.id)) for d in DEFS]


def is_default(id: str) -> bool:
    return _normalise(get(id)) == _normalise(_def(id).default)


# -- canvas helpers -----------------------------------------------------------

def _tool_key_map() -> Dict[int, str]:
    global _tool_key_cache
    if _tool_key_cache is None:
        m: Dict[int, str] = {}
        for d in DEFS:
            if d.kind != "tool":
                continue
            combos = _combos(get(d.id))
            if len(combos) == 1 and _combo_mods(combos[0]) == Qt.KeyboardModifier.NoModifier:
                m.setdefault(_combo_key(combos[0]), d.id.split(".", 1)[1])
        _tool_key_cache = m
    return _tool_key_cache


def tool_for_key(event_or_key, modifiers=None) -> Optional[str]:
    """Tool id bound to a key press, or None.

    *event_or_key* is a ``QKeyEvent`` or an int/``Qt.Key``; *modifiers* defaults
    to the event's.  Any real modifier (Ctrl/Alt/Shift/Meta) means "not a tool
    shortcut" (the keypad flag is ignored).
    """
    if hasattr(event_or_key, "key"):
        key = event_or_key.key()
        if modifiers is None and hasattr(event_or_key, "modifiers"):
            modifiers = event_or_key.modifiers()
    else:
        key = event_or_key
    key = int(getattr(key, "value", key))
    if modifiers is None:
        modifiers = Qt.KeyboardModifier.NoModifier
    real = modifiers & ~Qt.KeyboardModifier.KeypadModifier
    if real != Qt.KeyboardModifier.NoModifier:
        return None
    return _tool_key_map().get(key)


def key_text_for_tool(tool_id: str) -> str:
    """Current key text for a tool ('' if unassigned); accepts 'arrow' or 'tool.arrow'."""
    tid = tool_id if tool_id.startswith("tool.") else f"tool.{tool_id}"
    return get(tid) if tid in _BY_ID else ""


# -- import / export ------------------------------------------------------------

def export_json() -> str:
    data = {d.id: get(d.id) for d in DEFS if d.kind != "fixed"}
    return json.dumps({"version": 1, "shortcuts": data}, indent=2, ensure_ascii=False)


def import_json(text: str) -> List[str]:
    """Apply shortcuts from :func:`export_json` output.

    Unknown ids are ignored.  Every entry is validated; invalid ones are skipped
    and described in the returned list.  Collisions created by the import are
    applied but reported as warnings.
    """
    problems: List[str] = []
    try:
        data = json.loads(text)
        entries = data["shortcuts"] if isinstance(data, dict) and "shortcuts" in data else data
        if not isinstance(entries, dict):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return ["The file is not a valid Crabture shortcuts export."]

    s = _settings()
    applied: List[str] = []
    for id, value in entries.items():
        if id not in _BY_ID or _BY_ID[id].kind == "fixed":
            continue
        if not isinstance(value, str):
            problems.append(f"{id}: value must be text.")
            continue
        err = validate(id, value)
        if err:
            problems.append(f"{id}: {err}")
            continue
        norm = _normalise(value)
        s.setValue(_key(id), norm if norm else _UNASSIGNED)
        applied.append(id)
    s.sync()
    _invalidate()
    for id in applied:
        for other in conflicts(id, get(id)):
            msg = f"Warning: {_BY_ID[id].label} and {other.label} now share {get(id)}."
            rev = f"Warning: {other.label} and {_BY_ID[id].label} now share {get(id)}."
            if msg not in problems and rev not in problems:
                problems.append(msg)
    if applied:
        signals.changed.emit("")
    return problems
