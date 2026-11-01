"""
capture/winhotkeys.py — global capture hot-keys on Windows (``RegisterHotKey``).

The listener lives in the running Crabture process (the background instance started at login, or
any normal window).  Windows posts ``WM_HOTKEY`` to the thread that registered the key, and Qt hands
every thread message to a native event filter — no extra thread, no window handle.

    listener = WinHotkeys(parent)
    listener.triggered.connect(lambda shortcut: ...)    # a CaptureShortcut
    failures = listener.reload()                        # re-reads Configure ▸ Capture; {id: reason}

Only registered keys are consumed.  A key another program (or Windows itself) already owns cannot be
registered; :meth:`reload` reports it instead of failing silently.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal

from crabture.capture import config, hotkeys
from crabture.capture.log import log

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000                    # holding the key down must not start a capture every 30 ms
ERROR_HOTKEY_ALREADY_REGISTERED = 1409


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):                      # noqa: N802 - Qt API
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG" and message is not None:
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class WinHotkeys(QObject):
    triggered = pyqtSignal(object)       # CaptureShortcut

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ids: Dict[int, "config.CaptureShortcut"] = {}
        self._filter: Optional[_Filter] = None
        self._active = False
        config.signals.changed.connect(self._maybe_reload)
        config.signals.desktop_changed.connect(self._maybe_reload)

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> Dict[str, str]:
        """Install the event filter and register the saved shortcuts.  Returns ``{id: reason}`` for
        shortcuts that could not be registered."""
        from PyQt6.QtCore import QCoreApplication
        self._active = True
        if self._filter is None:
            self._filter = _Filter(self._on_hotkey)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
        return self.reload()

    def stop(self) -> None:
        self._active = False
        self._unregister_all()
        if self._filter is not None:
            from PyQt6.QtCore import QCoreApplication
            app = QCoreApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._filter)
            self._filter = None

    def _maybe_reload(self) -> None:
        if self._active:
            for sid, reason in self.reload().items():
                log(f"hotkey {sid}: {reason}")

    # -- registration ------------------------------------------------------------

    def _unregister_all(self) -> None:
        if sys.platform != "win32":
            self._ids.clear()
            return
        import ctypes
        user32 = ctypes.windll.user32                                       # type: ignore[attr-defined]
        for hotkey_id in list(self._ids):
            user32.UnregisterHotKey(None, hotkey_id)
        self._ids.clear()

    def reload(self) -> Dict[str, str]:
        """Make the registered keys match Configure ▸ Capture (only while "applied")."""
        self._unregister_all()
        failures: Dict[str, str] = {}
        if sys.platform != "win32" or not config.desktop_applied():
            return failures
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)               # type: ignore[attr-defined]
        next_id = 1
        seen = set()
        for sc in config.shortcuts():
            if not sc.key.strip():
                continue
            canon = hotkeys.canon_accel(sc.key)
            parsed = hotkeys.to_win32(sc.key)
            if parsed is None or not canon:
                failures[sc.id] = "that key cannot be used as a global shortcut"
                continue
            if canon in seen:
                failures[sc.id] = "already used by another Crabture shortcut"
                continue
            seen.add(canon)
            mods, vk = parsed
            if user32.RegisterHotKey(None, next_id, mods | MOD_NOREPEAT, vk):
                self._ids[next_id] = sc
                next_id += 1
            else:
                err = ctypes.get_last_error()
                failures[sc.id] = ("another program (or Windows) already uses " + sc.key
                                   if err == ERROR_HOTKEY_ALREADY_REGISTERED
                                   else f"Windows refused {sc.key} (error {err})")
        log(f"hotkeys registered: {[s.key for s in self._ids.values()]}; failed: {failures}")
        return failures

    def registered(self) -> List["config.CaptureShortcut"]:
        return list(self._ids.values())

    def _on_hotkey(self, hotkey_id: int) -> None:
        sc = self._ids.get(hotkey_id)
        if sc is not None:
            self.triggered.emit(sc)
