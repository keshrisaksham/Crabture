"""
redact/dialog.py — "Find sensitive data" dialog for Crabture.

Nothing here is automatic:

  • By default the dialog does NOT scan when it opens.  OCR starts only when
    the user clicks **Scan** — or, for ``autoscan=True`` (used only by
    ``run_saved_profile``, which is itself called from the user's click on the
    "Run sensitive-data scan" action), as soon as the dialog is shown.
  • The dialog never touches the canvas.  It only hands back rectangles, and
    only after the user clicks **Apply redaction**.  Cancel / closing the
    window returns nothing.

Usage (from the main window, in response to a button click)::

    dlg = RedactDialog(editor.background_pixmap(), self)
    if dlg.exec():
        rects = dlg.selected_rects()      # list[QRectF], image-pixel coordinates
        style = dlg.style()               # 'blur' | 'pixelate'
        strength = dlg.intensity()        # 2..50
        ...create BlurItem / PixelateItem for each rect...

Two-part workflow (configure once, run every time)::

    RedactConfigDialog(parent).exec()                 # edit + save the profile
    result = run_saved_profile(pixmap, parent)        # one click: scan with the saved profile
    if result:
        rects, style, intensity = result              # only after the user clicked Apply
"""

from __future__ import annotations

import re
import sys
import threading
from typing import List, Optional, Tuple

from PyQt6.QtCore import QEventLoop, QRect, QRectF, QRegularExpression, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QRegularExpressionValidator,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from crabture.redact import core as redact
from crabture.redact import profile as redact_profile
from crabture.redact.profile import RedactProfile

_ERROR_COLOR = QColor("#E5484D")   # readable on both the light and dark theme


def _role(widget, role: str):
    """Set the theme's ``role`` dynamic property (see ui/theme.py) and return the widget."""
    widget.setProperty("role", role)
    return widget


def _try_icon(widget, name: str, size: int = 16) -> None:
    """Attach a themed vector icon when ui/icons.py is available (never required)."""
    try:
        from crabture.ui import icons
        if icons.has_icon(name):
            icons.bind_icon(widget, name, size)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass

_CHECKED_FILL = QColor(220, 40, 40, 90)
_CHECKED_PEN = QColor(220, 40, 40)
_UNCHECKED_PEN = QColor(240, 170, 40)
_MIN_MANUAL_PX = 5


def mask_text(text: str) -> str:
    """Short, mostly-hidden preview so the dialog does not display secrets in full."""
    n = len(text)
    if n <= 4:
        return "•" * n
    return text[:2] + "•" * min(n - 4, 8) + text[-2:]


# ═══════════════════════════════════════════════════════════════════════════
#  Entries and scene items
# ═══════════════════════════════════════════════════════════════════════════

class _Entry:
    """One candidate redaction (automatic finding or user-drawn box)."""

    def __init__(self, kind: str, label: str, text: str, rect: QRectF, confidence: float = 1.0, manual: bool = False) -> None:
        self.kind = kind
        self.label = label
        self.text = text
        self.rect = QRectF(rect)
        self.confidence = confidence
        self.manual = manual
        self.checked = True
        self.box: Optional["_BoxItem"] = None
        self.row: Optional[QListWidgetItem] = None

    def key(self) -> tuple:
        r = self.rect
        return (self.kind, round(r.x()), round(r.y()), round(r.width()), round(r.height()))


class _BoxItem(QGraphicsRectItem):
    """Outline drawn over a finding: solid red when checked, dashed when not."""

    def __init__(self, entry: _Entry) -> None:
        super().__init__(entry.rect)
        self.entry = entry
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(10)
        self._hover = False
        self.refresh()

    def refresh(self) -> None:
        if self.entry.checked:
            pen = QPen(_CHECKED_PEN, 2.0)
            brush = QBrush(_CHECKED_FILL)
        else:
            pen = QPen(_UNCHECKED_PEN, 1.5, Qt.PenStyle.DashLine)
            brush = QBrush(Qt.BrushStyle.NoBrush)
        if self._hover:
            brush = QBrush(QColor(255, 255, 255, 50)) if not self.entry.checked else brush
            pen.setWidthF(pen.widthF() + 1.0)
        pen.setCosmetic(True)          # constant on-screen width at any zoom
        self.setPen(pen)
        self.setBrush(brush)

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.refresh()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.refresh()
        super().hoverLeaveEvent(event)


class _PreviewView(QGraphicsView):
    """Fit-to-window preview.  Click a box to toggle it; drag on empty image to add a box."""

    box_clicked = pyqtSignal(object)      # _Entry
    region_drawn = pyqtSignal(QRectF)     # scene (= image pixel) coordinates

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.fit_mode = True
        self._press_box: Optional[_BoxItem] = None
        self._origin: Optional[QRect] = None
        self._origin_pt = None
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())

    def fit(self) -> None:
        self.fit_mode = True
        rect = self.sceneRect()
        if not rect.isEmpty():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.fit_mode:
            self.fit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.fit_mode = False
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    @staticmethod
    def _box_at(item) -> Optional[_BoxItem]:
        while item is not None and not isinstance(item, _BoxItem):
            item = item.parentItem()
        return item

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        box = self._box_at(self.itemAt(pos))
        if box is not None:
            self._press_box = box
            self._origin_pt = None
        else:
            self._press_box = None
            self._origin_pt = pos
            self._band.setGeometry(QRect(pos, pos))
            self._band.show()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._origin_pt is not None:
            self._band.setGeometry(QRect(self._origin_pt, event.position().toPoint()).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        pos = event.position().toPoint()
        if self._press_box is not None:
            if self._box_at(self.itemAt(pos)) is self._press_box:
                self.box_clicked.emit(self._press_box.entry)
            self._press_box = None
        elif self._origin_pt is not None:
            view_rect = QRect(self._origin_pt, pos).normalized()
            self._band.hide()
            self._origin_pt = None
            if view_rect.width() >= _MIN_MANUAL_PX and view_rect.height() >= _MIN_MANUAL_PX:
                scene_rect = self.mapToScene(view_rect).boundingRect().intersected(self.sceneRect())
                if scene_rect.width() >= 2 and scene_rect.height() >= 2:
                    self.region_drawn.emit(scene_rect)
        event.accept()


# ═══════════════════════════════════════════════════════════════════════════
#  OCR worker thread
# ═══════════════════════════════════════════════════════════════════════════

class _OcrWorker(QThread):
    finished_ok = pyqtSignal(object)      # list[OcrWord]
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, image: QImage, lang: str, parent=None, psm: int = 11) -> None:
        super().__init__(parent)
        self._image = image
        self._lang = lang
        self._psm = psm
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D401
        try:
            words = redact.run_ocr(self._image, self._lang, psm=self._psm, cancel=self._cancel)
        except redact.OcrCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(str(exc))
        else:
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.finished_ok.emit(words)


# ═══════════════════════════════════════════════════════════════════════════
#  Dialog
# ═══════════════════════════════════════════════════════════════════════════

class RedactDialog(QDialog):
    """Review OCR-detected sensitive data and choose what to redact.

    ``selected_rects()`` / ``style()`` / ``intensity()`` are meaningful only
    after the dialog was accepted via **Apply redaction**.

    *profile* pre-configures the detectors, custom patterns, OCR language/mode,
    padding, confidence filter, style and strength.  *autoscan* starts the scan
    as soon as the dialog is first shown; it is off by default so that merely
    constructing or opening the dialog never scans.  Only ``run_saved_profile``
    (reached from the user's click on "Run") passes ``autoscan=True``.
    """

    def __init__(self, pixmap: QPixmap, parent=None, profile: Optional[RedactProfile] = None,
                 autoscan: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find sensitive data" if profile is None else "Sensitive data scan")
        self.resize(1120, 720)

        self._profile = profile
        self._psm = profile.psm if profile else 11
        self._pad = float(profile.pad) if profile else float(redact.PAD)
        self._min_conf = profile.min_confidence if profile else 0
        self._profile_customs: List[str] = profile.enabled_custom_regexes() if profile else []
        self._autoscan_pending = bool(autoscan)
        self.scan_count = 0                         # number of scans started (for tests/diagnostics)

        # Work in image pixels: strip any device pixel ratio so scene units == pixels.
        self._image: QImage = pixmap.toImage()
        self._image.setDevicePixelRatio(1.0)
        self._bounds = QRectF(0, 0, self._image.width(), self._image.height())

        self._accepted = False
        self._words: Optional[list] = None          # cached OCR result (None until a scan finished)
        self._entries: List[_Entry] = []
        self._worker: Optional[_OcrWorker] = None
        self._kind_boxes: dict = {}

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(self._bounds)
        bg = QGraphicsPixmapItem(QPixmap.fromImage(self._image))
        bg.setZValue(0)
        self._scene.addItem(bg)

        self._build_ui()
        if profile is not None:
            self._apply_profile_to_controls(profile)
        self._refresh_tesseract_state()
        self._update_apply()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Autoscan is only ever requested by run_saved_profile (a user click).
        if self._autoscan_pending:
            self._autoscan_pending = False
            QTimer.singleShot(0, self._on_scan_clicked)

    def _apply_profile_to_controls(self, p: RedactProfile) -> None:
        wanted = set(p.kinds)
        for kind, cb in self._kind_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(kind in wanted)
            cb.blockSignals(False)
        self._lang_combo.setCurrentText(p.lang)
        idx = self._style_combo.findData(p.style)
        if idx >= 0:
            self._style_combo.setCurrentIndex(idx)
        self._intensity_slider.setValue(p.intensity)
        notes = len(self._profile_customs)
        if notes:
            self._custom_note.setText(f"+ {notes} saved custom pattern(s) active")
            self._custom_note.show()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root.addWidget(splitter)

        # -- left: preview ------------------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self._view = _PreviewView(self._scene)
        self._view.box_clicked.connect(self._on_box_clicked)
        self._view.region_drawn.connect(self._on_region_drawn)
        lv.addWidget(self._view, 1)
        hint_row = QHBoxLayout()
        hint = QLabel("Click a box to toggle it · drag on the image to add your own · Ctrl+wheel to zoom")
        hint.setWordWrap(True)
        hint_row.addWidget(hint, 1)
        fit_btn = QPushButton("Fit")
        fit_btn.setAutoDefault(False)
        fit_btn.clicked.connect(self._view.fit)
        hint_row.addWidget(fit_btn)
        lv.addLayout(hint_row)
        splitter.addWidget(left)

        # -- right: controls ----------------------------------------------
        right = QWidget()
        right.setMinimumWidth(330)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Sensitive data scan" if self._profile is not None else "Find sensitive data")
        _role(title, "h2")
        rv.addWidget(title)
        if self._profile is not None:
            text = ("Using your saved configuration. Nothing on your image changes until you "
                    "review the findings and click <b>Apply redaction</b>.")
        else:
            text = ("Scanning only happens when you click <b>Scan</b>. Nothing on your image changes "
                    "until you click <b>Apply redaction</b>.")
        info = QLabel(text)
        info.setWordWrap(True)
        _role(info, "muted")
        rv.addWidget(info)

        # Detection options (detectors, custom regex, language).  Collapsed by
        # default when a saved profile pre-configured them.
        self._options_btn = QPushButton("Detection options")
        _role(self._options_btn, "chip")
        self._options_btn.setCheckable(True)
        self._options_btn.setAutoDefault(False)
        self._options_btn.setToolTip("Show or hide the detectors, custom regex and OCR language used by this scan")
        rv.addWidget(self._options_btn)
        self._options_box = QWidget()
        ov = QVBoxLayout(self._options_box)
        ov.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self._options_box)

        # detectors
        kinds_group = QGroupBox("What to look for")
        kg = QVBoxLayout(kinds_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(130)
        scroll.setMaximumHeight(170)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(4, 4, 4, 4)
        for kind, label in redact.KINDS.items():
            if kind == "custom":
                continue
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.toggled.connect(self._on_filters_changed)
            iv.addWidget(cb)
            self._kind_boxes[kind] = cb
        iv.addStretch(1)
        scroll.setWidget(inner)
        kg.addWidget(scroll)
        kbtns = QHBoxLayout()
        for text, state in (("All", True), ("None", False)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, s=state: self._set_all_kinds(s))
            kbtns.addWidget(b)
        kbtns.addStretch(1)
        kg.addLayout(kbtns)
        ov.addWidget(kinds_group)

        # custom regex + language
        form = QHBoxLayout()
        form.addWidget(QLabel("Custom regex:"))
        self._custom_edit = QLineEdit()
        self._custom_edit.setPlaceholderText(r"optional, e.g. ORD-\d+")
        self._custom_edit.editingFinished.connect(self._on_filters_changed)
        form.addWidget(self._custom_edit, 1)
        ov.addLayout(form)
        self._custom_note = QLabel("")
        _role(self._custom_note, "caption")
        self._custom_note.hide()
        ov.addWidget(self._custom_note)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("OCR language:"))
        self._lang_combo = QComboBox()
        self._lang_combo.setEditable(True)
        self._lang_combo.setValidator(QRegularExpressionValidator(QRegularExpression(r"[A-Za-z0-9_+\-]*")))
        langs = redact.list_languages()
        self._lang_combo.addItems(langs)
        if "eng" in langs:
            self._lang_combo.setCurrentText("eng")
        self._lang_combo.setToolTip("Combine languages with '+', e.g. eng+deu")
        lang_row.addWidget(self._lang_combo, 1)
        ov.addLayout(lang_row)
        if self._profile is not None:
            self._options_box.hide()
            self._options_btn.toggled.connect(self._options_box.setVisible)
        else:
            self._options_btn.hide()

        # scan controls
        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan")
        _try_icon(self._scan_btn, "scan")
        self._scan_btn.setAutoDefault(False)
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setAutoDefault(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.hide()
        scan_row.addWidget(self._scan_btn, 1)
        scan_row.addWidget(self._stop_btn)
        rv.addLayout(scan_row)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)           # indeterminate
        self._progress.setTextVisible(False)
        self._progress.hide()
        rv.addWidget(self._progress)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        _role(self._status, "muted")
        rv.addWidget(self._status)

        # results
        rv.addWidget(QLabel("Findings (checked ones will be redacted):"))
        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_list_item_changed)
        self._list.currentRowChanged.connect(self._on_row_changed)
        rv.addWidget(self._list, 1)
        sel_row = QHBoxLayout()
        for text, state in (("Select all", True), ("Select none", False)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, s=state: self._set_all_checked(s))
            sel_row.addWidget(b)
        self._remove_btn = QPushButton("Remove box")
        self._remove_btn.setToolTip("Remove the selected box you drew by hand")
        self._remove_btn.setAutoDefault(False)
        self._remove_btn.clicked.connect(self._remove_selected_manual)
        sel_row.addWidget(self._remove_btn)
        rv.addLayout(sel_row)

        # style + actions
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Redaction style:"))
        self._style_combo = QComboBox()
        self._style_combo.addItem("Blur", "blur")
        self._style_combo.addItem("Pixelate", "pixelate")
        style_row.addWidget(self._style_combo, 1)
        rv.addLayout(style_row)

        strength_row = QHBoxLayout()
        strength_row.addWidget(QLabel("Strength:"))
        self._intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self._intensity_slider.setRange(*redact_profile.INTENSITY_RANGE)
        self._intensity_slider.setValue(12)
        self._intensity_label = QLabel("12")
        self._intensity_label.setMinimumWidth(24)
        self._intensity_slider.valueChanged.connect(lambda v: self._intensity_label.setText(str(v)))
        strength_row.addWidget(self._intensity_slider, 1)
        strength_row.addWidget(self._intensity_label)
        rv.addLayout(strength_row)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._apply_btn = QPushButton("Apply redaction")
        self._apply_btn.setAutoDefault(False)
        self._apply_btn.setDefault(False)
        self._apply_btn.clicked.connect(self._on_apply)
        _role(self._apply_btn, "primary")
        self._cancel_btn = QPushButton("Cancel")
        _role(self._cancel_btn, "ghost")
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.clicked.connect(self.reject)
        actions.addWidget(self._cancel_btn)
        actions.addWidget(self._apply_btn)
        rv.addLayout(actions)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([760, 360])

    # ------------------------------------------------------------ public API

    def selected_rects(self) -> List[QRectF]:
        """Rectangles (image pixels) of the checked findings — only after Apply."""
        if not self._accepted:
            return []
        return [QRectF(e.rect) for e in self._entries if e.checked]

    def style(self) -> str:
        """'blur' or 'pixelate'."""
        return str(self._style_combo.currentData())

    def intensity(self) -> int:
        """Blur / pixelate strength chosen in the dialog (2..50)."""
        return int(self._intensity_slider.value())

    def _set_status(self, text: str, role: str = "muted") -> None:
        self._status.setText(text)
        if self._status.property("role") != role:
            _role(self._status, role)
            self._status.style().unpolish(self._status)
            self._status.style().polish(self._status)

    # --------------------------------------------------------- tesseract state

    def _refresh_tesseract_state(self) -> None:
        if redact.ocr_available():
            self._scan_btn.setEnabled(True)
            self._set_status("Ready. Click Scan to look for sensitive text.")
        else:
            self._scan_btn.setEnabled(False)
            self._set_status(redact.install_hint(), "warning")

    # --------------------------------------------------------------- scanning

    def _selected_kinds(self) -> set:
        kinds = {k for k, cb in self._kind_boxes.items() if cb.isChecked()}
        if self._custom_patterns():
            kinds.add("custom")
        return kinds

    def _custom_patterns(self) -> List[str]:
        patterns = list(self._profile_customs)
        text = self._custom_edit.text().strip()
        if text:
            patterns.append(text)
        return patterns

    def _on_scan_clicked(self) -> None:
        if self._worker is not None:
            return
        if not redact.ocr_available():
            self._refresh_tesseract_state()
            return
        lang = self._lang_combo.currentText().strip() or "eng"
        self._scan_btn.setEnabled(False)
        self._stop_btn.show()
        self._progress.show()
        self._set_status("Scanning… this can take a few seconds.")
        self.scan_count += 1
        worker = _OcrWorker(self._image, lang, self, psm=self._psm)
        worker.finished_ok.connect(self._on_scan_done)
        worker.failed.connect(self._on_scan_failed)
        worker.cancelled.connect(self._on_scan_cancelled)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_stop_clicked(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._set_status("Stopping…")

    def _scan_ui_idle(self) -> None:
        self._stop_btn.hide()
        self._progress.hide()
        self._scan_btn.setEnabled(redact.ocr_available())

    def _on_worker_finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self._scan_ui_idle()

    def _on_scan_done(self, words) -> None:
        self._words = list(words)
        self._rebuild()
        auto = sum(1 for e in self._entries if not e.manual)
        if auto:
            self._set_status(
                f"Found {auto} possible item(s) in {len(self._words)} OCR word(s). "
                "Review them, then click Apply redaction."
            )
        else:
            self._set_status(
                f"Nothing sensitive found ({len(self._words)} OCR word(s) read). "
                "You can still drag on the image to add boxes manually, or just close this window.",
                "success",
            )

    def _on_scan_failed(self, message: str) -> None:
        self._set_status("Scan failed: " + message, "error")

    def _on_scan_cancelled(self) -> None:
        self._set_status("Scan cancelled.")

    # ---------------------------------------------------------------- filters

    def _set_all_kinds(self, state: bool) -> None:
        for cb in self._kind_boxes.values():
            cb.blockSignals(True)
            cb.setChecked(state)
            cb.blockSignals(False)
        self._on_filters_changed()

    def _on_filters_changed(self, *_args) -> None:
        """Re-filter cached OCR words (no new OCR) when options change after a scan."""
        errors = redact.check_patterns(self._custom_patterns())
        if errors:
            self._set_status("Invalid custom regex — " + errors[0], "error")
        if self._words is not None:
            self._rebuild()

    def _rebuild(self) -> None:
        """Replace automatic entries with fresh detections; keep manual boxes and check states."""
        previous = {e.key(): e.checked for e in self._entries if not e.manual}
        for e in [e for e in self._entries if not e.manual]:
            self._remove_entry(e)
        findings = redact.find_sensitive(
            redact.filter_words(self._words or [], self._min_conf),
            self._selected_kinds(), self._custom_patterns(), bounds=self._bounds, pad=self._pad,
        )
        for f in findings:
            entry = _Entry(f.kind, f.label, f.text, f.rect, f.confidence)
            entry.checked = previous.get(entry.key(), True)
            self._add_entry(entry)
        self._update_apply()

    # ---------------------------------------------------------------- entries

    def _add_entry(self, entry: _Entry) -> None:
        self._entries.append(entry)
        box = _BoxItem(entry)
        self._scene.addItem(box)
        entry.box = box

        text = "Manual box" if entry.manual else f"{entry.label} — {mask_text(entry.text)}"
        row = QListWidgetItem(text)
        row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        row.setCheckState(Qt.CheckState.Checked if entry.checked else Qt.CheckState.Unchecked)
        row.setData(Qt.ItemDataRole.UserRole, entry)
        if entry.manual:
            row.setToolTip("Box drawn by you.")
        else:
            row.setToolTip(f"{entry.label}\n{entry.text}\nconfidence {entry.confidence:.0%}")
        self._list.blockSignals(True)
        self._list.addItem(row)
        self._list.blockSignals(False)
        entry.row = row
        box.refresh()

    def _remove_entry(self, entry: _Entry) -> None:
        if entry.box is not None:
            self._scene.removeItem(entry.box)
            entry.box = None
        if entry.row is not None:
            self._list.blockSignals(True)
            self._list.takeItem(self._list.row(entry.row))
            self._list.blockSignals(False)
            entry.row = None
        if entry in self._entries:
            self._entries.remove(entry)

    def _set_checked(self, entry: _Entry, checked: bool) -> None:
        entry.checked = checked
        if entry.box is not None:
            entry.box.refresh()
        if entry.row is not None:
            self._list.blockSignals(True)
            entry.row.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._list.blockSignals(False)
        self._update_apply()

    def _set_all_checked(self, checked: bool) -> None:
        for e in self._entries:
            e.checked = checked
            if e.box is not None:
                e.box.refresh()
            if e.row is not None:
                self._list.blockSignals(True)
                e.row.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                self._list.blockSignals(False)
        self._update_apply()

    def _on_box_clicked(self, entry: _Entry) -> None:
        self._set_checked(entry, not entry.checked)

    def _on_list_item_changed(self, row: QListWidgetItem) -> None:
        entry = row.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, _Entry):
            self._set_checked(entry, row.checkState() == Qt.CheckState.Checked)

    def _on_row_changed(self, index: int) -> None:
        row = self._list.item(index) if index >= 0 else None
        entry = row.data(Qt.ItemDataRole.UserRole) if row is not None else None
        if isinstance(entry, _Entry) and entry.box is not None:
            self._view.ensureVisible(entry.box, 40, 40)

    def _on_region_drawn(self, rect: QRectF) -> None:
        entry = _Entry("manual", "Manual", "", rect, 1.0, manual=True)
        self._add_entry(entry)
        self._update_apply()

    def _remove_selected_manual(self) -> None:
        row = self._list.currentItem()
        entry = row.data(Qt.ItemDataRole.UserRole) if row is not None else None
        if isinstance(entry, _Entry) and entry.manual:
            self._remove_entry(entry)
            self._update_apply()

    def _update_apply(self) -> None:
        self._apply_btn.setEnabled(any(e.checked for e in self._entries))

    # ---------------------------------------------------------- accept / reject

    def _on_apply(self) -> None:
        if not any(e.checked for e in self._entries):
            return
        self._accepted = True
        self.accept()

    def _stop_worker(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.wait(5000)

    def done(self, result: int) -> None:  # noqa: D401
        """Stop any running OCR before the dialog closes (Apply, Cancel, Esc, X)."""
        if result != QDialog.DialogCode.Accepted:
            self._accepted = False
        self._stop_worker()
        super().done(result)


# ═══════════════════════════════════════════════════════════════════════════
#  Configuration (part 1 of 2): edit + save the profile
# ═══════════════════════════════════════════════════════════════════════════

def _card(title: str, subtitle: str = "") -> Tuple[QFrame, QVBoxLayout]:
    """A themed section card (role='card') with an h2 title and optional muted subtitle."""
    frame = QFrame()
    _role(frame, "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    head = QLabel(title)
    _role(head, "h2")
    lay.addWidget(head)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        _role(sub, "muted")
        lay.addWidget(sub)
    return frame, lay


def _sample_image(size: QSize) -> QImage:
    """A fake screenshot snippet (gradient + text) used by the strength preview."""
    img = QImage(size, QImage.Format.Format_RGB32)
    painter = QPainter(img)
    painter.fillRect(img.rect(), QColor("#F4F6FA"))
    painter.fillRect(0, 0, size.width(), 26, QColor("#3B4A6B"))
    painter.setPen(QColor("#FFFFFF"))
    font = painter.font()
    font.setPointSize(10)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(12, 18, "Login — production")
    font.setBold(False)
    painter.setFont(font)
    painter.setPen(QColor("#1D2433"))
    painter.drawText(12, 52, "user:       alice@example.com")
    painter.drawText(12, 78, "password:   hunter2-S3cret!")
    painter.drawText(12, 104, "token:       ghp_a1B2c3D4e5F6g7H8")
    painter.end()
    return img


def render_effect_preview(style: str, intensity: int, size: QSize = QSize(340, 116)) -> QPixmap:
    """Show what blur / pixelate at *intensity* does to the secret values of a sample."""
    img = _sample_image(size)
    k = max(1, int(intensity))
    painter = QPainter(img)
    for top in (38, 64, 90):
        box = QRect(112, top, size.width() - 124, 20)
        region = img.copy(box)
        w, h = region.width(), region.height()
        mode = Qt.TransformationMode.FastTransformation if style == "pixelate" else Qt.TransformationMode.SmoothTransformation
        small = region.scaled(max(1, w // k), max(1, h // k), Qt.AspectRatioMode.IgnoreAspectRatio, mode)
        painter.drawImage(box.topLeft(), small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, mode))
    painter.end()
    return QPixmap.fromImage(img)


class RedactConfigWidget(QWidget):
    """Embeddable editor for the saved :class:`RedactProfile` (no OK/Cancel inside).

    It loads the saved profile on construction (or uses *profile*), emits
    ``changed`` on every user edit, and never saves by itself: call
    :meth:`save` (or ``redact_profile.save_profile(widget.profile())``).
    It scrolls internally, so embed it directly in a tab or dialog.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None, profile: Optional[RedactProfile] = None) -> None:
        super().__init__(parent)
        self._loading = True
        self._kind_checks: dict = {}
        self._build()
        self.set_profile(profile if profile is not None else redact_profile.load_profile())
        self._loading = False

    # ------------------------------------------------------------- building

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("redact_config_body")
        body.setStyleSheet("#redact_config_body { background: transparent; }")
        scroll.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(2, 2, 10, 2)
        col.setSpacing(14)

        col.addWidget(self._build_detectors())
        col.addWidget(self._build_custom())
        col.addWidget(self._build_ocr())
        col.addWidget(self._build_style())
        col.addWidget(self._build_workflow())
        col.addStretch(1)

    def _build_detectors(self) -> QFrame:
        card, lay = _card("What to look for",
                          "Tick the kinds of sensitive text the scan should flag. Hover a name for an example.")
        bar = QHBoxLayout()
        for text, state in (("Select all", True), ("Select none", False)):
            b = QPushButton(text)
            _role(b, "ghost")
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, st=state: self._set_all_kinds(st))
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        for category in redact.KIND_CATEGORIES:
            heading = QLabel(category.upper())
            _role(heading, "caption")
            lay.addWidget(heading)
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(8)
            ids = [k for k, info in redact.KIND_INFO.items() if info[0] == category]
            for i, kind in enumerate(ids):
                _cat, description, example = redact.KIND_INFO[kind]
                cell = QWidget()
                cl = QVBoxLayout(cell)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setSpacing(0)
                cb = QCheckBox(redact.KINDS.get(kind, kind))
                cb.setToolTip(f"Example: {example}")
                cb.toggled.connect(self._emit_changed)
                cl.addWidget(cb)
                desc = QLabel(description)
                desc.setWordWrap(True)
                desc.setToolTip(f"Example: {example}")
                _role(desc, "caption")
                desc.setContentsMargins(26, 0, 0, 0)
                cl.addWidget(desc)
                grid.addWidget(cell, i // 2, i % 2)
                self._kind_checks[kind] = cb
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)
        return card

    def _build_custom(self) -> QFrame:
        card, lay = _card("Custom patterns",
                          "Your own regular expressions, e.g. an order id like ORD-\\d{6} or an internal hostname.")
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["On", "Name", "Regular expression"])
        self._table.verticalHeader().hide()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(1, 160)
        self._table.setMinimumHeight(130)
        self._table.itemChanged.connect(self._on_table_changed)
        lay.addWidget(self._table)

        row = QHBoxLayout()
        add = QPushButton("Add pattern")
        _try_icon(add, "plus")
        add.setAutoDefault(False)
        add.clicked.connect(lambda: self._add_pattern_row("", "", True, edit=True))
        rem = QPushButton("Remove selected")
        _role(rem, "ghost")
        _try_icon(rem, "trash")
        rem.setAutoDefault(False)
        rem.clicked.connect(self._remove_pattern_row)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)

        self._pattern_error = QLabel("")
        self._pattern_error.setWordWrap(True)
        _role(self._pattern_error, "error")
        self._pattern_error.hide()
        lay.addWidget(self._pattern_error)

        test = QHBoxLayout()
        self._sample_edit = QLineEdit()
        self._sample_edit.setPlaceholderText("Try your patterns on sample text, e.g. Ticket ORD-123456 failed")
        self._sample_edit.textChanged.connect(self._update_sample_result)
        test.addWidget(self._sample_edit, 1)
        lay.addLayout(test)
        self._sample_result = QLabel("")
        self._sample_result.setWordWrap(True)
        _role(self._sample_result, "muted")
        lay.addWidget(self._sample_result)
        return card

    def _build_ocr(self) -> QFrame:
        card, lay = _card("Text recognition (OCR)",
                          "Crabture reads the text in your screenshot with Tesseract, then looks for sensitive "
                          "items in it. These settings affect how well text is read.")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(QLabel("Language"), 0, 0)
        self._lang = QComboBox()
        self._lang.setEditable(True)
        self._lang.setValidator(QRegularExpressionValidator(QRegularExpression(r"[A-Za-z0-9_+\-]*")))
        self._lang.addItems(redact.list_languages())
        self._lang.setToolTip("Combine languages with '+', e.g. eng+deu. Extra packs: "
                              + ("tick them in the Tesseract installer" if sys.platform == "win32"
                                 else "sudo apt install tesseract-ocr-<lang>"))
        self._lang.currentTextChanged.connect(self._emit_changed)
        form.addWidget(self._lang, 0, 1)

        form.addWidget(QLabel("Layout mode"), 1, 0)
        self._psm = QComboBox()
        for value, label in redact_profile.PSM_CHOICES.items():
            self._psm.addItem(label, value)
        self._psm.currentIndexChanged.connect(self._emit_changed)
        form.addWidget(self._psm, 1, 1)

        form.addWidget(QLabel("Ignore uncertain words"), 2, 0)
        conf_row = QHBoxLayout()
        self._conf = QSlider(Qt.Orientation.Horizontal)
        self._conf.setRange(0, 100)
        self._conf_label = QLabel("off")
        self._conf_label.setMinimumWidth(40)
        self._conf.valueChanged.connect(self._on_conf_changed)
        conf_row.addWidget(self._conf, 1)
        conf_row.addWidget(self._conf_label)
        form.addLayout(conf_row, 2, 1)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)

        hint = QLabel("Words Tesseract is less than this sure about are skipped. Raise it if you see "
                      "false matches; keep it low to catch more.")
        hint.setWordWrap(True)
        _role(hint, "caption")
        lay.addWidget(hint)
        return card

    def _build_style(self) -> QFrame:
        card, lay = _card("How to hide it", "Applied to every finding as an ordinary layer — you can undo it.")
        chips = QHBoxLayout()
        self._style_group = QButtonGroup(self)
        self._style_group.setExclusive(True)
        self._style_buttons: dict = {}
        for value, label in (("blur", "Blur"), ("pixelate", "Pixelate")):
            b = QPushButton(label)
            _role(b, "chip")
            b.setCheckable(True)
            b.setAutoDefault(False)
            self._style_group.addButton(b)
            self._style_buttons[value] = b
            chips.addWidget(b)
        chips.addStretch(1)
        self._style_group.buttonToggled.connect(self._on_style_changed)
        lay.addLayout(chips)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addWidget(QLabel("Strength"), 0, 0)
        srow = QHBoxLayout()
        self._strength = QSlider(Qt.Orientation.Horizontal)
        self._strength.setRange(*redact_profile.INTENSITY_RANGE)
        self._strength_label = QLabel("12")
        self._strength_label.setMinimumWidth(28)
        self._strength.valueChanged.connect(self._on_style_changed)
        srow.addWidget(self._strength, 1)
        srow.addWidget(self._strength_label)
        form.addLayout(srow, 0, 1)
        form.addWidget(QLabel("Margin around text"), 1, 0)
        self._pad = QSpinBox()
        self._pad.setRange(*redact_profile.PAD_RANGE)
        self._pad.setSuffix(" px")
        self._pad.valueChanged.connect(self._emit_changed)
        form.addWidget(self._pad, 1, 1, Qt.AlignmentFlag.AlignLeft)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(122)
        lay.addWidget(self._preview)
        cap = QLabel("Preview on a sample — your real screenshot is never used here.")
        _role(cap, "caption")
        lay.addWidget(cap)
        return card

    def _build_workflow(self) -> QFrame:
        card, lay = _card("When you run the scan")
        self._review = QCheckBox("Review the findings before anything is changed")
        self._review.toggled.connect(self._on_review_toggled)
        lay.addWidget(self._review)
        note = QLabel("Recommended. The scan shows what it found and you click Apply redaction yourself.")
        note.setWordWrap(True)
        _role(note, "muted")
        note.setContentsMargins(26, 0, 0, 0)
        lay.addWidget(note)
        self._review_warning = QLabel(
            "Review is off: when you click Run, every match is hidden immediately, including false "
            "positives. Check the result and use Undo if something was covered by mistake."
        )
        self._review_warning.setWordWrap(True)
        _role(self._review_warning, "warning")
        self._review_warning.setContentsMargins(26, 0, 0, 0)
        lay.addWidget(self._review_warning)
        return card

    # ---------------------------------------------------------- profile <-> UI

    def set_profile(self, p: RedactProfile) -> None:
        """Show *p* in the controls (does not emit ``changed``)."""
        was = self._loading
        self._loading = True
        try:
            wanted = set(p.kinds)
            for kind, cb in self._kind_checks.items():
                cb.setChecked(kind in wanted)

            self._table.blockSignals(True)
            self._table.setRowCount(0)
            for c in p.custom_patterns:
                self._add_pattern_row(c.get("name", ""), c.get("regex", ""), bool(c.get("enabled", True)))
            self._table.blockSignals(False)
            self._validate_patterns()

            self._lang.setCurrentText(p.lang)
            idx = self._psm.findData(p.psm)
            self._psm.setCurrentIndex(idx if idx >= 0 else self._psm.findData(11))
            self._conf.setValue(p.min_confidence)
            self._on_conf_changed()
            self._style_buttons.get(p.style, self._style_buttons["blur"]).setChecked(True)
            self._strength.setValue(p.intensity)
            self._pad.setValue(p.pad)
            self._review.setChecked(p.review_before_apply)
            self._review_warning.setVisible(not p.review_before_apply)
            self._refresh_preview()
            self._update_sample_result()
        finally:
            self._loading = was

    def profile(self) -> RedactProfile:
        """The profile described by the current controls."""
        customs = []
        for r in range(self._table.rowCount()):
            regex = self._cell_text(r, 2)
            if not regex:
                continue
            on = self._table.item(r, 0)
            customs.append({
                "name": self._cell_text(r, 1) or "Pattern",
                "regex": regex,
                "enabled": on is not None and on.checkState() == Qt.CheckState.Checked,
            })
        style = "pixelate" if self._style_buttons["pixelate"].isChecked() else "blur"
        lang = self._lang.currentText().strip() or "eng"
        return RedactProfile(
            kinds=[k for k in redact_profile._builtin_kind_ids()
                   if k in self._kind_checks and self._kind_checks[k].isChecked()],   # canonical order
            custom_patterns=customs,
            lang=lang,
            psm=int(self._psm.currentData() or 11),
            style=style,
            intensity=int(self._strength.value()),
            pad=int(self._pad.value()),
            min_confidence=int(self._conf.value()),
            review_before_apply=self._review.isChecked(),
        )

    def reset_to_defaults(self) -> None:
        """Restore the default profile in the controls (user action: emits ``changed``)."""
        self.set_profile(redact_profile.default_profile())
        self.changed.emit()

    def save(self) -> None:
        """Persist the current controls (also emits ``profile_signals.changed``)."""
        redact_profile.save_profile(self.profile())

    def has_errors(self) -> bool:
        """True while an enabled custom pattern is not a valid regular expression."""
        return bool(self._validate_patterns())

    # --------------------------------------------------------------- handlers

    def _emit_changed(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    def _set_all_kinds(self, state: bool) -> None:
        was = self._loading
        self._loading = True
        for cb in self._kind_checks.values():
            cb.setChecked(state)
        self._loading = was
        self._emit_changed()

    def _on_conf_changed(self, *_args) -> None:
        v = self._conf.value()
        self._conf_label.setText("off" if v == 0 else f"{v}%")
        self._emit_changed()

    def _on_style_changed(self, *_args) -> None:
        self._strength_label.setText(str(self._strength.value()))
        self._refresh_preview()
        self._emit_changed()

    def _on_review_toggled(self, on: bool) -> None:
        self._review_warning.setVisible(not on)
        self._emit_changed()

    def _refresh_preview(self) -> None:
        style = "pixelate" if self._style_buttons["pixelate"].isChecked() else "blur"
        self._preview.setPixmap(render_effect_preview(style, self._strength.value()))

    # ---- custom pattern table

    def _cell_text(self, row: int, col: int) -> str:
        item = self._table.item(row, col)
        return item.text().strip() if item is not None else ""

    def _add_pattern_row(self, name: str, regex: str, enabled: bool, edit: bool = False) -> None:
        table = self._table
        was = table.signalsBlocked()
        table.blockSignals(True)
        r = table.rowCount()
        table.insertRow(r)
        on = QTableWidgetItem()
        on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        on.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        table.setItem(r, 0, on)
        table.setItem(r, 1, QTableWidgetItem(name))
        table.setItem(r, 2, QTableWidgetItem(regex))
        table.blockSignals(was)
        if edit:
            table.setCurrentCell(r, 1)
            table.editItem(table.item(r, 1))
        if not self._loading:
            self._validate_patterns()
            self._update_sample_result()
            self._emit_changed()

    def _remove_pattern_row(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
            self._validate_patterns()
            self._update_sample_result()
            self._emit_changed()

    def _on_table_changed(self, _item) -> None:
        self._validate_patterns()
        self._update_sample_result()
        self._emit_changed()

    def _validate_patterns(self) -> List[Tuple[int, str]]:
        """Colour invalid enabled regexes red; return [(row, message)] for them."""
        problems: List[Tuple[int, str]] = []
        was = self._table.signalsBlocked()
        self._table.blockSignals(True)
        for r in range(self._table.rowCount()):
            cell = self._table.item(r, 2)
            if cell is None:
                continue
            regex = cell.text().strip()
            on = self._table.item(r, 0)
            enabled = on is not None and on.checkState() == Qt.CheckState.Checked
            message = ""
            if regex:
                try:
                    re.compile(regex)
                except re.error as exc:
                    message = str(exc)
            if message and enabled:
                problems.append((r, message))
                cell.setForeground(_ERROR_COLOR)
                cell.setToolTip(f"Invalid regular expression: {message}")
            else:
                cell.setData(Qt.ItemDataRole.ForegroundRole, None)
                cell.setToolTip("Invalid (disabled)" if message else "")
        self._table.blockSignals(was)
        if problems:
            row, message = problems[0]
            self._pattern_error.setText(f"Row {row + 1}: {message}. Fix or untick it before saving.")
            self._pattern_error.show()
        else:
            self._pattern_error.hide()
        return problems

    def _update_sample_result(self, *_args) -> None:
        sample = self._sample_edit.text()
        if not sample:
            self._sample_result.setText("")
            return
        matches: List[str] = []
        tried = 0
        for r in range(self._table.rowCount()):
            on = self._table.item(r, 0)
            regex = self._cell_text(r, 2)
            if not regex or on is None or on.checkState() != Qt.CheckState.Checked:
                continue
            try:
                pattern = re.compile(regex)
            except re.error:
                continue
            tried += 1
            matches.extend(m.group(0) for m in pattern.finditer(sample) if m.group(0))
        if not tried:
            self._sample_result.setText("No enabled valid pattern to test.")
        elif matches:
            shown = ", ".join(f"“{m}”" for m in matches[:8])
            self._sample_result.setText(f"Matches: {shown}")
        else:
            self._sample_result.setText("No match.")


class RedactConfigDialog(QDialog):
    """Modal editor for the saved profile: Reset, Cancel, Save."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sensitive data — configuration")
        self.resize(760, 780)
        self.setMinimumSize(560, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("dialog_header")
        hv = QVBoxLayout(header)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(2)
        title = QLabel("Sensitive data — configuration")
        _role(title, "h1")
        sub = QLabel("Set this up once. “Run sensitive-data scan” then uses these settings every time. "
                     "Nothing is scanned or changed until you run it.")
        sub.setWordWrap(True)
        _role(sub, "muted")
        hv.addWidget(title)
        hv.addWidget(sub)
        root.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(20, 14, 20, 16)
        body.setSpacing(12)
        root.addLayout(body, 1)

        self._widget = RedactConfigWidget(self)
        body.addWidget(self._widget, 1)

        foot = QHBoxLayout()
        self._reset_btn = QPushButton("Reset to defaults")
        _role(self._reset_btn, "ghost")
        self._reset_btn.setAutoDefault(False)
        self._reset_btn.clicked.connect(self._widget.reset_to_defaults)
        self._cancel_btn = QPushButton("Cancel")
        _role(self._cancel_btn, "ghost")
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.clicked.connect(self.reject)
        self._save_btn = QPushButton("Save")
        _role(self._save_btn, "primary")
        self._save_btn.setAutoDefault(False)
        self._save_btn.clicked.connect(self._on_save)
        foot.addWidget(self._reset_btn)
        foot.addStretch(1)
        foot.addWidget(self._cancel_btn)
        foot.addWidget(self._save_btn)
        body.addLayout(foot)

        self._widget.changed.connect(self._update_save)
        self._update_save()
        from PyQt6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._on_save)

    def widget(self) -> RedactConfigWidget:
        return self._widget

    def _update_save(self) -> None:
        self._save_btn.setEnabled(not self._widget.has_errors())

    def _on_save(self) -> None:
        if self._widget.has_errors():
            return
        self._widget.save()
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════
#  Run (part 2 of 2): one click, saved profile
# ═══════════════════════════════════════════════════════════════════════════

def _run_without_review(pixmap: QPixmap, parent, profile: RedactProfile):
    """Scan behind a small cancellable progress dialog; return every finding.

    Only reached when the user turned the review step off in the configuration
    AND clicked Run.  Returns ``(rects, style, intensity)`` or ``None``.
    """
    image = pixmap.toImage()
    image.setDevicePixelRatio(1.0)
    bounds = QRectF(0, 0, image.width(), image.height())

    progress = QProgressDialog("Scanning for sensitive data…", "Cancel", 0, 0, parent)
    progress.setWindowTitle("Sensitive data scan")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)

    outcome: dict = {}
    loop = QEventLoop()
    worker = _OcrWorker(image, profile.lang, None, psm=profile.psm)
    worker.finished_ok.connect(lambda words: outcome.update(words=list(words)))
    worker.failed.connect(lambda msg: outcome.update(error=msg))
    worker.cancelled.connect(lambda: outcome.update(cancelled=True))

    def _done() -> None:
        outcome["done"] = True
        loop.quit()

    worker.finished.connect(_done)
    progress.canceled.connect(worker.cancel)

    worker.start()
    progress.show()
    # `done` is set from a queued signal, so checking it first avoids hanging in
    # exec() when the worker already finished (quit() before exec() is a no-op).
    while not outcome.get("done"):
        loop.exec()
    worker.wait(5000)
    try:
        progress.canceled.disconnect()
    except TypeError:
        pass
    progress.close()
    progress.deleteLater()

    if outcome.get("cancelled"):
        return None
    if "error" in outcome:
        QMessageBox.warning(parent, "Sensitive data scan", "Scan failed: " + outcome["error"])
        return None
    findings = redact_profile.findings_for_profile(outcome.get("words", []), profile, bounds)
    if not findings:
        QMessageBox.information(parent, "Sensitive data scan", "Nothing sensitive found.")
        return None
    return [QRectF(f.rect) for f in findings], profile.style, profile.intensity


def run_saved_profile(pixmap: QPixmap, parent=None) -> Optional[Tuple[List[QRectF], str, int]]:
    """Scan *pixmap* with the saved profile.  Call this ONLY from a user action.

    Default (review on): opens the review dialog, which starts scanning as soon
    as it is shown; the user checks the findings and clicks **Apply redaction**.
    Returns ``(rects, style, intensity)`` after Apply, otherwise ``None``
    (Cancel, closed, scan failed, nothing found, tesseract missing).

    With ``review_before_apply`` off in the profile, the scan runs behind a
    progress dialog and every finding is returned without a review list.
    """
    if pixmap is None or pixmap.isNull():
        return None
    if not redact.ocr_available():
        QMessageBox.warning(parent, "Sensitive data scan", redact.install_hint())
        return None
    profile = redact_profile.load_profile()
    if not profile.review_before_apply:
        return _run_without_review(pixmap, parent, profile)

    dlg = RedactDialog(pixmap, parent, profile=profile, autoscan=True)
    try:
        if dlg.exec() == QDialog.DialogCode.Accepted:
            rects = dlg.selected_rects()
            if rects:
                return rects, dlg.style(), dlg.intensity()
        return None
    finally:
        dlg.deleteLater()
