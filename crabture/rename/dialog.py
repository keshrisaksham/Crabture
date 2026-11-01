"""
rename/dialog.py — Power Rename: bulk file renaming with a live preview.

Each file goes through this pipeline (every step optional, in this order; the dialog shows
steps 2 and 3 together in one card, "New name", with the numbering options inside it):

  1. Find & Replace   plain text or regex (\\1 / \\g<name> groups), whole word,
                      first/all matches, on name+extension, name only or ext only
  2. Template         sequential "base + number" or a token template
                      ({name} {ext} {n} {date} {mtime} {ctime} {parent} {w} {h}
                      {size} {rand:6} {original}); sets the new base name
  3. Insert / remove  remove characters, remove first/last N, insert at a
                      position, prefix, suffix
  4. Case             lower / UPPER / Title / Sentence / snake / kebab / camel /
                      Pascal, plus extension lower-casing / normalisation
  5. Cleanup          strip accents, remove illegal characters, trim, spaces to
                      _ or -, collapse repeated separators, strip edge separators

Steps 2-5 work on the base name (the part before the extension).  The engine
(``compute_names``) is independent of the dialog and is what the tests drive.
Disk access (two-phase renames, undo journal) lives in
``rename/history.py``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import re
import string
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSettings, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QImageReader, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from crabture.rename.history import (
    discard_last_rename_batch,
    has_rename_undo,
    journal_path,
    last_rename_batch,
    perform_renames,
    record_rename_batch,
    undo_last_rename,
)

__all__ = [
    "PowerRenameDialog", "RenameOptions", "RenameResult", "compute_names",
    "validate_template", "find_conflicts", "natural_key", "BUILTIN_PRESETS",
    "load_user_presets", "save_user_preset", "delete_user_preset",
    "options_to_dict", "options_from_dict",
    "last_rename_batch", "undo_last_rename", "has_rename_undo",
    "discard_last_rename_batch", "record_rename_batch", "perform_renames",
]

ERROR_RED = QColor("#E5484D")      # readable on both light and dark backgrounds
CASE_MODES = ("none", "lower", "upper", "title", "sentence", "snake", "kebab", "camel", "pascal")
SORT_KEYS = ("given", "name", "mtime", "size", "dimensions")
KNOWN_TOKENS = ("name", "ext", "n", "date", "mtime", "ctime", "parent", "w", "h", "size", "rand", "original")
_ILLEGAL = '\\/:*?"<>|'
_RAND_ALPHABET = string.ascii_lowercase + string.digits
_HAS_NUMBER_TOKEN = re.compile(r"(?<!\{)\{n(:[^{}]*)?\}")      # {n} or {n:03}, but not {name}


def template_uses_number(template: str) -> bool:
    """True if *template* contains the ``{n}`` counter token (``{name}`` does not count)."""
    return bool(_HAS_NUMBER_TOKEN.search(template or ""))


# ===========================================================================
#  Options
# ===========================================================================

@dataclass
class RenameOptions:
    """Everything the pipeline needs.  All defaults are 'do nothing'."""
    # 1. Find & Replace
    find_text: str = ""
    replace_text: str = ""
    find_regex: bool = False
    find_match_case: bool = False
    find_whole_word: bool = False
    find_all: bool = True            # False: first match only
    find_target: int = 0             # 0 name+ext, 1 name only, 2 extension only
    # 2. Template / numbering
    template_mode: str = "off"       # off | sequential | template
    seq_base: str = ""
    seq_sep: str = "_"
    template: str = "_{n}"
    counter_start: int = 1
    counter_step: int = 1
    counter_padding: int = 1
    sort_key: str = "given"          # numbering order
    sort_desc: bool = False
    # 3. Insert / remove
    prefix: str = ""
    suffix: str = ""
    insert_text: str = ""
    insert_pos: int = 0              # >=0 from start, <0 that many chars before the end
    remove_first: int = 0
    remove_last: int = 0
    remove_chars: str = ""
    # 4. Case
    case_mode: str = "none"
    ext_lower: bool = False
    ext_normalise: bool = False      # .jpeg -> .jpg, .tif -> .tiff
    # 5. Cleanup
    clean_accents: bool = False
    clean_illegal: bool = False
    clean_trim: bool = False
    clean_spaces: str = "none"       # none | _ | -
    clean_collapse: bool = False
    clean_strip_edges: bool = False


def default_options() -> RenameOptions:
    """What the dialog starts with (and Reset returns to): build the name from a pattern.

    The engine's own ``RenameOptions()`` stays "do nothing"; this is only the dialog's starting point."""
    return RenameOptions(template_mode="template")


def options_to_dict(o: RenameOptions) -> dict:
    return dataclasses.asdict(o)


def options_from_dict(d: dict) -> RenameOptions:
    """Tolerant loader: unknown keys are ignored, bad values fall back to defaults."""
    base = RenameOptions()
    if not isinstance(d, dict):
        return base
    for f in dataclasses.fields(RenameOptions):
        if f.name not in d:
            continue
        default = getattr(base, f.name)
        val = d[f.name]
        try:
            if isinstance(default, bool):
                val = bool(val)
            elif isinstance(default, int):
                val = int(val)
            else:
                val = str(val)
        except (TypeError, ValueError):
            continue
        setattr(base, f.name, val)
    if base.case_mode not in CASE_MODES:
        base.case_mode = "none"
    if base.sort_key not in SORT_KEYS:
        base.sort_key = "given"
    if base.template_mode not in ("off", "sequential", "template"):
        base.template_mode = "off"
    if base.clean_spaces not in ("none", "_", "-"):
        base.clean_spaces = "none"
    if base.find_target not in (0, 1, 2):
        base.find_target = 0
    return base


BUILTIN_PRESETS: dict[str, dict] = {
    "Clean up screenshot names": options_to_dict(RenameOptions(
        clean_accents=True, clean_illegal=True, clean_trim=True, clean_spaces="_",
        clean_collapse=True, clean_strip_edges=True, ext_lower=True,
    )),
    "Date + counter": options_to_dict(RenameOptions(
        template_mode="template", template="{mtime:%Y-%m-%d}_{n}",
        counter_start=1, counter_padding=3, sort_key="mtime",
    )),
    "Lowercase & dashes": options_to_dict(RenameOptions(
        case_mode="kebab", clean_accents=True, clean_illegal=True, clean_strip_edges=True,
        ext_lower=True,
    )),
}


# ===========================================================================
#  Per-file metadata (lazy)
# ===========================================================================

class _Meta:
    """Name parts plus lazily-read stat() and image dimensions."""
    __slots__ = ("path", "name", "stem", "ext", "parent", "_st", "_st_done", "_dims", "_dims_done")

    def __init__(self, path: str) -> None:
        self.path = path
        self.name = os.path.basename(path)
        self.stem, self.ext = os.path.splitext(self.name)
        self.parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
        self._st = None
        self._st_done = False
        self._dims = None
        self._dims_done = False

    def stat(self):
        if not self._st_done:
            self._st_done = True
            try:
                self._st = os.lstat(self.path)
            except OSError:
                self._st = None
        return self._st

    def dims(self) -> Optional[tuple[int, int]]:
        if not self._dims_done:
            self._dims_done = True
            size = QImageReader(self.path).size()
            self._dims = (size.width(), size.height()) if size.isValid() else None
        return self._dims


def natural_key(name: str) -> list:
    """Sort key where 'img2' < 'img10'."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)}B" if unit == "B" else f"{size:.1f}".rstrip("0").rstrip(".") + unit
        size /= 1024
    return f"{n}B"


# ===========================================================================
#  Template parsing / rendering
# ===========================================================================

_TOKEN_RE = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}|[{}]")


def _parse_template(template: str) -> tuple[list[tuple], list[str]]:
    """Return ``(parts, errors)``; parts are ``('lit', text)`` or ``('tok', name, spec)``."""
    parts: list[tuple] = []
    errors: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(template):
        if m.start() > pos:
            parts.append(("lit", template[pos:m.start()]))
        pos = m.end()
        text = m.group(0)
        if text == "{{":
            parts.append(("lit", "{"))
        elif text == "}}":
            parts.append(("lit", "}"))
        elif m.group(1) is None:
            msg = "Unmatched '{' or '}' in template (use {{ and }} for literal braces)."
            if msg not in errors:
                errors.append(msg)
        else:
            name, spec = m.group(1), m.group(2)
            err = _check_token(name, spec)
            if err and err not in errors:
                errors.append(err)
            parts.append(("tok", name, spec))
    if pos < len(template):
        parts.append(("lit", template[pos:]))
    return parts, errors


def _check_token(name: str, spec: Optional[str]) -> Optional[str]:
    if name not in KNOWN_TOKENS:
        return f"Unknown token {{{name}}}."
    if spec is None:
        return None
    if name == "n":
        if not re.fullmatch(r"\d{1,2}", spec):
            return "{n:…} takes a width such as {n:03}."
    elif name == "rand":
        if not re.fullmatch(r"\d{1,2}", spec) or not 1 <= int(spec) <= 64:
            return "{rand:…} takes a length from 1 to 64, e.g. {rand:6}."
    elif name in ("date", "mtime", "ctime"):
        pass                      # any strftime format
    elif name == "size":
        if spec != "b":
            return "{size:b} gives raw bytes; {size} is human readable."
    else:
        return f"Token {{{name}}} takes no ':' format."
    return None


def validate_template(template: str) -> list[str]:
    """Problems with *template* (unknown tokens, bad specs, stray braces)."""
    return _parse_template(template)[1]


def _escape_braces(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


# ===========================================================================
#  Case conversion
# ===========================================================================

_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD = re.compile(r"[^\W_]+")


def _words(s: str) -> list[str]:
    return _WORD.findall(_WORD_BOUNDARY.sub(" ", s))


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


def _case_title(s: str) -> str:
    return _WORD.sub(lambda m: _cap(m.group(0)), s)


def _case_sentence(s: str) -> str:
    s = s.lower()
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
    return s


def _case_fn(mode: str) -> Optional[Callable[[str], str]]:
    def joined(sep: str, first_lower: bool = False, caps: bool = False):
        def fn(s: str) -> str:
            ws = _words(s)
            if not ws:
                return s
            if caps:
                ws = [ws[0].lower() if (first_lower and i == 0) else _cap(w) for i, w in enumerate(ws)]
            else:
                ws = [w.lower() for w in ws]
            return sep.join(ws)
        return fn

    return {
        "lower": str.lower,
        "upper": str.upper,
        "title": _case_title,
        "sentence": _case_sentence,
        "snake": joined("_"),
        "kebab": joined("-"),
        "camel": joined("", first_lower=True, caps=True),
        "pascal": joined("", caps=True),
    }.get(mode)


# ===========================================================================
#  Engine
# ===========================================================================

@dataclass
class RenameResult:
    names: list[str]                                    # new file name per input index
    order: list[int]                                    # input indices in numbering order
    rank: list[int]                                     # input index -> position in `order`
    errors: list[str] = field(default_factory=list)     # option problems (block Apply)
    warnings: dict[int, list[str]] = field(default_factory=dict)


class _Plan:
    """Options compiled once per preview run."""

    def __init__(self, o: RenameOptions) -> None:
        self.o = o
        self.errors: list[str] = []
        self.find_rx: Optional[re.Pattern] = None
        self.find_repl = None
        self.find_error = ""
        self.template_error = ""
        self.parts: Optional[list[tuple]] = None
        self._compile_find()
        self._compile_template()
        self.remove_table = {ord(c): None for c in o.remove_chars} if o.remove_chars else None
        self.case_fn = _case_fn(o.case_mode)

    def _compile_find(self) -> None:
        o = self.o
        if not o.find_text:
            return
        flags = 0 if o.find_match_case else re.IGNORECASE
        pattern = o.find_text if o.find_regex else re.escape(o.find_text)
        if o.find_whole_word:
            pattern = rf"(?<!\w)(?:{pattern})(?!\w)"
        try:
            rx = re.compile(pattern, flags)
            if o.find_regex:
                rx.sub(o.replace_text, "")      # validates group references early
        except (re.error, IndexError) as e:
            self.find_error = f"Invalid regular expression or replacement: {e}"
            self.errors.append(self.find_error)
            return
        self.find_rx = rx
        if o.find_regex:
            self.find_repl = o.replace_text
        else:
            text = o.replace_text
            self.find_repl = lambda m, _t=text: _t

    def _compile_template(self) -> None:
        o = self.o
        template = None
        if o.template_mode == "sequential":
            if o.seq_base:
                template = _escape_braces(o.seq_base) + _escape_braces(o.seq_sep) + "{n}"
        elif o.template_mode == "template":
            template = o.template
            parts, errors = _parse_template(template)
            if errors:
                self.template_error = " ".join(errors)
                self.errors.extend(errors)
                return
            self.parts = parts
            return
        if template is not None:
            self.parts, _ = _parse_template(template)


def _sort_order(metas: list[_Meta], o: RenameOptions) -> list[int]:
    idx = list(range(len(metas)))
    key = o.sort_key
    if key == "name":
        idx.sort(key=lambda i: natural_key(metas[i].name), reverse=o.sort_desc)
    elif key == "mtime":
        idx.sort(key=lambda i: (metas[i].stat().st_mtime if metas[i].stat() else 0.0), reverse=o.sort_desc)
    elif key == "size":
        idx.sort(key=lambda i: (metas[i].stat().st_size if metas[i].stat() else 0), reverse=o.sort_desc)
    elif key == "dimensions":
        def area(i: int) -> int:
            d = metas[i].dims()
            return d[0] * d[1] if d else 0
        idx.sort(key=area, reverse=o.sort_desc)
    elif o.sort_desc:
        idx.reverse()
    return idx


def _render_template(parts, meta: _Meta, stem: str, ext: str, n_value: int,
                     o: RenameOptions, seed: int, today: datetime, warns: list[str]) -> str:
    out: list[str] = []
    for k, part in enumerate(parts):
        if part[0] == "lit":
            out.append(part[1])
            continue
        _, name, spec = part
        if name == "name":
            out.append(stem)
        elif name == "ext":
            out.append(ext[1:])
        elif name == "original":
            out.append(meta.name)
        elif name == "parent":
            out.append(meta.parent)
        elif name == "n":
            width = int(spec) if spec else o.counter_padding
            out.append(str(n_value).zfill(width))
        elif name == "date":
            out.append(_strftime(today, spec or "%Y-%m-%d"))
        elif name in ("mtime", "ctime"):
            st = meta.stat()
            if st is None:
                warns.append(f"No {name} available.")
                out.append("")
            else:
                stamp = st.st_mtime if name == "mtime" else getattr(st, "st_birthtime", st.st_ctime)
                out.append(_strftime(datetime.fromtimestamp(stamp), spec or "%Y-%m-%d"))
        elif name in ("w", "h"):
            d = meta.dims()
            if d is None:
                msg = "Image size unavailable."
                if msg not in warns:
                    warns.append(msg)
                out.append("")
            else:
                out.append(str(d[0] if name == "w" else d[1]))
        elif name == "size":
            st = meta.stat()
            if st is None:
                out.append("")
            else:
                out.append(str(st.st_size) if spec == "b" else _human_size(st.st_size))
        elif name == "rand":
            rng = random.Random(f"{seed}|{meta.path}|{k}")
            out.append("".join(rng.choice(_RAND_ALPHABET) for _ in range(int(spec) if spec else 6)))
    return "".join(out)


def _strftime(when: datetime, fmt: str) -> str:
    try:
        return when.strftime(fmt)
    except (ValueError, OSError):
        return ""


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_ILLEGAL_RE = re.compile("[" + re.escape(_ILLEGAL) + r"\x00-\x1f\x7f]")
_COLLAPSE_RE = re.compile(r"([ _\-.])\1+")
_EXT_NORMALISE = {".jpeg": ".jpg", ".tif": ".tiff"}


def _transform(meta: _Meta, n_value: int, plan: _Plan, seed: int, today: datetime,
               warns: list[str]) -> str:
    o = plan.o
    stem, ext = meta.stem, meta.ext

    # 1. Find & Replace
    if plan.find_rx is not None:
        rx, repl, count = plan.find_rx, plan.find_repl, (0 if o.find_all else 1)
        if o.find_target == 0:
            stem, ext = os.path.splitext(rx.sub(repl, meta.name, count=count))
        elif o.find_target == 1:
            stem = rx.sub(repl, stem, count=count)
        else:
            new_ext = rx.sub(repl, ext[1:], count=count).lstrip(".")
            ext = "." + new_ext if new_ext else ""

    # 2. Template / numbering
    if plan.parts is not None:
        stem = _render_template(plan.parts, meta, stem, ext, n_value, o, seed, today, warns)

    # 3. Insert / remove
    if plan.remove_table:
        stem = stem.translate(plan.remove_table)
    if o.remove_first > 0:
        stem = stem[o.remove_first:]
    if o.remove_last > 0:
        stem = stem[:-o.remove_last] if o.remove_last < len(stem) else ""
    if o.insert_text:
        at = o.insert_pos if o.insert_pos >= 0 else max(0, len(stem) + o.insert_pos)
        at = min(at, len(stem))
        stem = stem[:at] + o.insert_text + stem[at:]
    if o.prefix or o.suffix:
        stem = o.prefix + stem + o.suffix

    # 4. Case
    if plan.case_fn is not None:
        stem = plan.case_fn(stem)
    if o.ext_lower:
        ext = ext.lower()
    if o.ext_normalise:
        ext = _EXT_NORMALISE.get(ext.lower(), ext)

    # 5. Cleanup
    if o.clean_accents:
        stem = _strip_accents(stem)
    if o.clean_illegal:
        stem = _ILLEGAL_RE.sub("", stem)
    if o.clean_trim:
        stem = stem.strip()
    if o.clean_spaces in ("_", "-"):
        stem = re.sub(r"\s+", o.clean_spaces, stem)
    if o.clean_collapse:
        stem = _COLLAPSE_RE.sub(r"\1", stem)
    if o.clean_strip_edges:
        stem = stem.strip(" _-.")

    # An empty base name would turn ".png" into a hidden file: report it as empty.
    return stem + ext if stem else ""


def compute_names(filepaths: list[str], opts: RenameOptions, rand_seed: int = 0,
                  meta_cache: Optional[dict] = None, today: Optional[datetime] = None) -> RenameResult:
    """Run the pipeline for every path.  Pure: touches the disk only to read."""
    if meta_cache is None:
        meta_cache = {}
    metas = []
    for p in filepaths:
        m = meta_cache.get(p)
        if m is None:
            m = meta_cache[p] = _Meta(p)
        metas.append(m)

    plan = _Plan(opts)
    today = today or datetime.now()
    order = _sort_order(metas, opts)
    rank = [0] * len(metas)
    for r, i in enumerate(order):
        rank[i] = r

    names: list[str] = []
    warnings: dict[int, list[str]] = {}
    for i, meta in enumerate(metas):
        warns: list[str] = []
        n_value = opts.counter_start + rank[i] * opts.counter_step
        names.append(_transform(meta, n_value, plan, rand_seed, today, warns))
        if warns:
            warnings[i] = warns
    return RenameResult(names=names, order=order, rank=rank, errors=list(plan.errors), warnings=warnings)


def find_conflicts(filepaths: list[str], new_names: list[str]) -> dict[int, str]:
    """Return {row: reason} for every row whose new name cannot be applied."""
    conflicts: dict[int, str] = {}
    batch_paths = {os.path.abspath(fp) for fp in filepaths}

    # Group by the full target path so duplicates are caught per-directory.
    targets: dict[str, list[int]] = {}
    for i, fp in enumerate(filepaths):
        original = os.path.basename(fp)
        new_name = new_names[i]
        if new_name == original:
            targets.setdefault(os.path.abspath(fp), []).append(i)
            continue

        if not new_name or new_name.strip() == "":
            conflicts[i] = "Name is empty."
        elif new_name in (".", ".."):
            conflicts[i] = f"'{new_name}' is not a valid file name."
        elif "/" in new_name or os.sep in new_name or "\0" in new_name:
            conflicts[i] = "Name must not contain path separators."
        elif len(new_name.encode("utf-8", "surrogateescape")) > 255:
            conflicts[i] = "Name is too long."
        else:
            new_path = os.path.abspath(os.path.join(os.path.dirname(fp), new_name))
            targets.setdefault(new_path, []).append(i)
            # Collides with an existing file that is not part of this batch.
            if os.path.lexists(new_path) and new_path not in batch_paths:
                conflicts[i] = "A file with this name already exists."

    for rows in targets.values():
        if len(rows) > 1:
            for r in rows:
                if os.path.basename(filepaths[r]) != new_names[r]:
                    conflicts.setdefault(r, "Duplicate name in this batch.")
    return conflicts


# ===========================================================================
#  Presets (QSettings 'powerrename/presets' = JSON {name: options})
# ===========================================================================

_PRESET_KEY = "powerrename/presets"


def _settings() -> QSettings:
    return QSettings("YawnByte", "Crabture")


def load_user_presets() -> dict[str, dict]:
    raw = _settings().value(_PRESET_KEY, "")
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def save_user_preset(name: str, opts: RenameOptions) -> None:
    presets = load_user_presets()
    presets[name] = options_to_dict(opts)
    s = _settings()
    s.setValue(_PRESET_KEY, json.dumps(presets))
    s.sync()


def delete_user_preset(name: str) -> None:
    presets = load_user_presets()
    if presets.pop(name, None) is not None:
        s = _settings()
        s.setValue(_PRESET_KEY, json.dumps(presets))
        s.sync()


# ===========================================================================
#  Preview table model
# ===========================================================================

class _PreviewModel(QAbstractTableModel):
    HEADERS = ("#", "Original", "New name", "Status")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[int, int]] = []     # (file index, rank)
        self._paths: list[str] = []
        self._names: list[str] = []
        self._conflicts: dict[int, str] = {}
        self._warnings: dict[int, list[str]] = {}
        self._dim = QBrush()
        self._red = QBrush(ERROR_RED)
        self._bold = QFont()
        self._bold.setBold(True)

    def set_state(self, rows, paths, names, conflicts, warnings, dim_color: QColor) -> None:
        self.beginResetModel()
        self._rows, self._paths, self._names = rows, paths, names
        self._conflicts, self._warnings = conflicts, warnings
        self._dim = QBrush(dim_color)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def _state(self, i: int) -> str:
        if i in self._conflicts:
            return "conflict"
        return "changed" if self._names[i] != os.path.basename(self._paths[i]) else "same"

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        i, rank = self._rows[index.row()]
        col = index.column()
        state = self._state(i)
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return str(rank + 1)
            if col == 1:
                return os.path.basename(self._paths[i])
            if col == 2:
                return self._names[i]
            if state == "conflict":
                return "✕ " + self._conflicts[i]
            if state == "changed":
                return "⚠ " + self._warnings[i][0] if i in self._warnings else "✓ Will rename"
            return "– Unchanged"
        if role == Qt.ItemDataRole.ForegroundRole:
            if state == "conflict" and col >= 2:
                return self._red
            if state == "same":
                return self._dim
            return None
        if role == Qt.ItemDataRole.FontRole:
            return self._bold if (col == 2 and state != "same") else None
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 0:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            if state == "conflict":
                return self._conflicts[i]
            if i in self._warnings:
                return "\n".join(self._warnings[i])
            if col == 1:
                return self._paths[i]
        return None


# ===========================================================================
#  Dialog widgets
# ===========================================================================

def _role(widget: QWidget, role: str) -> QWidget:
    widget.setProperty("role", role)
    return widget


class _CardHeader(QWidget):
    """Clickable header row of a card."""

    def __init__(self, on_click, parent=None) -> None:
        super().__init__(parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)


class _Card(QFrame):
    """A collapsible group of options: title, ON badge, body."""

    def __init__(self, title: str, expanded: bool = True, parent=None) -> None:
        super().__init__(parent)
        _role(self, "card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        self._expanded = True
        header = _CardHeader(lambda: self.set_expanded(not self._expanded))
        head = QHBoxLayout(header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._title = QLabel(title)
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        head.addWidget(self._title)
        self._on = _role(QLabel("ON"), "badge")
        self._on.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._on.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._on.hide()
        head.addWidget(self._on)
        head.addStretch(1)
        self._arrow = QLabel()
        head.addWidget(self._arrow)
        outer.addWidget(header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        outer.addWidget(self.body)
        self.set_expanded(expanded)

    def set_expanded(self, on: bool) -> None:
        self._expanded = on
        self.body.setVisible(on)
        self._arrow.setText("▾" if on else "▸")

    def is_expanded(self) -> bool:
        return self._expanded

    def set_active(self, active: bool) -> None:
        self._on.setVisible(active)


def _more_options(body: QWidget, label: str = "More options") -> QPushButton:
    """A flat link-style button that shows/hides *body* (hidden to start with)."""
    btn = _role(QPushButton(), "ghost")
    btn.setAutoDefault(False)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def sync(on: bool) -> None:
        btn.setText(f"{'▾' if on else '▸'} {label}")
        body.setVisible(on)

    btn.setCheckable(True)
    btn.toggled.connect(sync)
    sync(False)
    return btn


def _error_label() -> QLabel:
    lbl = QLabel()
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {ERROR_RED.name()};")
    lbl.hide()
    return lbl


def _short_home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


class PowerRenameDialog(QDialog):
    """PowerToys-style bulk rename dialog.

    ``filepaths`` order is "as given"; ``new_names[i]`` always belongs to
    ``filepaths[i]``.  Call ``apply_renames()`` after ``exec()`` returns
    accepted.  ``undo_performed`` is True when the user used *Undo last rename*
    (the dialog then closes rejected; the caller should refresh its file list).
    """

    PREVIEW_DELAY_MS = 100

    def __init__(self, filepaths: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Power Rename")
        self.setMinimumSize(900, 600)
        self.resize(1120, 740)
        self.filepaths = list(filepaths)
        self.new_names = [os.path.basename(fp) for fp in self.filepaths]
        # Row index -> reason the row cannot be applied.
        self.conflicts: dict[int, str] = {}
        # (original path, reason) for every rename that failed in apply_renames().
        self.failures: list[tuple[str, str]] = []
        # original path -> new path for every rename apply_renames() performed.
        self.renamed: dict[str, str] = {}
        # Option problems (bad regex / template); Apply is disabled while any exist.
        self.errors: list[str] = []
        self.undo_performed = False
        self.undone_count = 0

        self._meta: dict[str, _Meta] = {}
        self._rand_seed = random.getrandbits(32)
        self._loading = False
        self._result: Optional[RenameResult] = None
        self._user_presets: dict[str, dict] = {}

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._preview)

        self._build_ui()
        for btn in self.findChildren(QPushButton):
            # Enter in a text field must never "click" a button (it used to open Save preset…).
            btn.setAutoDefault(False)
            btn.setDefault(False)
        self._reload_presets()
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._apply_shortcut)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._apply_shortcut)

        geometry = _settings().value("powerrename/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self._set_options(default_options())
        self._first_show = True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_first_show", False):
            self._first_show = False
            # Ready to type the name in front of the "_{n}" that is already there.
            QTimer.singleShot(0, self._cursor_before_pattern)

    def _cursor_before_pattern(self) -> None:
        # Focusing a line edit can select its text; a typed letter would then replace "_{n}".
        self.template_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.template_edit.deselect()
        self.template_edit.setCursorPosition(0)

    # ------------------------------------------------------------------
    #  Widget helpers
    # ------------------------------------------------------------------

    def _line(self, placeholder: str = "", text: str = "") -> QLineEdit:
        w = QLineEdit(text)
        w.setPlaceholderText(placeholder)
        w.setClearButtonEnabled(True)
        w.textChanged.connect(self._schedule_preview)
        return w

    def _check(self, text: str, checked: bool = False) -> QCheckBox:
        w = QCheckBox(text)
        w.setChecked(checked)
        w.toggled.connect(self._schedule_preview)
        return w

    def _spin(self, lo: int, hi: int, value: int) -> QSpinBox:
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setValue(value)
        w.valueChanged.connect(self._schedule_preview)
        return w

    def _combo(self, items: list[str]) -> QComboBox:
        w = QComboBox()
        w.addItems(items)
        w.currentIndexChanged.connect(self._schedule_preview)
        return w

    @staticmethod
    def _grid(rows: list[tuple[str, QWidget]]) -> QGridLayout:
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        g.setColumnStretch(1, 1)
        for r, (label, widget) in enumerate(rows):
            g.addWidget(QLabel(label), r, 0)
            g.addWidget(widget, r, 1)
        return g

    # ------------------------------------------------------------------
    #  UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- Header -----------------------------------------------------
        header = QWidget()
        header.setObjectName("dialog_header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 14, 20, 14)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_role(QLabel("Power Rename"), "h1"))
        self.subtitle = _role(QLabel(self._subtitle_text()), "muted")
        titles.addWidget(self.subtitle)
        hl.addLayout(titles)
        hl.addStretch(1)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(190)
        self.preset_combo.setToolTip("Load a saved set of options")
        self.preset_combo.activated.connect(self._on_preset_chosen)
        hl.addWidget(self.preset_combo)
        self.btn_save_preset = _role(QPushButton("Save preset…"), "ghost")
        self.btn_save_preset.clicked.connect(self._save_preset)
        hl.addWidget(self.btn_save_preset)
        self.btn_delete_preset = _role(QPushButton("Delete preset"), "ghost")
        self.btn_delete_preset.hide()
        self.btn_delete_preset.clicked.connect(self._delete_preset)
        hl.addWidget(self.btn_delete_preset)
        self.btn_reset = _role(QPushButton("Reset"), "ghost")
        self.btn_reset.clicked.connect(self._reset)
        hl.addWidget(self.btn_reset)
        root.addWidget(header)

        # -- Body: options (left) | preview (right) ------------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_options_column())
        splitter.addWidget(self._build_preview_column())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 620])
        self.splitter = splitter
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 8)
        bl.addWidget(splitter)
        root.addWidget(body, 1)

        # -- Footer ---------------------------------------------------------
        footer = QWidget()
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(20, 8, 20, 14)
        self.summary_label = QLabel()
        fl.addWidget(self.summary_label, 1)
        self.btn_undo = _role(QPushButton("Undo last rename"), "ghost")
        self.btn_undo.clicked.connect(self._undo_last)
        fl.addWidget(self.btn_undo)
        self.btn_cancel = _role(QPushButton("Cancel"), "secondary")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        fl.addWidget(self.btn_cancel)
        self.btn_apply = _role(QPushButton("Apply"), "primary")
        self.btn_apply.setAutoDefault(False)     # Enter in a field must not rename files
        self.btn_apply.setToolTip("Apply (Ctrl+Enter)")
        self.btn_apply.clicked.connect(self.accept)
        fl.addWidget(self.btn_apply)
        root.addWidget(footer)
        self._refresh_undo_button()

    def _subtitle_text(self) -> str:
        dirs = {os.path.dirname(os.path.abspath(fp)) for fp in self.filepaths}
        n = len(self.filepaths)
        noun = "file" if n == 1 else "files"
        if len(dirs) == 1:
            return f"{n} {noun} in {_short_home(next(iter(dirs)))}"
        return f"{n} {noun} in {len(dirs)} folders"

    def _build_options_column(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(480)
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(10)

        # 1. Find & replace
        self.card_find = _Card("Find & replace", expanded=False)
        self.search_edit = self._line("Text to look for")
        self.replace_edit = self._line("Replace it with")
        self.find_error = _error_label()
        self.regex_cb = self._check("Regular expression  (replace can use \\1, \\g<name>)")
        self.case_cb = self._check("Match upper/lower case")
        self.word_cb = self._check("Whole words only")
        self.all_cb = self._check("Replace every match", True)
        self.apply_to_combo = self._combo(["Name and extension", "Name only", "Extension only"])
        self.card_find.body_layout.addLayout(self._grid([("Find", self.search_edit), ("Replace", self.replace_edit)]))
        self.card_find.body_layout.addWidget(self.find_error)
        find_more = QWidget()
        fm = QVBoxLayout(find_more)
        fm.setContentsMargins(0, 0, 0, 0)
        fm.setSpacing(6)
        for w in (self.regex_cb, self.case_cb, self.word_cb, self.all_cb):
            fm.addWidget(w)
        fm.addLayout(self._grid([("Look in", self.apply_to_combo)]))
        self.card_find.body_layout.addWidget(_more_options(find_more), 0, Qt.AlignmentFlag.AlignLeft)
        self.card_find.body_layout.addWidget(find_more)
        col.addWidget(self.card_find)

        # 2. New name  (where the name comes from, and how files are numbered)
        self.card_tpl = _Card("New name", expanded=True)
        self.mode_combo = self._combo(["Keep the current name",
                                       "Type a name (photo_1, photo_2…)",
                                       "Build from a pattern"])
        self.mode_combo.currentIndexChanged.connect(self._update_template_visibility)
        self.mode_combo.activated.connect(self._focus_pattern)
        self.card_tpl.body_layout.addLayout(self._grid([("Name from", self.mode_combo)]))

        self.seq_widget = QWidget()
        sl = QVBoxLayout(self.seq_widget)
        sl.setContentsMargins(0, 0, 0, 0)
        self.base_name_edit = self._line("e.g. vacation_photo")
        self.base_name_edit.returnPressed.connect(self._apply_shortcut)
        self.separator_combo = self._combo(["_  underscore", "-  dash", "    space", "nothing"])
        sl.addLayout(self._grid([("Name", self.base_name_edit), ("Then", self.separator_combo)]))
        seq_help = _role(QLabel("A number is added after the name. The extension is kept."), "muted")
        seq_help.setWordWrap(True)
        sl.addWidget(seq_help)
        self.card_tpl.body_layout.addWidget(self.seq_widget)

        self.tpl_widget = QWidget()
        tl = QVBoxLayout(self.tpl_widget)
        tl.setContentsMargins(0, 0, 0, 0)
        self.template_edit = self._line("_{n}", "_{n}")
        self.template_edit.textChanged.connect(lambda *_: self._update_template_visibility())
        self.template_edit.returnPressed.connect(self._apply_shortcut)      # Enter = save the name
        tl.addLayout(self._grid([("Pattern", self.template_edit)]))
        tl.addWidget(_role(QLabel("Click to add to the pattern:"), "muted"))
        chips = QGridLayout()
        chips.setSpacing(4)

        def chip(label: str, tok: str) -> QPushButton:
            b = _role(QPushButton(label), "ghost")
            b.setAutoDefault(False)
            b.setToolTip(f"Adds {tok}")
            b.clicked.connect(lambda _=False, t=tok: self.template_edit.insert(t))
            return b

        # (button label, token inserted): the common ones first, the rest under "More".
        common = [("Current name", "{name}"), ("Number", "{n}"), ("Modified date", "{mtime:%Y-%m-%d}")]
        extra = [("Today's date", "{date}"), ("Created date", "{ctime}"), ("Folder name", "{parent}"),
                 ("Width", "{w}"), ("Height", "{h}"), ("File size", "{size}"),
                 ("Random text", "{rand:6}"), ("Full old name", "{original}"), ("Extension", "{ext}")]
        for k, (label, tok) in enumerate(common):
            chips.addWidget(chip(label, tok), 0, k)
        tl.addLayout(chips)
        chips_more = QWidget()
        cm = QGridLayout(chips_more)
        cm.setContentsMargins(0, 0, 0, 0)
        cm.setSpacing(4)
        for k, (label, tok) in enumerate(extra):
            cm.addWidget(chip(label, tok), k // 3, k % 3)
        self.regen_btn = _role(QPushButton("New random text"), "ghost")
        self.regen_btn.setToolTip("Pick different random text for {rand}")
        self.regen_btn.setAutoDefault(False)
        self.regen_btn.clicked.connect(self._regenerate)
        cm.addWidget(self.regen_btn, (len(extra) + 2) // 3, 0, 1, 3, Qt.AlignmentFlag.AlignLeft)
        tl.addWidget(_more_options(chips_more, "More choices"), 0, Qt.AlignmentFlag.AlignLeft)
        tl.addWidget(chips_more)
        self.template_error = _error_label()
        tl.addWidget(self.template_error)
        self.template_hint = _role(QLabel(""), "muted")
        self.template_hint.setWordWrap(True)
        self.template_hint.hide()
        tl.addWidget(self.template_hint)
        self.card_tpl.body_layout.addWidget(self.tpl_widget)

        # Numbering: only shown while the new name actually contains a number.
        self.counter_widget = QWidget()
        cl = QVBoxLayout(self.counter_widget)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.start_num_spin = self._spin(0, 999999, 1)
        self.step_spin = self._spin(1, 9999, 1)
        self.padding_spin = self._spin(1, 10, 1)
        self.start_num_spin.setToolTip("The first file gets this number")
        self.step_spin.setToolTip("Add this much for each next file (2 gives 1, 3, 5…)")
        self.padding_spin.setToolTip("Digits to show: 3 gives 001, 002…")
        num_row = QHBoxLayout()
        num_row.addWidget(QLabel("Number from"))
        num_row.addWidget(self.start_num_spin)
        num_row.addSpacing(8)
        num_row.addWidget(QLabel("Digits"))
        num_row.addWidget(self.padding_spin)
        num_row.addStretch(1)
        cl.addLayout(num_row)
        num_more = QWidget()
        nm = QVBoxLayout(num_more)
        nm.setContentsMargins(0, 0, 0, 0)
        nm.setSpacing(6)
        nm.addLayout(self._grid([("Count by", self.step_spin)]))
        self.sort_combo = self._combo(["The order listed", "Name (2 before 10)", "Date modified", "File size",
                                       "Image dimensions"])
        self.sort_desc_cb = self._check("Reverse")
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Number in order of"))
        sort_row.addWidget(self.sort_combo, 1)
        sort_row.addWidget(self.sort_desc_cb)
        nm.addLayout(sort_row)
        cl.addWidget(_more_options(num_more, "Numbering options"), 0, Qt.AlignmentFlag.AlignLeft)
        cl.addWidget(num_more)
        self.card_tpl.body_layout.addWidget(self.counter_widget)
        col.addWidget(self.card_tpl)

        # 3. Add / remove text
        self.card_ins = _Card("Add or remove text", expanded=False)
        self.prefix_edit = self._line("Text at the start of the name")
        self.suffix_edit = self._line("Text at the end of the name (before the extension)")
        self.insert_edit = self._line("Text to insert")
        self.insert_pos_spin = self._spin(-999, 999, 0)
        self.insert_pos_spin.setToolTip("0 = at the start · 3 = after 3 characters · -2 = before the last 2 characters")
        self.remove_first_spin = self._spin(0, 999, 0)
        self.remove_last_spin = self._spin(0, 999, 0)
        self.remove_chars_edit = self._line("e.g. ()[]#")
        self.card_ins.body_layout.addLayout(self._grid([
            ("Add at start", self.prefix_edit), ("Add at end", self.suffix_edit),
            ("Delete these", self.remove_chars_edit),
        ]))
        ins_more = QWidget()
        im = QVBoxLayout(ins_more)
        im.setContentsMargins(0, 0, 0, 0)
        im.addLayout(self._grid([
            ("Insert text", self.insert_edit), ("…after N characters", self.insert_pos_spin),
            ("Delete first N", self.remove_first_spin), ("Delete last N", self.remove_last_spin),
        ]))
        self.card_ins.body_layout.addWidget(_more_options(ins_more), 0, Qt.AlignmentFlag.AlignLeft)
        self.card_ins.body_layout.addWidget(ins_more)
        col.addWidget(self.card_ins)

        # 4. Letter case
        self.card_case = _Card("Letter case", expanded=False)
        self.case_combo = self._combo(["Keep as is", "lowercase", "UPPERCASE", "Title Case", "Sentence case",
                                       "snake_case", "kebab-case", "camelCase", "PascalCase"])
        self.ext_lower_cb = self._check("Make the extension lowercase (.PNG → .png)")
        self.ext_norm_cb = self._check("Use standard extensions (.jpeg → .jpg, .tif → .tiff)")
        self.card_case.body_layout.addLayout(self._grid([("Name", self.case_combo)]))
        self.card_case.body_layout.addWidget(self.ext_lower_cb)
        self.card_case.body_layout.addWidget(self.ext_norm_cb)
        col.addWidget(self.card_case)

        # 5. Tidy up
        self.card_clean = _Card("Tidy up", expanded=False)
        self.accents_cb = self._check("Remove accents (é → e)")
        self.illegal_cb = self._check('Remove characters not allowed in file names  \\ / : * ? " < > |')
        self.trim_cb = self._check("Remove spaces at the start and end")
        self.spaces_combo = self._combo(["Keep spaces", "Spaces → _", "Spaces → -"])
        self.collapse_cb = self._check("Merge repeated separators (__ → _)")
        self.edges_cb = self._check("Remove separators and dots at the start and end")
        for w in (self.accents_cb, self.illegal_cb, self.trim_cb):
            self.card_clean.body_layout.addWidget(w)
        self.card_clean.body_layout.addLayout(self._grid([("Spaces", self.spaces_combo)]))
        self.card_clean.body_layout.addWidget(self.collapse_cb)
        self.card_clean.body_layout.addWidget(self.edges_cb)
        col.addWidget(self.card_clean)

        col.addStretch(1)
        scroll.setWidget(host)
        self.template_edit.setCursorPosition(0)
        self._update_template_visibility()
        return scroll

    def _build_preview_column(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter rows…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._schedule_preview)
        bar.addWidget(self.filter_edit, 1)
        self.hide_unchanged_cb = QCheckBox("Hide unchanged")
        self.hide_unchanged_cb.toggled.connect(self._schedule_preview)
        bar.addWidget(self.hide_unchanged_cb)
        lay.addLayout(bar)

        self.model = _PreviewModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 48)
        lay.addWidget(self.table, 1)
        return host

    # ------------------------------------------------------------------
    #  Options <-> widgets
    # ------------------------------------------------------------------

    def _collect_options(self) -> RenameOptions:
        mode = ("off", "sequential", "template")[self.mode_combo.currentIndex()]
        sep = ("_", "-", " ", "")[self.separator_combo.currentIndex()]
        spaces = ("none", "_", "-")[self.spaces_combo.currentIndex()]
        return RenameOptions(
            find_text=self.search_edit.text(),
            replace_text=self.replace_edit.text(),
            find_regex=self.regex_cb.isChecked(),
            find_match_case=self.case_cb.isChecked(),
            find_whole_word=self.word_cb.isChecked(),
            find_all=self.all_cb.isChecked(),
            find_target=self.apply_to_combo.currentIndex(),
            template_mode=mode,
            seq_base=self.base_name_edit.text(),
            seq_sep=sep,
            template=self.template_edit.text(),
            counter_start=self.start_num_spin.value(),
            counter_step=self.step_spin.value(),
            counter_padding=self.padding_spin.value(),
            sort_key=SORT_KEYS[self.sort_combo.currentIndex()],
            sort_desc=self.sort_desc_cb.isChecked(),
            prefix=self.prefix_edit.text(),
            suffix=self.suffix_edit.text(),
            insert_text=self.insert_edit.text(),
            insert_pos=self.insert_pos_spin.value(),
            remove_first=self.remove_first_spin.value(),
            remove_last=self.remove_last_spin.value(),
            remove_chars=self.remove_chars_edit.text(),
            case_mode=CASE_MODES[self.case_combo.currentIndex()],
            ext_lower=self.ext_lower_cb.isChecked(),
            ext_normalise=self.ext_norm_cb.isChecked(),
            clean_accents=self.accents_cb.isChecked(),
            clean_illegal=self.illegal_cb.isChecked(),
            clean_trim=self.trim_cb.isChecked(),
            clean_spaces=spaces,
            clean_collapse=self.collapse_cb.isChecked(),
            clean_strip_edges=self.edges_cb.isChecked(),
        )

    def _set_options(self, o: RenameOptions) -> None:
        self._loading = True
        try:
            self.search_edit.setText(o.find_text)
            self.replace_edit.setText(o.replace_text)
            self.regex_cb.setChecked(o.find_regex)
            self.case_cb.setChecked(o.find_match_case)
            self.word_cb.setChecked(o.find_whole_word)
            self.all_cb.setChecked(o.find_all)
            self.apply_to_combo.setCurrentIndex(o.find_target)
            self.mode_combo.setCurrentIndex(("off", "sequential", "template").index(o.template_mode))
            self.base_name_edit.setText(o.seq_base)
            self.separator_combo.setCurrentIndex({"_": 0, "-": 1, " ": 2, "": 3}.get(o.seq_sep, 0))
            self.template_edit.setText(o.template)
            self.start_num_spin.setValue(o.counter_start)
            self.step_spin.setValue(max(1, o.counter_step))
            self.padding_spin.setValue(max(1, o.counter_padding))
            self.sort_combo.setCurrentIndex(SORT_KEYS.index(o.sort_key))
            self.sort_desc_cb.setChecked(o.sort_desc)
            self.prefix_edit.setText(o.prefix)
            self.suffix_edit.setText(o.suffix)
            self.insert_edit.setText(o.insert_text)
            self.insert_pos_spin.setValue(o.insert_pos)
            self.remove_first_spin.setValue(o.remove_first)
            self.remove_last_spin.setValue(o.remove_last)
            self.remove_chars_edit.setText(o.remove_chars)
            self.case_combo.setCurrentIndex(CASE_MODES.index(o.case_mode))
            self.ext_lower_cb.setChecked(o.ext_lower)
            self.ext_norm_cb.setChecked(o.ext_normalise)
            self.accents_cb.setChecked(o.clean_accents)
            self.illegal_cb.setChecked(o.clean_illegal)
            self.trim_cb.setChecked(o.clean_trim)
            self.spaces_combo.setCurrentIndex(("none", "_", "-").index(o.clean_spaces))
            self.collapse_cb.setChecked(o.clean_collapse)
            self.edges_cb.setChecked(o.clean_strip_edges)
        finally:
            self._loading = False
        self._update_template_visibility()
        # Open the cards a preset switched on so the user can see what it does.
        for card, active in self._active_steps(o).items():
            if active and not card.is_expanded():
                card.set_expanded(True)
        self._preview()

    def _active_steps(self, o: RenameOptions) -> dict:
        tpl_on = (o.template_mode == "template" and bool(o.template.strip())) or \
                 (o.template_mode == "sequential" and bool(o.seq_base))
        return {
            self.card_find: bool(o.find_text),
            self.card_tpl: tpl_on,
            self.card_ins: bool(o.prefix or o.suffix or o.insert_text or o.remove_first
                                or o.remove_last or o.remove_chars),
            self.card_case: o.case_mode != "none" or o.ext_lower or o.ext_normalise,
            self.card_clean: any((o.clean_accents, o.clean_illegal, o.clean_trim,
                                  o.clean_spaces != "none", o.clean_collapse, o.clean_strip_edges)),
        }

    def _update_template_visibility(self) -> None:
        mode = self.mode_combo.currentIndex()
        self.seq_widget.setVisible(mode == 1)
        self.tpl_widget.setVisible(mode == 2)
        # Numbering only shows when the name is typed, or when the pattern contains {n}.
        text = self.template_edit.text()
        uses_number = mode == 1 or (mode == 2 and template_uses_number(text))
        self.counter_widget.setVisible(uses_number)
        # {rand} is the only thing the "New random text" button affects.
        self.regen_btn.setVisible(mode == 2 and "{rand" in text)
        # Warn when a pattern would give every file the same name.
        varies = ("{n", "{name", "{original", "{rand", "{mtime", "{ctime", "{w", "{h", "{size")
        same = mode == 2 and len(self.filepaths) > 1 and not any(t in text for t in varies)
        if same:
            self.template_hint.setText("Every file would get this same name. Click “Number” or “Current name” "
                                       "above to tell them apart.")
        self.template_hint.setVisible(same)

    def _reset(self) -> None:
        self._set_options(default_options())
        self._cursor_before_pattern()

    def _focus_pattern(self) -> None:
        """After the user picks "Build from a pattern", put the cursor at the start so they can type a name."""
        if self.mode_combo.currentIndex() == 2:
            self.template_edit.setFocus()
            self.template_edit.setCursorPosition(0)

    def _regenerate(self) -> None:
        self._rand_seed = random.getrandbits(32)
        self._preview()

    # ------------------------------------------------------------------
    #  Presets
    # ------------------------------------------------------------------

    def _reload_presets(self, select: str = "") -> None:
        self._user_presets = load_user_presets()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("Presets…")
        for name in BUILTIN_PRESETS:
            self.preset_combo.addItem(name)
        if self._user_presets:
            self.preset_combo.insertSeparator(self.preset_combo.count())
            for name in self._user_presets:
                self.preset_combo.addItem(name)
        idx = self.preset_combo.findText(select) if select else 0
        self.preset_combo.setCurrentIndex(max(0, idx))
        self.preset_combo.blockSignals(False)
        self.btn_delete_preset.setVisible(select in self._user_presets)

    def _on_preset_chosen(self, index: int) -> None:
        name = self.preset_combo.itemText(index)
        data = BUILTIN_PRESETS.get(name) or self._user_presets.get(name)
        self.btn_delete_preset.setVisible(name in self._user_presets)
        if data is not None:
            self._set_options(options_from_dict(data))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        name = name.strip()
        if not ok or not name:
            return
        if name in BUILTIN_PRESETS:
            QMessageBox.information(self, "Save preset", "That name belongs to a built-in preset.")
            return
        save_user_preset(name, self._collect_options())
        self._reload_presets(select=name)

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name in self._user_presets:
            delete_user_preset(name)
            self._reload_presets()

    # ------------------------------------------------------------------
    #  Preview
    # ------------------------------------------------------------------

    def _schedule_preview(self, *_args) -> None:
        if getattr(self, "_loading", False) or not hasattr(self, "btn_apply"):
            return
        self._timer.start()

    def _preview(self) -> None:
        # Signals fire while the UI is still being built; wait for the last widget.
        if not hasattr(self, "btn_apply"):
            return
        self._timer.stop()
        opts = self._collect_options()
        result = compute_names(self.filepaths, opts, self._rand_seed, self._meta)
        self._result = result
        self.new_names = result.names
        self.errors = result.errors
        self.conflicts = self._find_conflicts()

        # Inline option errors.
        plan_find = [e for e in result.errors if e.startswith("Invalid regular")]
        plan_tpl = [e for e in result.errors if e not in plan_find]
        self.find_error.setText("\n".join(plan_find))
        self.find_error.setVisible(bool(plan_find))
        self.template_error.setText("\n".join(plan_tpl))
        self.template_error.setVisible(bool(plan_tpl))

        for card, active in self._active_steps(opts).items():
            card.set_active(active)

        # Rows shown: numbering order, filtered.
        needle = self.filter_edit.text().strip().casefold()
        hide_same = self.hide_unchanged_cb.isChecked()
        rows = []
        for i in result.order:
            original = os.path.basename(self.filepaths[i])
            new = self.new_names[i]
            if hide_same and new == original and i not in self.conflicts:
                continue
            if needle and needle not in original.casefold() and needle not in new.casefold():
                continue
            rows.append((i, result.rank[i]))
        dim = self.palette().color(QPalette.ColorRole.PlaceholderText)
        self.model.set_state(rows, self.filepaths, self.new_names, self.conflicts, result.warnings, dim)

        changed = sum(
            1 for i, fp in enumerate(self.filepaths)
            if self.new_names[i] != os.path.basename(fp) and i not in self.conflicts
        )
        n_conf = len(self.conflicts)
        unchanged = sum(1 for i, fp in enumerate(self.filepaths) if self.new_names[i] == os.path.basename(fp))
        parts = [f"{changed} will change", f"{unchanged} unchanged"]
        if n_conf:
            parts.append(f"{n_conf} conflict{'s' if n_conf != 1 else ''}")
        if result.warnings:
            parts.append(f"{len(result.warnings)} warning{'s' if len(result.warnings) != 1 else ''}")
        if self.errors:
            parts.append("fix the highlighted option errors")
        self.summary_label.setText(" · ".join(parts))

        can_apply = changed > 0 and not self.conflicts and not self.errors
        self.btn_apply.setEnabled(can_apply)
        if self.conflicts:
            tip = f"{n_conf} item(s) have naming conflicts"
        elif self.errors:
            tip = "Fix the option errors first"
        elif not changed:
            tip = "Nothing to rename yet"
        else:
            tip = "Apply (Ctrl+Enter)"
        self.btn_apply.setToolTip(tip)

    def _find_conflicts(self) -> dict[int, str]:
        """Return {row: reason} for every row whose new name cannot be applied."""
        return find_conflicts(self.filepaths, self.new_names)

    # ------------------------------------------------------------------
    #  Actions
    # ------------------------------------------------------------------

    def _apply_shortcut(self) -> None:
        if self._timer.isActive():
            self._preview()                 # Enter right after typing: use the final text, not the 100 ms old preview
        if self.btn_apply.isEnabled():
            self.accept()

    def apply_renames(self) -> int:
        """Execute renames and return the number of successful renames.

        Renames go through unique temporary names first so permutations
        (a -> b, b -> a) work.  Failures are recorded in ``self.failures``;
        successful renames are saved to the undo journal.
        """
        self.failures = []
        self.renamed = {}
        if self.errors:
            self.failures.extend(("", e) for e in self.errors)
            return 0
        if self.conflicts:
            for i in sorted(self.conflicts):
                self.failures.append((self.filepaths[i], self.conflicts[i]))
            return 0

        pairs = [
            (fp, os.path.join(os.path.dirname(fp), self.new_names[i]))
            for i, fp in enumerate(self.filepaths)
            if os.path.basename(fp) != self.new_names[i]
        ]
        renamed, failures = perform_renames(pairs)
        self.renamed = renamed
        self.failures = failures
        if renamed:
            try:
                record_rename_batch(renamed)
            except OSError as e:          # a journal problem must never fail the renames
                self.failures.append(("", f"Could not save the undo history: {e}"))
        self._refresh_undo_button()
        return len(renamed)

    def _refresh_undo_button(self) -> None:
        batch = last_rename_batch()
        self.btn_undo.setEnabled(batch is not None)
        if batch:
            self.btn_undo.setToolTip(f"Undo {batch.get('count', len(batch['items']))} rename(s) from {batch.get('time', '')}")
        else:
            self.btn_undo.setToolTip("Nothing to undo")

    def _undo_last(self) -> None:
        batch = last_rename_batch()
        if not batch:
            return
        n = batch.get("count", len(batch["items"]))
        if QMessageBox.question(
            self, "Undo last rename",
            f"Restore the original names of {n} file(s) renamed on {batch.get('time', '')}?",
        ) != QMessageBox.StandardButton.Yes:
            return
        count, failures = undo_last_rename()
        self.undone_count = count
        self.undo_performed = count > 0
        if failures:
            lines = [f"{os.path.basename(p) or '-'}: {why}" for p, why in failures[:10]]
            if len(failures) > 10:
                lines.append(f"… and {len(failures) - 10} more")
            QMessageBox.warning(self, "Undo last rename", f"{count} restored, {len(failures)} skipped:\n\n" + "\n".join(lines))
            if count == 0 and QMessageBox.question(
                self, "Undo last rename",
                "Nothing could be restored. Forget this batch so older ones can be undone?",
            ) == QMessageBox.StandardButton.Yes:
                discard_last_rename_batch()
        else:
            QMessageBox.information(self, "Undo last rename", f"Restored {count} file(s).")
        self._refresh_undo_button()
        if self.undo_performed:
            self.reject()        # file list is stale now; caller checks undo_performed

    def done(self, result: int) -> None:  # noqa: N802
        s = _settings()
        s.setValue("powerrename/geometry", self.saveGeometry())
        super().done(result)
