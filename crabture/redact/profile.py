"""
redact/profile.py — the saved "sensitive data" configuration for Crabture.

The feature is split in two:

  1. CONFIGURE once (``RedactConfigDialog`` / ``RedactConfigWidget`` in
     redact/dialog.py) — what to look for, OCR options, redaction style …
     stored here as a :class:`RedactProfile`.
  2. RUN it every time with one click (``run_saved_profile`` in
     redact/dialog.py) which loads the saved profile.

Nothing in this module scans or redacts anything.  It only stores settings
and turns OCR words into findings according to a profile.

Storage: QSettings('YawnByte', 'Crabture'), group ``redaction``, key
``profile`` holding a JSON object.  Loading is tolerant: missing, garbage or
unknown keys fall back to defaults and never raise.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional

from PyQt6.QtCore import QObject, QRectF, QSettings, pyqtSignal

from crabture.redact import core as redact

__all__ = [
    "RedactProfile", "default_profile", "load_profile", "save_profile",
    "profile_signals", "findings_for_profile", "PSM_CHOICES",
    "STYLES", "INTENSITY_RANGE", "PAD_RANGE",
]

STYLES = ("blur", "pixelate")
INTENSITY_RANGE = (2, 50)
PAD_RANGE = (0, 30)

# tesseract page-segmentation modes offered in the UI: value -> plain-English label.
PSM_CHOICES = {
    3: "Automatic page layout (documents)",
    6: "One uniform block of text",
    11: "Sparse text — best for screenshots (default)",
    12: "Sparse text, detect orientation",
}

_SETTINGS_GROUP = "redaction"
_SETTINGS_KEY = "profile"
_LANG_RE = re.compile(r"^[A-Za-z0-9_+\-]+$")


class _ProfileSignals(QObject):
    """Emits ``changed`` after a profile was saved so open UIs can refresh."""

    changed = pyqtSignal()


profile_signals = _ProfileSignals()


@dataclass
class RedactProfile:
    """Everything the 'Run sensitive-data scan' action needs.

    Defaults: every built-in detector EXCEPT the noisy generic ones
    (``long_hex``, ``long_token``), which flag hashes and random-looking
    identifiers all over typical screenshots.
    """

    kinds: List[str] = field(default_factory=list)            # enabled detector ids
    custom_patterns: List[dict] = field(default_factory=list)  # {'name','regex','enabled'}
    lang: str = "eng"
    psm: int = 11                       # tesseract page segmentation mode
    style: str = "blur"                 # 'blur' | 'pixelate'
    intensity: int = 12                 # 2..50
    pad: int = 3                        # px added around every finding box (0..30)
    min_confidence: int = 0             # drop OCR words below this (0..100)
    review_before_apply: bool = True    # False: Run redacts every finding without the review step

    # -- derived helpers ----------------------------------------------------

    def enabled_custom_regexes(self) -> List[str]:
        """Regex strings of the enabled, non-empty custom patterns (valid or not)."""
        return [c["regex"] for c in self.custom_patterns if c.get("enabled", True) and c.get("regex")]

    def scan_kinds(self) -> set:
        """Detector ids to pass to ``find_sensitive`` (adds ``custom`` when needed)."""
        kinds = set(self.kinds)
        if self.enabled_custom_regexes():
            kinds.add("custom")
        return kinds


def _builtin_kind_ids() -> List[str]:
    return [k for k in redact.KINDS if k != "custom"]


def default_profile() -> RedactProfile:
    return RedactProfile(
        kinds=[k for k in _builtin_kind_ids() if k not in redact.NOISY_KINDS],
        custom_patterns=[],
    )


# ---------------------------------------------------------------------------
#  Sanitising (used by load_profile; also usable for imported JSON)
# ---------------------------------------------------------------------------

def _as_int(value, default: int, lo: int, hi: int) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def profile_from_dict(data) -> RedactProfile:
    """Build a valid profile from arbitrary decoded JSON; never raises."""
    base = default_profile()
    if not isinstance(data, dict):
        return base

    known = set(_builtin_kind_ids())
    kinds = data.get("kinds")
    if isinstance(kinds, (list, tuple)):
        wanted = {k for k in kinds if isinstance(k, str) and k in known}
        base.kinds = [k for k in _builtin_kind_ids() if k in wanted]   # canonical order

    customs = data.get("custom_patterns")
    if isinstance(customs, (list, tuple)):
        clean = []
        for c in customs[:100]:
            if not isinstance(c, dict):
                continue
            regex = c.get("regex")
            if not isinstance(regex, str) or not regex.strip():
                continue
            name = c.get("name")
            clean.append({
                "name": name.strip()[:80] if isinstance(name, str) and name.strip() else "Pattern",
                "regex": regex[:500],
                "enabled": bool(c.get("enabled", True)),
            })
        base.custom_patterns = clean

    lang = data.get("lang")
    if isinstance(lang, str) and lang.strip() and _LANG_RE.match(lang.strip()):
        base.lang = lang.strip()

    psm = _as_int(data.get("psm"), base.psm, 0, 13)
    base.psm = psm if psm in PSM_CHOICES else base.psm

    style = data.get("style")
    if style in STYLES:
        base.style = style

    base.intensity = _as_int(data.get("intensity"), base.intensity, *INTENSITY_RANGE)
    base.pad = _as_int(data.get("pad"), base.pad, *PAD_RANGE)
    base.min_confidence = _as_int(data.get("min_confidence"), base.min_confidence, 0, 100)
    review = data.get("review_before_apply")
    if isinstance(review, bool):
        base.review_before_apply = review
    return base


def load_profile() -> RedactProfile:
    """Load the saved profile (defaults when nothing valid is stored)."""
    try:
        qs = QSettings("YawnByte", "Crabture")
        qs.beginGroup(_SETTINGS_GROUP)
        raw = qs.value(_SETTINGS_KEY, "")
        qs.endGroup()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if not isinstance(raw, str) or not raw.strip():
            return default_profile()
        return profile_from_dict(json.loads(raw))
    except Exception:  # noqa: BLE001 - settings must never break the app
        return default_profile()


def save_profile(p: RedactProfile) -> None:
    """Persist *p* (sanitised) and tell listeners via ``profile_signals.changed``."""
    clean = profile_from_dict(asdict(p))
    qs = QSettings("YawnByte", "Crabture")
    qs.beginGroup(_SETTINGS_GROUP)
    qs.setValue(_SETTINGS_KEY, json.dumps(asdict(clean), ensure_ascii=False))
    qs.endGroup()
    qs.sync()
    profile_signals.changed.emit()


# ---------------------------------------------------------------------------
#  Applying a profile to OCR words
# ---------------------------------------------------------------------------

def findings_for_profile(
    words: Iterable["redact.OcrWord"],
    profile: RedactProfile,
    bounds: Optional[QRectF] = None,
) -> List["redact.Finding"]:
    """Run the profile's detectors over OCR *words* (pure; no OCR, no UI)."""
    usable = redact.filter_words(words, profile.min_confidence)
    return redact.find_sensitive(
        usable,
        profile.scan_kinds(),
        profile.enabled_custom_regexes(),
        bounds=bounds,
        pad=float(profile.pad),
    )
