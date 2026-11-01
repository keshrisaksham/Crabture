"""
settings/dialog.py — the "Configure" window of Crabture.

A tabbed settings dialog whose edits apply LIVE and persist immediately
(there is no OK/Apply):

  • Shortcuts      – every tool key and menu shortcut (settings/keymap.py)
  • Tools          – per-tool defaults: colour, widths, font sizes … (settings/tool_config.py)
  • Sensitive data – the saved sensitive-data scan profile (redact/profile.py)
  • Capture        – screenshot folder, file name, behaviour and capture shortcuts
                     (settings/capture_page.py)
  • General        – theme, "reset everything"

Public API::

    SettingsDialog(parent=None, initial_tab=None)   # 'shortcuts'|'tools'|'capture'|'sensitive'|'general'
    settings_signals.capture_changed                # kept for older callers (capture.config.signals is the live one)

The dialog works as a normal modal (``exec()``) or as a non-modal window
(``show()``).  Nothing here scans or redacts anything: the Sensitive-data tab
only edits (and saves, on request) the profile that "Run sensitive-data scan"
uses later.
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QPointF, QRectF, QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from crabture.capture import config as capture_config
from crabture.settings import keymap
from crabture.redact import core as redact
from crabture.redact import profile as redact_profile
from crabture.ui import theme
from crabture.settings import tool_config
from crabture.redact.dialog import RedactConfigWidget

try:  # icons are cosmetic; the dialog works without them
    from crabture.ui import icons
except Exception:  # noqa: BLE001
    icons = None

__all__ = ["SettingsDialog", "SettingsSignals", "settings_signals"]


# ---------------------------------------------------------------------------
#  Signals
# ---------------------------------------------------------------------------

class SettingsSignals(QObject):
    """Notifications other windows may want (the main window's capture menu)."""

    capture_changed = pyqtSignal()


settings_signals = SettingsSignals()


# ---------------------------------------------------------------------------
#  Small helpers
# ---------------------------------------------------------------------------

def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _role(widget: QWidget, role: str) -> QWidget:
    """Set the theme's ``role`` dynamic property (re-polishing if already shown)."""
    widget.setProperty("role", role)
    _repolish(widget)
    return widget


def _icon(name: str, size: int = 18):
    if icons is not None and icons.has_icon(name):
        return icons.icon(name, size)
    return None


def _bind(widget, name: str, size: int = 16) -> None:
    if icons is not None and icons.has_icon(name):
        icons.bind_icon(widget, name, size)


def _card(title: str = "", subtitle: str = "") -> Tuple[QFrame, QVBoxLayout]:
    """A themed card (role='card') with an optional h2 title and muted subtitle."""
    frame = QFrame()
    frame.setProperty("role", "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(4, 2, 4, 4)
    lay.setSpacing(8)
    if title:
        head = QLabel(title)
        _role(head, "h2")
        lay.addWidget(head)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        _role(sub, "muted")
        lay.addWidget(sub)
    return frame, lay


def _button(text: str, role: str = "secondary", icon_name: str = "", tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setProperty("role", role)
    b.setAutoDefault(False)
    b.setDefault(False)
    if tip:
        b.setToolTip(tip)
    if icon_name:
        _bind(b, icon_name, 16)
    return b


def _muted_color(widget: QWidget) -> QColor:
    return widget.palette().color(QPalette.ColorRole.PlaceholderText)


def _display_key(text: str) -> str:
    """Portable shortcut text -> native display text ('' stays '')."""
    if not text:
        return ""
    return QKeySequence(text, QKeySequence.SequenceFormat.PortableText).toString(
        QKeySequence.SequenceFormat.NativeText
    )


# ===========================================================================
#  1. Shortcuts
# ===========================================================================

class _KeyCapture(QKeySequenceEdit):
    """QKeySequenceEdit where Esc cancels and Backspace/Delete clear."""

    cancelled = pyqtSignal()
    cleared = pyqtSignal()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if mods == Qt.KeyboardModifier.NoModifier:
            if event.key() == Qt.Key.Key_Escape:
                event.accept()
                self.cancelled.emit()
                return
            if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                event.accept()
                self.cleared.emit()
                return
        super().keyPressEvent(event)


class _ShortcutEditor(QWidget):
    """Inline editor shown in the shortcut cell while a row is being edited."""

    committed = pyqtSignal(str)     # portable text of the captured sequence
    cleared = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, single_key: bool, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(6)
        self.capture = _KeyCapture()
        self.capture.setFixedHeight(30)
        self.capture.setMaximumSequenceLength(1 if single_key else 4)
        self.capture.setClearButtonEnabled(False)
        self.capture.setToolTip(
            "Press the single key to use (no Ctrl/Alt/Shift)." if single_key
            else "Press the new shortcut, e.g. Ctrl+Shift+S."
        )
        self.capture.editingFinished.connect(self._finished)
        self.capture.cancelled.connect(self.cancelled)
        self.capture.cleared.connect(self.cleared)
        lay.addWidget(self.capture, 1)
        self.clear_btn = _button("Clear", "ghost", tip="Remove the shortcut (leave it unassigned)")
        self.clear_btn.clicked.connect(self.cleared)
        self.cancel_btn = _button("", "ghost", "close", "Cancel (Esc)")
        self.cancel_btn.setFixedWidth(34)
        self.cancel_btn.clicked.connect(self.cancelled)
        lay.addWidget(self.clear_btn)
        lay.addWidget(self.cancel_btn)
        self.setFocusProxy(self.capture)

    def _finished(self) -> None:
        text = self.capture.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if text:
            self.committed.emit(text)

    def reset_capture(self) -> None:
        self.capture.clear()
        self.capture.setFocus()


class _ShortcutRow:
    __slots__ = ("sdef", "item", "status", "reset_btn")

    def __init__(self, sdef, item, status, reset_btn) -> None:
        self.sdef = sdef
        self.item = item
        self.status = status
        self.reset_btn = reset_btn


class _ShortcutsPage(QWidget):
    """Searchable, grouped table of every shortcut with inline editing."""

    ROW_H = 38

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: Dict[str, _ShortcutRow] = {}
        self._editing: Optional[str] = None
        self._editor: Optional[_ShortcutEditor] = None
        self._pending: Optional[Tuple[str, str]] = None   # (id, text) awaiting conflict decision

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # -- toolbar ---------------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search actions or shortcuts…")
        self.search.setClearButtonEnabled(True)
        ic = _icon("search", 16)
        if ic is not None:
            self.search.addAction(ic, QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)
        self.count_label = QLabel()
        _role(self.count_label, "badge")
        bar.addWidget(self.count_label)
        self.btn_import = _button("Import…", "secondary", "open", "Load shortcuts from a JSON file")
        self.btn_export = _button("Export…", "secondary", "export", "Save your shortcuts to a JSON file")
        self.btn_reset_all = _button("Reset all", "danger", "refresh", "Restore every shortcut to its default")
        self.btn_import.clicked.connect(self.import_shortcuts)
        self.btn_export.clicked.connect(self.export_shortcuts)
        self.btn_reset_all.clicked.connect(self.reset_all)
        for b in (self.btn_import, self.btn_export, self.btn_reset_all):
            bar.addWidget(b)
        root.addLayout(bar)

        # -- tree ------------------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Action", "Shortcut", "Status", ""])
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        self.tree.setIndentation(0)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 250), (2, 110), (3, 46)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            hdr.resizeSection(col, width)
        hdr.setSectionsMovable(False)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemActivated.connect(self._on_item_activated)
        root.addWidget(self.tree, 1)

        # -- banner (hints, errors, conflict choices) ------------------------
        self.banner = QFrame()
        self.banner.setProperty("role", "card")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(4, 0, 4, 0)
        bl.setSpacing(8)
        self.banner_icon = QLabel()
        self.banner_icon.setFixedWidth(20)
        bl.addWidget(self.banner_icon)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.banner_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bl.addWidget(self.banner_label, 1)
        self.banner_buttons_box = QHBoxLayout()
        self.banner_buttons_box.setSpacing(6)
        bl.addLayout(self.banner_buttons_box)
        root.addWidget(self.banner)
        self.banner.hide()

        self._build_rows()
        self._refresh()
        keymap.signals.changed.connect(self._on_external_change)
        theme.signals.changed.connect(self._on_theme_changed)

    # ------------------------------------------------------------- building

    def _build_rows(self) -> None:
        for group in keymap.GROUPS:
            defs = [d for d in keymap.DEFS if d.group == group]
            if not defs:
                continue
            gi = QTreeWidgetItem(self.tree, [group])
            gi.setFirstColumnSpanned(True)
            f = gi.font(0)
            f.setBold(True)
            gi.setFont(0, f)
            gi.setForeground(0, QBrush(_muted_color(self)))
            gi.setFlags(Qt.ItemFlag.ItemIsEnabled)
            gi.setSizeHint(0, QSize(0, 34))
            gi.setData(0, Qt.ItemDataRole.UserRole, None)
            for d in defs:
                it = QTreeWidgetItem(gi, [d.label, "", "", ""])
                it.setData(0, Qt.ItemDataRole.UserRole, d.id)
                it.setSizeHint(0, QSize(0, self.ROW_H))
                status = QLabel()
                self.tree.setItemWidget(it, 2, status)
                # QTreeWidget forces item widgets visible, so the button lives in a
                # container: hiding the inner button works, hiding the item widget would not.
                holder = QWidget()
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(0, 0, 4, 0)
                btn = QToolButton()
                btn.setProperty("role", "toolbar")
                btn.setToolTip("Reset to default")
                _bind(btn, "refresh", 16)
                btn.clicked.connect(lambda _=False, i=d.id: self.reset_one(i))
                hl.addWidget(btn)
                self.tree.setItemWidget(it, 3, holder)
                self._rows[d.id] = _ShortcutRow(d, it, status, btn)
            gi.setExpanded(True)

    # ------------------------------------------------------------- refreshing

    def _refresh(self) -> None:
        customised = 0
        for sid, row in self._rows.items():
            d = row.sdef
            text = keymap.get(sid)
            shown = _display_key(text)
            it = row.item
            it.setText(1, shown if shown else "Unassigned")
            f = it.font(1)
            f.setBold(bool(shown) and d.kind != "fixed")
            it.setFont(1, f)
            if shown and d.kind != "fixed":
                it.setForeground(1, QBrush(self.palette().color(QPalette.ColorRole.Text)))
            else:
                it.setForeground(1, QBrush(_muted_color(self)))
            if d.kind == "fixed":
                it.setToolTip(0, "This shortcut is fixed and cannot be changed.")
                it.setToolTip(1, "Fixed")
                _role(row.status, "caption")
                row.status.setText("Fixed")
                row.reset_btn.setVisible(False)
                continue
            it.setToolTip(1, "Click to change this shortcut")
            if keymap.is_default(sid):
                _role(row.status, "caption")
                row.status.setText("Default")
                row.reset_btn.setVisible(False)
            else:
                customised += 1
                _role(row.status, "badge")
                row.status.setText("Customised")
                row.reset_btn.setVisible(True)
                row.reset_btn.setToolTip(
                    "Reset to default (" + (_display_key(d.default) or "unassigned") + ")"
                )
        self.count_label.setText(f"{customised} customised" if customised else "All defaults")
        self._customised = customised
        self._apply_filter(self.search.text())

    def _on_theme_changed(self, _name: str) -> None:
        self._restyle()

    def _restyle(self) -> None:
        """Colours that come from the palette must follow a theme switch."""
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setForeground(0, QBrush(_muted_color(self)))
        self._refresh()

    def customised_count(self) -> int:
        return getattr(self, "_customised", 0)

    def _on_external_change(self, _id: str) -> None:
        if self._editor is not None and self._editing:
            self._end_edit()
        self._refresh()

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            gi = self.tree.topLevelItem(i)
            any_visible = False
            for j in range(gi.childCount()):
                it = gi.child(j)
                sid = it.data(0, Qt.ItemDataRole.UserRole)
                row = self._rows.get(sid)
                if row is None:
                    continue
                hay = " ".join((
                    row.sdef.label, row.sdef.group, sid,
                    keymap.get(sid), _display_key(keymap.get(sid)),
                )).lower()
                match = (not needle) or all(tok in hay for tok in needle.split())
                it.setHidden(not match)
                any_visible = any_visible or match
            gi.setHidden(not any_visible)

    # ------------------------------------------------------------- editing

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        sid = item.data(0, Qt.ItemDataRole.UserRole)
        if sid and column in (0, 1, 2):
            if column == 1 or self._editing == sid:
                self.begin_edit(sid)

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        sid = item.data(0, Qt.ItemDataRole.UserRole)
        if sid:
            self.begin_edit(sid)

    def begin_edit(self, sid: str) -> Optional[_ShortcutEditor]:
        """Open the inline editor on row *sid* (returns it; None for fixed rows)."""
        row = self._rows.get(sid)
        if row is None or row.sdef.kind == "fixed":
            return None
        if self._editing == sid and self._editor is not None:
            self._editor.capture.setFocus()
            return self._editor
        self._end_edit()
        ed = _ShortcutEditor(single_key=row.sdef.kind == "tool")
        ed.committed.connect(lambda text, i=sid: self._on_captured(i, text))
        ed.cleared.connect(lambda i=sid: self._apply(i, ""))
        ed.cancelled.connect(self.cancel_edit)
        self.tree.setItemWidget(row.item, 1, ed)
        self.tree.setCurrentItem(row.item)
        self.tree.scrollToItem(row.item)
        self._editing, self._editor, self._pending = sid, ed, None
        if row.sdef.kind == "tool":
            self._hint("Press the single key for “%s” — no Ctrl, Alt or Shift. "
                       "Esc cancels, Backspace clears." % row.sdef.label)
        else:
            self._hint("Press the new shortcut for “%s” (for example Ctrl+Shift+S). "
                       "Esc cancels, Backspace clears." % row.sdef.label)
        ed.capture.setFocus()
        return ed

    def cancel_edit(self) -> None:
        self._end_edit()

    def _end_edit(self) -> None:
        if self._editing is None:
            self.banner.hide()
            return
        row = self._rows.get(self._editing)
        if row is not None:
            self.tree.removeItemWidget(row.item, 1)
        self._editing = self._editor = self._pending = None
        self._clear_banner_buttons()
        self.banner.hide()
        self.tree.setFocus()

    def _on_captured(self, sid: str, text: str) -> None:
        """A key sequence was captured: validate, resolve conflicts, then apply."""
        err = keymap.validate(sid, text)
        if err:
            self._show_banner("error", err)
            if self._editor is not None:
                QTimer.singleShot(0, self._editor.reset_capture)
            return
        clashes = keymap.conflicts(sid, text)
        if clashes:
            names = ", ".join(c.label for c in clashes)
            shown = _display_key(text)
            if any(c.kind == "fixed" for c in clashes):
                self._show_banner(
                    "error", f"{shown} is reserved for “{names}” and cannot be reassigned. Pick another key.")
                if self._editor is not None:
                    QTimer.singleShot(0, self._editor.reset_capture)
                return
            self._pending = (sid, text)
            self._show_banner(
                "warning", f"{shown} is already used by {names}.",
                [(f"Reassign (remove from {names})", "primary", self._reassign),
                 ("Cancel", "ghost", self.cancel_edit)])
            return
        self._apply(sid, text)

    def _reassign(self) -> None:
        if self._pending is None:
            return
        sid, text = self._pending
        self._apply(sid, text, unassign_conflicts=True)

    def _apply(self, sid: str, text: str, unassign_conflicts: bool = False) -> None:
        try:
            keymap.set(sid, text, unassign_conflicts=unassign_conflicts)
        except ValueError as e:
            self._show_banner("error", str(e))
            return
        self._end_edit()
        self._refresh()

    # ------------------------------------------------------------- banner

    def _clear_banner_buttons(self) -> None:
        while self.banner_buttons_box.count():
            item = self.banner_buttons_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _show_banner(self, kind: str, message: str,
                     actions: Optional[List[Tuple[str, str, Callable[[], None]]]] = None) -> None:
        self._clear_banner_buttons()
        role = {"error": "error", "warning": "warning"}.get(kind, "muted")
        _role(self.banner_label, role)
        self.banner_label.setText(message)
        name = {"error": "warning", "warning": "warning"}.get(kind, "info")
        if icons is not None and icons.has_icon(name):
            self.banner_icon.setPixmap(icons.pixmap(name, 16))
        self.banner_actions: List[QPushButton] = []
        for label, brole, cb in actions or []:
            b = _button(label, brole)
            b.clicked.connect(lambda _=False, f=cb: f())
            self.banner_buttons_box.addWidget(b)
            self.banner_actions.append(b)
        self.banner.show()

    def _hint(self, message: str) -> None:
        self._show_banner("info", message)

    def banner_text(self) -> str:
        return self.banner_label.text() if self.banner.isVisibleTo(self) else ""

    # ------------------------------------------------------------- actions

    def reset_one(self, sid: str) -> None:
        keymap.reset(sid)

    def reset_all(self) -> None:
        r = QMessageBox.question(
            self, "Reset all shortcuts",
            "Restore every tool key and menu shortcut to its default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Yes:
            self._end_edit()
            keymap.reset_all()

    def export_shortcuts(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export shortcuts", "crabture-shortcuts.json", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(keymap.export_json())
        except OSError as e:
            QMessageBox.warning(self, "Export shortcuts", f"Could not write the file:\n{e}")

    def import_shortcuts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import shortcuts", "", "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.warning(self, "Import shortcuts", f"Could not read the file:\n{e}")
            return
        problems = keymap.import_json(text)
        if problems:
            more = "" if len(problems) <= 15 else f"\n… and {len(problems) - 15} more"
            QMessageBox.warning(self, "Import shortcuts",
                                "Imported with some problems:\n\n" + "\n".join(problems[:15]) + more)
        else:
            QMessageBox.information(self, "Import shortcuts", "Shortcuts imported.")


# ===========================================================================
#  2. Tools
# ===========================================================================

_PREVIEW_TOOLS = (
    "arrow", "line", "pen", "rectangle", "ellipse", "highlight", "text",
    "balloon", "step", "sticker", "pixelate", "blur", "spotlight", "magnifier",
)

_EMOJI = ["😀", "😂", "😍", "😎", "🤔", "👍", "👎", "✅", "❌", "⚠️", "❓", "🔥", "💡", "🐛", "🔒", "🚀"]

_CHOICE_LABELS = {"rect": "Rectangle", "ellipse": "Ellipse"}


class _ToolPreview(QWidget):
    """A small live sketch of what the selected tool will draw with its settings."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tool = ""
        self.setMinimumHeight(96)
        self.setMaximumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self.setVisible(tool in _PREVIEW_TOOLS)
        self.update()

    # -- drawing -----------------------------------------------------------

    @staticmethod
    def _colour(v: dict, key: str = "color") -> QColor:
        """Fixed colour (with its opacity), or the theme text colour for 'palette colour'."""
        raw = v.get(key, "")
        if raw:
            c = QColor(raw)
            if "alpha" in v:
                c.setAlpha(int(v["alpha"]))
            return c
        return QColor(theme.tokens()["text"])

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._tool not in _PREVIEW_TOOLS:
            return
        t = theme.tokens()
        v = tool_config.values(self._tool)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t["border"]), 1))
        p.setBrush(QColor(t["surface"]))
        p.drawRoundedRect(frame, 8, 8)
        area = frame.adjusted(18, 12, -18, -12)

        def bars(alpha: int = 255) -> None:
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            col = QColor(t["surface3"])
            col.setAlpha(alpha)
            p.setBrush(col)
            for i, w in enumerate((0.92, 0.7, 0.82, 0.55)):
                p.drawRoundedRect(QRectF(area.left(), area.top() + 6 + i * 18, area.width() * w, 9), 4, 4)
            p.restore()

        getattr(self, "_draw_" + self._tool)(p, area, v, t, bars)
        p.end()

    def _draw_arrow(self, p, a, v, t, bars) -> None:
        col = self._colour(v)
        cy = a.center().y()
        x1, x2 = a.left() + 6, a.right() - 6
        head_l = min(float(v["head_length"]), (x2 - x1) * 0.6)
        head_w = min(float(v["head_width"]), a.height() * 0.95)
        shaft = min(float(v["shaft_width"]), head_w)
        poly = QPolygonF([
            QPointF(x1, cy - shaft / 2), QPointF(x2 - head_l, cy - shaft / 2),
            QPointF(x2 - head_l, cy - head_w / 2), QPointF(x2, cy),
            QPointF(x2 - head_l, cy + head_w / 2), QPointF(x2 - head_l, cy + shaft / 2),
            QPointF(x1, cy + shaft / 2),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPolygon(poly)

    def _draw_line(self, p, a, v, t, bars) -> None:
        pen = QPen(self._colour(v), min(float(v["width"]), a.height() * 0.8))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if v["round_caps"] else Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawLine(QPointF(a.left() + 10, a.bottom() - 12), QPointF(a.right() - 10, a.top() + 12))

    def _draw_pen(self, p, a, v, t, bars) -> None:
        pen = QPen(self._colour(v), min(float(v["width"]), a.height() * 0.6))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(QPointF(a.left() + 8, a.center().y()))
        w = a.width() - 16
        if v["smoothing"]:
            path.cubicTo(a.left() + 8 + w * 0.2, a.top(), a.left() + 8 + w * 0.3, a.bottom(),
                         a.left() + 8 + w * 0.5, a.center().y())
            path.cubicTo(a.left() + 8 + w * 0.7, a.top(), a.left() + 8 + w * 0.8, a.bottom(),
                         a.right() - 8, a.center().y())
        else:
            for i, (fx, fy) in enumerate(((0.12, 0.1), (0.25, 0.9), (0.38, 0.2), (0.5, 0.8),
                                          (0.62, 0.25), (0.75, 0.85), (0.88, 0.2), (1.0, 0.5))):
                path.lineTo(a.left() + 8 + w * fx, a.top() + a.height() * fy)
        p.drawPath(path)

    def _shape(self, p, a, v, ellipse: bool) -> None:
        col = self._colour(v)
        r = a.adjusted(40, 4, -40, -4)
        pen = QPen(col, min(float(v["stroke_width"]), r.height() / 3))
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        if v["fill"]:
            fc = QColor(col)
            fc.setAlpha(int(v["fill_opacity"]))
            p.setBrush(fc)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        if ellipse:
            p.drawEllipse(r)
        else:
            p.drawRect(r)

    def _draw_rectangle(self, p, a, v, t, bars) -> None:
        self._shape(p, a, v, False)

    def _draw_ellipse(self, p, a, v, t, bars) -> None:
        self._shape(p, a, v, True)

    def _draw_highlight(self, p, a, v, t, bars) -> None:
        bars()
        c = QColor(v["color"])
        c.setAlpha(int(v["alpha"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(QRectF(a.left() - 4, a.top() + 18, a.width() * 0.66, 22))

    def _draw_text(self, p, a, v, t, bars) -> None:
        f = QFont(str(v["font_family"]) or "Sans Serif")
        f.setPointSizeF(min(float(v["font_size"]), 30.0))
        f.setBold(bool(v["bold"]))
        p.setFont(f)
        p.setPen(self._colour(v))
        p.drawText(a, Qt.AlignmentFlag.AlignCenter, "Sample text")

    def _draw_balloon(self, p, a, v, t, bars) -> None:
        fill = QColor(v["fill"]) if v["fill"] else QColor("#FFFFFF")
        body = QRectF(a.center().x() - 90, a.top() + 2, 180, a.height() - 22)
        path = QPainterPath()
        path.addRoundedRect(body, 12, 12)
        tail = QPainterPath(QPointF(body.left() + 40, body.bottom() - 1))
        tail.lineTo(body.left() + 28, body.bottom() + 16)
        tail.lineTo(body.left() + 62, body.bottom() - 1)
        path = path.united(tail)
        p.setPen(QPen(QColor("#333333"), 1.5))
        p.setBrush(fill)
        p.drawPath(path)
        f = QFont()
        f.setPointSizeF(min(float(v["font_size"]), 20.0))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(v["text_color"]))
        p.drawText(body, Qt.AlignmentFlag.AlignCenter, "Hello!")

    def _draw_step(self, p, a, v, t, bars) -> None:
        d = min(float(v["diameter"]), a.height() - 2)
        col = self._colour(v)
        c = QRectF(a.center().x() - d / 2, a.center().y() - d / 2, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(c)
        lum = 0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()
        p.setPen(QColor("#000000") if lum > 150 else QColor("#FFFFFF"))
        f = QFont()
        f.setPixelSize(max(8, int(d * 0.55)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(c, Qt.AlignmentFlag.AlignCenter, "1")

    def _draw_sticker(self, p, a, v, t, bars) -> None:
        f = QFont()
        f.setPixelSize(int(min(float(v["size"]), a.height() - 2)))
        p.setFont(f)
        p.setPen(QColor(t["text"]))
        p.drawText(a, Qt.AlignmentFlag.AlignCenter, str(v["glyph"]))

    def _sample_image(self, w: int, h: int) -> QImage:
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor("#FFFFFF"))
        q = QPainter(img)
        for i in range(0, h, 6):
            q.fillRect(0, i, w, 3, QColor("#222222") if (i // 6) % 2 == 0 else QColor("#4A63E7"))
        q.end()
        return img

    def _draw_pixelate(self, p, a, v, t, bars) -> None:
        w, h = int(a.width() * 0.8), int(a.height())
        src = self._sample_image(w, h)
        k = max(1, int(v["intensity"]))
        small = src.scaled(max(1, w // k), max(1, h // k), Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)
        out = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        p.drawImage(QPointF(a.center().x() - w - 4, a.top()), src.scaled(w, h))
        p.drawImage(QPointF(a.center().x() + 4, a.top()), out)

    def _draw_blur(self, p, a, v, t, bars) -> None:
        w, h = int(a.width() * 0.8), int(a.height())
        src = self._sample_image(w, h)
        k = max(1, int(v["intensity"]))
        small = src.scaled(max(1, w // k), max(1, h // k), Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        out = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p.drawImage(QPointF(a.center().x() - w - 4, a.top()), src.scaled(w, h))
        p.drawImage(QPointF(a.center().x() + 4, a.top()), out)

    def _draw_spotlight(self, p, a, v, t, bars) -> None:
        bars()
        outer = QPainterPath()
        outer.addRect(a.adjusted(-10, -6, 10, 6))
        hole = QPainterPath()
        r = QRectF(a.center().x() - 60, a.top() + 6, 120, a.height() - 12)
        if v["shape"] == "ellipse":
            hole.addEllipse(r)
        else:
            hole.addRoundedRect(r, 4, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(v["dim"])))
        p.drawPath(outer.subtracted(hole))
        p.setPen(QPen(QColor(255, 255, 255, 160), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(hole)

    def _draw_magnifier(self, p, a, v, t, bars) -> None:
        bars()
        rad = min(float(v["radius"]) / 2.2, a.height() / 2 - 2)
        c = a.center()
        clip = QPainterPath()
        clip.addEllipse(c, rad, rad)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2), QColor(t["surface"]))
        z = float(v["zoom"])
        p.translate(c)
        p.scale(z, z)
        p.translate(-c)
        bars()
        p.restore()
        p.setPen(QPen(QColor(t["accent"]), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, rad, rad)


class _ToolsPage(QWidget):
    """Tool list on the left, a generated option form (with live preview) on the right."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tool = ""
        self._controls: Dict[str, Tuple[tool_config.OptionDef, Callable[[object], None]]] = {}
        self._widgets: Dict[str, QWidget] = {}      # option key -> its control container
        self._pending: Dict[str, object] = {}
        self._committing = 0
        self._last_colour: Dict[Tuple[str, str], str] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.flush)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(8)
        self.tool_list = QListWidget()
        self.tool_list.setFixedWidth(190)
        self.tool_list.setIconSize(QSize(18, 18))
        self.tool_list.setSpacing(1)
        for tid, label in tool_config.TOOL_LABELS.items():
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, tid)
            ic = _icon(tid, 18)
            if ic is not None:
                it.setIcon(ic)
            it.setSizeHint(QSize(0, 34))
            self.tool_list.addItem(it)
        left.addWidget(self.tool_list, 1)
        self.btn_reset_all = _button("Reset all tools", "ghost", "refresh", "Restore every tool's settings to their defaults")
        self.btn_reset_all.clicked.connect(self.reset_all)
        left.addWidget(self.btn_reset_all)
        root.addLayout(left)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 8, 0)
        bv.setSpacing(10)
        self.card, cl = _card()
        head = QHBoxLayout()
        self.title = QLabel()
        _role(self.title, "h2")
        head.addWidget(self.title)
        self.badge = QLabel("Customised")
        _role(self.badge, "badge")
        head.addWidget(self.badge)
        head.addStretch(1)
        self.btn_reset_tool = _button("Reset this tool", "ghost", "refresh")
        self.btn_reset_tool.clicked.connect(self.reset_tool)
        head.addWidget(self.btn_reset_tool)
        cl.addLayout(head)
        self.preview = _ToolPreview()
        cl.addWidget(self.preview)
        self.empty = QLabel()
        self.empty.setWordWrap(True)
        _role(self.empty, "muted")
        cl.addWidget(self.empty)
        self.form = QVBoxLayout()
        self.form.setSpacing(12)
        cl.addLayout(self.form)
        bv.addWidget(self.card)
        bv.addStretch(1)
        root.addWidget(scroll, 1)

        self.tool_list.currentItemChanged.connect(
            lambda cur, _prev: cur is not None and self.select_tool(cur.data(Qt.ItemDataRole.UserRole)))
        tool_config.signals.changed.connect(self._on_external_change)

        first = next((t for t, o in tool_config.TOOL_OPTIONS.items() if o), "select")
        self.select_tool(first)
        self._refresh_list_fonts()
        theme.signals.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _name: str) -> None:
        self.preview.update()

    # ------------------------------------------------------------- selection

    def select_tool(self, tool: str) -> None:
        self.flush()
        self._tool = tool
        for i in range(self.tool_list.count()):
            if self.tool_list.item(i).data(Qt.ItemDataRole.UserRole) == tool:
                if self.tool_list.currentRow() != i:
                    self.tool_list.blockSignals(True)
                    self.tool_list.setCurrentRow(i)
                    self.tool_list.blockSignals(False)
                break
        self.title.setText(tool_config.TOOL_LABELS.get(tool, tool))
        self.preview.set_tool(tool)
        self._build_form(tool)
        self._update_state()

    def current_tool(self) -> str:
        return self._tool

    # ------------------------------------------------------------- form

    def _clear_form(self) -> None:
        self._controls = {}
        self._widgets = {}
        while self.form.count():
            item = self.form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _build_form(self, tool: str) -> None:
        self._clear_form()
        opts = tool_config.TOOL_OPTIONS.get(tool, [])
        if not opts:
            label = tool_config.TOOL_LABELS.get(tool, tool)
            self.empty.setText(f"Nothing to configure for {label}.")
            self.empty.show()
        else:
            self.empty.hide()
        for opt in opts:
            row = QWidget()
            col = QVBoxLayout(row)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(2)
            line = QHBoxLayout()
            line.setSpacing(12)
            if opt.kind != "bool":
                lbl = QLabel(opt.label)
                lbl.setFixedWidth(170)
                lbl.setFixedHeight(30)          # lines up with the first control row
                line.addWidget(lbl, 0, Qt.AlignmentFlag.AlignTop)
            ctl, setter = self._make_control(tool, opt)
            line.addWidget(ctl, 1)
            col.addLayout(line)
            if opt.help:
                cap = QLabel(opt.help)
                cap.setWordWrap(True)
                _role(cap, "caption")
                wrap = QHBoxLayout()
                wrap.setContentsMargins(182 if opt.kind != "bool" else 24, 0, 0, 0)
                wrap.addWidget(cap)
                col.addLayout(wrap)
            self.form.addWidget(row)
            self._controls[opt.key] = (opt, setter)
            self._widgets[opt.key] = ctl
            setter(tool_config.get(tool, opt.key))

    def _make_control(self, tool: str, opt) -> Tuple[QWidget, Callable[[object], None]]:
        kind = opt.kind
        if kind == "bool":
            cb = QCheckBox(opt.label)
            cb.toggled.connect(lambda on, k=opt.key: self._commit(k, bool(on)))

            def set_bool(v, cb=cb):
                cb.blockSignals(True)
                cb.setChecked(bool(v))
                cb.blockSignals(False)
            return cb, set_bool
        if kind in ("int", "float"):
            return self._make_number(opt)
        if kind in ("color", "color_opt"):
            return self._make_colour(tool, opt)
        if kind == "choice":
            combo = QComboBox()
            for ch in opt.choices:
                combo.addItem(_CHOICE_LABELS.get(ch, str(ch).replace("_", " ").title()), ch)
            combo.currentIndexChanged.connect(
                lambda _i, k=opt.key, c=combo: self._commit(k, c.currentData()))
            combo.setMaximumWidth(220)

            def set_choice(v, c=combo):
                c.blockSignals(True)
                idx = c.findData(v)
                c.setCurrentIndex(max(0, idx))
                c.blockSignals(False)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(combo)
            hl.addStretch(1)
            return holder, set_choice
        # text
        edit = QLineEdit()
        edit.editingFinished.connect(lambda k=opt.key, e=edit: self._commit(k, e.text()))
        edit.textEdited.connect(lambda text, k=opt.key: self._queue(k, text))
        holder = QWidget()
        vl = QVBoxLayout(holder)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(6)
        vl.addWidget(edit)
        if tool == "sticker" and opt.key == "glyph":
            egrid = QGridLayout()
            egrid.setSpacing(2)
            egrid.setContentsMargins(0, 0, 0, 0)
            for i, g in enumerate(_EMOJI):
                b = QToolButton()
                b.setText(g)
                b.setAutoRaise(True)
                b.setFixedSize(32, 30)
                b.setStyleSheet("font-size: 18px;")
                b.setToolTip(f"Use {g}")
                b.clicked.connect(lambda _=False, g=g, e=edit, k=opt.key: (e.setText(g), self._commit(k, g)))
                egrid.addWidget(b, i // 6, i % 6)
            egrid.setColumnStretch(6, 1)
            vl.addLayout(egrid)

        def set_text(v, e=edit):
            e.blockSignals(True)
            e.setText(str(v))
            e.blockSignals(False)
        return holder, set_text

    def _make_number(self, opt) -> Tuple[QWidget, Callable[[object], None]]:
        is_float = opt.kind == "float"
        scale = 10 if is_float else 1
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(round(opt.min * scale)), int(round(opt.max * scale)))
        if is_float:
            spin = QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setSingleStep(0.1)
        else:
            spin = QSpinBox()
        spin.setRange(opt.min, opt.max) if is_float else spin.setRange(int(opt.min), int(opt.max))
        spin.setFixedWidth(96)
        h.addWidget(slider, 1)
        h.addWidget(spin)

        def on_slider(v: int) -> None:
            val = v / scale if is_float else v
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
            self._queue(opt.key, val)

        def on_spin(val) -> None:
            slider.blockSignals(True)
            slider.setValue(int(round(val * scale)))
            slider.blockSignals(False)
            self._commit(opt.key, val)

        slider.valueChanged.connect(on_slider)
        spin.valueChanged.connect(on_spin)

        def setter(v, slider=slider, spin=spin) -> None:
            slider.blockSignals(True)
            spin.blockSignals(True)
            spin.setValue(float(v) if is_float else int(v))
            slider.setValue(int(round(float(v) * scale)))
            slider.blockSignals(False)
            spin.blockSignals(False)
        return box, setter

    def _make_colour(self, tool: str, opt) -> Tuple[QWidget, Callable[[object], None]]:
        optional = opt.kind == "color_opt"
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        check = QCheckBox("Use a fixed colour")
        swatch = QPushButton()
        swatch.setProperty("role", "swatch")
        swatch.setAutoDefault(False)
        swatch.setFixedSize(54, 28)
        swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        hexlabel = QLabel()
        _role(hexlabel, "muted")
        hexlabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        state = {"value": ""}

        def paint(value: str) -> None:
            state["value"] = value
            if value:
                swatch.setStyleSheet(
                    f"QPushButton {{ background: {value}; border: 1px solid rgba(128,128,128,0.7);"
                    f" border-radius: 6px; }} QPushButton:hover {{ border: 2px solid rgba(128,128,128,0.9); }}"
                )
                swatch.setToolTip(f"{value} — click to change")
                hexlabel.setText(value)
            else:
                swatch.setStyleSheet("")
                swatch.setToolTip("")
                hexlabel.setText("Palette colour")
            swatch.setVisible(bool(value) or not optional)
            hexlabel.setVisible(True)

        def pick() -> None:
            start = QColor(state["value"] or opt.default or "#FF4444")
            c = QColorDialog.getColor(start, self, f"{opt.label}")
            if c.isValid():
                value = c.name().upper()
                self._last_colour[(tool, opt.key)] = value
                self._commit(opt.key, value)

        def toggled(on: bool) -> None:
            if on:
                value = self._last_colour.get((tool, opt.key)) or str(opt.default or "") or "#FF4444"
                self._commit(opt.key, value)
            else:
                self._commit(opt.key, "")

        swatch.clicked.connect(pick)
        if optional:
            check.toggled.connect(toggled)
            h.addWidget(check)
        h.addWidget(swatch)
        h.addWidget(hexlabel, 1)

        def setter(v) -> None:
            v = str(v or "")
            if v:
                self._last_colour[(tool, opt.key)] = v
            if optional:
                check.blockSignals(True)
                check.setChecked(bool(v))
                check.blockSignals(False)
            paint(v)
        return box, setter

    # ------------------------------------------------------------- committing

    def _queue(self, key: str, value) -> None:
        self._pending[key] = value
        self._timer.start()

    def flush(self) -> None:
        """Write any slider/text values that are still waiting for the debounce."""
        self._timer.stop()
        for key, value in list(self._pending.items()):
            self._commit(key, value)

    def _commit(self, key: str, value) -> None:
        self._pending.pop(key, None)
        if not self._tool:
            return
        self._committing += 1
        try:
            tool_config.set(self._tool, key, value)
        except KeyError:
            return
        finally:
            self._committing -= 1
        entry = self._controls.get(key)
        if entry is not None and entry[0].kind in ("color", "color_opt"):
            entry[1](tool_config.get(self._tool, key))      # swatch / checkbox follow the stored value
        self._update_state()

    def _on_external_change(self, tool: str) -> None:
        if self._committing:
            return
        if tool in ("", self._tool) and self._tool:
            for key, (opt, setter) in self._controls.items():
                setter(tool_config.get(self._tool, key))
        self._update_state()

    def _update_state(self) -> None:
        tool = self._tool
        custom = bool(tool) and not tool_config.is_default(tool)
        self.badge.setVisible(custom)
        self.btn_reset_tool.setEnabled(custom)
        self.preview.update()
        self._refresh_list_fonts()

    def _refresh_list_fonts(self) -> None:
        for i in range(self.tool_list.count()):
            it = self.tool_list.item(i)
            tid = it.data(Qt.ItemDataRole.UserRole)
            custom = bool(tool_config.TOOL_OPTIONS.get(tid)) and not tool_config.is_default(tid)
            f = it.font()
            f.setBold(custom)
            it.setFont(f)
            it.setToolTip("Customised" if custom else "")

    # ------------------------------------------------------------- actions

    def reset_tool(self) -> None:
        if self._tool:
            self.flush()
            tool_config.reset_tool(self._tool)

    def reset_all(self) -> None:
        r = QMessageBox.question(
            self, "Reset all tools",
            "Restore every tool's settings (colours, widths, font sizes, …) to their defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Yes:
            self._pending.clear()
            self._timer.stop()
            tool_config.reset_all()


# ===========================================================================
#  3. Sensitive data
# ===========================================================================

class _SensitivePage(QWidget):
    """Embeds the saved-profile editor; edits are saved only when the user clicks Save."""

    APT_COMMAND = ("winget install UB-Mannheim.TesseractOCR" if sys.platform == "win32"
                   else "sudo apt install tesseract-ocr")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        intro, il = _card()
        row = QHBoxLayout()
        row.setSpacing(10)
        ic = QLabel()
        if icons is not None and icons.has_icon("shield"):
            ic.setPixmap(icons.pixmap("shield", 22))
        ic.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(ic)
        txt = QLabel(
            "Configure once; run it any time from <b>Edit › Run Sensitive-Data Scan</b> (Ctrl+Shift+F). "
            "Nothing is ever scanned or changed until you click Run, and by default you review every "
            "finding before it is applied.")
        txt.setWordWrap(True)
        txt.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(txt, 1)
        il.addLayout(row)
        root.addWidget(intro)

        self.warn_card, wl = _card()
        wrow = QHBoxLayout()
        wrow.setSpacing(10)
        wlabel = QLabel("<b>Tesseract OCR is not installed</b> — the scan needs it. Install it with:")
        wlabel.setTextFormat(Qt.TextFormat.RichText)
        _role(wlabel, "warning")
        wrow.addWidget(wlabel, 1)
        wl.addLayout(wrow)
        crow = QHBoxLayout()
        cmd = QLabel(self.APT_COMMAND)
        cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        cmd.setFont(mono)
        crow.addWidget(cmd, 1)
        self.btn_copy = _button("Copy command", "secondary", "copy")
        self.btn_copy.clicked.connect(self.copy_command)
        crow.addWidget(self.btn_copy)
        wl.addLayout(crow)
        root.addWidget(self.warn_card)

        self.editor = RedactConfigWidget(self)
        root.addWidget(self.editor, 1)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.dot = QLabel("●")
        _role(self.dot, "warning")
        self.status = QLabel()
        _role(self.status, "muted")
        foot.addWidget(self.dot)
        foot.addWidget(self.status, 1)
        self.btn_reset = _button("Reset to defaults", "ghost", "refresh",
                                 "Put the default settings in the form (then Save to keep them)")
        self.btn_save = _button("Save", "primary", "check")
        foot.addWidget(self.btn_reset)
        foot.addWidget(self.btn_save)
        root.addLayout(foot)

        self.btn_reset.clicked.connect(self.editor.reset_to_defaults)
        self.btn_save.clicked.connect(self.save)
        self.editor.changed.connect(self._update_state)
        redact_profile.profile_signals.changed.connect(self._on_profile_saved)
        self._saved_flash = False
        self._update_state()
        self.refresh_ocr_state()

    @staticmethod
    def _as_dict(p) -> dict:
        return dataclasses.asdict(p)

    def is_dirty(self) -> bool:
        return self._as_dict(self.editor.profile()) != self._as_dict(redact_profile.load_profile())

    def refresh_ocr_state(self) -> None:
        self.warn_card.setVisible(not redact.ocr_available())

    def copy_command(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.APT_COMMAND)
        self.btn_copy.setText("Copied ✓")
        QTimer.singleShot(1800, lambda: self.btn_copy.setText("Copy command"))

    def _update_state(self) -> None:
        errors = self.editor.has_errors()
        dirty = self.is_dirty()
        self.btn_save.setEnabled(dirty and not errors)
        self.dot.setVisible(dirty or errors)
        if errors:
            _role(self.status, "error")
            self.status.setText("Fix the invalid custom pattern before saving.")
        elif dirty:
            _role(self.status, "muted")
            self.status.setText("Unsaved changes — click Save to keep them.")
        elif self._saved_flash:
            _role(self.status, "success")
            self.status.setText("Saved ✓")
        else:
            _role(self.status, "muted")
            self.status.setText("Settings are saved.")

    def save(self) -> None:
        if self.editor.has_errors():
            return
        self.editor.save()
        self._saved_flash = True
        QTimer.singleShot(2500, self._clear_flash)
        self._update_state()

    def _clear_flash(self) -> None:
        self._saved_flash = False
        self._update_state()

    def _on_profile_saved(self) -> None:
        """The saved profile changed.  Our own Save leaves the form equal to what was
        saved (nothing to do); a save from elsewhere (e.g. Reset ALL) is followed."""
        if self.is_dirty():
            self.editor.set_profile(redact_profile.load_profile())
        self._update_state()


# ===========================================================================
#  4. General
# ===========================================================================

class _GeneralPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = True
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

        # -- Appearance --
        card, lay = _card("Appearance", "Choose how Crabture looks. “System” follows your desktop's light/dark setting.")
        row = QHBoxLayout()
        row.setSpacing(18)
        self.theme_group = QButtonGroup(self)
        self.theme_buttons: Dict[str, QRadioButton] = {}
        for key, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            rb = QRadioButton(label)
            self.theme_group.addButton(rb)
            self.theme_buttons[key] = rb
            rb.toggled.connect(lambda on, k=key: on and self._on_theme(k))
            row.addWidget(rb)
        row.addStretch(1)
        lay.addLayout(row)
        note = QLabel("Crabture always starts maximized.")
        _role(note, "caption")
        lay.addWidget(note)
        col.addWidget(card)

        # -- Reset everything --
        card, lay = _card("Reset",
                          "Restores shortcuts, tool settings, the sensitive-data profile and the capture settings. "
                          "Your theme choice is kept.")
        self.btn_reset_all = _button("Reset ALL settings to defaults", "danger", "refresh")
        self.btn_reset_all.clicked.connect(self.reset_everything)
        rrow = QHBoxLayout()
        rrow.addWidget(self.btn_reset_all)
        rrow.addStretch(1)
        lay.addLayout(rrow)
        col.addWidget(card)
        col.addStretch(1)

        self.reload()
        self._loading = False

    def reload(self) -> None:
        """Re-read stored values into the controls (without writing anything)."""
        was = self._loading
        self._loading = True
        pref = theme.initial_theme()
        self.theme_buttons.get(pref, self.theme_buttons["system"]).setChecked(True)
        self._loading = was

    def _on_theme(self, name: str) -> None:
        if not self._loading:
            theme.set_theme_preference(name)

    def reset_everything(self) -> None:
        r = QMessageBox.warning(
            self, "Reset all settings",
            "Reset ALL settings to their defaults?\n\n"
            "• keyboard shortcuts\n• tool settings\n• the sensitive-data profile\n• capture folder, behaviour and shortcuts\n\n"
            "Your theme choice is kept. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if r != QMessageBox.StandardButton.Yes:
            return
        keymap.reset_all()
        tool_config.reset_all()
        redact_profile.save_profile(redact_profile.default_profile())
        capture_config.reset_preferences()
        capture_config.reset_shortcuts()
        self.reload()
        settings_signals.capture_changed.emit()


# ===========================================================================
#  The dialog
# ===========================================================================

_TABS = (
    ("shortcuts", "Shortcuts", "keyboard"),
    ("tools", "Tools", "palette"),
    ("capture", "Capture", "capture"),
    ("sensitive", "Sensitive data", "shield"),
    ("general", "General", "settings"),
)


class SettingsDialog(QDialog):
    """Tabbed Configure window.  All edits apply live; ``Close`` just hides it."""

    GEOMETRY_KEY = "settings_dialog/geometry"

    def __init__(self, parent=None, initial_tab: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure — Crabture")
        self.setMinimumSize(760, 520)
        self.resize(900, 640)
        self.setSizeGripEnabled(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("dialog_header")
        hv = QVBoxLayout(header)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(2)
        title = QLabel("Configure")
        _role(title, "h1")
        sub = QLabel("Shortcuts, tool defaults, screenshot capture, the sensitive-data scan and general preferences. "
                     "Changes apply immediately.")
        sub.setWordWrap(True)
        _role(sub, "muted")
        hv.addWidget(title)
        hv.addWidget(sub)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(16, 14, 16, 10)
        body.setSpacing(16)
        root.addLayout(body, 1)

        self.nav = QListWidget()
        self.nav.setObjectName("settings_nav")
        self.nav.setFixedWidth(180)
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setSpacing(2)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.nav)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        self.shortcuts_page = _ShortcutsPage()
        self.tools_page = _ToolsPage()
        self.sensitive_page = _SensitivePage()
        self.general_page = _GeneralPage()
        from crabture.settings.capture_page import CapturePage     # imports helpers from this module
        self.capture_page = CapturePage()
        self._pages = {
            "shortcuts": self.shortcuts_page,
            "tools": self.tools_page,
            "capture": self.capture_page,
            "sensitive": self.sensitive_page,
            "general": self.general_page,
        }
        for key, label, icon_name in _TABS:
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            ic = _icon(icon_name, 20)
            if ic is not None:
                it.setIcon(ic)
            it.setSizeHint(QSize(0, 40))
            self.nav.addItem(it)
            self.stack.addWidget(self._pages[key])
        self.nav.currentRowChanged.connect(self._on_nav)

        foot = QHBoxLayout()
        foot.setContentsMargins(16, 0, 16, 14)
        foot.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.close)
        foot.addWidget(self.close_btn)
        root.addLayout(foot)

        self.set_tab(initial_tab if initial_tab in self._pages else "shortcuts")

        geo = QSettings("YawnByte", "Crabture").value(self.GEOMETRY_KEY)
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:  # noqa: BLE001 - a bad saved value must not break the dialog
                pass

    # ------------------------------------------------------------- navigation

    def set_tab(self, name: str) -> None:
        keys = [k for k, _l, _i in _TABS]
        if name in keys:
            self.nav.setCurrentRow(keys.index(name))

    def current_tab(self) -> str:
        return _TABS[max(0, self.nav.currentRow())][0]

    def show_tool(self, tool: str) -> None:
        """Jump to Tools ▸ *tool* (used by the Properties panel's configure button)."""
        self.set_tab("tools")
        if tool in tool_config.TOOL_LABELS:
            self.tools_page.select_tool(tool)

    def focus_capture_folder(self) -> None:
        """Jump to Capture and put the cursor in the screenshot-folder field."""
        self.set_tab("capture")
        self.capture_page.folder.setFocus()
        self.capture_page.folder.selectAll()

    def _on_nav(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        key = _TABS[row][0]
        if key == "sensitive":
            self.sensitive_page.refresh_ocr_state()
        elif key == "general":
            self.general_page.reload()
        elif key == "capture":
            self.capture_page.refresh_status()

    # ------------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.general_page.reload()
        self.sensitive_page.refresh_ocr_state()

    def done(self, result: int) -> None:  # noqa: D401 - Qt override
        self._finish()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._finish()
        super().closeEvent(event)

    def _finish(self) -> None:
        self.tools_page.flush()
        self.shortcuts_page.cancel_edit()
        qs = QSettings("YawnByte", "Crabture")
        qs.setValue(self.GEOMETRY_KEY, self.saveGeometry())
        qs.sync()
