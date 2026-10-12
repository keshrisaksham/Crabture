"""
effects/dialog.py — EffectsDialog: live-preview editor for effects.EffectSettings.

    dlg = EffectsDialog(pixmap, parent)
    if dlg.exec():
        result = dlg.result_pixmap()      # full-resolution result

The preview runs the same pipeline on a downscaled copy of the image with all
pixel sizes scaled proportionally; the full-resolution pipeline only runs when
the dialog is accepted (or when ``result_pixmap()`` is first requested).
"""

from __future__ import annotations

import json
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QSettings, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from crabture.effects import core as fx
from crabture.effects.core import EffectSettings

_SETTINGS_ORG = "YawnByte"
_SETTINGS_APP = "Crabture"
_SETTINGS_GROUP = "effects"
_PREVIEW_MAX = 720          # longest side of the downscaled preview source
_CUSTOM_LABEL = "Custom"
_FIRST_RUN_PRESET = "Clean shadow"


# ---------------------------------------------------------------------------
#  Small widgets
# ---------------------------------------------------------------------------

class _ColorButton(QPushButton):
    """A button that shows its colour as an icon swatch and opens a picker."""

    def __init__(self, color: str, on_change: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self._color = QColor(color)
        self.setIconSize(QSize(36, 14))
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color.name().upper()

    def set_color(self, value: str) -> None:
        c = QColor(value)
        if c.isValid():
            self._color = c
            self._refresh()

    def _refresh(self) -> None:
        pm = QPixmap(36, 14)
        pm.fill(self._color)
        p = QPainter(pm)
        p.setPen(QColor(128, 128, 128))
        p.drawRect(0, 0, 35, 13)
        p.end()
        self.setIcon(QIcon(pm))
        self.setText(self.color())

    def _pick(self) -> None:
        c = QColorDialog.getColor(self._color, self, "Pick colour")
        if c.isValid():
            self._color = c
            self._refresh()
            self._on_change()


class _PreviewWidget(QWidget):
    """Shows a pixmap scaled to fit, over a checkerboard (palette-neutral)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_pixmap(self, pm: QPixmap) -> None:
        self._pixmap = pm
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.window().color())
        if self._pixmap is None or self._pixmap.isNull():
            return
        avail = self.rect().adjusted(8, 8, -8, -8)
        pm = self._pixmap
        scaled = pm.scaled(avail.size(), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation) \
            if (pm.width() > avail.width() or pm.height() > avail.height()) else pm
        x = avail.x() + (avail.width() - scaled.width()) // 2
        y = avail.y() + (avail.height() - scaled.height()) // 2
        # checkerboard under the image so transparency is visible
        base = pal.window().color()
        light = base.lighter(115) if base.lightness() < 128 else base.darker(104)
        dark = base.lighter(135) if base.lightness() < 128 else base.darker(118)
        cell = 10
        p.setClipRect(x, y, scaled.width(), scaled.height())
        for cy in range(0, scaled.height(), cell):
            for cx in range(0, scaled.width(), cell):
                p.fillRect(x + cx, y + cy, cell, cell, dark if (cx // cell + cy // cell) % 2 else light)
        p.setClipping(False)
        p.drawPixmap(x, y, scaled)
        p.end()


# ---------------------------------------------------------------------------
#  Dialog
# ---------------------------------------------------------------------------

class EffectsDialog(QDialog):
    """Pick image effects with a live preview."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Effects")
        self.resize(1120, 740)

        self._source: QImage = pixmap.toImage()
        longest = max(1, max(self._source.width(), self._source.height()))
        if longest > _PREVIEW_MAX:
            self._preview_src = self._source.scaled(
                _PREVIEW_MAX, _PREVIEW_MAX, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            self._preview_src = self._source
        self._scale = self._preview_src.width() / max(1, self._source.width())
        self._result: Optional[QPixmap] = None
        self._building = True

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._update_preview)

        self._build_ui()
        self._building = False
        self._apply_to_widgets(self._load_settings())
        self._update_preview()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        # ---- left: controls -------------------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)

        top = QHBoxLayout()
        top.addWidget(QLabel("Preset:"))
        self._preset = QComboBox()
        self._preset.addItem(_CUSTOM_LABEL)
        for name in fx.PRESETS:
            self._preset.addItem(name)
        self._preset.activated.connect(self._on_preset)
        top.addWidget(self._preset, 1)
        self._btn_reset = QPushButton("Reset")
        self._btn_reset.setToolTip("Disable every effect")
        self._btn_reset.clicked.connect(self._on_reset)
        top.addWidget(self._btn_reset)
        lv.addLayout(top)

        # Torn edge ---------------------------------------------------------
        self._g_torn = self._group("Torn edge")
        f = QFormLayout(self._g_torn)
        edges = QHBoxLayout()
        self._torn_cb: Dict[str, QCheckBox] = {}
        for name in fx.TORN_EDGE_NAMES:
            cb = QCheckBox(name.capitalize())
            cb.toggled.connect(self._changed)
            self._torn_cb[name] = cb
            edges.addWidget(cb)
        f.addRow("Edges:", edges)
        self._torn_depth = self._spin(1, 120, 10)
        f.addRow("Depth (px):", self._torn_depth)
        self._torn_teeth = self._spin(3, 300, 24)
        f.addRow("Teeth:", self._torn_teeth)
        seed_row = QHBoxLayout()
        self._torn_seed = self._spin(0, 99999, 7)
        seed_row.addWidget(self._torn_seed, 1)
        btn_seed = QPushButton("Randomize")
        btn_seed.clicked.connect(self._randomize_seed)
        seed_row.addWidget(btn_seed)
        f.addRow("Seed:", seed_row)
        lv.addWidget(self._g_torn)

        # Border ------------------------------------------------------------
        self._g_border = self._group("Border")
        f = QFormLayout(self._g_border)
        self._border_width = self._spin(1, 200, 4)
        f.addRow("Width (px):", self._border_width)
        self._border_color = _ColorButton("#000000", self._changed)
        f.addRow("Colour:", self._border_color)
        self._border_radius = self._spin(0, 200, 0)
        f.addRow("Corner radius:", self._border_radius)
        lv.addWidget(self._g_border)

        # Canvas frame ------------------------------------------------------
        self._g_frame = self._group("Canvas frame (padding + background)")
        f = QFormLayout(self._g_frame)
        self._frame_padding = self._spin(0, 800, 48)
        f.addRow("Padding (px):", self._frame_padding)
        self._frame_style = QComboBox()
        for label, key in (("Gradient", "gradient"), ("Solid colour", "solid"), ("Transparent", "transparent")):
            self._frame_style.addItem(label, key)
        self._frame_style.currentIndexChanged.connect(self._changed)
        f.addRow("Background:", self._frame_style)
        self._frame_c1 = _ColorButton("#667EEA", self._changed)
        f.addRow("Colour 1:", self._frame_c1)
        self._frame_c2 = _ColorButton("#764BA2", self._changed)
        f.addRow("Colour 2:", self._frame_c2)
        self._frame_angle = self._spin(0, 359, 135)
        f.addRow("Gradient angle:", self._frame_angle)
        self._image_radius = self._spin(0, 200, 12)
        f.addRow("Image corner radius:", self._image_radius)
        lv.addWidget(self._g_frame)

        # Shadow ------------------------------------------------------------
        self._g_shadow = self._group("Drop shadow")
        f = QFormLayout(self._g_shadow)
        self._shadow_blur = self._spin(0, 200, 24)
        f.addRow("Blur (px):", self._shadow_blur)
        off = QHBoxLayout()
        self._shadow_dx = self._spin(-200, 200, 0)
        self._shadow_dy = self._spin(-200, 200, 10)
        off.addWidget(QLabel("X"))
        off.addWidget(self._shadow_dx)
        off.addWidget(QLabel("Y"))
        off.addWidget(self._shadow_dy)
        f.addRow("Offset:", off)
        self._shadow_color = _ColorButton("#000000", self._changed)
        f.addRow("Colour:", self._shadow_color)
        self._shadow_opacity = self._slider(0, 100, 55)
        f.addRow("Opacity:", self._shadow_opacity[2])
        lv.addWidget(self._g_shadow)

        # Watermark ---------------------------------------------------------
        self._g_wm = self._group("Watermark")
        f = QFormLayout(self._g_wm)
        self._wm_text = QLineEdit("Crabture")
        self._wm_text.textChanged.connect(self._changed)
        f.addRow("Text:", self._wm_text)
        img_row = QHBoxLayout()
        self._wm_image = QLineEdit()
        self._wm_image.setPlaceholderText("optional logo image")
        self._wm_image.textChanged.connect(self._changed)
        img_row.addWidget(self._wm_image, 1)
        btn_img = QPushButton("Browse…")
        btn_img.clicked.connect(self._browse_logo)
        img_row.addWidget(btn_img)
        f.addRow("Image:", img_row)
        self._wm_size = self._spin(6, 400, 18)
        f.addRow("Font size (px):", self._wm_size)
        self._wm_color = _ColorButton("#FFFFFF", self._changed)
        f.addRow("Colour:", self._wm_color)
        self._wm_opacity = self._slider(0, 100, 60)
        f.addRow("Opacity:", self._wm_opacity[2])
        self._wm_pos = QComboBox()
        for key in fx.WATERMARK_POSITIONS:
            self._wm_pos.addItem(key.replace("-", " ").title(), key)
        self._wm_pos.currentIndexChanged.connect(self._changed)
        f.addRow("Position:", self._wm_pos)
        self._wm_margin = self._spin(0, 400, 12)
        f.addRow("Margin (px):", self._wm_margin)
        lv.addWidget(self._g_wm)

        lv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(360)
        scroll.setMaximumWidth(420)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body.addWidget(scroll)

        # ---- right: preview -------------------------------------------------
        right = QVBoxLayout()
        self._preview = _PreviewWidget()
        right.addWidget(self._preview, 1)
        self._info = QLabel("")
        self._info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self._info)
        body.addLayout(right, 1)

        # ---- buttons --------------------------------------------------------
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def _group(self, title: str) -> QGroupBox:
        g = QGroupBox(title)
        g.setCheckable(True)
        g.setChecked(False)
        g.toggled.connect(self._changed)
        return g

    def _spin(self, lo: int, hi: int, value: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(value)
        s.valueChanged.connect(self._changed)
        return s

    def _slider(self, lo: int, hi: int, value: int):
        """Returns (slider, label, container) wired to ``_changed``."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(value)
        lab = QLabel(f"{value}%")
        lab.setMinimumWidth(36)
        sl.valueChanged.connect(lambda v, lab=lab: lab.setText(f"{v}%"))
        sl.valueChanged.connect(self._changed)
        h.addWidget(sl, 1)
        h.addWidget(lab)
        return sl, lab, w

    # ------------------------------------------------------------ settings

    def settings(self) -> EffectSettings:
        """The settings currently shown in the controls."""
        edges = ",".join(n for n in fx.TORN_EDGE_NAMES if self._torn_cb[n].isChecked())
        return EffectSettings(
            torn_enabled=self._g_torn.isChecked() and bool(edges),
            torn_edges=edges or "bottom",
            torn_depth=self._torn_depth.value(),
            torn_teeth=self._torn_teeth.value(),
            torn_seed=self._torn_seed.value(),
            border_enabled=self._g_border.isChecked(),
            border_width=self._border_width.value(),
            border_color=self._border_color.color(),
            border_radius=self._border_radius.value(),
            frame_enabled=self._g_frame.isChecked(),
            frame_padding=self._frame_padding.value(),
            frame_style=self._frame_style.currentData() or "gradient",
            frame_color1=self._frame_c1.color(),
            frame_color2=self._frame_c2.color(),
            frame_angle=self._frame_angle.value(),
            image_radius=self._image_radius.value(),
            shadow_enabled=self._g_shadow.isChecked(),
            shadow_blur=self._shadow_blur.value(),
            shadow_offset_x=self._shadow_dx.value(),
            shadow_offset_y=self._shadow_dy.value(),
            shadow_color=self._shadow_color.color(),
            shadow_opacity=self._shadow_opacity[0].value(),
            watermark_enabled=self._g_wm.isChecked(),
            watermark_text=self._wm_text.text(),
            watermark_font_size=self._wm_size.value(),
            watermark_color=self._wm_color.color(),
            watermark_opacity=self._wm_opacity[0].value(),
            watermark_position=self._wm_pos.currentData() or "bottom-right",
            watermark_margin=self._wm_margin.value(),
            watermark_image_path=self._wm_image.text().strip(),
        )

    def _apply_to_widgets(self, s: EffectSettings) -> None:
        was = self._building
        self._building = True
        try:
            self._g_torn.setChecked(s.torn_enabled)
            active = {e.strip() for e in s.torn_edges.split(",")}
            for n, cb in self._torn_cb.items():
                cb.setChecked(n in active)
            self._torn_depth.setValue(s.torn_depth)
            self._torn_teeth.setValue(s.torn_teeth)
            self._torn_seed.setValue(s.torn_seed)
            self._g_border.setChecked(s.border_enabled)
            self._border_width.setValue(s.border_width)
            self._border_color.set_color(s.border_color)
            self._border_radius.setValue(s.border_radius)
            self._g_frame.setChecked(s.frame_enabled)
            self._frame_padding.setValue(s.frame_padding)
            i = self._frame_style.findData(s.frame_style)
            self._frame_style.setCurrentIndex(max(0, i))
            self._frame_c1.set_color(s.frame_color1)
            self._frame_c2.set_color(s.frame_color2)
            self._frame_angle.setValue(s.frame_angle)
            self._image_radius.setValue(s.image_radius)
            self._g_shadow.setChecked(s.shadow_enabled)
            self._shadow_blur.setValue(s.shadow_blur)
            self._shadow_dx.setValue(s.shadow_offset_x)
            self._shadow_dy.setValue(s.shadow_offset_y)
            self._shadow_color.set_color(s.shadow_color)
            self._shadow_opacity[0].setValue(s.shadow_opacity)
            self._g_wm.setChecked(s.watermark_enabled)
            self._wm_text.setText(s.watermark_text)
            self._wm_image.setText(s.watermark_image_path)
            self._wm_size.setValue(s.watermark_font_size)
            self._wm_color.set_color(s.watermark_color)
            self._wm_opacity[0].setValue(s.watermark_opacity)
            j = self._wm_pos.findData(s.watermark_position)
            self._wm_pos.setCurrentIndex(max(0, j))
            self._wm_margin.setValue(s.watermark_margin)
        finally:
            self._building = was
        self._sync_preset_label()
        self._changed()

    # -- persistence ----------------------------------------------------------

    @staticmethod
    def _qsettings() -> QSettings:
        return QSettings(_SETTINGS_ORG, _SETTINGS_APP)

    def _load_settings(self) -> EffectSettings:
        qs = self._qsettings()
        qs.beginGroup(_SETTINGS_GROUP)
        raw = qs.value("last", "")
        qs.endGroup()
        if raw:
            try:
                return fx.settings_from_dict(json.loads(str(raw)))
            except (ValueError, TypeError):
                pass
        return fx.PRESETS[_FIRST_RUN_PRESET]

    def _save_settings(self) -> None:
        qs = self._qsettings()
        qs.beginGroup(_SETTINGS_GROUP)
        qs.setValue("last", json.dumps(fx.settings_to_dict(self.settings())))
        qs.endGroup()
        qs.sync()

    # ------------------------------------------------------------- actions

    def _changed(self, *_args) -> None:
        if self._building:
            return
        self._result = None
        self._sync_preset_label()
        self._timer.start()

    def _sync_preset_label(self) -> None:
        cur = self.settings()
        name = _CUSTOM_LABEL
        for pname, ps in fx.PRESETS.items():
            if ps == cur or (not ps.any_enabled() and not cur.any_enabled()):
                name = pname
                break
        self._preset.blockSignals(True)
        self._preset.setCurrentText(name)
        self._preset.blockSignals(False)

    def _on_preset(self, _index: int) -> None:
        name = self._preset.currentText()
        if name in fx.PRESETS:
            self._apply_to_widgets(fx.PRESETS[name])

    def _on_reset(self) -> None:
        self._apply_to_widgets(EffectSettings())

    def _randomize_seed(self) -> None:
        import random
        self._torn_seed.setValue(random.randint(0, 99999))

    def _browse_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Watermark image", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp *.svg)")
        if path:
            self._wm_image.setText(path)

    def _update_preview(self) -> None:
        s = fx.scale_settings(self.settings(), self._scale)
        out = fx.apply_pipeline(self._preview_src, s)
        self._preview.set_pixmap(QPixmap.fromImage(out))
        # report the size the full-resolution result will have
        est_w = round(out.width() / self._scale) if self._scale else out.width()
        est_h = round(out.height() / self._scale) if self._scale else out.height()
        self._info.setText(f"Result ≈ {est_w} × {est_h} px")

    # -------------------------------------------------------------- results

    def result_pixmap(self) -> QPixmap:
        """Full-resolution result of the current settings (cached)."""
        if self._result is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                out = fx.apply_pipeline(self._source, self.settings())
                self._result = QPixmap.fromImage(out)
            finally:
                QApplication.restoreOverrideCursor()
        return self._result

    def accept(self) -> None:
        self._timer.stop()
        self._save_settings()
        self.result_pixmap()        # compute now so the caller gets it instantly
        super().accept()
