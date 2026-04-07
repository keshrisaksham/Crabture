#!/usr/bin/env bash
# =============================================================================
#  Crabture Build Script (PyInstaller Build)
#  Compiles the Crabture Python project into a single Linux executable.
# =============================================================================

set -e

# 1. Check for Python environment and PyInstaller
if ! command -v pyinstaller &> /dev/null; then
    echo "PyInstaller not found. Attempting to install it..."
    pip install pyinstaller --break-system-packages
fi

# 2. Get the script's directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "==> Building Crabture Linux Executable..."

# 3. Clean up old builds
rm -rf build/ dist/ *.spec

# 4. Run PyInstaller
# --onefile: Bundle everything into a single binary
# --windowed: Don't open a terminal console when running the GUI
# --add-data: Include logo.png in the bundle
# --name: Rename output executable to crabture
# --icon: (Optional) Not strictly necessary for Linux binaries but good practice
pyinstaller --noconfirm --onefile --windowed \
    --name "crabture" \
    --add-data "logo.png:." \
    --add-data "logo.svg:." \
    --icon "logo.png" \
    main.py

echo ""
echo "✅  Build Complete!"
echo "    Your standalone executable is located at: $PROJECT_DIR/dist/crabture"
echo "    You can run it with: ./dist/crabture"
echo ""
