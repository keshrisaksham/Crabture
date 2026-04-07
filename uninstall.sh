#!/usr/bin/env bash
# =============================================================================
#  Crabture Uninstall Script
# =============================================================================
set -e

INSTALL_DIR="$HOME/.local/share/crabture"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"

echo "==> Uninstalling Crabture..."

NAUTILUS_SCRIPTS="$HOME/.local/share/nautilus/scripts"

rm -rf "$INSTALL_DIR"                                                   && echo "    Removed $INSTALL_DIR"
rm -f  "$BIN_DIR/crabture"                                              && echo "    Removed $BIN_DIR/crabture"
rm -f  "$APP_DIR/crabture.desktop"                                      && echo "    Removed crabture.desktop"
rm -f  "$APP_DIR/crabture-edit-image.desktop"                           && echo "    Removed crabture-edit-image.desktop"
rm -f  "$APP_DIR/crabture-open-directory.desktop"                       && echo "    Removed crabture-open-directory.desktop"
rm -f  "$NAUTILUS_SCRIPTS/Open Directory with Crabture"                 && echo "    Removed Nautilus script"

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$APP_DIR"
fi

nautilus -q 2>/dev/null || true

echo ""
echo "✅  Crabture uninstalled."
