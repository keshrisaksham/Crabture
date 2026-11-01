"""
ui/theme.py — design tokens, stylesheet generator and theme switching for Crabture.

One stylesheet template is filled from a small token dictionary, giving a
matching *light* and *dark* theme.  Public API::

    apply_theme(app, name)        name: "light" | "dark" | "system"
    current_theme() -> str        resolved "light" / "dark"
    tokens(name=None) -> dict     colour tokens of `name` (default: current)
    initial_theme() -> str        saved preference ("system" if none)
    set_theme_preference(name)    save the preference and apply it
    signals.changed(str)          emitted after every apply (resolved name)
    contrast_ratio(a, b) -> float WCAG contrast of two colours

Tokens (all "#RRGGBB"): bg, surface, surface2, surface3, border,
border_strong, text, text_muted, text_disabled, accent (fill),
accent_hover, accent_pressed, accent_text (text on an accent fill),
accent_fg (accent used as text/border on surfaces), accent_soft (tinted
background), hover, pressed, danger, danger_soft, success, warning,
canvas_bg, checker_a, checker_b, selection, handle, tooltip_bg,
tooltip_text.

STYLING CONVENTIONS (other modules code against these)
------------------------------------------------------
Dynamic property ``role`` — remember that Qt only re-evaluates a stylesheet
when a property changes *after* polish, so call
``widget.style().unpolish(widget); widget.style().polish(widget)`` if you
change it at runtime.

QPushButton / QToolButton
    (none) / 'secondary'  surface fill + border (QToolButton without a role is flat)
    'primary'   accent fill, white text      'ghost'   transparent until hover
    'danger'    red outline, filled on hover 'toolbar' compact 32px icon button
    'tool'      40px checkable rail button (checked = accent fill, white icon)
    'chip'      small checkable pill
    A QPushButton that is the dialog's *default* button automatically looks primary.
QLabel
    'h1' 20px semibold   'h2' 15px semibold   'muted'   'caption' 11px muted
    'badge' small accent pill   'success' / 'warning' / 'error' coloured text
QFrame
    'card' (surface2, border, 12px radius, 12px padding)
    'panel' (surface, no radius)  'divider' (1px horizontal line)
    'vdivider' (1px vertical line)
objectName
    main_toolbar   top bar (bottom border)       tool_rail     vertical left rail
    side_panel     right/left panel              status_bar_widget  transparent
    welcome        empty-state page              canvas_surround    area around the image
    gallery        thumbnail QListWidget         layer_list    layer rows
    dialog_header  header strip inside dialogs
    NOTE: a plain QWidget does not paint a stylesheet background; use a QFrame
    (or set Qt.WA_StyledBackground) for tool_rail / side_panel / welcome / ...
Swatch buttons (QPushButton[role="swatch"]) are styled by their owners.
Popup windows (QMenu, QToolTip, combo lists) use square outer corners: Qt
cannot round a top-level popup without translucency, so this avoids corner
artefacts.  The ``image: url()`` assets (arrows, check marks) are generated
as PNGs in a cache directory the first time a theme is applied.
"""

from __future__ import annotations

import os
import tempfile
from string import Template
from typing import Dict, Optional

from crabture import osinfo

from PyQt6.QtCore import QObject, QSettings, QStandardPaths, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PyQt6.QtWidgets import QApplication

__all__ = [
    "apply_theme", "current_theme", "tokens", "initial_theme",
    "set_theme_preference", "signals", "contrast_ratio", "build_stylesheet",
]

# ---------------------------------------------------------------------------
#  Tokens
# ---------------------------------------------------------------------------

_LIGHT: Dict[str, str] = {
    "bg": "#F3F4F8",
    "surface": "#FFFFFF",
    "surface2": "#F8F9FC",
    "surface3": "#EEF0F6",
    "border": "#DADDE6",
    "border_strong": "#BFC5D4",
    "text": "#1A1D29",
    "text_muted": "#566074",
    "text_disabled": "#9AA0B2",
    "accent": "#4A63E7",
    "accent_hover": "#3D55D6",
    "accent_pressed": "#3346BC",
    "accent_text": "#FFFFFF",
    "accent_fg": "#3A52D4",
    "accent_soft": "#E6EAFD",
    "hover": "#E9ECF4",
    "pressed": "#DDE1EC",
    "danger": "#C62F3E",
    "danger_soft": "#FCE8EA",
    "success": "#17803D",
    "warning": "#965800",
    "canvas_bg": "#E3E6EE",
    "checker_a": "#FFFFFF",
    "checker_b": "#E8EBF2",
    "selection": "#4A63E7",
    "handle": "#FFFFFF",
    "tooltip_bg": "#1F2330",
    "tooltip_text": "#F2F4F8",
}

_DARK: Dict[str, str] = {
    "bg": "#14161B",
    "surface": "#1C1F26",
    "surface2": "#232730",
    "surface3": "#2A2F3A",
    "border": "#303542",
    "border_strong": "#454C5F",
    "text": "#E8EAF0",
    "text_muted": "#9BA3B6",
    "text_disabled": "#5F6678",
    "accent": "#4B5FDC",
    "accent_hover": "#5265E4",
    "accent_pressed": "#3E50C6",
    "accent_text": "#FFFFFF",
    "accent_fg": "#93A4FF",
    "accent_soft": "#262E57",
    "hover": "#2B303C",
    "pressed": "#343A48",
    "danger": "#FF7A86",
    "danger_soft": "#3A1F24",
    "success": "#4CC27A",
    "warning": "#E8A93C",
    "canvas_bg": "#101216",
    "checker_a": "#2A2E37",
    "checker_b": "#23262E",
    "selection": "#4B5FDC",
    "handle": "#FFFFFF",
    "tooltip_bg": "#E8EAF0",
    "tooltip_text": "#14161B",
}

_TOKENS = {"light": _LIGHT, "dark": _DARK}
_VALID_PREFS = ("light", "dark", "system")
_FONT_STACK = ("Inter", "Cantarell", "Noto Sans", "Ubuntu", "DejaVu Sans")


class ThemeSignals(QObject):
    """``changed(str)`` fires after every apply with the resolved theme name."""
    changed = pyqtSignal(str)


signals = ThemeSignals()

_state = {"pref": "system", "resolved": "light", "system_palette": None,
          "connected": False, "applying": False, "style_set": False}


def tokens(name: Optional[str] = None) -> Dict[str, str]:
    """Colour tokens for *name* ('light'/'dark'); default: the current theme."""
    key = name if name in _TOKENS else _state["resolved"]
    return dict(_TOKENS[key])


def current_theme() -> str:
    return _state["resolved"]


# ---------------------------------------------------------------------------
#  Contrast helper
# ---------------------------------------------------------------------------

def _luminance(c: str) -> float:
    col = QColor(c)

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(col.redF()) + 0.7152 * lin(col.greenF()) + 0.0722 * lin(col.blueF())


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 contrast ratio between two colours (1.0 – 21.0)."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
#  Generated image assets (arrows, check marks …) referenced by the QSS
# ---------------------------------------------------------------------------

def _asset_dir(theme: str) -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    candidates = []
    if base:
        candidates.append(os.path.join(base, "theme-assets"))
    candidates.append(os.path.join(tempfile.gettempdir(), f"crabture-theme-{osinfo.user_id()}"))
    for root in candidates:
        path = os.path.join(root, theme)
        try:
            os.makedirs(path, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except OSError:
            continue
    return tempfile.mkdtemp(prefix="crabture-theme-")


def _draw_asset(kind: str, color: str, px: int) -> QImage:
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = px / 24.0
    pen = QPen(QColor(color), 2.6 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()

    def pt(x, y):
        return x * s, y * s

    if kind == "chevron_down":
        path.moveTo(*pt(6, 9)); path.lineTo(*pt(12, 15)); path.lineTo(*pt(18, 9))
    elif kind == "chevron_up":
        path.moveTo(*pt(6, 15)); path.lineTo(*pt(12, 9)); path.lineTo(*pt(18, 15))
    elif kind == "chevron_right":
        path.moveTo(*pt(9, 6)); path.lineTo(*pt(15, 12)); path.lineTo(*pt(9, 18))
    elif kind == "check":
        path.moveTo(*pt(5.5, 12.5)); path.lineTo(*pt(10, 17)); path.lineTo(*pt(18.5, 7.5))
    elif kind == "dash":
        path.moveTo(*pt(6, 12)); path.lineTo(*pt(18, 12))
    elif kind == "close":
        path.moveTo(*pt(7, 7)); path.lineTo(*pt(17, 17))
        path.moveTo(*pt(17, 7)); path.lineTo(*pt(7, 17))
    elif kind == "float":
        path.addRoundedRect(7 * s, 5 * s, 10 * s, 10 * s, 1.5 * s, 1.5 * s)
        path.moveTo(*pt(5, 9)); path.lineTo(*pt(5, 19)); path.lineTo(*pt(15, 19))
    elif kind == "dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(int(8 * s), int(8 * s), int(8 * s), int(8 * s))
        p.end()
        return img
    p.drawPath(path)
    p.end()
    return img


def _ensure_assets(name: str, t: Dict[str, str]) -> Dict[str, str]:
    """Create the PNGs for theme *name*; return {key: forward-slash path}."""
    folder = _asset_dir(name)
    specs = {
        "chevron_down": t["text_muted"], "chevron_up": t["text_muted"],
        "chevron_right": t["text_muted"], "check": t["accent_text"],
        "check_text": t["text"], "dash": t["accent_text"],
        "dot": t["accent_text"], "close": t["text_muted"], "float": t["text_muted"],
    }
    out: Dict[str, str] = {}
    for key, color in specs.items():
        kind = "check" if key == "check_text" else key
        size = 14 if kind in ("check", "dot", "dash") else 12
        path1 = os.path.join(folder, f"{key}.png")
        path2 = os.path.join(folder, f"{key}@2x.png")
        try:
            _draw_asset(kind, color, size).save(path1)
            _draw_asset(kind, color, size * 2).save(path2)
        except Exception:
            pass
        out[key] = path1.replace("\\", "/")
    return out


# ---------------------------------------------------------------------------
#  Stylesheet
# ---------------------------------------------------------------------------

_QSS = Template(r"""
/* ── Base ─────────────────────────────────────────────────────────── */
QWidget { color: $text; }
QWidget:disabled { color: $text_disabled; }
QMainWindow, QDialog { background: $bg; }
QLabel { background: transparent; }
QMainWindow::separator { background: $bg; width: 6px; height: 6px; }
QMainWindow::separator:hover { background: $border_strong; }
QAbstractScrollArea::corner { background: transparent; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ── Buttons: base + default ──────────────────────────────────────── */
QPushButton {
    background: $surface; color: $text;
    border: 1px solid $border_strong; border-radius: 8px;
    padding: 6px 14px; min-height: 20px;
}
QPushButton:hover { background: $hover; }
QPushButton:pressed { background: $pressed; }
QPushButton:focus { border-color: $accent; }
QPushButton:checked { background: $accent_soft; border-color: $accent_fg; color: $accent_fg; }
QPushButton:disabled { background: $surface2; color: $text_disabled; border-color: $border; }
QPushButton:default { background: $accent; color: $accent_text; border-color: $accent; }
QPushButton:default:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton:default:pressed { background: $accent_pressed; }
QPushButton::menu-indicator {
    image: url("$chevron_down"); width: 10px; height: 10px;
    subcontrol-position: right center; subcontrol-origin: padding; right: 8px;
}
QDialogButtonBox QPushButton { min-width: 80px; }

QToolButton {
    background: transparent; color: $text; border: 1px solid transparent;
    border-radius: 8px; padding: 6px; min-height: 20px;
}
QToolButton:hover { background: $hover; }
QToolButton:pressed { background: $pressed; }
QToolButton:checked { background: $accent_soft; color: $accent_fg; }
QToolButton:disabled { color: $text_disabled; }
QToolButton::menu-indicator {
    image: url("$chevron_down"); width: 8px; height: 8px;
    subcontrol-position: right bottom; subcontrol-origin: padding; right: 3px; bottom: 3px;
}
QToolButton::menu-button { border: none; width: 16px; }
QToolButton[popupMode="1"] { padding-right: 14px; }

/* ── Button roles ─────────────────────────────────────────────────── */
QPushButton[role="secondary"], QToolButton[role="secondary"] {
    background: $surface; border: 1px solid $border_strong; border-radius: 8px; padding: 6px 14px;
}
QPushButton[role="secondary"]:hover, QToolButton[role="secondary"]:hover { background: $hover; }
QPushButton[role="secondary"]:pressed, QToolButton[role="secondary"]:pressed { background: $pressed; }

QPushButton[role="primary"], QToolButton[role="primary"] {
    background: $accent; color: $accent_text; border: 1px solid $accent;
    border-radius: 8px; padding: 6px 14px; font-weight: 600;
}
QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[role="primary"]:pressed, QToolButton[role="primary"]:pressed { background: $accent_pressed; border-color: $accent_pressed; }
QPushButton[role="primary"]:disabled, QToolButton[role="primary"]:disabled {
    background: $border; color: $text_disabled; border-color: $border;
}

QPushButton[role="ghost"], QToolButton[role="ghost"] {
    background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px 12px;
}
/* Text ghost buttons read as quiet links (accent text) so they stay visible without a fill. */
QPushButton[role="ghost"] { color: $accent_fg; }
QPushButton[role="ghost"]:disabled { color: $text_disabled; }
QPushButton[role="ghost"]:hover, QToolButton[role="ghost"]:hover { background: $hover; }
QPushButton[role="ghost"]:pressed, QToolButton[role="ghost"]:pressed { background: $pressed; }
QPushButton[role="ghost"]:checked, QToolButton[role="ghost"]:checked { background: $accent_soft; color: $accent_fg; }

QPushButton[role="danger"], QToolButton[role="danger"] {
    background: transparent; color: $danger; border: 1px solid $danger;
    border-radius: 8px; padding: 6px 14px;
}
QPushButton[role="danger"]:hover, QToolButton[role="danger"]:hover { background: $danger; color: $surface; }
QPushButton[role="danger"]:pressed, QToolButton[role="danger"]:pressed { background: $danger; color: $surface; }
QPushButton[role="danger"]:disabled, QToolButton[role="danger"]:disabled { color: $text_disabled; border-color: $border; background: transparent; }

QPushButton[role="toolbar"], QToolButton[role="toolbar"] {
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 0; min-width: 32px; min-height: 32px;
}
QPushButton[role="toolbar"]:hover, QToolButton[role="toolbar"]:hover { background: $hover; }
QPushButton[role="toolbar"]:pressed, QToolButton[role="toolbar"]:pressed { background: $pressed; }
QPushButton[role="toolbar"]:checked, QToolButton[role="toolbar"]:checked { background: $accent_soft; color: $accent_fg; }
QPushButton[role="toolbar"]:disabled, QToolButton[role="toolbar"]:disabled { background: transparent; border-color: transparent; }

QPushButton[role="tool"], QToolButton[role="tool"] {
    background: transparent; border: 1px solid transparent; border-radius: 10px;
    padding: 0; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px;
}
QPushButton[role="tool"]:hover, QToolButton[role="tool"]:hover { background: $hover; }
QPushButton[role="tool"]:pressed, QToolButton[role="tool"]:pressed { background: $pressed; }
QPushButton[role="tool"]:checked, QToolButton[role="tool"]:checked {
    background: $accent; border-color: $accent; color: $accent_text;
}
QPushButton[role="tool"]:checked:hover, QToolButton[role="tool"]:checked:hover { background: $accent_hover; }
QPushButton[role="tool"]:disabled, QToolButton[role="tool"]:disabled { background: transparent; border-color: transparent; }

QPushButton[role="chip"], QToolButton[role="chip"] {
    background: $surface; border: 1px solid $border_strong; border-radius: 14px;
    padding: 4px 12px; min-height: 20px;
}
QPushButton[role="chip"]:hover, QToolButton[role="chip"]:hover { background: $hover; }
QPushButton[role="chip"]:checked, QToolButton[role="chip"]:checked {
    background: $accent_soft; border-color: $accent_fg; color: $accent_fg; font-weight: 600;
}

/* ── Text inputs ──────────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: $surface; color: $text;
    border: 1px solid $border_strong; border-radius: 8px;
    padding: 5px 10px; min-height: 20px;
    selection-background-color: $accent; selection-color: $accent_text;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: $text_disabled; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color: $accent; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QPlainTextEdit:disabled, QTextEdit:disabled { background: $surface2; color: $text_disabled; border-color: $border; }
QLineEdit[readOnly="true"] { background: $surface2; }

QSpinBox, QDoubleSpinBox { padding-right: 26px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right;
    width: 22px; border: none; background: transparent; margin: 3px 3px 0 0; border-top-right-radius: 5px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 22px; border: none; background: transparent; margin: 0 3px 3px 0; border-bottom-right-radius: 5px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: $hover; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("$chevron_up"); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("$chevron_down"); width: 10px; height: 10px; }

QComboBox { padding-right: 28px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; }
QComboBox::down-arrow { image: url("$chevron_down"); width: 10px; height: 10px; }
QComboBox QAbstractItemView {
    background: $surface; color: $text; border: 1px solid $border_strong; border-radius: 0;
    padding: 4px; outline: 0; selection-background-color: $accent_soft; selection-color: $text;
}
QComboBox QAbstractItemView::item { padding: 6px 10px; border-radius: 6px; min-height: 22px; }
QComboBox QAbstractItemView::item:hover { background: $hover; }
QComboBox QAbstractItemView::item:selected { background: $accent_soft; color: $text; }

/* ── Check / radio ────────────────────────────────────────────────── */
QCheckBox, QRadioButton { spacing: 8px; background: transparent; }
QCheckBox::indicator, QGroupBox::indicator {
    width: 16px; height: 16px; border: 1px solid $border_strong; border-radius: 4px; background: $surface;
}
QCheckBox::indicator:hover, QGroupBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked {
    background: $accent; border-color: $accent; image: url("$check");
}
QCheckBox::indicator:indeterminate { background: $accent; border-color: $accent; image: url("$dash"); }
QCheckBox::indicator:disabled, QGroupBox::indicator:disabled { background: $surface3; border-color: $border; }
QRadioButton::indicator {
    width: 16px; height: 16px; border: 1px solid $border_strong; border-radius: 9px; background: $surface;
}
QRadioButton::indicator:hover { border-color: $accent; }
QRadioButton::indicator:checked { background: $accent; border-color: $accent; image: url("$dot"); }
QRadioButton::indicator:disabled { background: $surface3; border-color: $border; }

/* ── Slider / progress ────────────────────────────────────────────── */
QSlider::groove:horizontal { background: $border_strong; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal {
    background: $handle; border: 2px solid $accent; width: 12px; height: 12px;
    margin: -7px 0; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: $accent_soft; }
QSlider::handle:horizontal:pressed { background: $accent; }
QSlider::groove:vertical { background: $border_strong; width: 4px; border-radius: 2px; }
QSlider::add-page:vertical { background: $accent; width: 4px; border-radius: 2px; }
QSlider::handle:vertical {
    background: $handle; border: 2px solid $accent; width: 12px; height: 12px;
    margin: 0 -7px; border-radius: 8px;
}
QSlider::groove:horizontal:disabled, QSlider::groove:vertical:disabled { background: $border; }
QSlider::handle:disabled { border-color: $border_strong; }
QSlider::sub-page:horizontal:disabled { background: $border_strong; }

QProgressBar {
    background: $border; border: none; border-radius: 4px; min-height: 8px; max-height: 8px;
    color: transparent; qproperty-textVisible: false;
}
QProgressBar::chunk { background: $accent; border-radius: 4px; }

/* ── Tabs / groups ────────────────────────────────────────────────── */
QTabWidget::pane { border: 1px solid $border; border-radius: 10px; background: $surface; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent; color: $text_muted; padding: 8px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $accent_fg; border-bottom: 2px solid $accent; font-weight: 600; }
QTabBar::tab:disabled { color: $text_disabled; }

QGroupBox {
    background: transparent; border: 1px solid $border; border-radius: 10px;
    margin-top: 14px; padding: 14px 10px 10px 10px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px;
    color: $text_muted; font-weight: 600;
}

/* ── Scrollbars ───────────────────────────────────────────────────── */
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 4px; min-height: 28px; margin: 0 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 4px; min-width: 28px; margin: 2px 0; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: $text_disabled; }
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed { background: $text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ── Item views ───────────────────────────────────────────────────── */
QListView, QTreeView, QTableView, QListWidget, QTreeWidget, QTableWidget {
    background: $surface; color: $text; border: 1px solid $border; border-radius: 10px;
    outline: 0; alternate-background-color: $surface2; gridline-color: $border;
    selection-background-color: $accent_soft; selection-color: $text;
}
QListView::item, QListWidget::item { padding: 6px 8px; border-radius: 6px; }
QListView::item:hover, QListWidget::item:hover { background: $hover; }
QListView::item:selected, QListWidget::item:selected { background: $accent_soft; color: $text; }
QTreeView::item, QTreeWidget::item { padding: 4px 6px; }
QTreeView::item:hover { background: $hover; }
QTreeView::item:selected { background: $accent_soft; color: $text; }
QTableView::item { padding: 4px 8px; }
QTableView::item:selected { background: $accent_soft; color: $text; }
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings { image: url("$chevron_right"); }
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings { image: url("$chevron_down"); }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: $surface2; color: $text_muted; border: none; border-bottom: 1px solid $border;
    padding: 6px 10px; font-weight: 600;
}
QTableCornerButton::section { background: $surface2; border: none; border-bottom: 1px solid $border; }

QListWidget#gallery { background: $bg; border: none; border-radius: 0; padding: 8px; }
QListWidget#gallery::item {
    background: $surface; color: $text; border: 1px solid $border; border-radius: 10px;
    padding: 8px; margin: 4px;
}
QListWidget#gallery::item:hover { border-color: $border_strong; background: $surface; }
QListWidget#gallery::item:selected { border: 2px solid $accent; background: $accent_soft; color: $text; }
QListWidget#layer_list { background: transparent; border: none; border-radius: 0; }
QListWidget#layer_list::item { padding: 8px 10px; border-radius: 8px; margin: 1px 4px; }
QListWidget#layer_list::item:hover { background: $hover; }
QListWidget#layer_list::item:selected { background: $accent_soft; color: $text; }

/* ── Splitter / docks ─────────────────────────────────────────────── */
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QSplitter::handle:hover { background: $border_strong; }

QDockWidget {
    titlebar-close-icon: url("$close"); titlebar-normal-icon: url("$float"); font-weight: 600;
}
QDockWidget::title {
    background: $surface; padding: 8px 12px; border-bottom: 1px solid $border; text-align: left;
}
QDockWidget::close-button, QDockWidget::float-button {
    border: none; background: transparent; border-radius: 4px; padding: 2px;
}
QDockWidget::close-button:hover, QDockWidget::float-button:hover { background: $hover; }

/* ── Menus ────────────────────────────────────────────────────────── */
QMenuBar { background: $surface; border-bottom: 1px solid $border; padding: 2px 8px; }
QMenuBar::item { background: transparent; padding: 6px 10px; border-radius: 6px; }
QMenuBar::item:selected { background: $hover; }
QMenuBar::item:pressed { background: $accent_soft; color: $accent_fg; }
QMenu { background: $surface; color: $text; border: 1px solid $border_strong; padding: 6px; }
QMenu::item { padding: 6px 28px 6px 10px; border-radius: 6px; margin: 1px 0; }
QMenu::item:selected { background: $accent_soft; color: $text; }
QMenu::item:disabled { color: $text_disabled; background: transparent; }
QMenu::icon { padding-left: 4px; }
QMenu::separator { height: 1px; background: $border; margin: 6px 8px; }
QMenu::indicator { width: 14px; height: 14px; left: 6px; }
QMenu::indicator:checked { image: url("$check_text"); }
QMenu::right-arrow { image: url("$chevron_right"); width: 10px; height: 10px; margin-right: 8px; }

/* ── Toolbars / rails / panels ────────────────────────────────────── */
QToolBar { background: $surface; border: none; spacing: 4px; padding: 4px; }
QToolBar::handle { image: none; width: 0; }
QToolBar::separator { background: $border; width: 1px; height: 1px; margin: 6px; }
QToolBar#main_toolbar { border-bottom: 1px solid $border; padding: 6px 10px; spacing: 6px; }
QFrame#tool_rail, QToolBar#tool_rail, QWidget#tool_rail {
    background: $surface; border: none; border-right: 1px solid $border;
}
QToolBar#tool_rail { padding: 8px 6px; spacing: 4px; }
QFrame#side_panel, QWidget#side_panel { background: $surface; border: none; border-left: 1px solid $border; }
QWidget#status_bar_widget, QFrame#status_bar_widget { background: transparent; border: none; }
QFrame#welcome, QWidget#welcome { background: $bg; border: none; }
QFrame#canvas_surround, QWidget#canvas_surround { background: $canvas_bg; border: none; }
QFrame#dialog_header, QWidget#dialog_header {
    background: $surface2; border: none; border-bottom: 1px solid $border; padding: 12px 16px;
}
QGraphicsView { background: $canvas_bg; border: none; }

QStatusBar { background: $surface; border-top: 1px solid $border; color: $text_muted; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $text_muted; }

/* ── Frames & labels by role ──────────────────────────────────────── */
QFrame[role="card"] {
    background: $surface2; border: 1px solid $border; border-radius: 12px; padding: 12px;
}
QFrame[role="panel"] { background: $surface; border: none; }
QFrame[role="divider"] { background: $border; border: none; min-height: 1px; max-height: 1px; }
QFrame[role="vdivider"] { background: $border; border: none; min-width: 1px; max-width: 1px; }
QLabel[role="h1"] { font-size: 20px; font-weight: 600; }
QLabel[role="h2"] { font-size: 15px; font-weight: 600; }
QLabel[role="muted"] { color: $text_muted; }
QLabel[role="caption"] { color: $text_muted; font-size: 11px; }
QLabel[role="badge"] {
    background: $accent_soft; color: $accent_fg; border-radius: 9px;
    padding: 2px 8px; font-size: 11px; font-weight: 600;
}
QLabel[role="success"] { color: $success; }
QLabel[role="warning"] { color: $warning; }
QLabel[role="error"] { color: $danger; }

/* ── Tooltips / dialogs ───────────────────────────────────────────── */
QToolTip {
    background: $tooltip_bg; color: $tooltip_text; border: 1px solid $tooltip_bg;
    padding: 6px 8px; font-size: 12px;
}
QMessageBox, QInputDialog, QColorDialog, QFileDialog { background: $bg; }
QMessageBox QLabel { color: $text; }
QColorDialog QLabel, QFileDialog QLabel { background: transparent; }
""")


def build_stylesheet(t: Dict[str, str], assets: Optional[Dict[str, str]] = None) -> str:
    """Fill the stylesheet template with tokens (and asset paths)."""
    mapping = dict(t)
    mapping.update(assets or {})
    return _QSS.safe_substitute(mapping)


# ---------------------------------------------------------------------------
#  Palette / font / apply
# ---------------------------------------------------------------------------

def _palette(t: Dict[str, str]) -> QPalette:
    pal = QPalette()
    R = QPalette.ColorRole
    G = QPalette.ColorGroup
    pairs = {
        R.Window: t["bg"], R.WindowText: t["text"], R.Base: t["surface"],
        R.AlternateBase: t["surface2"], R.Text: t["text"], R.Button: t["surface"],
        R.ButtonText: t["text"], R.ToolTipBase: t["tooltip_bg"], R.ToolTipText: t["tooltip_text"],
        R.Highlight: t["selection"], R.HighlightedText: t["accent_text"],
        R.PlaceholderText: t["text_muted"], R.Link: t["accent_fg"],
        R.LinkVisited: t["accent_fg"], R.BrightText: t["accent_text"],
        R.Light: t["surface"], R.Midlight: t["surface2"], R.Mid: t["border"],
        R.Dark: t["border_strong"], R.Shadow: "#000000",
    }
    for role, color in pairs.items():
        pal.setColor(role, QColor(color))
    for role in (R.WindowText, R.Text, R.ButtonText):
        pal.setColor(G.Disabled, role, QColor(t["text_disabled"]))
    return pal


def _pick_font() -> QFont:
    families = set(QFontDatabase.families())
    font = QFont()
    for fam in _FONT_STACK:
        if fam in families:
            font.setFamily(fam)
            break
    font.setPixelSize(13)
    return font


def _system_is_dark(app: QApplication) -> bool:
    hints = app.styleHints()
    try:
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:
        pass
    pal = _state["system_palette"] or app.palette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def _resolve(app: QApplication, name: str) -> str:
    if name in ("light", "dark"):
        return name
    return "dark" if _system_is_dark(app) else "light"


def _on_scheme_changed(*_args) -> None:
    app = QApplication.instance()
    if app is not None and _state["pref"] == "system" and not _state["applying"]:
        apply_theme(app, "system")


def apply_theme(app: QApplication, name: str = "system") -> None:
    """Apply 'light', 'dark' or 'system' to *app* (palette, font, stylesheet).

    Unknown names (e.g. the removed 'glass' theme) fall back to 'light'.
    """
    if _state["system_palette"] is None:
        _state["system_palette"] = QPalette(app.palette())
    pref = name if name in _VALID_PREFS else "light"
    _state["applying"] = True
    try:
        resolved = _resolve(app, pref)
        t = _TOKENS[resolved]
        if not _state["style_set"]:
            # With a stylesheet active app.style() is a proxy with no name,
            # so remember that Fusion was already installed.
            app.setStyle("Fusion")
            _state["style_set"] = True
        app.setPalette(_palette(t))
        app.setFont(_pick_font())
        app.setStyleSheet(build_stylesheet(t, _ensure_assets(resolved, t)))
        _state["pref"] = pref
        _state["resolved"] = resolved
    finally:
        _state["applying"] = False
    if not _state["connected"]:
        try:
            app.styleHints().colorSchemeChanged.connect(_on_scheme_changed)
        except AttributeError:
            pass
        _state["connected"] = True
    signals.changed.emit(resolved)


def initial_theme() -> str:
    """Saved preference ('light' / 'dark' / 'system'); default 'system'."""
    value = str(QSettings("YawnByte", "Crabture").value("ui/theme", "system") or "system")
    return value if value in _VALID_PREFS else "system"


def set_theme_preference(name: str) -> None:
    """Persist *name* and apply it to the running application."""
    pref = name if name in _VALID_PREFS else "system"
    QSettings("YawnByte", "Crabture").setValue("ui/theme", pref)
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, pref)
