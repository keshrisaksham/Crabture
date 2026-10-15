"""
settings/tool_config.py — per-tool default settings for Crabture.

Every drawing tool can have saved defaults (colour, opacity, stroke width, font
size, blur strength, …).  The canvas reads them when it creates an item and
when a tool is selected, and the Settings dialog edits them through the API
below.  Values live in QSettings('YawnByte', 'Crabture') under group ``tools``
as ``tools/<tool>/<option>``.

Option kinds
------------
``color_opt``  hex colour or ``''`` – empty means "use the palette colour"
``color``      hex colour (never empty)
``int`` / ``float``  numbers clamped to ``min`` .. ``max``
``bool``       checkbox
``choice``     one of ``choices``
``text``       free text

Note: a fixed ``color_opt`` colour is applied (together with the tool's
``alpha`` option, if it has one) when the tool is selected; ``alpha`` has no
effect while the colour is left on "palette colour".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List

from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtGui import QColor


@dataclass(frozen=True)
class OptionDef:
    key: str
    label: str
    kind: str                       # color_opt | color | int | float | bool | choice | text
    default: object
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    help: str = ""


# ---------------------------------------------------------------------------
#  Definitions
# ---------------------------------------------------------------------------

TOOL_LABELS: Dict[str, str] = {
    "select": "Select",
    "arrow": "Arrow",
    "rectangle": "Rectangle",
    "highlight": "Highlight",
    "pixelate": "Pixelate",
    "blur": "Blur",
    "text": "Text",
    "crop": "Crop",
    "magnifier": "Magnifier",
    "balloon": "Speech Balloon",
    "ellipse": "Ellipse",
    "line": "Line",
    "pen": "Freehand Pen",
    "step": "Numbered Step",
    "spotlight": "Spotlight",
    "sticker": "Sticker",
    "eyedropper": "Eyedropper",
    "hand": "Hand",
}

_COLOR_HELP = "Leave on 'Palette colour' to use whatever colour is selected in the side panel."
_ALPHA_HELP = "Opacity applied together with a fixed colour (ignored while the colour is 'Palette colour')."


def _color(label: str = "Colour") -> OptionDef:
    return OptionDef("color", label, "color_opt", "", help=_COLOR_HELP)


def _alpha(default: int = 255) -> OptionDef:
    return OptionDef("alpha", "Opacity (0–255)", "int", default, 0, 255, help=_ALPHA_HELP)


def _shape_options() -> List[OptionDef]:
    return [
        _color(),
        _alpha(),
        OptionDef("stroke_width", "Stroke width (px)", "int", 3, 1, 30),
        OptionDef("fill", "Fill the inside", "bool", False,
                  help="Also fill the shape with its colour at the fill opacity below."),
        OptionDef("fill_opacity", "Fill opacity (0–255)", "int", 60, 0, 255),
    ]


TOOL_OPTIONS: Dict[str, List[OptionDef]] = {
    "select": [],
    "crop": [],
    "eyedropper": [],
    "hand": [],
    "arrow": [
        _color(),
        _alpha(),
        OptionDef("shaft_width", "Shaft width (px)", "int", 3, 1, 20),
        OptionDef("head_length", "Head length (px)", "int", 18, 6, 80),
        OptionDef("head_width", "Head width (px)", "int", 14, 6, 80),
    ],
    "line": [
        _color(),
        _alpha(),
        OptionDef("width", "Width (px)", "int", 3, 1, 30),
        OptionDef("round_caps", "Round line ends", "bool", True),
    ],
    "pen": [
        _color(),
        _alpha(),
        OptionDef("width", "Width (px)", "int", 3, 1, 40),
        OptionDef("smoothing", "Smooth the stroke", "bool", True),
    ],
    "rectangle": _shape_options(),
    "ellipse": _shape_options(),
    "highlight": [
        OptionDef("color", "Highlight colour", "color", "#FFFF00"),
        OptionDef("alpha", "Opacity (0–255)", "int", 80, 0, 255,
                  help="Used when the side-panel opacity slider is at 100%."),
    ],
    "text": [
        _color(),
        _alpha(),
        OptionDef("font_size", "Font size (pt)", "int", 16, 6, 200),
        OptionDef("bold", "Bold", "bool", True),
        OptionDef("font_family", "Font family", "text", "Sans Serif"),
    ],
    "balloon": [
        OptionDef("fill", "Balloon colour", "color_opt", "", help=_COLOR_HELP),
        OptionDef("text_color", "Text colour", "color", "#1E1E1E"),
        OptionDef("font_size", "Font size (pt)", "int", 13, 6, 100),
    ],
    "step": [
        _color(),
        _alpha(),
        OptionDef("diameter", "Marker size (px)", "int", 28, 16, 120),
    ],
    "sticker": [
        OptionDef("glyph", "Default sticker", "text", "😀",
                  help="Emoji or short text placed by the sticker tool."),
        OptionDef("size", "Size (px)", "int", 48, 16, 300),
    ],
    "pixelate": [
        OptionDef("intensity", "Block size", "int", 10, 2, 50),
    ],
    "blur": [
        OptionDef("intensity", "Strength", "int", 10, 2, 50),
    ],
    "spotlight": [
        OptionDef("dim", "Darkness outside (0–255)", "int", 160, 0, 255),
        OptionDef("shape", "Shape", "choice", "rect", choices=("rect", "ellipse")),
    ],
    "magnifier": [
        OptionDef("radius", "Radius (px)", "int", 80, 20, 300),
        OptionDef("zoom", "Zoom factor", "float", 2.0, 1.2, 8.0),
    ],
}


# ---------------------------------------------------------------------------
#  Signals / storage
# ---------------------------------------------------------------------------

class _Signals(QObject):
    changed = pyqtSignal(str)       # tool id ('' = several tools changed)


signals = _Signals()


def _settings() -> QSettings:
    """Factory for the settings store (tests may monkeypatch this)."""
    return QSettings("YawnByte", "Crabture")


def _option(tool_id: str, key: str) -> OptionDef:
    for opt in TOOL_OPTIONS.get(tool_id, ()):
        if opt.key == key:
            return opt
    raise KeyError(f"unknown option {key!r} for tool {tool_id!r}")


def _skey(tool_id: str, key: str) -> str:
    return f"tools/{tool_id}/{key}"


def _coerce(opt: OptionDef, value):
    """Convert *value* to the option's type, clamped / validated; falls back to the default."""
    try:
        if opt.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if opt.kind in ("int", "float"):
            num = float(value)
            if opt.min is not None:
                num = max(opt.min, num)
            if opt.max is not None:
                num = min(opt.max, num)
            return int(round(num)) if opt.kind == "int" else float(num)
        if opt.kind == "choice":
            return str(value) if str(value) in opt.choices else opt.default
        if opt.kind in ("color", "color_opt"):
            text = str(value or "").strip()
            if not text:
                return "" if opt.kind == "color_opt" else opt.default
            c = QColor(text)
            return c.name().upper() if c.isValid() else opt.default
        text = str(value)
        return text if text.strip() else opt.default
    except (TypeError, ValueError):
        return opt.default


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def get(tool_id: str, key: str):
    """Saved (or default) value of option *key* for *tool_id*."""
    opt = _option(tool_id, key)
    s = _settings()
    k = _skey(tool_id, key)
    if not s.contains(k):
        return opt.default
    return _coerce(opt, s.value(k, opt.default))


def values(tool_id: str) -> dict:
    """All options of a tool as ``{key: value}`` ({} for tools without options)."""
    return {opt.key: get(tool_id, opt.key) for opt in TOOL_OPTIONS.get(tool_id, ())}


def set(tool_id: str, key: str, value) -> None:   # noqa: A001
    """Persist one option (coerced and clamped to its limits)."""
    opt = _option(tool_id, key)
    s = _settings()
    s.setValue(_skey(tool_id, key), _coerce(opt, value))
    s.sync()
    signals.changed.emit(tool_id)


def is_default(tool_id: str) -> bool:
    return all(get(tool_id, o.key) == o.default for o in TOOL_OPTIONS.get(tool_id, ()))


def reset_tool(tool_id: str) -> None:
    s = _settings()
    s.remove(f"tools/{tool_id}")
    s.sync()
    signals.changed.emit(tool_id)


def reset_all() -> None:
    s = _settings()
    s.remove("tools")
    s.sync()
    signals.changed.emit("")


# -- import / export ------------------------------------------------------------

def export_json() -> str:
    data = {tool: values(tool) for tool, opts in TOOL_OPTIONS.items() if opts}
    return json.dumps({"version": 1, "tools": data}, indent=2, ensure_ascii=False)


def import_json(text: str) -> List[str]:
    """Apply settings from :func:`export_json` output; returns a list of problems.

    Unknown tools/options are ignored; values are coerced to their limits.
    """
    problems: List[str] = []
    try:
        data = json.loads(text)
        tools = data["tools"] if isinstance(data, dict) and "tools" in data else data
        if not isinstance(tools, dict):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return ["The file is not a valid Crabture tool-settings export."]

    s = _settings()
    touched = False
    for tool_id, opts in tools.items():
        if tool_id not in TOOL_OPTIONS or not isinstance(opts, dict):
            continue
        for key, value in opts.items():
            try:
                opt = _option(tool_id, key)
            except KeyError:
                continue
            coerced = _coerce(opt, value)
            if opt.kind in ("int", "float", "choice", "color", "color_opt") and coerced != value:
                if opt.kind in ("int", "float") and isinstance(value, (int, float)) and float(coerced) == float(value):
                    pass
                elif str(coerced) != str(value):
                    problems.append(f"{TOOL_LABELS.get(tool_id, tool_id)} · {opt.label}: "
                                    f"'{value}' adjusted to '{coerced}'.")
            s.setValue(_skey(tool_id, key), coerced)
            touched = True
    s.sync()
    if touched:
        signals.changed.emit("")
    return problems
