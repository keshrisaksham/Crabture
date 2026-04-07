#!/usr/bin/env bash
# =============================================================================
#  Crabture Install Script
#  Installs Crabture so it can be run from anywhere and integrates with the
#  GNOME/Nautilus right-click context menus.
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/crabture"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"

echo "==> Installing Crabture..."

# ── 1. Copy source files ──────────────────────────────────────────────────────
echo "    Copying source files to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR"/*.py "$INSTALL_DIR/"

# ── 2. Create the launcher wrapper ───────────────────────────────────────────
echo "    Creating launcher at $BIN_DIR/crabture"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/crabture" << 'LAUNCHER'
#!/usr/bin/env bash
# Crabture launcher — forwards all arguments to main.py
cd "$HOME/.local/share/crabture"
exec python3 "$HOME/.local/share/crabture/main.py" "$@"
LAUNCHER
chmod +x "$BIN_DIR/crabture"

# ── 3. Install the main .desktop entry ───────────────────────────────────────
echo "    Installing application .desktop entry"
mkdir -p "$APP_DIR"
cat > "$APP_DIR/crabture.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Crabture
GenericName=Image Editor
Comment=Annotate and edit images with Crabture
Exec=$BIN_DIR/crabture %f
Icon=image-editor
Categories=Graphics;2DGraphics;Photography;
Keywords=image;editor;annotate;screenshot;
StartupNotify=true
MimeType=image/png;image/jpeg;image/bmp;image/webp;image/gif;image/tiff;
DESKTOP

# ── 4. Install "Open with Crabture" right-click entry (images) ───────────────
# Note: GNOME prepends "Open with" to the Name automatically, so Name=Crabture
# produces the correct "Open with Crabture" label in the context menu.
echo "    Installing 'Open with Crabture' image context menu entry"
cat > "$APP_DIR/crabture-edit-image.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Crabture
Comment=Open this image in Crabture for editing/annotation
Exec=$BIN_DIR/crabture %f
Icon=image-editor
MimeType=image/png;image/jpeg;image/jpg;image/bmp;image/webp;image/gif;image/tiff;image/x-png;image/pjpeg;
NoDisplay=true
StartupNotify=true
DESKTOP

# ── 5. Remove the unused inode/directory .desktop (doesn't work in Nautilus) ─
rm -f "$APP_DIR/crabture-open-directory.desktop"

# ── 6. Install Nautilus script for "Open Directory with Crabture" ─────────────
# Nautilus scripts appear under right-click → Scripts for ALL file/folder types.
echo "    Installing Nautilus script for directory right-click"
NAUTILUS_SCRIPTS="$HOME/.local/share/nautilus/scripts"
mkdir -p "$NAUTILUS_SCRIPTS"
cat > "$NAUTILUS_SCRIPTS/Open Directory with Crabture" << 'SCRIPT'
#!/usr/bin/env bash
# Nautilus script: Open Directory with Crabture
# NAUTILUS_SCRIPT_SELECTED_FILE_PATHS contains newline-separated absolute paths.
SELECTED=$(echo "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" | head -n 1 | tr -d '\r')
if [ -z "$SELECTED" ]; then
    SELECTED="$NAUTILUS_SCRIPT_CURRENT_URI"
    SELECTED="${SELECTED#file://}"
    SELECTED=$(python3 -c "import urllib.parse, sys; print(urllib.parse.unquote(sys.argv[1]))" "$SELECTED")
fi
if [ -d "$SELECTED" ]; then
    TARGET="$SELECTED"
elif [ -f "$SELECTED" ]; then
    TARGET="$(dirname "$SELECTED")"
else
    TARGET="$HOME"
fi
exec /home/ben/.local/bin/crabture --directory "$TARGET"
SCRIPT
chmod +x "$NAUTILUS_SCRIPTS/Open Directory with Crabture"

# ── 7. Update MIME / desktop databases ───────────────────────────────────────
echo "    Updating desktop and MIME databases..."
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$APP_DIR"
fi
# NOTE: We do NOT call xdg-mime default here — Crabture should appear in
# "Open With" but must NOT replace the user's existing default image viewer.

# ── 7. Make sure ~/.local/bin is on PATH ──────────────────────────────────────
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo ""
    echo "  ⚠  $BIN_DIR is not on your PATH."
    echo "     Add the following line to your ~/.bashrc or ~/.zshrc:"
    echo "     export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "     Then run: source ~/.bashrc"
fi

echo ""
echo "✅  Crabture installed successfully!"
echo ""
echo "    • Run from terminal:  crabture"
echo "    • Open an image:      crabture /path/to/image.png"
echo "    • Set gallery dir:    crabture --directory /path/to/folder"
echo ""
echo "    Right-click context menus are now registered."
echo "    You may need to log out and back in (or restart Nautilus) for"
echo "    the context menus to appear:"
echo "      nautilus -q && nautilus &"
