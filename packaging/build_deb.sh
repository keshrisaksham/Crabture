#!/usr/bin/env bash
# =============================================================================
#  Crabture .deb builder
#  Produces dist/crabture_<version>_all.deb  (override dir with OUT_DIR=...)
#  Needs: dpkg-deb, gzip, python3 (+ PyQt6 or ImageMagick `convert` for icons)
# =============================================================================
set -euo pipefail

PACKAGING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$PACKAGING_DIR")"
OUT_DIR="${OUT_DIR:-$PROJECT_DIR/dist}"

command -v dpkg-deb >/dev/null || { echo "dpkg-deb not found (install the 'dpkg-dev' package)." >&2; exit 1; }

VERSION="$(sed -n 's/^__version__ *= *["'\'']\([^"'\'']*\)["'\''].*/\1/p' "$PROJECT_DIR/crabture/version.py" | head -n1)"
[ -n "$VERSION" ] || { echo "Could not read __version__ from crabture/version.py" >&2; exit 1; }

PKG="crabture"
DEB="$OUT_DIR/${PKG}_${VERSION}_all.deb"
EPOCH="${SOURCE_DATE_EPOCH:-$(date +%s)}"
DATE_RFC="$(date -R -d "@$EPOCH")"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ROOT="$STAGE/root"

echo "==> Building $PKG $VERSION (.deb)"

# ── 1. Application files ─────────────────────────────────────────────────────
LIB="$ROOT/usr/lib/crabture"
install -d "$LIB"
# main.py (launcher) + the whole `crabture` package, discovered at build time so
# new modules are picked up automatically; __pycache__ is never shipped.
[ -f "$PROJECT_DIR/main.py" ] || { echo "main.py not found in $PROJECT_DIR" >&2; exit 1; }
install -m 0644 "$PROJECT_DIR/main.py" "$LIB/main.py"
COUNT=0
while IFS= read -r -d '' f; do
    rel="${f#"$PROJECT_DIR"/}"
    install -Dm 0644 "$f" "$LIB/$rel"
    COUNT=$((COUNT + 1))
done < <(find "$PROJECT_DIR/crabture" -name '*.py' -not -path '*/__pycache__/*' -print0 | sort -z)
[ "$COUNT" -gt 0 ] || { echo "No Python modules found in $PROJECT_DIR/crabture" >&2; exit 1; }
install -Dm 0644 "$PROJECT_DIR/assets/logo.png" "$LIB/assets/logo.png"
echo "    Packaged main.py + $COUNT package modules + assets/logo.png"

# ── 2. Launcher ──────────────────────────────────────────────────────────────
install -d "$ROOT/usr/bin"
cat > "$ROOT/usr/bin/crabture" <<'WRAP'
#!/bin/sh
exec python3 /usr/lib/crabture/main.py "$@"
WRAP
chmod 0755 "$ROOT/usr/bin/crabture"

# ── 3. Desktop entry ─────────────────────────────────────────────────────────
install -Dm 0644 "$PACKAGING_DIR/crabture.desktop" "$ROOT/usr/share/applications/crabture.desktop"

# ── 4. Icons (generated from assets/logo.png) ───────────────────────────────────────
SIZES=(16 24 32 48 64 128 256 512)
ICON_SCRIPT='
import sys
from PyQt6.QtGui import QImage
from PyQt6.QtCore import Qt
src = QImage(sys.argv[1])
if src.isNull():
    sys.exit(1)
size = int(sys.argv[3])
src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
           Qt.TransformationMode.SmoothTransformation).save(sys.argv[2], "PNG")
'
for s in "${SIZES[@]}"; do
    dest="$ROOT/usr/share/icons/hicolor/${s}x${s}/apps"
    install -d "$dest"
    if QT_QPA_PLATFORM=offscreen python3 -c "$ICON_SCRIPT" "$PROJECT_DIR/assets/logo.png" "$dest/crabture.png" "$s" 2>/dev/null; then
        :
    elif command -v convert >/dev/null; then
        convert "$PROJECT_DIR/assets/logo.png" -resize "${s}x${s}" "$dest/crabture.png"
    else
        echo "Cannot resize icons: need PyQt6 or ImageMagick." >&2; exit 1
    fi
    chmod 0644 "$dest/crabture.png"
done
# (assets/logo.svg is ~1 MB, so no scalable icon is shipped.)

# ── 5. Documentation, changelog, man page ────────────────────────────────────
DOC="$ROOT/usr/share/doc/crabture"
install -d "$DOC"
install -m 0644 "$PACKAGING_DIR/copyright" "$DOC/copyright"
install -m 0644 "$PROJECT_DIR/README.md" "$DOC/README.md"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@DATE@/$DATE_RFC/g" "$PACKAGING_DIR/changelog" \
    | gzip -9n > "$DOC/changelog.Debian.gz"
chmod 0644 "$DOC/changelog.Debian.gz"

install -d "$ROOT/usr/share/man/man1"
sed -e "s/@VERSION@/$VERSION/g" "$PACKAGING_DIR/crabture.1" | gzip -9n > "$ROOT/usr/share/man/man1/crabture.1.gz"
chmod 0644 "$ROOT/usr/share/man/man1/crabture.1.gz"

# ── 6. Control files ─────────────────────────────────────────────────────────
install -d "$ROOT/DEBIAN"
INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)"
cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: $PKG
Version: $VERSION
Architecture: all
Section: graphics
Priority: optional
Maintainer: Crabture maintainers <noreply@example.invalid>
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10), python3-pyqt6
Recommends: tesseract-ocr, xdotool, libglib2.0-bin
Suggests: maim, grim, gnome-screenshot, xdg-desktop-portal-gnome | xdg-desktop-portal-gtk, imagemagick
Description: screenshot capture and annotation tool (ShareX-style)
 Crabture captures the screen (region, fullscreen, active window, monitor
 or scrolling pages), lets you annotate the result with arrows, shapes,
 text, numbered steps, blur, pixelation and a spotlight, applies image
 effects such as borders, shadows and padding, and keeps a watched gallery
 of your captures with bulk renaming.
 .
 An optional scanner can locate emails, IP addresses and secrets in a
 screenshot using OCR (tesseract-ocr) and offers to blur them; nothing is
 redacted until you confirm.
CONTROL
install -m 0755 "$PACKAGING_DIR/postinst" "$ROOT/DEBIAN/postinst"
install -m 0755 "$PACKAGING_DIR/postrm" "$ROOT/DEBIAN/postrm"

# ── 7. Normalise permissions and build ───────────────────────────────────────
find "$ROOT" -type d -exec chmod 0755 {} +
chmod 0755 "$ROOT/usr/bin/crabture" "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"

install -d "$OUT_DIR"
rm -f "$DEB"
dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$DEB" >/dev/null

echo "✅  Built: $DEB"
