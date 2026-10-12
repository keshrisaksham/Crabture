"""
ui.py — CrabtureMainWindow: the main GUI shell.

Main window with:
  • Left dock   – tool palette + color palette
  • Right dock  – layer manager panel
  • Bottom dock – image gallery populated by QFileSystemWatcher
  • Top toolbar – zoom controls, clipboard button, theme toggle
  • Central     – EditorContainer canvas
  • Menubar     – File / Edit / View

V2 additions:
  • Color swatch row + custom color button
  • Zoom slider + Fit / +/- buttons
  • Copy-to-Clipboard toolbar button
  • Magnifier & Speech Balloon tool buttons
  • Light/Dark theme toggle
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSlot
from PyQt6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeySequence,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QColorDialog,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QFileSystemWatcher

from canvas import EditorContainer

# Directory to watch.
CRABSHOTS_DIR = os.path.expanduser("~/Downloads/Crabshots")
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tiff"}

# Preset swatch colors.
SWATCH_COLORS = [
    "#FF4444", "#FF9800", "#FFEB3B", "#4CAF50",
    "#2196F3", "#9C27B0", "#FFFFFF", "#1E1E1E",
]


# =========================================================================
#  Color palette widget
# =========================================================================

class _ColorPalette(QWidget):
    """A compact grid of color swatches + a Custom button."""

    def __init__(self, editor: EditorContainer, parent=None) -> None:
        super().__init__(parent)
        self._editor = editor
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 2, 8)
        layout.setSpacing(10)

        # We can use a small grid for colors. 2 items per row.
        from PyQt6.QtWidgets import QGridLayout
        cgrid = QGridLayout()
        cgrid.setSpacing(6)
        cgrid.setContentsMargins(0, 0, 0, 0)
        
        row, col = 0, 0
        for hex_color in SWATCH_COLORS:
            btn = QPushButton()
            btn.setFixedSize(18, 18)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {hex_color};
                    border: 1px solid rgba(0,0,0,0.15);
                    border-radius: 9px;
                }}
                QPushButton:hover {{
                    border: 2px solid #4A6CF7;
                }}
            """)
            btn.setToolTip(hex_color)
            btn.clicked.connect(lambda checked, c=hex_color: self._pick(c))
            cgrid.addWidget(btn, row, col, Qt.AlignmentFlag.AlignCenter)
            col += 1
            if col > 1:
                col = 0
                row += 1
                
        # Custom color button
        custom_btn = QPushButton("🖌️")
        custom_btn.setFixedSize(42, 22)
        custom_btn.setToolTip("Custom Color")
        custom_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #FFD1E8, stop:0.5 #D1E8FF, stop:1 #E8FFD1);
                border: 1px solid rgba(0,0,0,0.2);
                border-radius: 11px;
                font-size: 16px;
            }
            QPushButton:hover {
                border-color: #4A6CF7;
            }
        """)
        custom_btn.clicked.connect(self._custom)
        cgrid.addWidget(custom_btn, row, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        
        layout.addLayout(cgrid)

        # -- Opacity slider ------------------------------------------------
        from PyQt6.QtWidgets import QSlider
        self._alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self._alpha_slider.setRange(0, 255)
        self._alpha_slider.setValue(255)
        self._alpha_slider.setFixedWidth(42)
        self._alpha_slider.setToolTip("Brush Opacity")
        self._alpha_slider.setObjectName("alpha_slider")
        self._alpha_slider.valueChanged.connect(self._on_alpha)
        
        layout.addWidget(self._alpha_slider, 0, Qt.AlignmentFlag.AlignCenter)

    def _pick(self, hex_color: str) -> None:
        self._editor.set_active_color(QColor(hex_color))

    def _custom(self) -> None:
        color = QColorDialog.getColor(self._editor.active_color(), self, "Pick Color")
        if color.isValid():
            self._editor.set_active_color(color)

    def _on_alpha(self, value: int) -> None:
        self._editor.set_active_alpha(value)


# =========================================================================
#  Tool palette widget
# =========================================================================

class _ToolPalette(QWidget):
    """A vertical column of tool buttons, using only icons."""

    TOOLS = [
        ("select",    "🖱️", "Select"),
        ("arrow",     "➜", "Arrow"),
        ("rectangle", "▭", "Rectangle"),
        ("highlight", "🖍️", "Highlight"),
        ("pixelate",  "▦", "Pixelate"),
        ("text",      "𝐓", "Text"),
        ("crop",      "✂", "Crop"),
        ("magnifier", "🔍", "Magnifier"),
        ("balloon",   "💬", "Speech Balloon"),
    ]

    def __init__(self, editor: EditorContainer, parent=None) -> None:
        super().__init__(parent)
        self._editor = editor
        from PyQt6.QtWidgets import QGridLayout
        layout = QGridLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self._buttons: dict[str, QPushButton] = {}

        row = col = 0
        for tool_id, icon, name in self.TOOLS:
            btn = QPushButton(icon)
            btn.setCheckable(True)
            btn.setFixedSize(36, 36)
            btn.setToolTip(name)
            btn.setObjectName(f"tool_{tool_id}")
            btn.clicked.connect(lambda checked, t=tool_id: self._on_tool(t))
            layout.addWidget(btn, row, col)
            self._buttons[tool_id] = btn
            col += 1
            if col > 1:
                col = 0
                row += 1

        layout.setRowStretch(row + 1, 1)
        self._buttons["select"].setChecked(True)
        self._editor.tool_changed.connect(self._sync)

    def _on_tool(self, tool_id: str) -> None:
        self._editor.set_tool(tool_id)

    @pyqtSlot(str)
    def _sync(self, tool_id: str) -> None:
        for tid, btn in self._buttons.items():
            btn.setChecked(tid == tool_id)


# =========================================================================
#  Layer panel widget
# =========================================================================

class _LayerPanel(QWidget):
    """Layer list with management buttons."""

    def __init__(self, editor: EditorContainer, parent=None) -> None:
        super().__init__(parent)
        self._editor = editor
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # Button row
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("＋")
        self._btn_del = QPushButton("🗑")
        self._btn_up = QPushButton("▲")
        self._btn_down = QPushButton("▼")
        self._btn_vis = QPushButton("👁")
        for b in (self._btn_add, self._btn_del, self._btn_up, self._btn_down, self._btn_vis):
            b.setFixedSize(32, 32)
            b.setObjectName("layer_btn")
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Layer list
        self._list = QListWidget()
        self._list.setObjectName("layer_list")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._list)

        # Connections
        self._btn_add.clicked.connect(self._add_layer)
        self._btn_del.clicked.connect(self._del_layer)
        self._btn_up.clicked.connect(self._move_up)
        self._btn_down.clicked.connect(self._move_down)
        self._btn_vis.clicked.connect(self._toggle_vis)
        self._editor.layers_changed.connect(self.refresh)

        self.refresh()

    def refresh(self) -> None:
        self._list.clear()
        for lid, name, visible in self._editor.layer_manager.layer_names():
            prefix = "👁 " if visible else "🚫 "
            item = QListWidgetItem(prefix + name)
            item.setData(Qt.ItemDataRole.UserRole, lid)
            self._list.addItem(item)

    def _selected_lid(self) -> Optional[str]:
        cur = self._list.currentItem()
        return cur.data(Qt.ItemDataRole.UserRole) if cur else None
        
    def _selected_lids(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self._list.selectedItems()]

    def _add_layer(self) -> None:
        idx = self._list.count() + 1
        self._editor.layer_manager.add_layer(f"Layer {idx}")
        self._editor.layers_changed.emit()

    def _del_layer(self) -> None:
        lids = self._selected_lids()
        if not lids:
            return
        scene = self._editor.scene()
        for lid in lids:
            items = self._editor.layer_manager.delete_layer(lid)
            for item in items:
                if scene:
                    scene.removeItem(item)
        self._editor.layers_changed.emit()

    def _move_up(self) -> None:
        lid = self._selected_lid()
        if lid:
            self._editor.layer_manager.move_layer_up(lid)
            self._editor.layers_changed.emit()

    def _move_down(self) -> None:
        lid = self._selected_lid()
        if lid:
            self._editor.layer_manager.move_layer_down(lid)
            self._editor.layers_changed.emit()

    def _toggle_vis(self) -> None:
        lid = self._selected_lid()
        if lid:
            self._editor.layer_manager.toggle_visibility(lid)
            self._editor.layers_changed.emit()


# =========================================================================
#  CrabtureMainWindow
# =========================================================================

class CrabtureMainWindow(QMainWindow):
    """Main application window for Crabture."""

    def __init__(self, initial_image: str | None = None, initial_directory: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Crabture — Image Editor")
        from main import get_resource
        self.setWindowIcon(QIcon(get_resource("logo.png")))
        self.resize(1280, 820)
        self._is_dark = False  # current theme state (default: light)

        # -- Central editor ------------------------------------------------
        self._editor = EditorContainer(self)
        self.setCentralWidget(self._editor)

        # -- Dock: Tool palette + color palette (left) --------------------
        tool_dock = QDockWidget("Tools", self)
        tool_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        tool_container = QWidget()
        tool_layout = QVBoxLayout(tool_container)
        tool_layout.setContentsMargins(0, 0, 0, 0)
        tool_layout.setSpacing(0)
        tool_layout.addWidget(self._build_left_actions())
        tool_layout.addWidget(_ToolPalette(self._editor))
        tool_layout.addWidget(_ColorPalette(self._editor))
        tool_dock.setWidget(tool_container)
        tool_dock.setMinimumWidth(60)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, tool_dock)

        # -- Dock: Gallery (top right) ------------------------------------
        self._gallery_dock = QDockWidget("Gallery", self)
        self._gallery_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        
        gallery_container = QWidget()
        gallery_layout = QVBoxLayout(gallery_container)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)
        
        # Controls setup
        g_ctrl_widget = QWidget()
        g_ctrl_widget.setStyleSheet("background: rgba(255,255,255,0.4); border-bottom: 1px solid rgba(0,0,0,0.1);")
        g_ctrl_layout = QHBoxLayout(g_ctrl_widget)
        g_ctrl_layout.setContentsMargins(4, 4, 4, 4)
        
        self._thumb_size = 120
        self._is_list_view = True
        self._thumb_size_slider = QSlider(Qt.Orientation.Horizontal)
        self._thumb_size_slider.setRange(40, 200)
        self._thumb_size_slider.setValue(self._thumb_size)
        self._thumb_size_slider.setToolTip("Thumbnail Size")
        self._thumb_size_slider.valueChanged.connect(self._on_thumb_scale)
        
        size_lbl = QLabel("Size:")
        size_lbl.setStyleSheet("font-size: 11px;")
        g_ctrl_layout.addWidget(size_lbl)
        g_ctrl_layout.addWidget(self._thumb_size_slider)
        
        self._list_view_btn = QPushButton("📄 List")
        self._list_view_btn.setCheckable(True)
        self._list_view_btn.setChecked(True)
        self._list_view_btn.setFixedSize(48, 20)
        self._list_view_btn.setStyleSheet("font-size: 10px;")
        self._list_view_btn.clicked.connect(self._toggle_gallery_view)
        g_ctrl_layout.addWidget(self._list_view_btn)
        
        gallery_layout.addWidget(g_ctrl_widget)

        self._gallery = QListWidget()
        self._gallery.setViewMode(QListWidget.ViewMode.ListMode)
        self._gallery.setIconSize(QSize(self._thumb_size, int(self._thumb_size * 0.75)))
        self._gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._gallery.setSpacing(8)
        self._gallery.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._gallery.setMovement(QListWidget.Movement.Static)
        self._gallery.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._gallery.setObjectName("gallery")
        self._gallery.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._gallery.customContextMenuRequested.connect(self._show_gallery_context_menu)
        self._gallery.itemClicked.connect(self._on_gallery_click)
        gallery_layout.addWidget(self._gallery)
        
        self._gallery_dock.setWidget(gallery_container)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._gallery_dock)

        # -- Dock: Layers (bottom right) ----------------------------------
        layer_dock = QDockWidget("Layers", self)
        layer_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        layer_dock.setWidget(_LayerPanel(self._editor))
        layer_dock.setMinimumWidth(180)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, layer_dock)
        
        # Split right docks vertically
        self.splitDockWidget(self._gallery_dock, layer_dock, Qt.Orientation.Vertical)

        # -- Menubar & Status bar ------------------------------------------
        self._build_menu()
        self._build_status_bar()

        # -- File watcher --------------------------------------------------
        self._watcher: Optional[QFileSystemWatcher] = None
        self._watched_dir: str = CRABSHOTS_DIR
        # Honour --directory flag: watch the requested directory instead.
        start_dir = initial_directory if initial_directory else CRABSHOTS_DIR
        self.watch_directory(start_dir)

    # =================================================================
    #  UI Builders
    # =================================================================

    def _build_left_actions(self) -> QWidget:
        container = QWidget()
        from PyQt6.QtWidgets import QGridLayout
        layout = QGridLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        
        actions = [
            ("↩", "Undo (Ctrl+Z)", self._editor.undo),
            ("↪", "Redo (Ctrl+Y)", self._editor.redo),
            ("📂", "Open image (Ctrl+O)", self._on_open),
            ("💾", "Save (Ctrl+S)", self._on_save),
            ("📋", "Copy to Clipboard", self._on_copy_clipboard),
            ("📤", "Export As", self._on_save_as),
        ]
        
        row = col = 0
        for icon, tooltip, func in actions:
            btn = QPushButton(icon)
            btn.setFixedSize(36, 36)
            btn.setToolTip(tooltip)
            btn.setObjectName("tool_action")
            btn.clicked.connect(func)
            layout.addWidget(btn, row, col)
            col += 1
            if col > 1:
                col = 0
                row += 1
            
        return container

    def _build_status_bar(self) -> None:
        self.statusBar().showMessage("Ready")
        
        # Container for right-aligned items
        right_widget = QWidget()
        right_layout = QHBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 8, 0)
        right_layout.setSpacing(8)
        
        # Zoom controls
        btn_zout = QPushButton("➖")
        btn_zout.setFixedSize(24, 24)
        btn_zout.setToolTip("Zoom Out")
        btn_zout.clicked.connect(self._editor.zoom_out)
        right_layout.addWidget(btn_zout)

        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(10, 400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setFixedWidth(100)
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)
        right_layout.addWidget(self._zoom_slider)

        btn_zin = QPushButton("➕")
        btn_zin.setFixedSize(24, 24)
        btn_zin.setToolTip("Zoom In")
        btn_zin.clicked.connect(self._editor.zoom_in)
        right_layout.addWidget(btn_zin)

        self._zoom_label = QLabel(" 100%")
        self._zoom_label.setFixedWidth(40)
        right_layout.addWidget(self._zoom_label)

        btn_fit = QPushButton("Fit")
        btn_fit.setFixedSize(32, 24)
        btn_fit.clicked.connect(self._editor.fit_to_screen)
        right_layout.addWidget(btn_fit)

        # Theme toggle
        self._theme_btn = QPushButton("🌙 Dark")
        self._theme_btn.setObjectName("theme_btn")
        self._theme_btn.setFixedSize(100, 24)
        self._theme_btn.setToolTip("Switch theme")
        self._theme_btn.clicked.connect(self._toggle_theme)
        right_layout.addWidget(self._theme_btn)
        
        self.statusBar().addPermanentWidget(right_widget)
        
        self._editor.zoom_changed.connect(self._sync_zoom_ui)

    @pyqtSlot(int)
    def _on_zoom_slider(self, value: int) -> None:
        self._editor.update_zoom_slider(value)

    @pyqtSlot(int)
    def _sync_zoom_ui(self, pct: int) -> None:
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(pct)
        self._zoom_slider.blockSignals(False)
        self._zoom_label.setText(f" {pct}%")

    # =================================================================
    #  Menu bar
    # =================================================================

    def _build_menu(self) -> None:
        mb = self.menuBar()

        # -- File --
        file_menu = mb.addMenu("&File")

        act_open = QAction("&Open…", self)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        act_open.triggered.connect(self._on_open)
        file_menu.addAction(act_open)

        file_menu.addSeparator()

        act_save = QAction("&Save (Overwrite)", self)
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        act_save.triggered.connect(self._on_save)
        file_menu.addAction(act_save)

        act_save_as = QAction("Save &As…", self)
        act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_save_as.triggered.connect(self._on_save_as)
        file_menu.addAction(act_save_as)

        file_menu.addSeparator()

        act_clipboard = QAction("&Copy to Clipboard", self)
        act_clipboard.setShortcut(QKeySequence("Ctrl+Shift+C"))
        act_clipboard.triggered.connect(self._on_copy_clipboard)
        file_menu.addAction(act_clipboard)

        file_menu.addSeparator()

        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # -- Edit --
        edit_menu = mb.addMenu("&Edit")

        act_undo = QAction("&Undo", self)
        act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        act_undo.triggered.connect(self._editor.undo)
        edit_menu.addAction(act_undo)

        act_redo = QAction("&Redo", self)
        act_redo.setShortcut(QKeySequence("Ctrl+Y"))
        act_redo.triggered.connect(self._editor.redo)
        edit_menu.addAction(act_redo)

        edit_menu.addSeparator()

        act_rename = QAction("Power &Rename...", self)
        act_rename.setShortcut(QKeySequence("Ctrl+R"))
        act_rename.triggered.connect(self._on_rename_action)
        edit_menu.addAction(act_rename)

        act_del = QAction("&Delete Selected", self)
        act_del.setShortcut(QKeySequence("Delete"))
        act_del.triggered.connect(self._editor.delete_selected)
        edit_menu.addAction(act_del)

        # -- View --
        view_menu = mb.addMenu("&View")

        act_zin = QAction("Zoom &In", self)
        act_zin.setShortcut(QKeySequence("Ctrl+="))
        act_zin.triggered.connect(self._editor.zoom_in)
        view_menu.addAction(act_zin)

        act_zout = QAction("Zoom &Out", self)
        act_zout.setShortcut(QKeySequence("Ctrl+-"))
        act_zout.triggered.connect(self._editor.zoom_out)
        view_menu.addAction(act_zout)

        act_zreset = QAction("&Fit to Window", self)
        act_zreset.setShortcut(QKeySequence("Ctrl+0"))
        act_zreset.triggered.connect(self._editor.reset_zoom)
        view_menu.addAction(act_zreset)

    # =================================================================
    #  Gallery
    # =================================================================

    def watch_directory(self, directory: str | None = None) -> None:
        """Start monitoring *directory* (defaults to ~/Downloads/Crabshots) for new images."""
        target = directory if directory else CRABSHOTS_DIR
        self._watched_dir = target
        Path(target).mkdir(parents=True, exist_ok=True)

        # Update the gallery dock title to reflect the active directory.
        short = target.replace(str(Path.home()), "~")
        self._gallery_dock.setWindowTitle(f"Gallery — {short}")
        self.statusBar().showMessage(f"Watching: {short}")

        if self._watcher is not None:
            # Remove old watches.
            self._watcher.removePaths(self._watcher.directories())
        else:
            self._watcher = QFileSystemWatcher(self)
            self._watcher.directoryChanged.connect(self._refresh_gallery)

        self._watcher.addPath(target)
        self._refresh_gallery()

    def _refresh_gallery(self) -> None:
        self._gallery.clear()
        try:
            entries = sorted(Path(self._watched_dir).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
            
        th_w = getattr(self, "_thumb_size", 120)
        th_h = int(th_w * 0.75)

        for path in entries:
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                pix = QPixmap(str(path))
                if pix.isNull():
                    continue
                thumb = pix.scaled(
                    th_w, th_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                item = QListWidgetItem(QIcon(thumb), path.name)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(str(path))
                self._gallery.addItem(item)
            except Exception:
                continue

    @pyqtSlot(int)
    def _on_thumb_scale(self, value: int) -> None:
        self._thumb_size = value
        self._gallery.setIconSize(QSize(self._thumb_size, int(self._thumb_size * 0.75)))
        self._refresh_gallery()

    @pyqtSlot()
    def _toggle_gallery_view(self) -> None:
        self._is_list_view = not getattr(self, "_is_list_view", False)
        if self._is_list_view:
            self._gallery.setViewMode(QListWidget.ViewMode.ListMode)
        else:
            self._gallery.setViewMode(QListWidget.ViewMode.IconMode)

    @pyqtSlot(QListWidgetItem)
    def _on_gallery_click(self, item: QListWidgetItem) -> None:
        filepath = item.data(Qt.ItemDataRole.UserRole)
        if filepath:
            self.load_image_to_editor(filepath)

    def _show_gallery_context_menu(self, pos) -> None:
        selected_items = self._gallery.selectedItems()
        if not selected_items:
            return
            
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        menu = QMenu(self._gallery)
        menu.setObjectName("context_menu")
        
        rename_action = QAction(f"Power Rename {len(selected_items)} item(s)...", self)
        rename_action.triggered.connect(lambda: self._trigger_power_rename(selected_items))
        menu.addAction(rename_action)
        
        menu.addSeparator()
        
        open_action = QAction("Open in File Explorer", self)
        open_action.triggered.connect(lambda: self._open_file_explorer(selected_items))
        menu.addAction(open_action)
        
        menu.exec(self._gallery.viewport().mapToGlobal(pos))
        
    def _open_file_explorer(self, items):
        if not items:
            return
            
        filepath = items[0].data(Qt.ItemDataRole.UserRole)
        import subprocess, os
        dir_path = os.path.dirname(filepath)
        subprocess.Popen(['xdg-open', dir_path])
        
    def _trigger_power_rename(self, items):
        filepaths = [item.data(Qt.ItemDataRole.UserRole) for item in items]
        from power_rename import PowerRenameDialog
        dlg = PowerRenameDialog(filepaths, self)
        if dlg.exec():
            count = dlg.apply_renames()
            if count > 0:
                self.statusBar().showMessage(f"Power Rename: {count} items renamed.")
                self._refresh_gallery()

    def _on_rename_action(self):
        items = self._gallery.selectedItems()
        if not items:
            QMessageBox.information(self, "Power Rename", "Please select items in the gallery first.")
            return
        self._trigger_power_rename(items)

    def load_image_to_editor(self, filepath: str) -> None:
        """Load *filepath* into the EditorContainer."""
        ok = self._editor.load_image(filepath)
        if ok:
            self.statusBar().showMessage(f"Loaded: {os.path.basename(filepath)}")
        else:
            QMessageBox.warning(
                self,
                "Load Failed",
                f"Could not open image:\n{filepath}\n\nThe file may be corrupted or unsupported.",
            )

    # =================================================================
    #  Menu / button handlers
    # =================================================================

    def _on_open(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open Image",
            os.path.expanduser("~/Downloads/Crabshots"),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tiff)",
        )
        if filepath:
            self.load_image_to_editor(filepath)

    def _on_save(self) -> None:
        if self._editor.save_overwrite():
            self.statusBar().showMessage("Saved (overwritten).")
            self._refresh_gallery()
        else:
            self.statusBar().showMessage("Save failed.")

    def _on_save_as(self) -> None:
        if self._editor.save_as():
            self.statusBar().showMessage("Saved successfully.")
            self._refresh_gallery()

    def _on_copy_clipboard(self) -> None:
        self._editor.copy_to_clipboard()
        self.statusBar().showMessage("Copied to clipboard.")

    # =================================================================
    #  Theme toggle (V2)
    # =================================================================

    def _toggle_theme(self) -> None:
        """Switch between light and dark themes at runtime."""
        app = QApplication.instance()
        if app is None:
            return

        from main import apply_theme
        if self._is_dark:
            apply_theme(app, "light")
            self._theme_btn.setText("🌙 Dark")
            self._theme_btn.setToolTip("Switch to dark mode")
        else:
            apply_theme(app, "dark")
            self._theme_btn.setText("☀ Light")
            self._theme_btn.setToolTip("Switch to light mode")
        self._is_dark = not self._is_dark
