"""
settings/capture_page.py — "Capture" tab of the Configure window.

Three cards, all applied live (like the rest of the dialog):

  • Where screenshots go   – folder, extra folders for copies, file-name pattern, image format
  • After a capture        – open in the editor or just save, delay, clipboard, …
  • Capture shortcuts      – any number of key combinations, each taking one kind of
                             screenshot (optionally with its own delay / folder /
                             "after" behaviour), and the button that hands them to the
                             desktop so they work from anywhere.

The data lives in ``crabture.capture.config``; desktop registration in
``crabture.capture.hotkeys``.  This module is only the widgets.
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from crabture.capture import config, hotkeys
from crabture.settings import keymap
from crabture.settings.dialog import _KeyCapture, _button, _card, _display_key, _role
from crabture.ui.common import divider

_NAME_HELP = (
    "Characters: %Y year, %m month, %d day, %H hour, %M minute, %S second,\n"
    "{mode} the kind of capture (region, fullscreen, …).\n"
    "A number is added automatically if the name already exists."
)


class _ShortcutRow(QFrame):
    """One capture shortcut: kind, key, and its optional overrides."""

    def __init__(self, sc: config.CaptureShortcut, page: "CapturePage") -> None:
        super().__init__()
        self.sc = sc
        self._page = page
        self._loading = True
        self.setProperty("role", "card")
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 8, 10, 8)
        col.setSpacing(6)

        # -- line 1: kind, key, status, try, remove ---------------------------------
        top = QHBoxLayout()
        top.setSpacing(8)
        self.mode = QComboBox()
        for mode in config.CAPTURE_MODES:
            self.mode.addItem(config.MODE_LABELS[mode], mode)
        self.mode.setMinimumWidth(150)
        self.mode.currentIndexChanged.connect(self._on_mode)
        top.addWidget(self.mode)

        self.key = _KeyCapture()
        self.key.setMaximumSequenceLength(1)
        self.key.setClearButtonEnabled(False)
        self.key.setMinimumWidth(190)
        self.key.setFixedHeight(34)
        self.key.setToolTip("Click, then press the key combination (Esc cancels, Backspace clears).")
        self.key.editingFinished.connect(self._on_key)
        self.key.cancelled.connect(self._revert_key)
        self.key.cleared.connect(self._clear_key)
        top.addWidget(self.key, 1)

        self.status = QLabel("")
        _role(self.status, "caption")
        top.addWidget(self.status)

        self.try_btn = _button("Try", "ghost", "capture", "Take this screenshot now")
        self.try_btn.clicked.connect(lambda: page.try_shortcut(self.sc))
        top.addWidget(self.try_btn)
        self.remove_btn = _button("", "ghost", "trash", "Remove this shortcut")
        self.remove_btn.setFixedWidth(34)
        self.remove_btn.clicked.connect(lambda: page.remove_row(self))
        top.addWidget(self.remove_btn)
        col.addLayout(top)

        # -- line 2: overrides ----------------------------------------------------------
        low = QHBoxLayout()
        low.setSpacing(8)
        low.addWidget(self._caption("Delay"))
        self.delay = QComboBox()
        self.delay.addItem("Default", None)
        for s in config.DELAY_CHOICES:
            self.delay.addItem("None" if s == 0 else f"{s} s", float(s))
        self.delay.currentIndexChanged.connect(self._on_delay)
        low.addWidget(self.delay)
        low.addWidget(self._caption("Save to"))
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Default folder")
        self.folder.setClearButtonEnabled(True)
        self.folder.editingFinished.connect(self._on_folder)
        low.addWidget(self.folder, 1)
        browse = _button("", "ghost", "folder", "Choose a folder for this shortcut")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._browse)
        low.addWidget(browse)
        low.addWidget(self._caption("Then"))
        self.after = QComboBox()
        for key in config.AFTER_CHOICES:
            self.after.addItem(config.AFTER_LABELS[key], key)
        self.after.currentIndexChanged.connect(self._on_after)
        low.addWidget(self.after)
        col.addLayout(low)

        self.load()
        self._loading = False

    @staticmethod
    def _caption(text: str) -> QLabel:
        lab = QLabel(text)
        _role(lab, "caption")
        return lab

    # -- model <-> widgets ---------------------------------------------------------------

    def load(self) -> None:
        was, self._loading = self._loading, True
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.sc.mode)))
        self.key.setKeySequence(QKeySequence(self.sc.key, QKeySequence.SequenceFormat.PortableText))
        self.delay.setCurrentIndex(0 if self.sc.delay is None else
                                   max(0, self.delay.findData(float(self.sc.delay))))
        self.folder.setText(self.sc.folder)
        # A row that never chose follows the default ("Just save it" unless changed above).
        self.after.setCurrentIndex(max(0, self.after.findData(self.sc.after or config.after())))
        self._loading = was

    def set_status(self, text: str, problem: bool = False) -> None:
        self.status.setText(text)
        _role(self.status, "badge" if text and not problem else "caption")
        self.status.setStyleSheet("color: #D32F2F;" if problem else "")
        self.status.setToolTip(text)

    def _emit(self) -> None:
        if not self._loading:
            self._page.rows_changed()

    # -- handlers -------------------------------------------------------------------------

    def _on_mode(self) -> None:
        self.sc.mode = self.mode.currentData()
        self._emit()

    def _on_delay(self) -> None:
        self.sc.delay = self.delay.currentData()
        self._emit()

    def _on_after(self) -> None:
        self.sc.after = self.after.currentData() or ""
        self._emit()

    def refresh_default(self) -> None:
        """The global default changed: rows without their own choice follow it."""
        if not self.sc.after:
            was, self._loading = self._loading, True
            self.after.setCurrentIndex(max(0, self.after.findData(config.after())))
            self._loading = was

    def _on_folder(self) -> None:
        text = self.folder.text().strip()
        if text != self.sc.folder:
            self.sc.folder = text
            self._emit()

    def _browse(self) -> None:
        start = config.expand(self.sc.folder) if self.sc.folder else config.folder()
        path = QFileDialog.getExistingDirectory(self, "Save this shortcut's screenshots in…", start)
        if path:
            self.folder.setText(path)
            self._on_folder()

    def _revert_key(self) -> None:
        self.load()
        self.key.clearFocus()

    def _clear_key(self) -> None:
        self.sc.key = ""
        self.key.clear()
        self.key.clearFocus()
        self.set_status("")
        self._emit()

    def _on_key(self) -> None:
        if self._loading:
            return
        text = self.key.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        self.key.clearFocus()
        if not text:
            return
        err = self._page.check_key(self, text)
        if err:
            self.load()
            self.set_status(err, problem=True)
            QTimer.singleShot(5000, lambda: self._page.refresh_status())
            return
        self.sc.key = hotkeys.normalise_accelerator(text)
        self.load()
        self._emit()


class CapturePage(QWidget):
    """The Capture tab (a scrollable column of cards)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = True
        self._saving = False
        self._rows: List[_ShortcutRow] = []
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(700)
        self._sync_timer.timeout.connect(self._auto_sync)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(12)

        # ---- where screenshots go ----------------------------------------------------
        card, lay = _card("Where screenshots go",
                          "Every capture is saved here, and the gallery shows this folder.")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(config.DEFAULT_FOLDER)
        self.folder.editingFinished.connect(self._on_folder_edit)
        row.addWidget(self.folder, 1)
        b = _button("Browse…", "secondary", "folder")
        b.clicked.connect(self._browse_folder)
        row.addWidget(b)
        b = _button("Open", "ghost", tip="Open the folder in your file manager")
        b.clicked.connect(self._open_folder)
        row.addWidget(b)
        self.folder_reset = _button("Default", "ghost", tip=f"Use {config.DEFAULT_FOLDER}")
        self.folder_reset.clicked.connect(self._reset_folder)
        row.addWidget(self.folder_reset)
        lay.addLayout(row)
        self.folder_note = QLabel("")
        _role(self.folder_note, "caption")
        self.folder_note.setWordWrap(True)
        lay.addWidget(self.folder_note)

        crow = QHBoxLayout()
        crow.setSpacing(8)
        crow.addWidget(QLabel("Also save a copy to"))
        crow.addStretch(1)
        b = _button("Add folder…", "secondary", "folder", "Every capture is also copied into this folder")
        b.clicked.connect(self._add_copy_folder)
        crow.addWidget(b)
        self.copy_remove = _button("Remove", "ghost", tip="Stop copying captures to the selected folder")
        self.copy_remove.clicked.connect(self._remove_copy_folder)
        crow.addWidget(self.copy_remove)
        lay.addLayout(crow)
        self.copies = QListWidget()
        self.copies.setFixedHeight(64)
        self.copies.setToolTip("Every capture is saved in the folder above and copied into each of these.\n"
                               "A folder that cannot be written to is skipped and mentioned in the notification.")
        self.copies.itemSelectionChanged.connect(lambda: self.copy_remove.setEnabled(bool(self.copies.selectedItems())))
        lay.addWidget(self.copies)

        nrow = QHBoxLayout()
        nrow.setSpacing(8)
        nrow.addWidget(QLabel("File name"))
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText(config.DEFAULT_PATTERN)
        self.pattern.setToolTip(_NAME_HELP)
        self.pattern.editingFinished.connect(self._on_pattern)
        self.pattern.textChanged.connect(lambda _t: self._update_preview())
        nrow.addWidget(self.pattern, 1)
        nrow.addWidget(QLabel("Format"))
        self.format = QComboBox()
        labels = {"png": "PNG (lossless)", "jpg": "JPEG", "webp": "WebP"}
        for key in config.available_formats():
            self.format.addItem(labels[key], key)
        self.format.currentIndexChanged.connect(self._on_format)
        nrow.addWidget(self.format)
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setPrefix("Quality ")
        self.quality.valueChanged.connect(self._on_quality)
        nrow.addWidget(self.quality)
        lay.addLayout(nrow)
        self.preview = QLabel("")
        _role(self.preview, "caption")
        lay.addWidget(self.preview)
        col.addWidget(card)

        # ---- after a capture -----------------------------------------------------------
        card, lay = _card("How screenshots are taken")
        e1 = QHBoxLayout()
        e1.setSpacing(8)
        e1.addWidget(QLabel("Tool"))
        self.engine = QComboBox()
        self.engine.addItem("Crabture's own region selector (recommended)", "crabture")
        self.engine.addItem("System screenshot tool", "system")
        self.engine.currentIndexChanged.connect(self._on_engine)
        e1.addWidget(self.engine)
        e1.addStretch(1)
        lay.addLayout(e1)
        self.engine_note = QLabel("")
        self.engine_note.setWordWrap(True)
        _role(self.engine_note, "caption")
        lay.addWidget(self.engine_note)
        col.addWidget(card)

        card, lay = _card("After a capture")
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(QLabel("Then"))
        self.after = QComboBox()
        for key in config.AFTER_CHOICES:
            self.after.addItem(config.AFTER_LABELS[key], key)
        self.after.currentIndexChanged.connect(self._on_after)
        r1.addWidget(self.after)
        r1.addSpacing(14)
        r1.addWidget(QLabel("Delay"))
        self.delay = QComboBox()
        for s in config.DELAY_CHOICES:
            self.delay.addItem("None" if s == 0 else f"{s} s", float(s))
        self.delay.currentIndexChanged.connect(self._on_delay)
        r1.addWidget(self.delay)
        r1.addStretch(1)
        lay.addLayout(r1)
        self.hide_cb = QCheckBox("Hide the Crabture window while capturing (only for captures started from Crabture's own buttons and menus)")
        self.clip_cb = QCheckBox("Copy every capture to the clipboard")
        self.notify_cb = QCheckBox("Show a desktop notification when a capture is only saved")
        self.release_cb = QCheckBox("Crabture's selector: capture the moment I let go of the mouse (no ✔ button)")
        for cb, handler in ((self.hide_cb, config.set_hide_window), (self.clip_cb, config.set_clipboard),
                            (self.notify_cb, config.set_notify),
                            (self.release_cb, lambda on: config.set_confirm_mode("release" if on else "button"))):
            cb.toggled.connect(lambda on, h=handler: self._apply(h, on))
            lay.addWidget(cb)
        col.addWidget(card)

        # ---- shortcuts -------------------------------------------------------------------
        card, lay = _card(
            "Capture shortcuts",
            "Add as many as you like — each key combination takes one kind of screenshot and saves it in its own "
            "folder (optionally with its own delay). They work inside Crabture right away; use “Apply to desktop” "
            "to make them work from anywhere.")
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(8)
        lay.addLayout(self.rows_box)
        arow = QHBoxLayout()
        self.add_btn = _button("Add shortcut", "secondary", "plus")
        self.add_btn.clicked.connect(self.add_row)
        arow.addWidget(self.add_btn)
        self.reset_btn = _button("Reset shortcuts", "ghost", "refresh", "Back to the three starter shortcuts")
        self.reset_btn.clicked.connect(self._reset_shortcuts)
        arow.addWidget(self.reset_btn)
        arow.addStretch(1)
        lay.addLayout(arow)

        lay.addWidget(divider())
        self.desktop_title = QLabel("")
        _role(self.desktop_title, "h3")
        lay.addWidget(self.desktop_title)
        self.desktop_note = QLabel("")
        self.desktop_note.setWordWrap(True)
        _role(self.desktop_note, "muted")
        lay.addWidget(self.desktop_note)
        drow = QHBoxLayout()
        self.apply_btn = _button("Apply to desktop", "primary", "keyboard")
        self.apply_btn.clicked.connect(self.apply_to_desktop)
        drow.addWidget(self.apply_btn)
        self.remove_desktop_btn = _button("Remove from desktop", "ghost")
        self.remove_desktop_btn.clicked.connect(self.remove_from_desktop)
        drow.addWidget(self.remove_desktop_btn)
        self.copy_cmd_btn = _button("Copy commands", "secondary", "copy",
                                    "Copy the commands so you can bind them in your desktop's keyboard settings")
        self.copy_cmd_btn.clicked.connect(self._copy_commands)
        drow.addWidget(self.copy_cmd_btn)
        drow.addStretch(1)
        lay.addLayout(drow)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setMaximumHeight(110)
        self.commands.hide()
        lay.addWidget(self.commands)
        col.addWidget(card)

        # ---- reset -------------------------------------------------------------------------
        card, lay = _card("Reset", "Puts the folder, file name, behaviour and shortcuts back to how they started.")
        rrow = QHBoxLayout()
        b = _button("Reset capture settings", "danger", "refresh")
        b.clicked.connect(self.reset_all)
        rrow.addWidget(b)
        rrow.addStretch(1)
        lay.addLayout(rrow)
        col.addWidget(card)
        col.addStretch(1)

        config.signals.changed.connect(self._on_external_change)
        self.reload()
        self._loading = False

    # ------------------------------------------------------------------ loading

    def reload(self) -> None:
        """Re-read everything stored into the controls (writes nothing)."""
        was, self._loading = self._loading, True
        self.folder.setText("" if config.folder_is_default() else config.folder())
        self.copies.clear()
        self.copies.addItems(config.extra_folders())
        self.copy_remove.setEnabled(False)
        self.pattern.setText("" if config.pattern() == config.DEFAULT_PATTERN else config.pattern())
        self.format.setCurrentIndex(max(0, self.format.findData(config.image_format())))
        self.quality.setValue(config.quality())
        self.after.setCurrentIndex(max(0, self.after.findData(config.after())))
        self.engine.setCurrentIndex(max(0, self.engine.findData(config.engine())))
        self._update_engine_note()
        idx = min(range(self.delay.count()), key=lambda i: abs(self.delay.itemData(i) - config.delay()))
        self.delay.setCurrentIndex(idx)
        self.hide_cb.setChecked(config.hide_window())
        self.clip_cb.setChecked(config.clipboard())
        self.notify_cb.setChecked(config.notify())
        self.release_cb.setChecked(config.confirm_mode() == "release")
        self._update_folder_note()
        self._update_preview()
        self._load_rows(config.shortcuts())
        self._update_desktop_ui()
        self._loading = was

    def _load_rows(self, items: List[config.CaptureShortcut]) -> None:
        for r in self._rows:
            self.rows_box.removeWidget(r)
            r.deleteLater()
        self._rows = []
        for sc in items:
            self._append_row(sc)
        self.refresh_status()

    def _append_row(self, sc: config.CaptureShortcut) -> _ShortcutRow:
        r = _ShortcutRow(sc, self)
        self._rows.append(r)
        self.rows_box.addWidget(r)
        return r

    def _on_external_change(self) -> None:
        if not self._saving and not self._loading:
            self.reload()

    # ------------------------------------------------------------------ simple settings

    def _apply(self, setter, value) -> None:
        if self._loading:
            return
        self._saving = True
        try:
            setter(value)
        finally:
            self._saving = False

    def _on_after(self) -> None:
        self._apply(config.set_after, self.after.currentData())
        for r in self._rows:
            r.refresh_default()

    def _on_engine(self) -> None:
        self._apply(config.set_engine, self.engine.currentData())
        self._update_engine_note()

    def _update_engine_note(self) -> None:
        from crabture.capture.service import system_tool_description
        if config.engine() == "system":
            self.engine_note.setText(
                f"Uses {system_tool_description()}. Your shortcuts just start it and Crabture saves "
                "the result where you chose. Scrolling capture always uses Crabture's own selector.")
        else:
            self.engine_note.setText(
                "Crabture freezes the screen and lets you drag a rectangle itself (✔/✖ buttons, pixel loupe).")
        self.release_cb.setEnabled(config.engine() == "crabture")

    def _on_delay(self) -> None:
        self._apply(config.set_delay, self.delay.currentData())

    def _on_pattern(self) -> None:
        self._apply(config.set_pattern, self.pattern.text())
        self._update_preview()

    def _on_format(self) -> None:
        self.quality.setEnabled(self.format.currentData() != "png")
        self._apply(config.set_image_format, self.format.currentData())
        self._update_preview()

    def _on_quality(self, value: int) -> None:
        self._apply(config.set_quality, value)

    def _update_preview(self) -> None:
        fmt = self.format.currentData() or "png"
        stem = config.render_name(self.pattern.text().strip() or config.DEFAULT_PATTERN, "region")
        self.preview.setText(f"Example: {stem}{config.FORMATS[fmt][1]}")
        self.quality.setEnabled(fmt != "png")

    # ------------------------------------------------------------------ folder

    def _on_folder_edit(self) -> None:
        text = self.folder.text().strip()
        if text == ("" if config.folder_is_default() else config.folder()):
            return
        self._apply(config.set_folder, text)
        self._update_folder_note()

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save screenshots in…", config.folder())
        if path:
            self.folder.setText(path)
            self._on_folder_edit()

    def _add_copy_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Also save a copy in…", config.folder())
        if not path:
            return
        folders = config.extra_folders()
        full = config.expand(path)
        if full not in folders:
            self._apply(config.set_extra_folders, folders + [full])
            self.copies.addItem(full)

    def _remove_copy_folder(self) -> None:
        gone = {i.text() for i in self.copies.selectedItems()}
        if not gone:
            return
        self._apply(config.set_extra_folders, [f for f in config.extra_folders() if f not in gone])
        for item in self.copies.selectedItems():
            self.copies.takeItem(self.copies.row(item))

    def _reset_folder(self) -> None:
        self.folder.setText("")
        self._on_folder_edit()

    def _open_folder(self) -> None:
        folder, _note = config.writable_folder(config.folder())
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _update_folder_note(self) -> None:
        folder = config.folder()
        short = folder.replace(os.path.expanduser("~"), "~")
        if os.path.isdir(folder):
            ok = os.access(folder, os.W_OK)
            self.folder_note.setText(f"Saving to {short}" if ok else f"{short} exists but is not writable.")
        else:
            self.folder_note.setText(f"Saving to {short} (it will be created on the first capture).")
        self.folder_reset.setEnabled(not config.folder_is_default())

    # ------------------------------------------------------------------ shortcut rows

    def rows_changed(self) -> None:
        """A row was edited: persist, refresh badges and (maybe) re-sync the desktop."""
        if self._loading:
            return
        self._saving = True
        try:
            config.save_shortcuts([r.sc for r in self._rows])
        finally:
            self._saving = False
        self.refresh_status()
        if config.desktop_applied():
            self._sync_timer.start()
        self._update_desktop_ui()

    def check_key(self, row: _ShortcutRow, text: str) -> Optional[str]:
        """Why *text* cannot be used for *row* (None if it is fine)."""
        err = hotkeys.validate_accelerator(text)
        if err:
            return err
        canon = hotkeys.canon_accel(text)
        for other in self._rows:
            if other is not row and other.sc.key and hotkeys.canon_accel(other.sc.key) == canon:
                return f"Already used by your “{other.sc.describe()}” shortcut."
        clash = keymap.conflicts("", hotkeys.normalise_accelerator(text))
        if clash:
            return f"Already used inside Crabture for “{clash[0].label}”."
        return None

    def add_row(self) -> None:
        used = {r.sc.mode for r in self._rows}
        mode = next((m for m in config.CAPTURE_MODES if m not in used), "region")
        sc = config.CaptureShortcut(mode=mode)
        row = self._append_row(sc)
        self.rows_changed()
        QTimer.singleShot(0, row.key.setFocus)

    def remove_row(self, row: _ShortcutRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            self.rows_box.removeWidget(row)
            row.deleteLater()
            self.rows_changed()

    def _reset_shortcuts(self) -> None:
        self._saving = True
        try:
            config.reset_shortcuts()
        finally:
            self._saving = False
        self._load_rows(config.shortcuts())
        if config.desktop_applied():
            self._sync_timer.start()
        self._update_desktop_ui()

    def try_shortcut(self, sc: config.CaptureShortcut) -> None:
        from crabture.capture.controller import shared
        shared().request(config.request_for(sc, "settings"))

    def refresh_status(self) -> None:
        active = set()
        if config.desktop_applied():
            try:
                active = hotkeys.registered_accels()
            except Exception:                          # noqa: BLE001 - cosmetic only
                active = set()
        seen = set()
        for r in self._rows:
            canon = hotkeys.canon_accel(r.sc.key) if r.sc.key else ""
            if not r.sc.key:
                r.set_status("No key")
            elif canon in seen:
                r.set_status("Duplicate key", problem=True)
            elif canon in active:
                r.set_status("Active on desktop")
            else:
                r.set_status("Only inside Crabture")
            if canon:
                seen.add(canon)

    # ------------------------------------------------------------------ desktop registration

    def _update_desktop_ui(self) -> None:
        supported = hotkeys.supported()
        name = hotkeys.desktop_name()
        self.desktop_title.setText(f"Make them work from anywhere ({name})")
        if supported:
            self.desktop_note.setText(
                "“Apply to desktop” adds these as keyboard shortcuts of your desktop; each runs "
                "“crabture --capture …”, which is instant when Crabture is open and otherwise takes the shot "
                "in the background. Shortcuts already used by something else are skipped and reported. "
                + ("Changes here are applied to the desktop automatically from now on."
                   if config.desktop_applied() else ""))
        else:
            self.desktop_note.setText(
                f"Crabture can't add keyboard shortcuts to {name} by itself. Copy the commands below and "
                "bind them in your desktop's keyboard settings (Settings ▸ Keyboard ▸ Shortcuts).")
        self.apply_btn.setVisible(supported)
        self.remove_desktop_btn.setVisible(supported)
        self.apply_btn.setText("Update desktop shortcuts" if config.desktop_applied() else "Apply to desktop")
        self.copy_cmd_btn.setVisible(True)
        self.commands.setVisible(not supported and any(r.sc.key for r in self._rows))
        if not supported:
            self.commands.setPlainText(self._commands_text())

    def _commands_text(self) -> str:
        lines = []
        for r in self._rows:
            if r.sc.key:
                lines.append(f"{_display_key(r.sc.key):<22}{hotkeys.command_for(r.sc)}")
        return "\n".join(lines)

    def _copy_commands(self) -> None:
        text = self._commands_text()
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "Commands copied", text or "No shortcut has a key yet.")

    def apply_to_desktop(self, quiet: bool = False) -> None:
        items = [r.sc for r in self._rows]
        report = hotkeys.apply_shortcuts(items)
        if report.supported:
            config.set_desktop_applied(True)
        for r in self._rows:
            msg = report.status.get(r.sc.id)
            if msg and msg != "ok":
                r.set_status(msg, problem=True)
        if not quiet or report.skipped or not report.ok:
            QMessageBox.information(
                self, "Desktop shortcuts",
                report.text() or "Nothing to register — give a shortcut a key first.")
        self._update_desktop_ui()
        QTimer.singleShot(6000, self.refresh_status)
        if all(report.status.get(r.sc.id) in (None, "ok") for r in self._rows):
            self.refresh_status()

    def _auto_sync(self) -> None:
        if config.desktop_applied() and hotkeys.supported():
            self.apply_to_desktop(quiet=True)

    def remove_from_desktop(self) -> None:
        report = hotkeys.remove_shortcuts()
        config.set_desktop_applied(False)
        QMessageBox.information(self, "Desktop shortcuts", report.text() or "Nothing to remove.")
        self.refresh_status()
        self._update_desktop_ui()

    # ------------------------------------------------------------------ reset

    def reset_all(self) -> None:
        r = QMessageBox.warning(
            self, "Reset capture settings",
            "Reset the capture folder, file name, behaviour and shortcuts to their defaults?\n\n"
            "Shortcuts already added to your desktop are left as they are until you press "
            "“Apply to desktop” or “Remove from desktop”.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if r != QMessageBox.StandardButton.Yes:
            return
        config.reset_preferences()
        config.reset_shortcuts()
        self.reload()
