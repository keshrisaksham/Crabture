"""
effects/core.py — ShareX-style image effects for Crabture.

Pure functions on ``QImage`` (no widgets, no QApplication needed) plus an
``EffectSettings`` dataclass describing a whole effect stack.

Pipeline order (``apply_pipeline``)::

    torn edge -> border -> rounded corners (frame only) -> shadow
              -> canvas frame / background -> watermark

* The torn edge keeps the image size and cuts a seeded zig-zag (paper tear)
  into the chosen edges with transparency.
* The border grows the image by ``border_width`` on every side and follows the
  torn outline / rounded corners.
* ``image_radius`` rounds the screenshot's corners, but only when the frame is
  enabled.
* The shadow grows the canvas by ``blur`` + offset on a transparent
  background, so it works with or without the frame.  When both are enabled the
  frame padding is added *around* the shadowed image.
* The frame adds ``frame_padding`` on every side with a solid / gradient /
  transparent background.
* The watermark is drawn last so it sits on the final canvas.

Every function returns a new ``Format_ARGB32_Premultiplied`` image with a
device-pixel-ratio of 1 and never mutates its input.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

_FMT = QImage.Format.Format_ARGB32_Premultiplied

TORN_EDGE_NAMES = ("top", "bottom", "left", "right")
WATERMARK_POSITIONS = (
    "top-left", "top-center", "top-right",
    "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)
_POSITION_ALIASES = {
    "tl": "top-left", "tc": "top-center", "tr": "top-right",
    "ml": "middle-left", "mc": "middle-center", "mr": "middle-right",
    "bl": "bottom-left", "bc": "bottom-center", "br": "bottom-right",
}
FRAME_STYLES = ("solid", "gradient", "transparent")


# ---------------------------------------------------------------------------
#  Settings
# ---------------------------------------------------------------------------

@dataclass
class EffectSettings:
    """A full effect stack.  Every effect is disabled by default."""

    # torn edge
    torn_enabled: bool = False
    torn_edges: str = "bottom"        # comma list of top,bottom,left,right
    torn_depth: int = 10
    torn_teeth: int = 24
    torn_seed: int = 7
    # border
    border_enabled: bool = False
    border_width: int = 4
    border_color: str = "#000000"
    border_radius: int = 0
    # canvas frame (padding + background)
    frame_enabled: bool = False
    frame_padding: int = 48
    frame_style: str = "gradient"     # solid | gradient | transparent
    frame_color1: str = "#667EEA"
    frame_color2: str = "#764BA2"
    frame_angle: int = 135            # CSS style: 0 = to top, 90 = to right, 135 = to bottom-right
    image_radius: int = 12
    # shadow
    shadow_enabled: bool = False
    shadow_blur: int = 24
    shadow_offset_x: int = 0
    shadow_offset_y: int = 10
    shadow_color: str = "#000000"
    shadow_opacity: int = 55          # 0..100
    # watermark
    watermark_enabled: bool = False
    watermark_text: str = "Crabture"
    watermark_font_size: int = 18     # pixels
    watermark_color: str = "#FFFFFF"
    watermark_opacity: int = 60       # 0..100
    watermark_position: str = "bottom-right"
    watermark_margin: int = 12
    watermark_image_path: str = ""

    def any_enabled(self) -> bool:
        return (self.torn_enabled or self.border_enabled or self.frame_enabled
                or self.shadow_enabled or self.watermark_enabled)


def settings_to_dict(s: EffectSettings) -> Dict[str, Any]:
    return asdict(s)


def settings_from_dict(d: Dict[str, Any]) -> EffectSettings:
    """Build settings from a (possibly partial / malformed) dict.

    Unknown keys are ignored; values are coerced to the field's default type
    and fall back to the default when coercion fails.
    """
    base = EffectSettings()
    kwargs: Dict[str, Any] = {}
    for f in fields(EffectSettings):
        if not isinstance(d, dict) or f.name not in d:
            continue
        default = getattr(base, f.name)
        value = d[f.name]
        try:
            if isinstance(default, bool):
                if isinstance(value, str):
                    value = value.strip().lower() in ("1", "true", "yes", "on")
                else:
                    value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            continue
        kwargs[f.name] = value
    return replace(base, **kwargs)


def scale_settings(s: EffectSettings, k: float) -> EffectSettings:
    """Scale every pixel-based size by *k* (used for downscaled previews)."""
    def sc(v: int, minimum: int = 0) -> int:
        if v == 0:
            return 0
        return max(minimum, int(round(v * k)))

    return replace(
        s,
        torn_depth=sc(s.torn_depth, 1),
        border_width=sc(s.border_width, 1),
        border_radius=sc(s.border_radius, 1),
        frame_padding=sc(s.frame_padding, 1),
        image_radius=sc(s.image_radius, 1),
        shadow_blur=sc(s.shadow_blur, 1),
        shadow_offset_x=int(round(s.shadow_offset_x * k)),
        shadow_offset_y=int(round(s.shadow_offset_y * k)),
        watermark_font_size=sc(s.watermark_font_size, 6),
        watermark_margin=sc(s.watermark_margin, 1),
    )


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _norm(img: QImage) -> QImage:
    """Return an ARGB32-premultiplied copy with DPR 1 (input untouched)."""
    out = img.convertToFormat(_FMT)
    if out.devicePixelRatio() != 1.0:
        out = out.copy()
        out.setDevicePixelRatio(1.0)
    return out


def _canvas(w: int, h: int) -> QImage:
    out = QImage(max(1, w), max(1, h), _FMT)
    out.fill(Qt.GlobalColor.transparent)
    return out


def _color(value: str, fallback: str = "#000000") -> QColor:
    c = QColor(value)
    return c if c.isValid() else QColor(fallback)


def _clip_to_path(img: QImage, path: QPainterPath) -> QImage:
    """Keep only the part of *img* inside *path* (anti-aliased)."""
    mask = _canvas(img.width(), img.height())
    p = QPainter(mask)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillPath(path, QColor("white"))
    p.end()
    out = img.copy()
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawImage(0, 0, mask)
    p.end()
    return out


def _soft_blur(img: QImage, radius: float) -> QImage:
    """Approximate gaussian blur with repeated smooth down/up scaling."""
    if radius < 1.0:
        return img
    w, h = img.width(), img.height()
    f = max(2, int(round(radius / 2.0)))
    out = img
    for _ in range(3):
        sw, sh = max(1, w // f), max(1, h // f)
        small = out.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        out = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    return out


# ---------------------------------------------------------------------------
#  Rounded corners
# ---------------------------------------------------------------------------

def round_corners(img: QImage, radius: int) -> QImage:
    img = _norm(img)
    r = min(float(radius), img.width() / 2.0, img.height() / 2.0)
    if r <= 0:
        return img
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, img.width(), img.height()), r, r)
    return _clip_to_path(img, path)


# ---------------------------------------------------------------------------
#  Torn edge
# ---------------------------------------------------------------------------

def _parse_edges(edges: str) -> List[str]:
    return [e for e in (x.strip().lower() for x in str(edges).split(",")) if e in TORN_EDGE_NAMES]


def _torn_path(w: int, h: int, edges: str, depth: int, teeth: int, seed: int) -> QPainterPath:
    """Outline of the *kept* area: the chosen edges are replaced by a seeded
    zig-zag that bites up to *depth* pixels into the image."""
    torn = set(_parse_edges(edges))
    depth = max(0, min(int(depth), min(w, h) // 4))
    rng = random.Random(seed)
    longest = max(w, h)

    def offsets(length: float) -> List[Tuple[float, float]]:
        """[(t, inset)] for t in 0..1 with zero inset at both ends."""
        n = max(3, int(round(teeth * length / max(1, longest))))
        pts = [(0.0, 0.0)]
        for i in range(1, n):
            t = i / n + (rng.random() - 0.5) * 0.6 / n
            if i % 2:       # deep bite
                d = depth * (0.6 + 0.4 * rng.random())
            else:           # shallow
                d = depth * 0.45 * rng.random()
            pts.append((t, d))
        pts.append((1.0, 0.0))
        return pts

    poly: List[QPointF] = []
    # top: (0,0) -> (w,0), inward = +y
    if "top" in torn and depth > 0:
        poly += [QPointF(w * t, d) for t, d in offsets(w)[:-1]]
    else:
        poly.append(QPointF(0, 0))
    # right: (w,0) -> (w,h), inward = -x
    if "right" in torn and depth > 0:
        poly += [QPointF(w - d, h * t) for t, d in offsets(h)[:-1]]
    else:
        poly.append(QPointF(w, 0))
    # bottom: (w,h) -> (0,h), inward = -y
    if "bottom" in torn and depth > 0:
        poly += [QPointF(w * (1 - t), h - d) for t, d in offsets(w)[:-1]]
    else:
        poly.append(QPointF(w, h))
    # left: (0,h) -> (0,0), inward = +x
    if "left" in torn and depth > 0:
        poly += [QPointF(d, h * (1 - t)) for t, d in offsets(h)[:-1]]
    else:
        poly.append(QPointF(0, h))

    path = QPainterPath()
    path.addPolygon(QPolygonF(poly))
    path.closeSubpath()
    return path


def torn_edge(img: QImage, edges: str = "bottom", depth: int = 10,
              teeth: int = 24, seed: int = 7) -> QImage:
    img = _norm(img)
    if not _parse_edges(edges) or depth <= 0:
        return img
    return _clip_to_path(img, _torn_path(img.width(), img.height(), edges, depth, teeth, seed))


# ---------------------------------------------------------------------------
#  Border
# ---------------------------------------------------------------------------

def add_border(img: QImage, width: int, color: str = "#000000", radius: int = 0,
               shape: Optional[QPainterPath] = None) -> QImage:
    """Grow *img* by *width* px on every side with a border in *color*.

    *shape* (image coordinates) is the outline to stroke, e.g. a torn edge
    path; by default the image rectangle, with rounded corners if *radius*.
    """
    img = _norm(img)
    bw = int(width)
    if bw <= 0:
        return img
    w, h = img.width(), img.height()
    join = Qt.PenJoinStyle.RoundJoin
    if shape is None:
        r = min(float(radius), w / 2.0, h / 2.0)
        shape = QPainterPath()
        if r > 0:
            shape.addRoundedRect(QRectF(0, 0, w, h), r, r)
            img = round_corners(img, int(r))
        else:
            shape.addRect(QRectF(0, 0, w, h))
            join = Qt.PenJoinStyle.MiterJoin

    out = _canvas(w + 2 * bw, h + 2 * bw)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(bw, bw)
    pen = QPen(_color(color), 2 * bw)
    pen.setJoinStyle(join)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(shape)           # the inner half of the stroke is hidden by the image
    p.drawImage(0, 0, img)
    p.end()
    return out


# ---------------------------------------------------------------------------
#  Shadow
# ---------------------------------------------------------------------------

def add_shadow(img: QImage, blur: int = 24, dx: int = 0, dy: int = 10,
               color: str = "#000000", opacity: int = 55) -> QImage:
    """Grow the canvas and draw a blurred drop shadow of *img*'s silhouette."""
    img = _norm(img)
    blur = max(0, int(blur))
    dx, dy = int(dx), int(dy)
    if blur == 0 and dx == 0 and dy == 0:
        return img
    iw, ih = img.width(), img.height()
    pad_l, pad_r = max(0, blur - dx), max(0, blur + dx)
    pad_t, pad_b = max(0, blur - dy), max(0, blur + dy)
    cw, ch = iw + pad_l + pad_r, ih + pad_t + pad_b

    # The shadow is soft, so build it on a reduced canvas and scale it up.
    scale = min(1.0, 480.0 / max(cw, ch))
    sw, sh = max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))
    small_img = img.scaled(max(1, int(round(iw * scale))), max(1, int(round(ih * scale))),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    tint = _color(color)
    tint.setAlphaF(max(0, min(100, int(opacity))) / 100.0)
    sil = QImage(small_img.size(), _FMT)
    sil.fill(tint)
    p = QPainter(sil)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawImage(0, 0, small_img)
    p.end()

    layer = _canvas(sw, sh)
    p = QPainter(layer)
    p.drawImage(int(round((pad_l + dx) * scale)), int(round((pad_t + dy) * scale)), sil)
    p.end()
    layer = _soft_blur(layer, blur * scale)
    layer = layer.scaled(cw, ch, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    p = QPainter(layer)
    p.drawImage(pad_l, pad_t, img)
    p.end()
    return layer


# ---------------------------------------------------------------------------
#  Frame / background
# ---------------------------------------------------------------------------

def _gradient(w: int, h: int, angle: float, c1: QColor, c2: QColor) -> QLinearGradient:
    """CSS-style linear gradient spanning the whole *w* x *h* box."""
    a = math.radians(angle)
    ux, uy = math.sin(a), -math.cos(a)
    half = (abs(ux) * w + abs(uy) * h) / 2.0
    cx, cy = w / 2.0, h / 2.0
    g = QLinearGradient(cx - ux * half, cy - uy * half, cx + ux * half, cy + uy * half)
    g.setColorAt(0.0, c1)
    g.setColorAt(1.0, c2)
    return g


def add_frame(img: QImage, padding: int = 48, style: str = "gradient",
              color1: str = "#667EEA", color2: str = "#764BA2", angle: int = 135,
              image_radius: int = 0) -> QImage:
    """Surround *img* with *padding* px of solid / gradient / transparent canvas."""
    img = _norm(img)
    pad = max(0, int(padding))
    if image_radius > 0:
        img = round_corners(img, image_radius)
    w, h = img.width() + 2 * pad, img.height() + 2 * pad
    out = _canvas(w, h)
    p = QPainter(out)
    if style == "solid":
        p.fillRect(0, 0, w, h, _color(color1))
    elif style == "gradient":
        p.fillRect(0, 0, w, h, _gradient(w, h, angle, _color(color1), _color(color2, "#FFFFFF")))
    p.drawImage(pad, pad, img)
    p.end()
    return out


# ---------------------------------------------------------------------------
#  Watermark
# ---------------------------------------------------------------------------

def add_watermark(img: QImage, text: str = "Crabture", font_size: int = 18,
                  color: str = "#FFFFFF", opacity: int = 60,
                  position: str = "bottom-right", margin: int = 12,
                  image_path: str = "") -> QImage:
    """Draw a text and/or logo watermark.  Returns a copy of *img*."""
    out = _norm(img)
    font_size = max(4, int(font_size))
    logo: Optional[QImage] = None
    if image_path:
        loaded = QImage(image_path)
        if not loaded.isNull():
            lh = max(8, font_size * 2)
            logo = loaded.convertToFormat(_FMT).scaledToHeight(
                lh, Qt.TransformationMode.SmoothTransformation)
    if not text and logo is None:
        return out

    font = QFont("Sans Serif")
    font.setPixelSize(font_size)
    font.setBold(True)
    fm = QFontMetrics(font)
    tw = fm.horizontalAdvance(text) if text else 0
    th = fm.height() if text else 0
    gap = font_size // 2 if (text and logo is not None) else 0
    lw = logo.width() if logo is not None else 0
    lh = logo.height() if logo is not None else 0
    bw_, bh_ = lw + gap + tw, max(th, lh)

    pos = _POSITION_ALIASES.get(position, position)
    if pos not in WATERMARK_POSITIONS:
        pos = "bottom-right"
    vert, horiz = pos.split("-")
    m = int(margin)
    if horiz == "left":
        x = m
    elif horiz == "center":
        x = (out.width() - bw_) // 2
    else:
        x = out.width() - bw_ - m
    if vert == "top":
        y = m
    elif vert == "middle":
        y = (out.height() - bh_) // 2
    else:
        y = out.height() - bh_ - m

    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    op = max(0, min(100, int(opacity))) / 100.0
    p.setOpacity(op)
    if logo is not None:
        p.drawImage(x, y + (bh_ - lh) // 2, logo)
    if text:
        p.setFont(font)
        tx, ty = x + lw + gap, y + (bh_ - th) // 2 + fm.ascent()
        main = _color(color, "#FFFFFF")
        outline = QColor("#000000") if main.lightness() > 128 else QColor("#FFFFFF")
        p.setOpacity(op * 0.35)
        p.setPen(outline)
        p.drawText(tx + 1, ty + 1, text)
        p.setOpacity(op)
        p.setPen(main)
        p.drawText(tx, ty, text)
    p.end()
    return out


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

def apply_pipeline(img: QImage, s: EffectSettings) -> QImage:
    """Run every enabled effect in the documented order."""
    out = _norm(img)
    shape: Optional[QPainterPath] = None

    if s.torn_enabled and _parse_edges(s.torn_edges) and s.torn_depth > 0:
        out = torn_edge(out, s.torn_edges, s.torn_depth, s.torn_teeth, s.torn_seed)
        shape = _torn_path(out.width(), out.height(), s.torn_edges,
                           s.torn_depth, s.torn_teeth, s.torn_seed)
    if s.border_enabled and s.border_width > 0:
        out = add_border(out, s.border_width, s.border_color, s.border_radius, shape)
    if s.frame_enabled and s.image_radius > 0:
        out = round_corners(out, s.image_radius)
    if s.shadow_enabled:
        out = add_shadow(out, s.shadow_blur, s.shadow_offset_x, s.shadow_offset_y,
                         s.shadow_color, s.shadow_opacity)
    if s.frame_enabled:
        out = add_frame(out, s.frame_padding, s.frame_style, s.frame_color1,
                        s.frame_color2, s.frame_angle, 0)
    if s.watermark_enabled:
        out = add_watermark(out, s.watermark_text, s.watermark_font_size,
                            s.watermark_color, s.watermark_opacity,
                            s.watermark_position, s.watermark_margin,
                            s.watermark_image_path)
    return out


# ---------------------------------------------------------------------------
#  Built-in presets (used by the dialog)
# ---------------------------------------------------------------------------

PRESETS: Dict[str, EffectSettings] = {
    "None": EffectSettings(),
    "Clean shadow": EffectSettings(
        shadow_enabled=True, shadow_blur=24, shadow_offset_y=10, shadow_opacity=45,
        frame_enabled=True, frame_padding=40, frame_style="solid",
        frame_color1="#FFFFFF", image_radius=8,
    ),
    "Gradient card": EffectSettings(
        frame_enabled=True, frame_padding=56, frame_style="gradient",
        frame_color1="#667EEA", frame_color2="#764BA2", frame_angle=135, image_radius=14,
        shadow_enabled=True, shadow_blur=30, shadow_offset_y=14, shadow_opacity=55,
    ),
    "Torn paper": EffectSettings(
        torn_enabled=True, torn_edges="top,bottom", torn_depth=12, torn_teeth=28,
        shadow_enabled=True, shadow_blur=18, shadow_offset_y=6, shadow_opacity=40,
        frame_enabled=True, frame_padding=36, frame_style="solid",
        frame_color1="#ECE7DD", image_radius=0,
    ),
    "Plain border": EffectSettings(
        border_enabled=True, border_width=3, border_color="#222222",
    ),
}
