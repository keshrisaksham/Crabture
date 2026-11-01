"""
ui/window.py — CrabtureMainWindow: the main GUI shell.

Layout
  • Top toolbar    – file / edit / capture / effects actions, theme and help menus
  • Left tool rail – every annotation tool (ui/toolrail.py)
  • Centre         – a stack: welcome page (no image) or the canvas
  • Right panel    – Properties on top; Layers | Gallery tabs below (ui/panels.py)
  • Status bar     – message, image info, capture backend, zoom controls

The visual language comes from ui/theme.py (stylesheet roles) and ui/icons.py
(vector icons).  Behaviour that other modules rely on — capture flow,
unsaved-changes guard, effects, opt-in sensitive-data scan, Power Rename —
lives here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSettings, QSize, Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsTextItem,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from crabture.capture import config as capture_config
from crabture.ui import icons
from crabture.settings import keymap
from crabture.ui import theme
from crabture.settings import tool_config
from crabture.editor.canvas import EditorContainer
from crabture.resources import get_resource
from crabture.ui.common import SUPPORTED_EXTENSIONS as _DEFAULT_EXTENSIONS
from crabture.ui.common import divider, set_role
from crabture.ui.dialogs import AboutDialog, ShortcutsDialog
from crabture.ui.panels import GalleryPanel, LayersPanel, PropertiesPanel
from crabture.ui.stickers import STICKERS, pick_sticker
from crabture.ui.toolrail import ToolRail
from crabture.ui.welcome import WelcomePage

# Directory to watch.
# Default capture folder; the folder actually used is capture_config.folder() (Configure ▸ Capture).
CRABSHOTS_DIR = capture_config.default_folder()
SUPPORTED_EXTENSIONS = set(_DEFAULT_EXTENSIONS)

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tiff)"
ICON = 20          # toolbar icon size


class CrabtureMainWindow(QMainWindow):
    """Main application window for Crabture."""

    def __init__(self, initial_image: str | None = None, initial_directory: str | None = None) -> None:
        # The gallery starts on the folder the user asked for (--directory), else on the folder of the
        # file being opened, else on the screenshot folder.
        if not initial_directory and initial_image:
            initial_directory = os.path.dirname(os.path.abspath(initial_image))
        super().__init__()
        self._base_title = "Crabture"
        self.setWindowTitle(self._base_title)
        self.setWindowIcon(QIcon(get_resource("logo.png")))
        self.resize(1280, 820)
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)

        # Screen capture: the controller is shared with shortcuts / `--capture` (see app.py).
        self._capture_menu: Optional[QMenu] = None
        self._capture_shortcuts: list[QShortcut] = []
        self._last_capture_folder = capture_config.folder()
        qs = QSettings()
        self._side_panel_wanted = str(qs.value("ui/side_panel", "true")).lower() != "false"
        self._watched_dir: str = CRABSHOTS_DIR
        self._settings_dlg = None            # single SettingsDialog instance (created lazily)
        self._did_first_show = False         # Crabture always starts maximized
        # keymap id -> QAction; shortcuts and tooltips follow keymap live.
        self._keyed_actions: dict[str, QAction] = {}
        self._tip_base: dict[QAction, str] = {}

        # -- Core widgets --------------------------------------------------
        self._editor = EditorContainer(self)
        self._editor.modified_changed.connect(self._update_title)
        self._editor.color_picked.connect(
            lambda c: self.statusBar().showMessage(f"Picked colour {c.name().upper()}")
        )
        theme.signals.changed.connect(lambda _n: self._editor.apply_theme_colors())

        self._build_actions()
        self._apply_shortcuts()
        self._build_central()
        self._build_toolbar()
        self._build_menu()
        self._build_status_bar()
        self._connect_signals()

        # -- Gallery / watcher --------------------------------------------
        self._gallery = self._gallery_panel.list
        start_dir = initial_directory if initial_directory else capture_config.folder()
        self.watch_directory(start_dir)

        self._set_page(False)
        self._restore_state()
        self._refresh_enabled()
        self._update_image_chip()
        self._refresh_title()
        keymap.signals.changed.connect(self._apply_shortcuts)
        capture_config.signals.changed.connect(self._reload_capture_settings)
        self._rebuild_capture_shortcuts()

    # =====================================================================
    #  Actions (shared by toolbar and menus)
    # =====================================================================

    def _act(self, text: str, icon_name: str | None, shortcut: str | None, slot, tip: str | None = None,
             checkable: bool = False, key_id: str | None = None, tooltip: str | None = None) -> QAction:
        """Create an action.  *key_id* (a keymap id) makes its shortcut and
        tooltip follow the user's configuration; *shortcut* is then ignored."""
        act = QAction(text.replace("&", ""), self)
        act.setText(text)
        if icon_name:
            icons.bind_icon(act, icon_name, ICON)
        act.setToolTip(tooltip or tip or text.replace("&", "").rstrip("…"))
        self._tip_base[act] = tooltip or tip or text.replace("&", "").rstrip("…")
        act.setStatusTip(tip or "")
        act.setCheckable(checkable)
        if key_id:
            self._keyed_actions[key_id] = act
        elif shortcut:
            act.setShortcut(QKeySequence(shortcut))
            act.setToolTip(f"{self._tip_base[act]} ({shortcut})")
        if slot is not None:
            act.triggered.connect(lambda _checked=False, s=slot: s())
        return act

    def _apply_shortcuts(self, _changed: str = "") -> None:
        """(Re)assign every keyed action's shortcut and tooltip from keymap."""
        used: set[str] = set()
        for key_id, act in self._keyed_actions.items():
            seq = keymap.get(key_id)
            norm = QKeySequence(seq).toString() if seq else ""
            if norm and norm in used:
                # keymap.conflicts() prevents this; never create an ambiguous shortcut anyway.
                seq, norm = "", ""
            if norm:
                used.add(norm)
            act.setShortcut(QKeySequence(seq) if seq else QKeySequence())
            base = self._tip_base.get(act, act.text().replace("&", ""))
            act.setToolTip(f"{base} ({seq})" if seq else base)
        if hasattr(self, "_tb_capture"):
            self._tb_capture_act.setToolTip(self._tb_capture_tip())

    def _tb_capture_tip(self) -> str:
        seq = capture_config.shortcut_for_mode("region")
        return "Capture a region (arrow: other capture modes)" + (f" ({seq})" if seq else "")

    def _build_actions(self) -> None:
        ed = self._editor
        self.act_open = self._act("&Open Image…", "open", None, self._on_open, "Open an image", key_id="action.open")
        self.act_open_folder = self._act("Open &Folder…", "folder", None, self._on_open_folder,
                                         "Show a folder in the gallery", key_id="action.open_folder")
        self.act_close = self._act("&Close Image", "close", None, self.close_image, "Close the image",
                                   key_id="action.close_image")
        self.act_save = self._act("&Save (Overwrite)", "save", None, self._on_save, "Save the image",
                                  key_id="action.save")
        self.act_save_as = self._act("Save &As…", "save_as", None, self._on_save_as,
                                     "Save to a new file", key_id="action.save_as")
        self.act_copy = self._act("&Copy to Clipboard", "copy", None, self._on_copy_clipboard,
                                  "Copy the image to the clipboard", key_id="action.copy")
        self.act_quit = self._act("&Quit", None, None, self.close, "Quit Crabture", key_id="action.quit")

        self.act_undo = self._act("&Undo", "undo", None, ed.undo, "Undo", key_id="action.undo")
        self.act_redo = self._act("&Redo", "redo", None, ed.redo, "Redo", key_id="action.redo")
        self.act_delete = self._act("&Delete Selected", "trash", None, self._on_delete_selected,
                                    "Delete the selected annotation", key_id="action.delete")
        # Only fire the shortcut while the canvas has focus (not the gallery,
        # layer list or dialogs); the menu entry still works everywhere.
        self.act_delete.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        ed.addAction(self.act_delete)
        self.act_effects = self._act("Image &Effects…", "effects", None, self._on_effects,
                                     "Borders, shadow, torn edge, watermark, background",
                                     key_id="action.effects")
        # Sensitive data is split in two: Configure once, then Run with one click.
        self.act_sensitive_run = self._act(
            "&Run Sensitive-Data Scan", "shield", None, self._on_run_sensitive,
            "Scan with your saved sensitive-data settings",
            key_id="action.sensitive_run",
            tooltip="Run Sensitive-Data Scan — runs your saved settings (Configure ▸ Sensitive Data). "
                    "You review the findings before anything is applied.")
        self.act_sensitive = self.act_sensitive_run      # backwards-compatible name
        self.act_sensitive_configure = self._act(
            "Configure &Sensitive Data…", "settings", None, self._on_configure_sensitive,
            "Choose what the scan looks for and how it redacts", key_id="action.sensitive_configure")
        self.act_rename = self._act("Power &Rename…", "rename", None, self._on_rename_action,
                                    "Bulk-rename the files selected in the gallery", key_id="action.rename")
        self.act_undo_rename = self._act("Undo Last Re&name", "history", None, self._on_undo_last_rename,
                                         "Restore the file names from the last Power Rename",
                                         key_id="action.undo_rename")
        self.act_sticker = self._act("Choose Stic&ker…", "sticker", None,
                                     lambda: pick_sticker(self._editor, self), "Choose the sticker emoji",
                                     key_id="action.choose_sticker")
        self.act_steps = self._act("Reset Step &Counter", "step", None, ed.reset_step_counter,
                                   "Number the next step marker 1 again", key_id="action.reset_steps")

        self.act_zoom_in = self._act("Zoom &In", "zoom_in", None, ed.zoom_in, key_id="action.zoom_in")
        self.act_zoom_out = self._act("Zoom &Out", "zoom_out", None, ed.zoom_out, key_id="action.zoom_out")
        self.act_fit = self._act("&Fit to Window", "fit", None, ed.reset_zoom, key_id="action.fit")
        self.act_actual = self._act("&Actual Size", "actual_size", None, lambda: ed.update_zoom_slider(100),
                                    key_id="action.actual_size")
        self.act_side = self._act("Toggle Side &Panel", "layers", None, None, "Show or hide the side panel",
                                  checkable=True, key_id="action.toggle_panel")
        self.act_side.setChecked(self._side_panel_wanted)
        self.act_side.toggled.connect(self._on_side_toggled)

        self.act_shortcuts = self._act("&Keyboard Shortcuts", "keyboard", None, self._show_shortcuts,
                                       key_id="action.shortcuts_help")
        self.act_about = self._act("&About Crabture", "info", None, self._show_about)

        # Configure
        self.act_configure = self._act("&Preferences…", "settings", None,
                                       lambda: self._open_settings(None),
                                       "Open the configuration window", key_id="action.configure")
        self.act_cfg_shortcuts = self._act("&Keyboard Shortcuts…", "keyboard", None,
                                           lambda: self._open_settings("shortcuts"),
                                           "Change tool keys and menu shortcuts")
        self.act_cfg_tools = self._act("&Tool Settings…", "wand", None,
                                       lambda: self._open_settings("tools"),
                                       "Default colours, widths and sizes for each tool")
        self.act_cfg_folder = self._act("Screenshot &Folder…", "folder", None,
                                        lambda: self._open_settings("capture", focus_folder=True),
                                        "Choose the folder screenshots are saved in")

        # Capture actions (their shortcuts come from Configure ▸ Capture, not from keymap)
        self.capture_actions: dict[str, QAction] = {}
        self._capture_labels: dict[str, str] = {}
        for mode, label, icon_name, tip in (
            ("region", "&Region…", "capture_region", "Freeze the screen and drag a rectangle"),
            ("fullscreen", "&Fullscreen", "capture_fullscreen", "All monitors"),
            ("window", "Active &Window", "capture_window",
             "Windows / X11: the focused window. Wayland: the system window picker"),
            ("monitor", "&Monitor…", "capture_monitor",
             "One monitor: the one under the pointer, or click the one you want when there are several"),
            ("scroll", "&Scrolling capture…", "capture_scroll",
             "Select a region, then scroll the content; frames are stitched"),
        ):
            self.capture_actions[mode] = self._act(
                label, icon_name, None, lambda m=mode: self.run_capture(m), tip
            )
            self._capture_labels[mode] = label

    # =====================================================================
    #  Layout
    # =====================================================================

    def _build_central(self) -> None:
        # Pages
        self._welcome = WelcomePage()
        self._editor_page = QFrame()
        self._editor_page.setObjectName("canvas_surround")
        el = QVBoxLayout(self._editor_page)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(self._editor)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._welcome)
        self._stack.addWidget(self._editor_page)

        # Side panel
        self._properties = PropertiesPanel(self._editor)
        self._layers_panel = LayersPanel(self._editor)
        self._gallery_panel = GalleryPanel(lambda: SUPPORTED_EXTENSIONS, capture_config.folder())
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._layers_panel, "Layers")
        self._tabs.addTab(self._gallery_panel, "Gallery")

        self._side_split = QSplitter(Qt.Orientation.Vertical)
        self._side_split.setChildrenCollapsible(False)
        self._side_split.addWidget(self._properties)
        self._side_split.addWidget(self._tabs)
        self._side_split.setStretchFactor(0, 0)
        self._side_split.setStretchFactor(1, 1)
        self._side_split.setSizes([260, 460])
        # Let the Properties area follow its content until the user drags the divider.
        self._split_user_set = False
        self._side_split.splitterMoved.connect(lambda *_: setattr(self, "_split_user_set", True))
        self._properties.content_height_changed.connect(self._fit_properties)

        self._side_panel = QFrame()
        self._side_panel.setObjectName("side_panel")
        sl = QVBoxLayout(self._side_panel)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self._side_split)
        self._side_panel.setMinimumWidth(330)

        self._rail = ToolRail(self._editor)

        self._main_split = QSplitter(Qt.Orientation.Horizontal)
        self._main_split.setChildrenCollapsible(False)
        self._main_split.addWidget(self._stack)
        self._main_split.addWidget(self._side_panel)
        self._main_split.setStretchFactor(0, 1)
        self._main_split.setStretchFactor(1, 0)
        self._main_split.setSizes([900, 320])

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._rail)
        h.addWidget(self._main_split, 1)
        self.setCentralWidget(central)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(ICON, ICON))
        tb.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self._toolbar = tb

        self._tb_logo = QLabel()
        self._tb_logo.setContentsMargins(6, 0, 10, 0)
        tb.addWidget(self._tb_logo)
        self._retint_logo()
        theme.signals.changed.connect(lambda _n: self._retint_logo())

        def btn(action: QAction) -> QToolButton:
            b = QToolButton()
            b.setProperty("role", "toolbar")
            b.setDefaultAction(action)
            b.setIconSize(QSize(ICON, ICON))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            return b

        def group(*actions: QAction) -> None:
            for a in actions:
                tb.addWidget(btn(a))

        def sep() -> None:
            tb.addWidget(self._spacer_divider())

        group(self.act_open, self.act_save, self.act_save_as, self.act_copy)
        sep()
        group(self.act_undo, self.act_redo)
        sep()

        # Capture: click = region capture, arrow = full menu.
        self._tb_capture = QToolButton()
        self._tb_capture.setProperty("role", "toolbar")
        self._tb_capture_act = QAction("Capture", self)
        self._tb_capture_act.triggered.connect(lambda _=False: self.run_capture("region"))
        self._tb_capture_act.setToolTip(self._tb_capture_tip())
        icons.bind_icon(self._tb_capture_act, "capture", ICON)
        self._tb_capture.setDefaultAction(self._tb_capture_act)
        self._tb_capture.setMenu(self._get_capture_menu())
        self._tb_capture.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._tb_capture.setToolTip(self._tb_capture_tip())
        self._tb_capture.setIconSize(QSize(ICON, ICON))
        tb.addWidget(self._tb_capture)
        sep()
        group(self.act_effects, self.act_sensitive_run)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # Configure button, then the theme and help menus
        tb.addWidget(btn(self.act_configure))

        # Theme menu button
        self._theme_menu = QMenu("Theme", self)
        self._theme_group = QActionGroup(self)
        self._theme_actions: dict[str, QAction] = {}
        for name, label, icon_name in (("system", "System", "settings"), ("light", "Light", "sun"),
                                       ("dark", "Dark", "moon")):
            a = QAction(label, self, checkable=True)
            icons.bind_icon(a, icon_name, 16)
            a.triggered.connect(lambda _=False, n=name: self._set_theme(n))
            self._theme_group.addAction(a)
            self._theme_menu.addAction(a)
            self._theme_actions[name] = a
        self._theme_actions.get(theme.initial_theme(), self._theme_actions["system"]).setChecked(True)
        self._tb_theme = QToolButton()
        self._tb_theme.setProperty("role", "toolbar")
        self._tb_theme.setMenu(self._theme_menu)
        self._tb_theme.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._tb_theme.setToolTip("Theme")
        self._tb_theme.setIconSize(QSize(ICON, ICON))
        tb.addWidget(self._tb_theme)
        self._update_theme_icon()
        theme.signals.changed.connect(lambda _n: self._update_theme_icon())

        # Help menu button
        self._help_menu = QMenu("Help", self)
        self._help_menu.addAction(self.act_shortcuts)
        self._help_menu.addAction(self.act_about)
        tb_help = QToolButton()
        tb_help.setProperty("role", "toolbar")
        tb_help.setMenu(self._help_menu)
        tb_help.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        icons.bind_icon(tb_help, "info", ICON)
        tb_help.setToolTip("Help")
        tb_help.setIconSize(QSize(ICON, ICON))
        tb.addWidget(tb_help)

    @staticmethod
    def _spacer_divider() -> QWidget:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(6, 6, 6, 6)
        line = divider(vertical=True)
        line.setFixedHeight(20)
        lay.addWidget(line)
        return wrap

    def _retint_logo(self) -> None:
        self._tb_logo.setPixmap(icons.pixmap("logo_mark", 22, "accent_fg"))

    def _update_theme_icon(self) -> None:
        self._tb_theme.setIcon(icons.icon("moon" if theme.current_theme() == "dark" else "sun", ICON))

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        for a in (self.act_open, self.act_open_folder, None, self.act_close, None, self.act_save,
                  self.act_save_as, self.act_copy, None, self.act_quit):
            file_menu.addSeparator() if a is None else file_menu.addAction(a)

        self._edit_menu = mb.addMenu("&Edit")
        for a in (self.act_undo, self.act_redo, self.act_delete, None, self.act_effects,
                  self.act_sensitive_run, self.act_sensitive_configure, None, self.act_rename,
                  self.act_undo_rename, None, self.act_sticker, self.act_steps):
            self._edit_menu.addSeparator() if a is None else self._edit_menu.addAction(a)
        self._edit_menu.aboutToShow.connect(self._refresh_rename_undo)

        mb.addMenu(self._get_capture_menu())

        self._configure_menu = mb.addMenu("Con&figure")
        for a in (self.act_configure, None, self.act_cfg_shortcuts, self.act_cfg_tools,
                  self.act_cfg_folder, self.act_sensitive_configure):
            self._configure_menu.addSeparator() if a is None else self._configure_menu.addAction(a)

        view_menu = mb.addMenu("&View")
        for a in (self.act_zoom_in, self.act_zoom_out, self.act_fit, self.act_actual, None, self.act_side):
            view_menu.addSeparator() if a is None else view_menu.addAction(a)
        view_menu.addSeparator()
        view_menu.addMenu(self._theme_menu)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self.act_shortcuts)
        help_menu.addAction(self.act_about)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.showMessage("Ready")

        holder = QWidget()
        holder.setObjectName("status_bar_widget")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(10)

        # Image chip + modified dot
        self._chip_image = QLabel("")
        set_role(self._chip_image, "badge")
        self._dot = QLabel("●")
        set_role(self._dot, "warning")
        self._dot.setToolTip("Unsaved changes")
        self._dot.hide()
        lay.addWidget(self._chip_image)
        lay.addWidget(self._dot)

        # Capture backend chip
        backend = ""
        try:
            from crabture.capture.service import backend_info
            backend = backend_info()
        except Exception:
            backend = ""
        self._backend = backend
        self._backend_chip = None
        if backend:
            chip = QLabel(backend)
            set_role(chip, "caption")
            chip.setToolTip("Screen-capture backend in use")
            lay.addWidget(chip)
            self._backend_chip = chip
        lay.addWidget(divider(vertical=True))

        # Zoom controls
        def tbtn(action: QAction) -> QToolButton:
            b = QToolButton()
            b.setProperty("role", "toolbar")
            b.setDefaultAction(action)
            b.setIconSize(QSize(16, 16))
            icons.bind_icon(action, action_icon[action], 16)
            return b

        action_icon = {self.act_zoom_out: "zoom_out", self.act_zoom_in: "zoom_in",
                       self.act_fit: "fit", self.act_actual: "actual_size"}
        # Status-bar buttons share the actions but show a smaller icon.
        lay.addWidget(tbtn(self.act_zoom_out))
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(10, 400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setFixedWidth(110)
        self._zoom_slider.setToolTip("Zoom")
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)
        lay.addWidget(self._zoom_slider)
        lay.addWidget(tbtn(self.act_zoom_in))
        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(44)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._zoom_label)
        lay.addWidget(tbtn(self.act_fit))
        lay.addWidget(tbtn(self.act_actual))
        sb.addPermanentWidget(holder)
        self._editor.zoom_changed.connect(self._sync_zoom_ui)

    def _connect_signals(self) -> None:
        # Welcome page
        self._welcome.capture_requested.connect(lambda m: self.run_capture(m))
        self._welcome.open_image_requested.connect(self._on_open)
        self._welcome.open_folder_requested.connect(self._on_open_folder)
        self._welcome.image_chosen.connect(self.load_image_to_editor)
        self._welcome.shortcuts_setup_requested.connect(lambda: self._open_settings("capture"))
        # Rail / panels
        self._rail.sticker_reclicked.connect(lambda w: pick_sticker(self._editor, w))
        gp = self._gallery_panel
        gp.open_requested.connect(self.load_image_to_editor)
        gp.open_files_requested.connect(self.open_files)
        gp.rename_requested.connect(self._trigger_power_rename)
        gp.change_folder_requested.connect(self._on_open_folder)
        gp.listing_changed.connect(self._on_listing_changed)
        self._properties.configure_requested.connect(lambda tool: self._open_settings("tools", tool=tool))
        # Tool defaults / configuration changes refresh the Properties panel
        self._editor.tool_defaults_applied.connect(self._properties.refresh_from_editor)
        tool_config.signals.changed.connect(lambda _t: QTimer.singleShot(0, self._properties.refresh_from_editor))
        keymap.signals.changed.connect(lambda _i: self._properties.refresh_from_editor())
        # Editor
        self._editor.layers_changed.connect(self._update_image_chip)
        self._editor.layers_changed.connect(self._update_history_actions)
        self._editor.modified_changed.connect(lambda _m: self._update_image_chip())
        self._editor.zoom_changed.connect(lambda _z: self._update_image_chip())

    # =====================================================================
    #  Pages / state
    # =====================================================================

    def _set_page(self, editor_page: bool) -> None:
        self._stack.setCurrentWidget(self._editor_page if editor_page else self._welcome)
        self._rail.setVisible(editor_page)
        self._side_panel.setVisible(editor_page and self.act_side.isChecked())
        self._refresh_enabled()

    def _has_image(self) -> bool:
        return self._editor.background_pixmap() is not None

    def _refresh_enabled(self) -> None:
        has = self._has_image()
        for a in (self.act_save, self.act_save_as, self.act_copy, self.act_close, self.act_effects,
                  self.act_sensitive_run, self.act_undo, self.act_redo, self.act_delete, self.act_sticker,
                  self.act_steps, self.act_zoom_in, self.act_zoom_out, self.act_fit, self.act_actual):
            a.setEnabled(has)
        self._zoom_slider.setEnabled(has)
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        """Undo / Redo: greyed out when there is nothing to step to, and named after what they would do."""
        has = self._has_image()
        for act, can, label, verb in ((self.act_undo, self._editor.can_undo(), self._editor.undo_label(), "Undo"),
                                      (self.act_redo, self._editor.can_redo(), self._editor.redo_label(), "Redo")):
            act.setEnabled(has and can)
            act.setText(f"&{verb} {label}" if can and label else f"&{verb}")

    def _fit_properties(self, needed: int) -> None:
        if self._split_user_set:
            return
        total = sum(self._side_split.sizes()) or self._side_split.height()
        top = max(150, min(needed, int(total * 0.6)))
        self._side_split.setSizes([top, max(120, total - top)])

    @pyqtSlot(bool)
    def _on_side_toggled(self, on: bool) -> None:
        self._side_panel_wanted = on
        QSettings().setValue("ui/side_panel", "true" if on else "false")
        self._side_panel.setVisible(on and self._stack.currentWidget() is self._editor_page)

    def _restore_state(self) -> None:
        qs = QSettings()
        # Crabture always starts maximized, so the window geometry is neither
        # saved nor restored (a stale un-maximized geometry from an older
        # version is dropped).
        qs.remove("ui/geometry")
        for key, split in (("ui/split_main", self._main_split), ("ui/split_side", self._side_split)):
            state = qs.value(key)
            if state is not None:
                try:
                    split.restoreState(state)
                    if split is self._side_split:
                        self._split_user_set = True
                except Exception:
                    pass
        self.setMinimumSize(900, 600)

    def _save_state(self) -> None:
        qs = QSettings()
        qs.setValue("ui/split_main", self._main_split.saveState())
        qs.setValue("ui/split_side", self._side_split.saveState())

    def _doc_name(self) -> str:
        path = self._editor.current_filepath()
        return os.path.basename(path) if path else ("Untitled" if self._has_image() else "")

    def _refresh_title(self) -> None:
        name = self._doc_name()
        mark = "*" if self._editor.is_modified() else ""
        self.setWindowTitle(f"{mark}{name} — {self._base_title}" if name else self._base_title)

    @pyqtSlot(bool)
    def _update_title(self, modified: bool) -> None:
        self._refresh_title()

    def _update_image_chip(self) -> None:
        pm = self._editor.background_pixmap()
        if pm is None:
            self._chip_image.setText("No image")
            self._chip_image.setToolTip("")
        else:
            full = f"{self._doc_name()}  ·  {pm.width()}×{pm.height()}"
            fm = self._chip_image.fontMetrics()
            self._chip_image.setText(fm.elidedText(full, Qt.TextElideMode.ElideMiddle, 280))
            self._chip_image.setToolTip(full)
        self._dot.setVisible(self._editor.is_modified())
        self._refresh_title()
        self._refresh_enabled()

    @pyqtSlot(int)
    def _on_zoom_slider(self, value: int) -> None:
        self._editor.update_zoom_slider(value)

    @pyqtSlot(int)
    def _sync_zoom_ui(self, pct: int) -> None:
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(pct)
        self._zoom_slider.blockSignals(False)
        self._zoom_label.setText(f"{pct}%")

    # =====================================================================
    #  Theme
    # =====================================================================

    def _set_theme(self, name: str) -> None:
        theme.set_theme_preference(name)
        act = self._theme_actions.get(name)
        if act:
            act.setChecked(True)

    # =====================================================================
    #  Gallery
    # =====================================================================

    def watch_directory(self, directory: str | None = None) -> None:
        """Start monitoring *directory* (defaults to the capture folder) for new images."""
        target = directory if directory else capture_config.folder()
        self._watched_dir = target
        self._gallery_panel.watch(target)
        short = target.replace(str(Path.home()), "~")
        self.statusBar().showMessage(f"Watching: {short}")

    def _refresh_gallery(self) -> None:
        self._gallery_panel.refresh()

    def _on_listing_changed(self, paths: list) -> None:
        short = self._watched_dir.replace(str(Path.home()), "~")
        self._welcome.set_recent(paths, short)
        self._update_hotkey_hint()

    def _update_hotkey_hint(self) -> None:
        show = False
        if str(QSettings().value("ui/hotkey_hint_done", "false")).lower() != "true" \
                and not capture_config.desktop_applied():
            try:
                from crabture.capture import hotkeys
                show = hotkeys.supported()
            except Exception:
                show = False
        self._welcome.set_hotkey_hint_visible(show)

    def _on_open_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open Folder", self._watched_dir)
        if directory:
            self.watch_directory(directory)
            self._tabs.setCurrentWidget(self._gallery_panel)

    def _trigger_power_rename(self, items):
        # Number files in gallery display order, not in selection order.
        ordered = sorted(items, key=self._gallery.row)
        filepaths = [item.data(Qt.ItemDataRole.UserRole) for item in ordered]
        from crabture.rename.dialog import PowerRenameDialog
        batch_before = self._last_rename_batch()
        dlg = PowerRenameDialog(filepaths, self)
        if dlg.exec():
            count = dlg.apply_renames()
            for old, new in dlg.renamed.items():
                self._editor.notify_file_renamed(old, new)
            if count > 0:
                self.statusBar().showMessage(f"Power Rename: {count} items renamed.")
            if dlg.failures:
                lines = [f"{os.path.basename(p)}: {why}" for p, why in dlg.failures[:10]]
                if len(dlg.failures) > 10:
                    lines.append(f"… and {len(dlg.failures) - 10} more")
                QMessageBox.warning(
                    self,
                    "Power Rename",
                    f"{count} renamed, {len(dlg.failures)} failed:\n\n" + "\n".join(lines),
                )
            self._refresh_gallery()
        elif getattr(dlg, "undo_performed", False):
            self._notify_restored(batch_before)
            self._refresh_gallery()
            self.statusBar().showMessage(
                f"Undid Power Rename: {getattr(dlg, 'undone_count', 0)} file(s) restored."
            )

    def _on_rename_action(self):
        items = self._gallery_panel.selected_items()
        if not items:
            QMessageBox.information(self, "Power Rename", "Please select items in the gallery first.")
            self._tabs.setCurrentWidget(self._gallery_panel)
            return
        self._trigger_power_rename(items)

    # -- rename undo -------------------------------------------------------

    @staticmethod
    def _last_rename_batch():
        try:
            from crabture.rename.history import last_rename_batch
            return last_rename_batch()
        except Exception:
            return None

    def _rename_undo_available(self) -> bool:
        try:
            from crabture.rename.dialog import has_rename_undo
        except ImportError:
            try:
                from crabture.rename.history import has_rename_undo
            except ImportError:
                return False
        try:
            return bool(has_rename_undo())
        except Exception:
            return False

    def _refresh_rename_undo(self) -> None:
        self.act_undo_rename.setEnabled(self._rename_undo_available())

    def _notify_restored(self, batch) -> None:
        """After an undo, point the editor back at a restored open file."""
        if not batch:
            return
        for item in batch.get("items", []):
            old, new = item.get("old"), item.get("new")
            if old and new and os.path.exists(old) and not os.path.exists(new):
                self._editor.notify_file_renamed(new, old)

    def _on_undo_last_rename(self) -> None:
        batch = self._last_rename_batch()
        if not batch:
            QMessageBox.information(self, "Undo Last Rename", "There is no rename to undo.")
            return
        if QMessageBox.question(
            self,
            "Undo Last Rename",
            f"Restore the original names of {batch.get('count', '?')} file(s) renamed on "
            f"{batch.get('time', 'an earlier date')}?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            from crabture.rename.history import undo_last_rename
        except ImportError:
            from crabture.rename.dialog import undo_last_rename
        count, failures = undo_last_rename()
        self._notify_restored(batch)
        self._refresh_gallery()
        self.statusBar().showMessage(f"Undid Power Rename: {count} file(s) restored.")
        if failures:
            lines = [f"{os.path.basename(p) or '—'}: {why}" for p, why in failures[:10]]
            QMessageBox.warning(self, "Undo Last Rename",
                                f"{count} restored, {len(failures)} could not be:\n\n" + "\n".join(lines))

    # =====================================================================
    #  Loading / closing images
    # =====================================================================

    def load_image_to_editor(self, filepath: str) -> bool:
        """Load *filepath* into the EditorContainer.  True if it is now the open image."""
        if not self._confirm_discard_changes():
            return False
        ok = self._editor.load_image(filepath)
        if ok:
            self._set_page(True)
            self._update_image_chip()
            self._properties.update_sections()
            self.statusBar().showMessage(f"Loaded: {os.path.basename(filepath)}")
        else:
            QMessageBox.warning(
                self,
                "Load Failed",
                f"Could not open image:\n{filepath}\n\nThe file may be corrupted or unsupported.",
            )
        return ok

    def open_path(self, filepath: str) -> bool:
        """Open a file chosen from *outside* the gallery (File ▸ Open, command line, drag and drop).

        The gallery follows the file: it shows the folder the file came from.
        """
        if not self.load_image_to_editor(filepath):
            return False
        folder = os.path.dirname(os.path.abspath(filepath))
        panel = self._gallery_panel
        in_session = panel.is_file_session() and os.path.abspath(filepath) in panel._files
        if not in_session and (panel.is_file_session() or os.path.abspath(self._watched_dir) != folder):
            self.watch_directory(folder)
        return True

    def open_files(self, paths: list[str]) -> bool:
        """Open several files as ONE session: the first is edited, and the gallery lists only these files."""
        paths = [p for p in dict.fromkeys(os.path.abspath(p) for p in paths)
                 if os.path.isfile(p) and Path(p).suffix.lower() in SUPPORTED_EXTENSIONS]
        if not paths:
            self.statusBar().showMessage("None of those files is a supported image.")
            return False
        if len(paths) == 1:
            return self.open_path(paths[0])
        if not self.load_image_to_editor(paths[0]):
            return False
        self._watched_dir = os.path.dirname(paths[0])
        self._gallery_panel.show_files(paths)
        self._tabs.setCurrentWidget(self._gallery_panel)
        self.statusBar().showMessage(f"Opened {len(paths)} files")
        return True

    def close_image(self) -> None:
        """Close the current image (asks about unsaved changes) and show the welcome page."""
        if not self._has_image():
            return
        if not self._confirm_discard_changes():
            return
        self._editor.clear_image()
        self._set_page(False)
        self._update_image_chip()
        self.statusBar().showMessage("Image closed.")

    # -- drag and drop -----------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._handle_dropped_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def _handle_dropped_paths(self, paths: list[str]) -> None:
        for path in paths:
            if os.path.isdir(path):
                self.watch_directory(path)
                self._tabs.setCurrentWidget(self._gallery_panel)
                return
        images = [p for p in paths if Path(p).suffix.lower() in SUPPORTED_EXTENSIONS]
        if images:
            self.open_files(images)
            return
        if paths:
            self.statusBar().showMessage("Dropped file is not a supported image.")

    # =====================================================================
    #  Menu / button handlers
    # =====================================================================

    def _on_open(self) -> None:
        filepaths, _ = QFileDialog.getOpenFileNames(
            self, "Open Image", self._watched_dir, IMAGE_FILTER
        )
        if filepaths:
            self.open_files(filepaths)

    def _on_save(self) -> bool:
        """Save the current image.  Returns True only if it was written."""
        if self._editor.save_overwrite():
            self._editor.mark_clean()
            self.statusBar().showMessage(f"Saved {os.path.basename(self._editor.current_filepath() or '')}.")
            self._refresh_gallery()
            self._update_image_chip()
            return True
        # False covers both a failed write and a cancelled Save As dialog.
        return self._report_save_failure()

    def _report_save_failure(self) -> bool:
        """Tell the user why a save failed (nothing is said when they just cancelled the dialog).

        Offers "Save As…" so the work is never stuck behind a read-only location.
        Returns True only if that second attempt wrote a file.
        """
        reason = self._editor.save_error()
        if not reason:
            self.statusBar().showMessage("Not saved.")
            return False
        self.statusBar().showMessage("Not saved.")
        box = QMessageBox(QMessageBox.Icon.Warning, "Could not save", reason, parent=self)
        save_as = box.addButton("Save As…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is save_as and self._editor.save_as():
            self.statusBar().showMessage("Saved.")
            self._refresh_gallery()
            self._update_image_chip()
            return True
        return False

    def _on_save_as(self) -> None:
        if self._editor.save_as():
            self.statusBar().showMessage(f"Saved {os.path.basename(self._editor.current_filepath() or '')}.")
            self._refresh_gallery()
            self._update_image_chip()
        else:
            self._report_save_failure()

    def _on_delete_selected(self) -> None:
        # Never delete the item whose text is being typed into.
        if isinstance(self._editor.scene().focusItem(), QGraphicsTextItem):
            return
        if self._layers_panel.list_widget.hasFocus() and self._layers_panel.list_widget.selectedItems():
            self._layers_panel._del_layer()          # the user is working in the Layers list
            return
        self._editor.delete_selected()

    def _on_copy_clipboard(self) -> None:
        self._editor.copy_to_clipboard()
        self.statusBar().showMessage("Copied to clipboard.")

    # =====================================================================
    #  Screen capture
    # =====================================================================

    def _get_capture_menu(self) -> QMenu:
        """Build (once) the Capture menu shared by the menubar and the toolbar button."""
        if self._capture_menu is not None:
            return self._capture_menu
        menu = QMenu("&Capture", self)
        for mode in capture_config.CAPTURE_MODES:
            menu.addAction(self.capture_actions[mode])

        menu.addSeparator()
        delay_menu = menu.addMenu("&Delay")
        icons.bind_icon(delay_menu.menuAction(), "timer", 16)
        group = QActionGroup(self)
        group.setExclusive(True)
        self._delay_actions: dict[int, QAction] = {}
        for secs in capture_config.DELAY_CHOICES:
            act = QAction("None" if secs == 0 else f"{secs} s", self, checkable=True)
            self._delay_actions[secs] = act
            act.triggered.connect(lambda _=False, v=secs: capture_config.set_delay(v))
            group.addAction(act)
            delay_menu.addAction(act)

        act_hide = QAction("&Hide Crabture while capturing", self, checkable=True)
        act_hide.toggled.connect(capture_config.set_hide_window)
        self._act_hide = act_hide
        menu.addAction(act_hide)

        act_clip = QAction("Copy captures to &clipboard", self, checkable=True)
        act_clip.toggled.connect(capture_config.set_clipboard)
        self._act_clip = act_clip
        menu.addAction(act_clip)

        menu.addSeparator()
        act_folder = QAction("Open capture &folder", self)
        icons.bind_icon(act_folder, "folder", 16)
        act_folder.triggered.connect(self._open_capture_folder)
        menu.addAction(act_folder)
        act_cfg = QAction("Capture &settings and shortcuts…", self)
        icons.bind_icon(act_cfg, "settings", 16)
        act_cfg.triggered.connect(lambda: self._open_settings("capture"))
        menu.addAction(act_cfg)
        act_info = QAction("Capture &backend…", self)
        icons.bind_icon(act_info, "info", 16)
        act_info.triggered.connect(self._show_capture_backend)
        menu.addAction(act_info)

        self._capture_menu = menu
        self._reload_capture_settings()
        return menu

    def _reload_capture_settings(self) -> None:
        """Re-read the capture options (they may have changed in Configure ▸ Capture)."""
        delay = capture_config.delay()
        for secs, act in getattr(self, "_delay_actions", {}).items():
            act.setChecked(abs(delay - secs) < 1e-6)
        for act, value in ((getattr(self, "_act_hide", None), capture_config.hide_window()),
                           (getattr(self, "_act_clip", None), capture_config.clipboard())):
            if act is not None:
                act.blockSignals(True)
                act.setChecked(value)
                act.blockSignals(False)
        # The gallery follows the capture folder when it was showing the old one.
        folder = capture_config.folder()
        if folder != self._last_capture_folder:
            if (os.path.abspath(self._watched_dir) == os.path.abspath(self._last_capture_folder)
                    and not self._gallery_panel.is_file_session()):
                self.watch_directory(folder)
            self._last_capture_folder = folder
        self._rebuild_capture_shortcuts()

    def _rebuild_capture_shortcuts(self) -> None:
        """(Re)create the in-app capture shortcuts and show them in the menu."""
        for sc in self._capture_shortcuts:
            sc.setEnabled(False)
            sc.setParent(None)
            sc.deleteLater()
        self._capture_shortcuts = []
        used: set[str] = set()
        for item in capture_config.shortcuts():
            if not item.key:
                continue
            seq = QKeySequence(item.key, QKeySequence.SequenceFormat.PortableText)
            norm = seq.toString()
            if seq.isEmpty() or norm in used or self._keymap_uses(norm):
                continue                                  # never create an ambiguous shortcut
            used.add(norm)
            sc = QShortcut(seq, self)
            sc.activated.connect(lambda it=item: self.run_capture_shortcut(it))
            self._capture_shortcuts.append(sc)
        for mode, act in self.capture_actions.items():
            key = capture_config.shortcut_for_mode(mode)
            label = self._capture_labels.get(mode, act.text())
            act.setText(f"{label}\t{QKeySequence(key, QKeySequence.SequenceFormat.PortableText).toString(QKeySequence.SequenceFormat.NativeText)}"
                        if key else label)
        if hasattr(self, "_tb_capture_act"):
            self._tb_capture_act.setToolTip(self._tb_capture_tip())
            self._tb_capture.setToolTip(self._tb_capture_tip())

    @staticmethod
    def _keymap_uses(norm_sequence: str) -> bool:
        return any(QKeySequence(text).toString() == norm_sequence
                   for _d, text in keymap.all() if text)

    def run_capture_shortcut(self, item) -> None:
        self._submit_capture(capture_config.request_for(item, "shortcut"))

    def _open_capture_folder(self) -> None:
        folder = capture_config.writable_folder(capture_config.folder())[0]
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def run_capture(self, mode: str, delay: float | None = None, hide: bool | None = None,
                    quit_on_cancel: bool = False) -> None:
        """Start a screen capture from the menu, toolbar or welcome page.

        ``None`` for *delay* / *hide* means the saved defaults.  (*quit_on_cancel* is
        accepted for older callers; the application object handles that now.)
        """
        self._submit_capture(capture_config.CaptureRequest(mode=mode, delay=delay, hide=hide, source="menu"))

    def _submit_capture(self, request) -> None:
        from crabture.capture.controller import shared
        if not shared().request(request):
            self.statusBar().showMessage("A capture is already in progress.")

    def _present(self) -> None:
        if self.isFullScreen():
            self.show()
        else:
            self.showMaximized()
        self.raise_()
        self.activateWindow()

    def show_capture_result(self, result) -> None:
        """A capture finished (called by the application object)."""
        name = os.path.basename(result.path)
        extra = f" — {result.note}" if result.note else ""
        if result.request.after == "editor":
            self._present()
            self.load_image_to_editor(result.path)
            self.statusBar().showMessage(f"Captured ({result.request.mode}): {name}{extra}")
        else:
            where = os.path.dirname(result.path).replace(str(Path.home()), "~")
            self.statusBar().showMessage(f"Saved {name} in {where}{extra}")
        self._refresh_gallery()

    def show_capture_failure(self, message: str) -> None:
        QMessageBox.warning(self if self.isVisible() else None, "Capture failed", message)

    def show_capture_cancelled(self) -> None:
        self.statusBar().showMessage("Capture cancelled.")

    def _show_capture_backend(self) -> None:
        try:
            from crabture.capture.service import backend_info
            info = backend_info()
        except Exception as e:  # pragma: no cover - defensive
            info = f"unknown ({e})"
        QMessageBox.information(self, "Capture backend", info)

    # =====================================================================
    #  Effects / sensitive-data scan
    # =====================================================================

    def _require_image(self, what: str) -> bool:
        if self._editor.background_pixmap() is None:
            QMessageBox.information(self, what, "Open or capture an image first.")
            return False
        return True

    def _on_effects(self) -> None:
        if not self._require_image("Image Effects"):
            return
        from crabture.effects.dialog import EffectsDialog
        dlg = EffectsDialog(self._editor.render_flattened(), self)
        if dlg.exec():
            # Annotations are flattened into the result; Undo restores everything.
            self._editor.replace_image(dlg.result_pixmap(), "Effects")
            self._update_image_chip()
            self.statusBar().showMessage("Effects applied (Ctrl+Z to undo).")

    def _on_run_sensitive(self) -> None:
        """Run the saved sensitive-data settings.  Only ever called because the
        user triggered this action: nothing scans or redacts by itself, and
        (unless the user turned review off in the settings) every finding is
        reviewed in the dialog before anything is applied."""
        if not self._require_image("Run Sensitive-Data Scan"):
            return
        from crabture.redact import dialog as redact_dialog
        result = redact_dialog.run_saved_profile(self._editor.render_flattened(), self)
        if not result or not result[0]:
            self.statusBar().showMessage("No redaction applied.")
            return
        rects, style, intensity = result
        n = self._editor.add_blur_regions(rects, style, intensity=intensity)
        self.statusBar().showMessage(f"Redacted {n} region(s) — Ctrl+Z undoes each.")

    _on_find_sensitive = _on_run_sensitive      # compatibility alias

    def _on_configure_sensitive(self) -> None:
        self._open_settings("sensitive")

    # =====================================================================
    #  Configure window
    # =====================================================================

    def _open_settings(self, tab: str | None = None, tool: str | None = None,
                       focus_folder: bool = False) -> None:
        """Show the (single, non-modal) Configure window, optionally on *tab*.

        *tool* selects that tool on the Tools tab; *focus_folder* puts the cursor in the
        screenshot-folder field of the Capture tab."""
        dlg = self._settings_dlg
        if dlg is not None and tab is not None:
            switch = next((getattr(dlg, n) for n in ("show_tab", "set_tab", "select_tab", "set_current_tab")
                           if hasattr(dlg, n)), None)
            if switch is not None:
                switch(tab)
            else:                      # no tab switcher: recreate on the requested tab
                dlg.close()
                dlg.deleteLater()
                dlg = self._settings_dlg = None
        if dlg is None:
            from crabture.settings.dialog import SettingsDialog
            dlg = SettingsDialog(self, initial_tab=tab)
            self._settings_dlg = dlg
            dlg.destroyed.connect(lambda *_: setattr(self, "_settings_dlg", None))
        if tool and hasattr(dlg, "show_tool"):
            dlg.show_tool(tool)
        if focus_folder and hasattr(dlg, "focus_capture_folder"):
            dlg.focus_capture_folder()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # =====================================================================
    #  Dialogs
    # =====================================================================

    def _show_shortcuts(self) -> None:
        dlg = ShortcutsDialog(self)         # built fresh: always shows the current keymap
        dlg.customise_requested.connect(
            lambda: (dlg.accept(), QTimer.singleShot(0, lambda: self._open_settings("shortcuts"))))
        dlg.exec()

    def _show_about(self) -> None:
        try:
            from crabture.version import __version__ as version
        except Exception:
            version = "unknown"
        AboutDialog(str(version), getattr(self, "_backend", ""), self).exec()

    # =====================================================================
    #  Unsaved changes
    # =====================================================================

    def _confirm_discard_changes(self) -> bool:
        """Return True if it is safe to replace/close the current image."""
        if not self._editor.is_modified():
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved Changes",
            "The current image has unsaved annotations. Save them first?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self._on_save()
        return choice == QMessageBox.StandardButton.Discard

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Always start maximized, also when the capture service re-shows a
        # window it hid.  Only the first show: don't fight a later un-maximize.
        if not self._did_first_show:
            self._did_first_show = True
            if not self.isMaximized() and not self.isFullScreen():
                self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Make room for the status message on narrow windows.
        chip = getattr(self, "_backend_chip", None)
        if chip is not None:
            chip.setVisible(self.width() >= 1100)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._confirm_discard_changes():
            self._save_state()
            event.accept()
        else:
            event.ignore()
