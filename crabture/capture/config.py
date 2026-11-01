"""
capture/config.py — capture preferences and capture shortcuts.

Everything the capture feature remembers lives here, stored with
QSettings('YawnByte', 'Crabture') under the group ``capture``:

    capture/folder          where screenshots are saved
    capture/pattern         file-name pattern (strftime codes + ``{mode}``)
    capture/format          png | jpg | webp
    capture/quality         JPEG / WebP quality (1–100)
    capture/after           save | editor   (just save it — the default — or open it in the editor)
    capture/engine          crabture | system  (Crabture's own selector (default), or the machine's own screenshot tool)
    capture/delay           seconds to wait before grabbing
    capture/hide            hide the Crabture window while grabbing
    capture/clipboard       also copy every capture to the clipboard
    capture/notify          show a desktop notification when a capture is saved
    capture/confirm         button | release   (region: wait for ✔ / Enter, or grab on mouse release)
    capture/shortcuts       JSON list of capture shortcuts (see :class:`CaptureShortcut`)
    capture/applied         True once the shortcuts were handed to the desktop
    capture/applied_keys    JSON list of the accelerators handed over (Windows bookkeeping)

A *capture shortcut* is one key combination that takes one kind of screenshot,
optionally with its own delay, its own folder and its own "after capture"
behaviour.  There can be any number of them.

No Qt widgets are used here, so the module is safe to import in headless runs.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QSettings, pyqtSignal

CAPTURE_MODES = ("region", "fullscreen", "window", "monitor", "scroll")

MODE_LABELS: Dict[str, str] = {
    "region": "Region",
    "fullscreen": "Full screen",
    "window": "Active window",
    "monitor": "Monitor",
    "scroll": "Scrolling capture",
}

DEFAULT_FOLDER = "~/Downloads/Crabshots"
DEFAULT_PATTERN = "Crabture_%Y-%m-%d_%H-%M-%S"

FORMATS: Dict[str, Tuple[str, str]] = {      # key -> (Qt format name, file suffix)
    "png": ("PNG", ".png"),
    "jpg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
}

def available_formats() -> Tuple[str, ...]:
    """Image formats this Qt build can write (WebP needs an image-format plugin)."""
    from PyQt6.QtGui import QImageWriter
    have = {bytes(f).decode().lower() for f in QImageWriter.supportedImageFormats()}
    return tuple(k for k, (qt_name, _s) in FORMATS.items()
                 if k == "png" or qt_name.lower() in have or (k == "jpg" and "jpg" in have))


AFTER_CHOICES = ("save", "editor")
AFTER_LABELS = {"save": "Just save it", "editor": "Open in the editor"}
DEFAULT_AFTER = "save"

ENGINES = ("system", "crabture")

DELAY_CHOICES = (0, 1, 2, 3, 5, 10)

# Shortcuts a fresh install starts with.  They are NOT given to the desktop until
# the user asks for that (Configure ▸ Capture ▸ Apply to desktop).
DEFAULT_SHORTCUTS: Tuple[Tuple[str, str], ...] = (
    ("region", "Ctrl+Shift+Print"),
    ("fullscreen", "Ctrl+Print"),
    ("window", "Ctrl+Alt+Print"),
    ("monitor", ""),
    ("scroll", ""),
)

_ORG, _APP = "YawnByte", "Crabture"


class _Signals(QObject):
    changed = pyqtSignal()           # any capture preference or shortcut changed
    desktop_changed = pyqtSignal()   # "applied to the desktop" was switched / re-applied (Windows listens)


signals = _Signals()


def _qs() -> QSettings:
    """Factory for the settings store (tests may monkeypatch this)."""
    return QSettings(_ORG, _APP)


def _bool(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------
#  Simple preferences
# ---------------------------------------------------------------------------

def _get(key: str, default=None):
    s = _qs()
    return s.value(f"capture/{key}", default) if s.contains(f"capture/{key}") else default


def _put(key: str, value) -> None:
    s = _qs()
    s.setValue(f"capture/{key}", value)
    s.sync()
    signals.changed.emit()


def _remove(*keys: str) -> None:
    s = _qs()
    for k in keys:
        s.remove(f"capture/{k}")
    s.sync()
    signals.changed.emit()


def expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def default_folder() -> str:
    return expand(DEFAULT_FOLDER)


def folder() -> str:
    """Folder captures are saved to (absolute)."""
    raw = str(_get("folder", "") or "").strip()
    return expand(raw) if raw else default_folder()


def set_folder(path: str) -> None:
    path = (path or "").strip()
    _put("folder", expand(path) if path else "")


def folder_is_default() -> bool:
    return folder() == default_folder()


def pattern() -> str:
    raw = str(_get("pattern", "") or "").strip()
    return raw or DEFAULT_PATTERN


def set_pattern(text: str) -> None:
    _put("pattern", (text or "").strip())


def image_format() -> str:
    f = str(_get("format", "png") or "png").lower()
    return f if f in FORMATS else "png"


def set_image_format(fmt: str) -> None:
    _put("format", fmt if fmt in FORMATS else "png")


def quality() -> int:
    try:
        return max(1, min(100, int(_get("quality", 92))))
    except (TypeError, ValueError):
        return 92


def set_quality(q: int) -> None:
    _put("quality", max(1, min(100, int(q))))


def after() -> str:
    a = str(_get("after", DEFAULT_AFTER) or DEFAULT_AFTER).lower()
    return a if a in AFTER_CHOICES else DEFAULT_AFTER


def set_after(a: str) -> None:
    _put("after", a if a in AFTER_CHOICES else DEFAULT_AFTER)


def engine() -> str:
    """'crabture' = our own selector (default); 'system' = the machine's own screenshot tool."""
    e = str(_get("engine", "crabture") or "crabture").lower()
    return e if e in ENGINES else "crabture"


def set_engine(e: str) -> None:
    _put("engine", e if e in ENGINES else "crabture")


def delay() -> float:
    try:
        return max(0.0, float(_get("delay", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def set_delay(seconds: float) -> None:
    _put("delay", max(0.0, float(seconds)))


def hide_window() -> bool:
    return _bool(_get("hide", "true"), True)


def set_hide_window(on: bool) -> None:
    _put("hide", "true" if on else "false")


def clipboard() -> bool:
    return _bool(_get("clipboard", "false"), False)


def set_clipboard(on: bool) -> None:
    _put("clipboard", "true" if on else "false")


def notify() -> bool:
    return _bool(_get("notify", "true"), True)


def set_notify(on: bool) -> None:
    _put("notify", "true" if on else "false")


def confirm_mode() -> str:
    c = str(_get("confirm", "button") or "button").lower()
    return c if c in ("button", "release") else "button"


def set_confirm_mode(mode: str) -> None:
    _put("confirm", mode if mode in ("button", "release") else "button")


PREFERENCE_KEYS = ("folder", "pattern", "format", "quality", "after", "engine", "delay", "hide",
                   "clipboard", "notify", "confirm")


def reset_preferences() -> None:
    """Forget every saved preference (shortcuts are handled separately)."""
    _remove(*PREFERENCE_KEYS)


# ---------------------------------------------------------------------------
#  Capture shortcuts
# ---------------------------------------------------------------------------

@dataclass
class CaptureShortcut:
    """One shortcut → one kind of screenshot.

    ``delay``  seconds, or ``None`` for the default delay.
    ``folder`` '' for the default folder.
    ``after``  '' for the default, else 'editor' / 'save'.
    """
    id: str = ""
    mode: str = "region"
    key: str = ""                     # portable Qt text, e.g. "Ctrl+Shift+Print" ('' = none)
    delay: Optional[float] = None
    folder: str = ""
    after: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id()

    @classmethod
    def from_dict(cls, d: dict) -> Optional["CaptureShortcut"]:
        if not isinstance(d, dict) or d.get("mode") not in CAPTURE_MODES:
            return None
        delay = d.get("delay")
        try:
            delay = None if delay in (None, "") else max(0.0, float(delay))
        except (TypeError, ValueError):
            delay = None
        after_ = d.get("after") if d.get("after") in AFTER_CHOICES else ""
        raw_id = str(d.get("id") or "")
        sid = raw_id if re.fullmatch(r"[A-Za-z0-9_]{1,24}", raw_id) else ""
        return cls(id=sid, mode=d["mode"], key=str(d.get("key") or ""),
                   delay=delay, folder=str(d.get("folder") or ""), after=after_)

    def describe(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def default_shortcuts() -> List[CaptureShortcut]:
    return [CaptureShortcut(id=f"d_{mode}", mode=mode, key=key) for mode, key in DEFAULT_SHORTCUTS]


def _legacy_key(mode: str) -> str:
    """A key the user assigned in the old Configure ▸ Shortcuts ▸ Capture group."""
    s = _qs()
    k = f"shortcuts/action.capture_{mode}"
    if not s.contains(k):
        return ""
    value = str(s.value(k) or "")
    return "" if value == "<none>" else value


def _norm_key(text: str) -> str:
    from PyQt6.QtGui import QKeySequence
    return QKeySequence(text or "", QKeySequence.SequenceFormat.PortableText).toString()


def shortcuts() -> List[CaptureShortcut]:
    """The saved capture shortcuts (the defaults on a fresh install)."""
    raw = _get("shortcuts", None)
    if raw:
        try:
            data = json.loads(str(raw))
            items = [CaptureShortcut.from_dict(d) for d in data] if isinstance(data, list) else []
            items = [i for i in items if i is not None]
            seen: set = set()
            for it in items:                          # ids must be unique
                if it.id in seen:
                    it.id = new_id()
                seen.add(it.id)
            return items
        except (ValueError, TypeError):
            pass
    out = default_shortcuts()
    taken = set()
    for sc in out:                                    # carry over keys from the old shortcut page
        legacy = _legacy_key(sc.mode)
        if legacy:
            sc.key = legacy
            taken.add(_norm_key(legacy))
    for sc in out:                                    # …and let those win over a starter key they collide with
        if _norm_key(sc.key) in taken and not _legacy_key(sc.mode):
            sc.key = ""
    return out


def save_shortcuts(items: List[CaptureShortcut]) -> None:
    _put("shortcuts", json.dumps([asdict(i) for i in items], ensure_ascii=False))


def reset_shortcuts() -> None:
    _remove("shortcuts")


def shortcut_for_mode(mode: str) -> str:
    """First key assigned to *mode* ('' if none) — shown next to menu entries."""
    for sc in shortcuts():
        if sc.mode == mode and sc.key:
            return sc.key
    return ""


def desktop_applied() -> bool:
    return _bool(_get("applied", "false"), False)


def set_desktop_applied(on: bool) -> None:
    s = _qs()
    s.setValue("capture/applied", "true" if on else "false")
    s.sync()                                           # `changed` is not emitted: it is bookkeeping only
    signals.desktop_changed.emit()


def applied_keys() -> List[str]:
    """Accelerators last handed to the desktop (only kept where the desktop can't be asked)."""
    try:
        data = json.loads(str(_get("applied_keys", "[]")))
    except (ValueError, TypeError):
        return []
    return [str(k) for k in data] if isinstance(data, list) else []


def set_applied_keys(keys: List[str]) -> None:
    s = _qs()
    s.setValue("capture/applied_keys", json.dumps(list(keys)))
    s.sync()


# ---------------------------------------------------------------------------
#  Requests
# ---------------------------------------------------------------------------

@dataclass
class CaptureRequest:
    """One screenshot to take.  ``None`` fields mean "use the saved default"."""
    mode: str = "region"
    delay: Optional[float] = None
    folder: Optional[str] = None
    after: Optional[str] = None
    hide: Optional[bool] = None
    clipboard: Optional[bool] = None
    source: str = "menu"              # menu | shortcut | cli | ipc

    def resolved(self) -> "CaptureRequest":
        """A copy with every ``None`` replaced by the saved default."""
        return CaptureRequest(
            mode=self.mode,
            delay=delay() if self.delay is None else max(0.0, float(self.delay)),
            folder=expand(self.folder) if self.folder else folder(),
            after=self.after if self.after in AFTER_CHOICES else after(),
            hide=hide_window() if self.hide is None else bool(self.hide),
            clipboard=clipboard() if self.clipboard is None else bool(self.clipboard),
            source=self.source,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Optional["CaptureRequest"]:
        if not isinstance(d, dict) or d.get("mode") not in CAPTURE_MODES:
            return None
        delay_ = d.get("delay")
        try:
            delay_ = None if delay_ is None else max(0.0, float(delay_))
        except (TypeError, ValueError):
            delay_ = None
        return cls(
            mode=d["mode"], delay=delay_,
            folder=str(d["folder"]) if d.get("folder") else None,
            after=d.get("after") if d.get("after") in AFTER_CHOICES else None,
            hide=d["hide"] if isinstance(d.get("hide"), bool) else None,
            clipboard=d["clipboard"] if isinstance(d.get("clipboard"), bool) else None,
            source=str(d.get("source") or "ipc"),
        )


def request_for(sc: CaptureShortcut, source: str = "shortcut") -> CaptureRequest:
    return CaptureRequest(
        mode=sc.mode, delay=sc.delay, folder=sc.folder or None,
        after=sc.after or None, source=source,
    )


# ---------------------------------------------------------------------------
#  File names / saving
# ---------------------------------------------------------------------------

_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def render_name(pattern_text: str, mode: str = "region", now: Optional[datetime] = None) -> str:
    """Expand *pattern_text* (strftime codes and ``{mode}``) into a safe file stem."""
    now = now or datetime.now()
    text = (pattern_text or DEFAULT_PATTERN).replace("{mode}", mode)
    try:
        text = now.strftime(text)
    except ValueError:                                 # a lone '%' etc.
        text = now.strftime(DEFAULT_PATTERN)
    text = _BAD_NAME_CHARS.sub("_", text).strip().strip(".")
    return text or now.strftime(DEFAULT_PATTERN)


def unique_path(directory: str, stem: str, suffix: str) -> str:
    path = os.path.join(directory, stem + suffix)
    n = 1
    while os.path.exists(path):
        path = os.path.join(directory, f"{stem}_{n}{suffix}")
        n += 1
    return path


def writable_folder(preferred: str) -> Tuple[str, str]:
    """Return ``(folder, note)``: *preferred* if it can be created and written to,
    otherwise the default folder, otherwise the system temp dir.  *note* explains
    a fallback ('' when none was needed)."""
    candidates = [(expand(preferred), "")]
    if expand(preferred) != default_folder():
        candidates.append((default_folder(), f"“{preferred}” is not writable — saved in the default folder instead"))
    candidates.append((os.path.join(tempfile.gettempdir(), "Crabshots"),
                       "no capture folder is writable — saved in the temporary folder"))
    for path, why in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            if os.access(path, os.W_OK):
                return path, why
        except OSError:
            pass
    return tempfile.gettempdir(), "no capture folder is writable — saved in the temporary folder"


def save_pixmap(pixmap, directory: Optional[str] = None, mode: str = "region") -> Tuple[Optional[str], str]:
    """Write *pixmap* using the saved name pattern and format.

    Returns ``(path, note)``; *path* is None when saving failed, *note* describes
    a folder fallback or the failure.  The file appears atomically (written under
    a temporary name, then renamed) so the gallery never sees a half-written image.
    """
    if pixmap is None or pixmap.isNull():
        return None, "nothing to save"
    target, note = writable_folder(directory or folder())
    stem = render_name(pattern(), mode)
    wanted = image_format()
    for fmt_key in ([wanted] if wanted == "png" else [wanted, "png"]):
        qt_format, suffix = FORMATS[fmt_key]
        path = unique_path(target, stem, suffix)
        tmp = os.path.join(target, f".{os.path.basename(path)}.{uuid.uuid4().hex[:6]}.part")
        if not pixmap.save(tmp, qt_format, quality() if fmt_key != "png" else -1):
            _silent_remove(tmp)
            continue                               # e.g. no WebP plugin: fall back to PNG
        try:
            os.replace(tmp, path)
        except OSError as e:
            _silent_remove(tmp)
            return None, f"could not write to {target}: {e.strerror or e}"
        if fmt_key != wanted:
            note = (note + "; " if note else "") + f"{wanted.upper()} is not supported here — saved as PNG"
        return path, note
    return None, f"could not write to {target}"


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
