#!/usr/bin/env bash
# =============================================================================
#  Crabture Unified Builder
#  Handles: Build (PyInstaller), Install (Local Source), Uninstall, Reinstall, Debian package
# =============================================================================
set -e

# This script lives in <project>/scripts; the project root is one level up.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
NAUTILUS_SCRIPTS="$HOME/.local/share/nautilus/scripts"

show_help() {
    echo "Crabture Builder — Usage:"
    echo "  ./scripts/builder.sh --install     Install icons and desktop entry (points to current source)"
    echo "  ./scripts/builder.sh --build       Build a standalone executable using PyInstaller"
    echo "  ./scripts/builder.sh --uninstall   Remove all Crabture system integrations"
    echo "  ./scripts/builder.sh --deb         Build a Debian package (dist/crabture_<version>_all.deb)"
    echo "  ./scripts/builder.sh --reinstall   Remove any previous installation, then install cleanly"
    echo "  ./scripts/builder.sh --help        Show this help message"
}

do_install() {
    echo "==> Integrating Crabture with desktop..."
    
    mkdir -p "$BIN_DIR"
    mkdir -p "$APP_DIR"
    mkdir -p "$NAUTILUS_SCRIPTS"

    # 1. Create the launcher wrapper pointing to this project directory
    echo "    Creating launcher at $BIN_DIR/crabture"
    cat > "$BIN_DIR/crabture" << LAUNCHER
#!/usr/bin/env bash
# Crabture launcher — dynamically points to source at $PROJECT_DIR
cd "$PROJECT_DIR"
exec python3 "$PROJECT_DIR/main.py" "\$@"
LAUNCHER
    chmod +x "$BIN_DIR/crabture"

    # 2. Create the .desktop entry
    echo "    Installing .desktop entry"
    cat > "$APP_DIR/crabture.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Crabture
GenericName=Image Editor
Comment=Annotate and edit images with Crabture
Exec="$BIN_DIR/crabture" %F
Icon=$PROJECT_DIR/assets/logo.png
Categories=Graphics;2DGraphics;Photography;Utility;
Keywords=screenshot;capture;annotate;image;editor;sharex;
StartupNotify=true
MimeType=image/png;image/jpeg;image/jpg;image/bmp;image/webp;image/gif;image/tiff;image/x-png;image/pjpeg;
Actions=CaptureRegion;CaptureFullscreen;CaptureWindow;

[Desktop Action CaptureRegion]
Name=Capture Region
Exec="$BIN_DIR/crabture" --capture region

[Desktop Action CaptureFullscreen]
Name=Capture Fullscreen
Exec="$BIN_DIR/crabture" --capture fullscreen

[Desktop Action CaptureWindow]
Name=Capture Active Window
Exec="$BIN_DIR/crabture" --capture window
DESKTOP

    # 3. Nautilus scripts (right-click ▸ Scripts): the same files the .deb ships, from packaging/nautilus
    echo "    Installing Nautilus scripts"
    for script in "$PROJECT_DIR"/packaging/nautilus/*; do
        install -m 0755 "$script" "$NAUTILUS_SCRIPTS/$(basename "$script")"
    done

    echo "    Updating desktop database..."
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$APP_DIR"
    fi

    echo "✅  Crabture integrated from source!"
    echo "    No need to reinstall after updating code — changes will apply 'at once'."
}

do_build() {
    echo "==> Building Crabture Linux Executable..."
    if ! command -v pyinstaller &> /dev/null; then
        echo "    PyInstaller was not found on your PATH."
        echo "    Install it in a virtual environment (recommended):"
        echo "        python3 -m venv .venv && . .venv/bin/activate"
        echo "        pip install pyinstaller PyQt6"
        echo "    or per-user:  python3 -m pip install --user pyinstaller"
        if [ -t 0 ]; then
            read -r -p "    Try 'python3 -m pip install --user pyinstaller' now? [y/N] " reply
            if [[ "$reply" =~ ^[Yy]$ ]]; then
                python3 -m pip install --user pyinstaller || {
                    echo "    pip refused (externally-managed environment?). Use the venv steps above."
                    exit 1
                }
                export PATH="$HOME/.local/bin:$PATH"
            else
                exit 1
            fi
        else
            exit 1
        fi
        command -v pyinstaller &> /dev/null || { echo "    pyinstaller still not on PATH."; exit 1; }
    fi

    cd "$PROJECT_DIR"
    # Remove only generated output; the .spec file is generated into build/.
    rm -rf build/ dist/
    pyinstaller --noconfirm --clean --onefile --windowed \
        --name "crabture" \
        --specpath "$PROJECT_DIR/build" \
        --add-data "$PROJECT_DIR/assets/logo.png:assets" \
        --icon "$PROJECT_DIR/assets/logo.png" \
        "$PROJECT_DIR/main.py"

    echo "✅  Build complete! Standalone binary: $PROJECT_DIR/dist/crabture"
}

do_uninstall() {
    echo "==> Uninstalling Crabture system integrations..."
    rm -f "$BIN_DIR/crabture"
    rm -f "$APP_DIR/crabture.desktop"
    rm -f "$NAUTILUS_SCRIPTS/Open Directory with Crabture"
    rm -f "$NAUTILUS_SCRIPTS/Edit with Crabture"

    # Leftovers from the old copy-based install.sh (earlier versions).
    rm -rf "$HOME/.local/share/crabture"
    rm -f "$APP_DIR/crabture-edit-image.desktop"
    rm -f "$APP_DIR/crabture-open-directory.desktop"
    
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$APP_DIR"
    fi
    
    echo "✅  Crabture integrations removed."
}

do_deb() {
    echo "==> Building Debian package..."
    local version deb
    version="$(sed -n 's/^__version__ *= *["'"'"']\([^"'"'"']*\)["'"'"'].*/\1/p' "$PROJECT_DIR/crabture/version.py" | head -n1)"
    deb="$PROJECT_DIR/dist/crabture_${version}_all.deb"
    bash "$PROJECT_DIR/packaging/build_deb.sh"
    echo "    Install with:    sudo apt install \"$deb\""
    echo "    Remove with:     sudo apt remove crabture"
}

do_reinstall() {
    echo "==> Reinstalling Crabture (clean)..."
    do_uninstall
    do_install
}

case "$1" in
    --install)   do_install ;;
    --build)     do_build ;;
    --uninstall) do_uninstall ;;
    --deb)       do_deb ;;
    --reinstall) do_reinstall ;;
    --help|-h|"") show_help ;;
    *)
        echo "Unknown option: $1" >&2
        echo >&2
        show_help >&2
        exit 1
        ;;
esac
