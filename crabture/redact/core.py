"""
redact/core.py — OCR-based "find sensitive data" helper for Crabture.

This module NEVER touches a canvas and NEVER redacts anything by itself.  It
only (1) runs the ``tesseract`` CLI on an image the caller hands it and
(2) returns rectangles (in image-pixel coordinates) that look like sensitive
data.  The caller (see ``redact/dialog.py``) decides what to do with them,
and only after the user explicitly confirms.

Pipeline::

    run_ocr(image)            -> list[OcrWord]     (tesseract TSV, boxes mapped back to image px)
    find_sensitive(words, …)  -> list[Finding]     (regex detectors over geometric text lines)

No Python dependencies beyond PyQt6: tesseract is invoked as a subprocess
(``sudo apt install tesseract-ocr``).
"""

from __future__ import annotations

import ipaddress
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple, Union

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from crabture import osinfo


# ═══════════════════════════════════════════════════════════════════════════
#  tesseract discovery
# ═══════════════════════════════════════════════════════════════════════════

# Extra places to look when ``tesseract`` is not on PATH (e.g. GUI launchers
# with a trimmed PATH).  Module-level so tests can override it.
if osinfo.IS_WINDOWS:
    FALLBACK_DIRS: Tuple[str, ...] = tuple(
        os.path.join(base, *rest)
        for base, rest in (
            (os.environ.get("ProgramFiles", r"C:\Program Files"), ("Tesseract-OCR",)),
            (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), ("Tesseract-OCR",)),
            (os.environ.get("LOCALAPPDATA", ""), ("Programs", "Tesseract-OCR")),
            (os.environ.get("ProgramData", r"C:\ProgramData"), ("chocolatey", "bin")),
            (os.environ.get("USERPROFILE", ""), ("scoop", "shims")),
        )
        if base
    )
    _EXE_NAMES: Tuple[str, ...] = ("tesseract.exe",)
else:
    FALLBACK_DIRS = ("/usr/bin", "/usr/local/bin", "/snap/bin", "/opt/homebrew/bin")
    _EXE_NAMES = ("tesseract",)


class OcrError(RuntimeError):
    """OCR could not be performed (missing binary, crash, timeout, bad lang…)."""


class OcrCancelled(OcrError):
    """The caller cancelled a running OCR job."""


def tesseract_path() -> Optional[str]:
    """Return the path of the tesseract executable, or None.

    ``CRABTURE_TESSERACT`` (path to an executable) overrides discovery.
    """
    override = os.environ.get("CRABTURE_TESSERACT")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which("tesseract")
    if found:
        return found
    for directory in FALLBACK_DIRS:
        for name in _EXE_NAMES:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def ocr_available() -> bool:
    """True if a tesseract executable can be found."""
    return tesseract_path() is not None


def install_hint() -> str:
    """Human-readable instructions for installing tesseract on this system."""
    if osinfo.IS_WINDOWS:
        return (
            "Tesseract OCR is not installed.\n"
            "Install it from a Command Prompt with:\n"
            "    winget install UB-Mannheim.TesseractOCR\n"
            "or download the installer from https://github.com/UB-Mannheim/tesseract/wiki\n"
            "(tick the extra languages you need). Crabture finds it in C:\\Program Files\\Tesseract-OCR\n"
            "automatically; if you put it elsewhere, set the CRABTURE_TESSERACT environment variable\n"
            "to the full path of tesseract.exe."
        )
    return (
        "Tesseract OCR is not installed.\n"
        "Install it with:\n"
        "    sudo apt install tesseract-ocr\n"
        "For other languages also install the matching pack, e.g.:\n"
        "    sudo apt install tesseract-ocr-deu"
    )


_LANG_RE = re.compile(r"^[A-Za-z0-9_+\-]+$")
_lang_cache: Optional[List[str]] = None


def list_languages(refresh: bool = False) -> List[str]:
    """Languages reported by ``tesseract --list-langs`` (cached; ['eng'] on failure)."""
    global _lang_cache
    if _lang_cache is not None and not refresh:
        return list(_lang_cache)
    langs: List[str] = []
    exe = tesseract_path()
    if exe:
        try:
            proc = subprocess.run(
                [exe, "--list-langs"], capture_output=True, timeout=15,
                env=_tesseract_env(), **_hidden_console(),
            )
            out = proc.stdout.decode("utf-8", "replace").splitlines()
            # First line is "List of available languages (N):".
            langs = [ln.strip() for ln in out[1:] if ln.strip() and _LANG_RE.match(ln.strip())]
            langs = [ln for ln in langs if ln != "osd"]
        except (OSError, subprocess.SubprocessError):
            langs = []
    if not langs:
        langs = ["eng"]
    _lang_cache = langs
    return list(langs)


def _hidden_console() -> dict:
    """``subprocess`` arguments that keep a console window from flashing up on Windows."""
    if osinfo.IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def _tesseract_env() -> dict:
    env = dict(os.environ)
    env["LC_NUMERIC"] = "C"            # tesseract misbehaves with locale decimal commas
    env.setdefault("OMP_THREAD_LIMIT", "1")  # documented speed-up for single images
    return env


# ═══════════════════════════════════════════════════════════════════════════
#  OCR
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OcrWord:
    """One OCR word.  ``rect`` is in the ORIGINAL image's pixel coordinates."""
    text: str
    rect: QRectF
    conf: float          # 0-100, -1 if unknown
    block: int = 0
    par: int = 0
    line: int = 0
    word: int = 0


def ocr_scale_factor(width: int, height: int, max_pixels: int = 24_000_000) -> float:
    """Up-scale factor applied before OCR.

    Screenshot text is often tiny; tesseract does much better when the text
    is ~2x larger.  Big images are left alone (and the scaled size is capped).
    """
    if max(width, height) >= 2000:
        return 1.0
    factor = 2.0
    if width * height * factor * factor > max_pixels:
        return 1.0
    return factor


def parse_tsv(tsv: str, scale: float = 1.0) -> List[OcrWord]:
    """Parse tesseract TSV output into words, dividing boxes by *scale*.

    Columns: level page_num block_num par_num line_num word_num
             left top width height conf text
    Only level-5 (word) rows with non-empty text are kept.
    """
    if scale <= 0:
        scale = 1.0
    words: List[OcrWord] = []
    for raw in tsv.splitlines():
        parts = raw.rstrip("\r\n").split("\t", 11)
        if len(parts) < 12 or not parts[0].strip().isdigit():
            continue                      # header row / malformed
        try:
            level = int(parts[0])
            block, par, line, word = (int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]))
            left, top, width, height = (float(parts[6]), float(parts[7]), float(parts[8]), float(parts[9]))
            conf = float(parts[10])
        except ValueError:
            continue
        text = parts[11].strip()
        if level != 5 or not text or width <= 0 or height <= 0:
            continue
        words.append(OcrWord(
            text=text,
            rect=QRectF(left / scale, top / scale, width / scale, height / scale),
            conf=conf, block=block, par=par, line=line, word=word,
        ))
    return words


def _prepare_image(image: QImage) -> QImage:
    """Return an opaque, DPR-1 RGB32 copy of *image* (alpha flattened onto white)."""
    if image.isNull():
        raise OcrError("Cannot run OCR on an empty image.")
    src = QImage(image)
    src.setDevicePixelRatio(1.0)
    flat = QImage(src.size(), QImage.Format.Format_RGB32)
    flat.fill(QColor("white"))
    painter = QPainter(flat)
    painter.drawImage(0, 0, src)
    painter.end()
    return flat


def _kill(proc: "subprocess.Popen") -> None:
    """Kill *proc* and its process group, then reap it."""
    osinfo.kill_process_tree(proc)
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def run_ocr(
    image: QImage,
    lang: str = "eng",
    timeout: float = 60,
    psm: int = 11,
    cancel: Optional[threading.Event] = None,
) -> List[OcrWord]:
    """Run tesseract on *image* and return its words (boxes in image pixels).

    Safe to call from a worker thread.  Set *cancel* to abort a running job
    (raises :class:`OcrCancelled`).  Raises :class:`OcrError` for a missing
    binary, bad language string, non-zero exit or timeout.
    """
    exe = tesseract_path()
    if exe is None:
        raise OcrError(install_hint())
    lang = (lang or "eng").strip()
    if not _LANG_RE.match(lang):
        raise OcrError(f"Invalid OCR language: {lang!r}")

    flat = _prepare_image(image)
    scale = ocr_scale_factor(flat.width(), flat.height())
    if scale != 1.0:
        flat = flat.scaled(
            int(round(flat.width() * scale)), int(round(flat.height() * scale)),
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )

    with tempfile.TemporaryDirectory(prefix="crabture_ocr_") as tmp:
        png = os.path.join(tmp, "input.png")
        if not flat.save(png, "PNG"):
            raise OcrError("Could not write the temporary image for OCR.")
        cmd = [exe, png, "stdout", "-l", lang, "--psm", str(int(psm)), "--dpi", "300", "tsv"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_tesseract_env(),
                **osinfo.subprocess_kwargs(),   # own process group / no console window
            )
        except OSError as exc:
            raise OcrError(f"Could not start tesseract: {exc}") from exc

        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                _kill(proc)
                raise OcrCancelled("OCR cancelled.")
            try:
                out, err = proc.communicate(timeout=0.15)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() > deadline:
                    _kill(proc)
                    raise OcrError(f"OCR timed out after {timeout:g}s.")

    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise OcrError("tesseract failed: " + (" ".join(tail) or f"exit code {proc.returncode}"))
    return parse_tsv(out.decode("utf-8", "replace"), scale)


# ═══════════════════════════════════════════════════════════════════════════
#  Text lines from OCR words
# ═══════════════════════════════════════════════════════════════════════════

def group_lines(words: Iterable[OcrWord]) -> List[List[OcrWord]]:
    """Group words into visual text lines using geometry.

    Tesseract's own line numbers are unreliable in sparse mode (a label and
    its value are often reported as separate "lines"), so words are clustered
    by vertical position, ordered left-to-right, and only split where there is
    a very wide horizontal gap (separate columns).
    """
    items = sorted((w for w in words if w.text.strip()), key=lambda w: w.rect.center().y())
    clusters: List[dict] = []
    for w in items:
        cy, h = w.rect.center().y(), w.rect.height()
        target = None
        for cl in reversed(clusters[-8:]):
            if abs(cy - cl["cy"]) <= 0.5 * min(h, cl["h"]):
                target = cl
                break
        if target is None:
            clusters.append({"cy": cy, "h": h, "words": [w]})
        else:
            target["words"].append(w)
            n = len(target["words"])
            target["cy"] += (cy - target["cy"]) / n
            target["h"] = max(target["h"], h)
    lines: List[List[OcrWord]] = []
    for cl in clusters:
        ws = sorted(cl["words"], key=lambda w: w.rect.left())
        heights = sorted(w.rect.height() for w in ws)
        median_h = heights[len(heights) // 2]
        max_gap = max(40.0, 12.0 * median_h)
        seg = [ws[0]]
        for prev, cur in zip(ws, ws[1:]):
            if cur.rect.left() - prev.rect.right() > max_gap:
                lines.append(seg)
                seg = []
            seg.append(cur)
        lines.append(seg)
    lines.sort(key=lambda ln: (min(w.rect.top() for w in ln), ln[0].rect.left()))
    return lines


def _line_text(line: Sequence[OcrWord]) -> Tuple[str, List[Tuple[int, int]]]:
    """Join a line's words with single spaces; return text and per-word (start, end)."""
    parts: List[str] = []
    offsets: List[Tuple[int, int]] = []
    pos = 0
    for i, w in enumerate(line):
        if i:
            pos += 1
        offsets.append((pos, pos + len(w.text)))
        parts.append(w.text)
        pos += len(w.text)
    return " ".join(parts), offsets


def _span_rect(line: Sequence[OcrWord], offsets: Sequence[Tuple[int, int]], start: int, end: int) -> Optional[Tuple[QRectF, float]]:
    """Union box (and mean OCR confidence) of the words overlapping [start, end).

    A word that is only partially covered (e.g. ``password=hunter2`` as ONE
    OCR word) is cut proportionally by character count, with half a
    character of slack on the cut side so the secret is not under-covered.
    """
    rect: Optional[QRectF] = None
    confs: List[float] = []
    for w, (ws, we) in zip(line, offsets):
        if we <= start or ws >= end:
            continue
        r = QRectF(w.rect)
        n = max(1, we - ws)
        a = max(start, ws) - ws
        b = min(end, we) - ws
        if a > 0 or b < n:
            cw = r.width() / n
            left = r.left() + (a * cw - 0.5 * cw if a > 0 else 0.0)
            right = r.left() + (b * cw + 0.5 * cw if b < n else r.width())
            left = max(r.left(), left)
            right = min(r.right(), right)
            if right > left:
                r = QRectF(left, r.top(), right - left, r.height())
        rect = r if rect is None else rect.united(r)
        if w.conf >= 0:
            confs.append(w.conf)
    if rect is None:
        return None
    mean_conf = sum(confs) / len(confs) if confs else -1.0
    return rect, mean_conf


# ═══════════════════════════════════════════════════════════════════════════
#  Validators
# ═══════════════════════════════════════════════════════════════════════════

def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _valid_credit_card(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    if not 13 <= len(digits) <= 19 or len(set(digits)) < 2:
        return False
    has_sep = bool(re.search(r"[ \-]", text))
    if not has_sep and digits[0] not in "3456":
        return False                       # bare digit runs (timestamps, ids) need an issuer-like prefix
    return _luhn_ok(digits)


def _valid_ipv6(text: str) -> bool:
    t = text.split("%", 1)[0]
    if t == "::" or t.count(":") < 2:
        return False
    try:
        ipaddress.IPv6Address(t)
    except ValueError:
        return False
    return True


def _ipv4_score(text: str) -> float:
    """Loopback / unspecified / broadcast addresses are rarely sensitive."""
    if text.startswith("127.") or text in ("0.0.0.0", "255.255.255.255"):
        return 0.4
    return 0.9


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _random_looking(s: str) -> bool:
    has_digit = any(c.isdigit() for c in s)
    has_alpha = any(c.isalpha() for c in s)
    has_upper = any(c.isupper() for c in s)
    has_lower = any(c.islower() for c in s)
    return (has_digit and has_alpha) or (has_upper and has_lower and len(s) >= 20)


def _valid_long_hex(text: str) -> bool:
    return any(c.isdigit() for c in text) and len(set(text.lower())) >= 6


def _valid_long_token(text: str) -> bool:
    t = text.rstrip("=")
    return (
        any(c.isdigit() for c in t)
        and any(c.isupper() for c in t)
        and any(c.islower() for c in t)
        and _entropy(t) >= 3.5
    )


_KV_STOP = {
    "none", "null", "nil", "true", "false", "required", "optional", "your", "enter", "string",
    "undefined", "empty", "hidden", "bearer", "basic", "digest", "yes", "no", "off", "on",
    "password", "token", "secret", "here", "changeme_", "xxxx",
}
_MASKED_RE = re.compile(r"^[\*•●·x#\.\-_]+$", re.IGNORECASE)


def _valid_kv_value(value: str) -> bool:
    v = value.strip()
    if len(v) < 3 or _MASKED_RE.match(v):
        return False
    if v.lower() in _KV_STOP:
        return False
    if v[0] in "$<{%[":                    # template/env references, placeholders
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  Detectors (data-driven — append to DETECTORS to add one)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Detector:
    id: str                                     # kind id (several detectors may share one)
    label: str                                  # human label
    pattern: "re.Pattern[str]"
    group: Union[int, str] = 0                  # which group is the sensitive part
    validate: Optional[Callable[[str], bool]] = None
    confidence: float = 0.9
    score: Optional[Callable[[str], float]] = None   # overrides `confidence` per match


_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_KV_KEYWORDS = (
    r"(?:password|passwd|pwd|passphrase|secret|token|api[_\-]?key|apikey|auth(?:orization)?"
    r"|access[_\-]?key|private[_\-]?key|credentials?)"
)

DETECTORS: List[Detector] = [
    Detector("private_key_header", "Private key header",
             re.compile(r"-{3,}\s*BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?\s*-{3,}"), 0, None, 0.95),
    Detector("jwt", "JSON Web Token",
             re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*"), 0, None, 0.95),
    Detector("aws_access_key", "AWS access key id",
             re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"), 0, None, 0.95),
    Detector("aws_secret", "AWS secret key",
             re.compile(r"(?i)aws[_\- ]?secret[_\- ]?(?:access[_\- ]?)?key[\"']?\s*[=:]\s*[\"']?"
                        r"(?P<v>[A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"), "v", None, 0.95),
    Detector("github_token", "GitHub token",
             re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"), 0, None, 0.95),
    Detector("slack_token", "Slack token",
             re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), 0, None, 0.95),
    Detector("google_api_key", "Google API key",
             re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0, None, 0.95),
    Detector("url_credentials", "Credentials in URL",
             re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://(?P<cred>[^\s/:@]+:[^\s/@]+)@[^\s/]+"), "cred", None, 0.9),
    Detector("bearer_token", "Bearer / Basic auth token",
             re.compile(r"(?i)\b(?:bearer|basic)\s+(?P<v>[A-Za-z0-9\-._~+/]{16,}={0,2})"),
             "v", _random_looking, 0.85),
    Detector("key_value_secret", "Password / secret value",
             re.compile(r"(?i)\b[\w.\-]*" + _KV_KEYWORDS + r"[\"']?\s*(?:=>|[=:])\s*[\"']?(?P<v>[^\s\"',;]{3,})"),
             "v", _valid_kv_value, 0.8),
    Detector("key_value_secret", "Password / secret value",     # CLI style:  --password hunter2
             re.compile(r"(?i)(?<![\w])--?(?:password|passwd|pass|pwd|passphrase|secret|token|api[_\-]?key|apikey)"
                        r"(?:\s+|=)(?P<v>[^\s\"']{3,})"),
             "v", _valid_kv_value, 0.7),
    Detector("email", "Email address",
             re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*"
                        r"\.[A-Za-z]{2,24}(?![A-Za-z0-9\-])"), 0, None, 0.9),
    Detector("ipv4", "IPv4 address",
             re.compile(rf"(?<![\w.])(?:{_OCTET}\.){{3}}{_OCTET}(?!\w|\.\d)"), 0, None, 0.9, _ipv4_score),
    Detector("ipv6", "IPv6 address",
             re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%\w+)?(?![\w:])"),
             0, _valid_ipv6, 0.85),
    Detector("mac", "MAC address",
             re.compile(r"(?<![0-9A-Fa-f:\-])(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f]|[:\-][0-9A-Fa-f])"),
             0, None, 0.85),
    Detector("credit_card", "Credit card number",
             re.compile(r"(?<![\d\-])(?:\d[ \-]?){12,18}\d(?![\d\-])"), 0, _valid_credit_card, 0.85),
    Detector("long_hex", "Long hex string (hash / key)",
             re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{32,}(?![0-9A-Za-z])"), 0, _valid_long_hex, 0.6),
    Detector("long_token", "Long random token",
             re.compile(r"(?<![A-Za-z0-9+/_\-=])[A-Za-z0-9+/_\-]{32,}={0,2}(?![A-Za-z0-9+/_\-])"),
             0, _valid_long_token, 0.5),
]

# kind id -> label (first detector of a kind wins), plus user patterns.
KINDS: dict = {}
for _d in DETECTORS:
    KINDS.setdefault(_d.id, _d.label)
KINDS["custom"] = "Custom pattern"
del _d

# Presentation metadata for the configuration UI: id -> (category, description, example).
KIND_CATEGORIES: Tuple[str, ...] = (
    "Credentials & keys", "Network", "Personal & financial", "Generic tokens",
)
KIND_INFO: dict = {
    "private_key_header": ("Credentials & keys", "The '-----BEGIN … PRIVATE KEY-----' line of a key file.",
                           "-----BEGIN RSA PRIVATE KEY-----"),
    "jwt": ("Credentials & keys", "JSON Web Tokens (three dot-separated base64 parts).",
            "eyJhbGciOi….eyJzdWIiOi….SflKxwRJ…"),
    "aws_access_key": ("Credentials & keys", "AWS access key ids (AKIA…, ASIA…).", "AKIAIOSFODNN7EXAMPLE"),
    "aws_secret": ("Credentials & keys", "The value after 'aws_secret_access_key ='.",
                   "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    "github_token": ("Credentials & keys", "GitHub personal-access and app tokens.", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    "slack_token": ("Credentials & keys", "Slack bot / user tokens.", "xoxb-1234567890-abcdefghijkl"),
    "google_api_key": ("Credentials & keys", "Google API keys.", "AIzaSyA1234567890abcdefghijklmnopqrstuv"),
    "url_credentials": ("Credentials & keys", "user:password embedded in a URL.", "https://admin:hunter2@example.com"),
    "bearer_token": ("Credentials & keys", "'Bearer …' / 'Basic …' authorization values.", "Authorization: Bearer 3f9a1c…"),
    "key_value_secret": ("Credentials & keys", "The value after password=, token:, api_key=, --password …",
                         "password = hunter2   ·   --token abc123"),
    "ipv4": ("Network", "IPv4 addresses.", "192.168.1.10"),
    "ipv6": ("Network", "IPv6 addresses.", "fe80::1ff:fe23:4567:890a"),
    "mac": ("Network", "MAC addresses.", "00:1A:2B:3C:4D:5E"),
    "email": ("Personal & financial", "Email addresses.", "alice@example.com"),
    "credit_card": ("Personal & financial", "Card numbers that pass the Luhn check.", "4111 1111 1111 1111"),
    "long_hex": ("Generic tokens", "Long hex strings (hashes, keys). Noisy — off by default.",
                 "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"),
    "long_token": ("Generic tokens", "Long random-looking strings. Noisy — off by default.",
                   "dGhpcyBpcyBhIHZlcnkgbG9uZyByYW5kb20gdG9rZW4"),
}
# Detectors that produce many false positives; excluded from the default profile.
NOISY_KINDS: Tuple[str, ...] = ("long_hex", "long_token")


def check_patterns(patterns: Iterable[str]) -> List[str]:
    """Return human-readable errors for invalid user regexes (never raises)."""
    errors: List[str] = []
    for p in patterns:
        if not p:
            continue
        try:
            re.compile(p)
        except re.error as exc:
            errors.append(f"{p!r}: {exc}")
    return errors


def filter_words(words: Iterable[OcrWord], min_confidence: float = 0) -> List[OcrWord]:
    """Drop OCR words whose confidence is below *min_confidence* (0..100).

    Words with an unknown confidence (-1) are kept: dropping them would hide
    real text from engines that do not report one.
    """
    if min_confidence <= 0:
        return list(words)
    return [w for w in words if w.conf < 0 or w.conf >= min_confidence]


# ═══════════════════════════════════════════════════════════════════════════
#  Finder
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    kind: str
    text: str
    rect: QRectF           # image-pixel coordinates
    confidence: float      # 0..1  (detector confidence x OCR confidence)

    @property
    def label(self) -> str:
        return KINDS.get(self.kind, self.kind)


PAD = 3.0
_MAX_MATCHES_PER_LINE = 50


def find_sensitive(
    words: Iterable[OcrWord],
    kinds: Optional[Set[str]] = None,
    extra_patterns: Optional[Iterable[str]] = None,
    bounds: Optional[QRectF] = None,
    pad: float = PAD,
) -> List[Finding]:
    """Scan OCR *words* for sensitive text and return padded boxes.

    *kinds* limits the detectors (None = all built-ins; include ``"custom"``
    to be explicit — custom patterns run whenever *extra_patterns* is given and
    *kinds* is None or contains ``"custom"``).  Invalid custom regexes are
    skipped silently; use :func:`check_patterns` to report them.
    """
    active = [d for d in DETECTORS if kinds is None or d.id in kinds]
    customs: List["re.Pattern[str]"] = []
    if extra_patterns and (kinds is None or "custom" in kinds):
        for p in extra_patterns:
            if not p:
                continue
            try:
                customs.append(re.compile(p))
            except re.error:
                continue

    raw: List[Tuple[int, Finding]] = []     # (priority, finding)
    for line in group_lines(words):
        text, offsets = _line_text(line)
        count = 0
        jobs: List[Tuple[int, "re.Pattern[str]", Union[int, str], Optional[Callable], float, Optional[Callable], str]] = []
        for prio, d in enumerate(active):
            jobs.append((prio, d.pattern, d.group, d.validate, d.confidence, d.score, d.id))
        for j, pat in enumerate(customs):
            jobs.append((len(DETECTORS) + j, pat, 0, None, 0.8, None, "custom"))
        for prio, pat, group, validate, conf, score, kind in jobs:
            for m in pat.finditer(text):
                if count >= _MAX_MATCHES_PER_LINE:
                    break
                try:
                    s, e = m.span(group)
                except IndexError:
                    continue
                if s < 0 or e <= s:
                    continue
                value = text[s:e]
                if validate is not None and not validate(value):
                    continue
                hit = _span_rect(line, offsets, s, e)
                if hit is None:
                    continue
                rect, ocr_conf = hit
                rect = rect.adjusted(-pad, -pad, pad, pad)
                if bounds is not None:
                    rect = rect.intersected(bounds)
                    if rect.isEmpty():
                        continue
                det_conf = score(value) if score is not None else conf
                ocr_factor = 0.8 if ocr_conf < 0 else min(1.0, max(0.3, ocr_conf / 100.0))
                raw.append((prio, Finding(kind, value, rect, round(det_conf * ocr_factor, 3))))
                count += 1

    # Merge/dedupe: more specific detectors (lower priority number) win the
    # kind/text; overlapping boxes are united so a secret is never under-covered.
    raw.sort(key=lambda t: t[0])
    kept: List[Finding] = []
    for _, f in raw:
        for k in kept:
            inter = k.rect.intersected(f.rect)
            if inter.isEmpty():
                continue
            smaller = min(k.rect.width() * k.rect.height(), f.rect.width() * f.rect.height())
            if inter.width() * inter.height() >= 0.5 * smaller:
                k.rect = k.rect.united(f.rect)
                k.confidence = max(k.confidence, f.confidence)
                break
        else:
            kept.append(Finding(f.kind, f.text, QRectF(f.rect), f.confidence))
    kept.sort(key=lambda f: (round(f.rect.top() / 4), f.rect.left()))
    return kept


# ═══════════════════════════════════════════════════════════════════════════
#  Saved-profile API (lives in redact/profile.py / redact/dialog.py)
# ═══════════════════════════════════════════════════════════════════════════
# Re-exported lazily so ``redact.RedactProfile`` / ``redact.run_saved_profile``
# work without a circular import (those modules import this one).

_PROFILE_EXPORTS = {
    "RedactProfile", "default_profile", "load_profile", "save_profile",
    "profile_signals", "findings_for_profile",
}
_DIALOG_EXPORTS = {"RedactConfigWidget", "RedactConfigDialog", "run_saved_profile"}


def __getattr__(name: str):
    if name in _PROFILE_EXPORTS:
        from crabture.redact import profile as redact_profile
        return getattr(redact_profile, name)
    if name in _DIALOG_EXPORTS:
        from crabture.redact import dialog as redact_dialog
        return getattr(redact_dialog, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
