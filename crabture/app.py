#!/usr/bin/env python3
"""
main.py — Entry point for Crabture.

Creates the QApplication, applies a global dark stylesheet, instantiates
CrabtureMainWindow, and enters the event loop.

V2: adds a LIGHT_STYLESHEET and an apply_theme() helper so the UI can
toggle themes at runtime.
"""

from __future__ import annotations

import argparse
import sys
import os

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from ui import CrabtureMainWindow

def get_resource(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# =========================================================================
#  Dark theme
# =========================================================================
DARK_STYLESHEET = """
/* ── Global ──────────────────────────────────────────────────────── */
* {
    font-family: "Segoe UI", "Roboto", "Noto Sans", "Ubuntu", sans-serif;
}
QMainWindow { background: #1E1E1E; }

/* ── Menu bar ────────────────────────────────────────────────────── */
QMenuBar {
    background: #252525; color: #DDD;
    border-bottom: 1px solid #333; padding: 2px; font-size: 13px;
}
QMenuBar::item:selected { background: #0078D4; border-radius: 3px; }
QMenu {
    background: #2A2A2A; color: #DDD;
    border: 1px solid #444; padding: 4px 0;
}
QMenu::item:selected { background: #0078D4; }
QMenu::separator { height: 1px; background: #444; margin: 4px 8px; }

/* ── Dock widgets ────────────────────────────────────────────────── */
QDockWidget { color: #DDD; font-size: 13px; font-weight: bold; }
QDockWidget::title {
    background: #252525; padding: 6px 8px; border-bottom: 1px solid #333;
}

/* ── Toolbar ─────────────────────────────────────────────────────── */
QToolBar {
    background: #252525; border-bottom: 1px solid #333;
    spacing: 4px; padding: 2px 6px;
}
QPushButton#tb_btn {
    background: #2A2A2A; color: #DDD; border: 1px solid #444;
    border-radius: 4px; padding: 4px 8px; font-size: 12px;
}
QPushButton#tb_btn:hover { background: #383838; }
QSlider#zoom_slider::groove:horizontal {
    background: #444; height: 4px; border-radius: 2px;
}
QSlider#zoom_slider::handle:horizontal {
    background: #0078D4; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}
QLabel#zoom_label { color: #AAA; font-size: 12px; }
QSlider#alpha_slider::groove:horizontal {
    background: #444; height: 4px; border-radius: 2px;
}
QSlider#alpha_slider::handle:horizontal {
    background: #E91E63; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}

/* ── Tool palette buttons ────────────────────────────────────────── */
QPushButton[objectName^="tool_"], QPushButton[objectName="tool_action"] {
    text-align: center; padding: 6px 4px;
    border: 1px solid #444; border-radius: 6px;
    color: #DDD; background: #2A2A2A; font-size: 20px;
}
QPushButton[objectName^="tool_"]:hover, QPushButton[objectName="tool_action"]:hover { background: #383838; }
QPushButton[objectName^="tool_"]:checked {
    background: #0078D4; border-color: #0078D4; color: #FFF;
}

/* ── Layer panel ─────────────────────────────────────────────────── */
QPushButton#layer_btn {
    background: #2A2A2A; border: 1px solid #444;
    border-radius: 4px; color: #DDD; font-size: 20px;
}
QPushButton#layer_btn:hover { background: #383838; }
QListWidget#layer_list {
    background: #1E1E1E; color: #DDD;
    border: 1px solid #333; font-size: 13px;
}
QListWidget#layer_list::item { padding: 4px 8px; }
QListWidget#layer_list::item:selected { background: #0078D4; }

/* ── Gallery ─────────────────────────────────────────────────────── */
QListWidget#gallery {
    background: #1A1A1A; border: 1px solid #333;
}
QListWidget#gallery::item {
    background: #252525; border: 2px solid transparent;
    border-radius: 6px; padding: 4px; color: #CCC; font-size: 11px;
}
QListWidget#gallery::item:selected {
    border-color: #0078D4; background: #2C2C2C;
}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollBar:vertical, QScrollBar:horizontal {
    background: #1E1E1E; width: 10px; height: 10px; margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #555; min-height: 24px; min-width: 24px; border-radius: 4px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #777;
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: none; height: 0; width: 0;
}

/* ── Status bar ──────────────────────────────────────────────────── */
QStatusBar { background: #252525; border-top: 1px solid #333; color: #999; font-size: 12px; }
QStatusBar QPushButton#theme_btn { background: #333; color: #FFF; border: 1px solid #555; border-radius: 4px; padding: 2px; }
QStatusBar QPushButton#theme_btn:hover { background: #444; }

/* ── Tooltips ────────────────────────────────────────────────────── */
QToolTip { background: #333; color: #EEE; border: 1px solid #555; padding: 4px; }

/* ── Dialogs / inputs ────────────────────────────────────────────── */
QInputDialog, QFileDialog, QMessageBox { background: #2A2A2A; color: #DDD; }
QLineEdit {
    background: #333; color: #EEE; border: 1px solid #555;
    border-radius: 4px; padding: 4px 8px; selection-background-color: #0078D4;
}
QDialogButtonBox QPushButton, QMessageBox QPushButton {
    background: #0078D4; color: #FFF; border: none;
    border-radius: 4px; padding: 6px 18px; font-weight: bold;
}
QDialogButtonBox QPushButton:hover, QMessageBox QPushButton:hover {
    background: #1A8CD8;
}
"""

# =========================================================================
#  Light theme
# =========================================================================
LIGHT_STYLESHEET = """
/* ── Global ──────────────────────────────────────────────────────── */
* {
    font-family: "Segoe UI", "Roboto", "Noto Sans", "Ubuntu", sans-serif;
}
QMainWindow { background: #F5F5F5; }

/* ── Menu bar ────────────────────────────────────────────────────── */
QMenuBar {
    background: #E8E8E8; color: #333;
    border-bottom: 1px solid #CCC; padding: 2px; font-size: 13px;
}
QMenuBar::item:selected { background: #0078D4; color: #FFF; border-radius: 3px; }
QMenu {
    background: #FAFAFA; color: #333;
    border: 1px solid #CCC; padding: 4px 0;
}
QMenu::item:selected { background: #0078D4; color: #FFF; }
QMenu::separator { height: 1px; background: #CCC; margin: 4px 8px; }

/* ── Dock widgets ────────────────────────────────────────────────── */
QDockWidget { color: #333; font-size: 13px; font-weight: bold; }
QDockWidget::title {
    background: #E8E8E8; padding: 6px 8px; border-bottom: 1px solid #CCC;
}

/* ── Toolbar ─────────────────────────────────────────────────────── */
QToolBar {
    background: #E8E8E8; border-bottom: 1px solid #CCC;
    spacing: 4px; padding: 2px 6px;
}
QPushButton#tb_btn {
    background: #FAFAFA; color: #333; border: 1px solid #CCC;
    border-radius: 4px; padding: 4px 8px; font-size: 12px;
}
QPushButton#tb_btn:hover { background: #DDD; }
QSlider#zoom_slider::groove:horizontal {
    background: #CCC; height: 4px; border-radius: 2px;
}
QSlider#zoom_slider::handle:horizontal {
    background: #0078D4; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}
QLabel#zoom_label { color: #555; font-size: 12px; }
QSlider#alpha_slider::groove:horizontal {
    background: #CCC; height: 4px; border-radius: 2px;
}
QSlider#alpha_slider::handle:horizontal {
    background: #E91E63; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}

/* ── Tool palette buttons ────────────────────────────────────────── */
QPushButton[objectName^="tool_"], QPushButton[objectName="tool_action"] {
    text-align: center; padding: 6px 4px;
    border: 1px solid #CCC; border-radius: 6px;
    color: #333; background: #FAFAFA; font-size: 20px;
}
QPushButton[objectName^="tool_"]:hover, QPushButton[objectName="tool_action"]:hover { background: #E0E0E0; }
QPushButton[objectName^="tool_"]:checked {
    background: #0078D4; border-color: #0078D4; color: #FFF;
}

/* ── Layer panel ─────────────────────────────────────────────────── */
QPushButton#layer_btn {
    background: #FAFAFA; border: 1px solid #CCC;
    border-radius: 4px; color: #333; font-size: 20px;
}
QPushButton#layer_btn:hover { background: #E0E0E0; }
QListWidget#layer_list {
    background: #FFF; color: #333;
    border: 1px solid #CCC; font-size: 13px;
}
QListWidget#layer_list::item { padding: 4px 8px; }
QListWidget#layer_list::item:selected { background: #0078D4; color: #FFF; }

/* ── Gallery ─────────────────────────────────────────────────────── */
QListWidget#gallery {
    background: #FFF; border: 1px solid #CCC;
}
QListWidget#gallery::item {
    background: #F5F5F5; border: 2px solid transparent;
    border-radius: 6px; padding: 4px; color: #333; font-size: 11px;
}
QListWidget#gallery::item:selected {
    border-color: #0078D4; background: #E3F2FD;
}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollBar:vertical, QScrollBar:horizontal {
    background: #F0F0F0; width: 10px; height: 10px; margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #BBB; min-height: 24px; min-width: 24px; border-radius: 4px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #999;
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: none; height: 0; width: 0;
}

/* ── Status bar ──────────────────────────────────────────────────── */
QStatusBar { background: #E8E8E8; border-top: 1px solid #CCC; color: #666; font-size: 12px; }
QStatusBar QPushButton#theme_btn { background: #FFF; color: #333; border: 1px solid #CCC; border-radius: 4px; padding: 2px; }
QStatusBar QPushButton#theme_btn:hover { background: #EEE; }

/* ── Tooltips ────────────────────────────────────────────────────── */
QToolTip { background: #FFF; color: #333; border: 1px solid #CCC; padding: 4px; }

/* ── Dialogs / inputs ────────────────────────────────────────────── */
QInputDialog, QFileDialog, QMessageBox { background: #FAFAFA; color: #333; }
QLineEdit {
    background: #FFF; color: #333; border: 1px solid #CCC;
    border-radius: 4px; padding: 4px 8px; selection-background-color: #0078D4;
}
QDialogButtonBox QPushButton, QMessageBox QPushButton {
    background: #0078D4; color: #FFF; border: none;
    border-radius: 4px; padding: 6px 18px; font-weight: bold;
}
QDialogButtonBox QPushButton:hover, QMessageBox QPushButton:hover {
    background: #1A8CD8;
}
"""

# =========================================================================
#  Glassmorphic periwinkle-blue theme  (DEFAULT)
# =========================================================================
GLASS_STYLESHEET = """
/* ══════════════════════════════════════════════════════════════════
   CRABTURE — Glassmorphic Light-Blue Theme v2
   The glassmorphic effect is achieved by contrasting a vivid
   saturated blue gradient backdrop against very-bright, semi-
   transparent white panels. Real glass glints are simulated by
   making the top/left panel borders bright white and the bottom/
   right edges a darker blue — as frosted glass would look under
   a top-left light source.
   Palette:
     Backdrop           : #7BA7DC → #93B8E8 → #A8C4E8  (rich periwinkle)
     Panel glass        : rgba(248,251,255,0.90)
     Primary accent     : #3D5FD9
     Top-glint border   : rgba(255,255,255,0.92)
     Shadow border      : rgba(130,158,215,0.42)
     Text primary       : #18203A
     Text muted         : #5A6480
     Hover              : #E8EEFF
══════════════════════════════════════════════════════════════════ */

/* ─── Global reset + font ────────────────────────────────────────── */
* {
    font-family: "Inter", "Segoe UI", "Roboto", "Noto Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    color: #18203A;
}

/* ─── Main window — vivid periwinkle-indigo gradient backdrop ────── */
QMainWindow {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0   #7BA7DC,
        stop:0.5 #93B8E8,
        stop:1   #A8C4E8
    );
}

/* ─── Canvas (QGraphicsView) — white card floating on backdrop ────── */
QGraphicsView {
    background: #FAFCFF;
    border: 1.5px solid rgba(255,255,255,0.75);
    border-radius: 10px;
}

/* ─── Menu bar ───────────────────────────────────────────────────── */
QMenuBar {
    background: rgba(255,255,255,0.80);
    color: #18203A;
    border-bottom: 1px solid rgba(144,168,220,0.50);
    padding: 3px 8px;
    font-size: 13px;
    font-weight: 500;
}
QMenuBar::item {
    background: transparent;
    border-radius: 5px;
    padding: 4px 10px;
}
QMenuBar::item:selected {
    background: #3D5FD9;
    color: #FFFFFF;
}
QMenu {
    background: rgba(245,248,255,0.98);
    color: #18203A;
    border: 1px solid rgba(160,180,230,0.55);
    border-radius: 12px;
    padding: 6px 0;
    font-size: 13px;
}
QMenu::item {
    padding: 7px 20px 7px 14px;
    border-radius: 7px;
    margin: 1px 5px;
}
QMenu::item:selected {
    background: #E8EEFF;
    color: #3D5FD9;
}
QMenu::item:disabled { color: #A0AABE; }
QMenu::separator {
    height: 1px;
    background: rgba(160,180,230,0.40);
    margin: 5px 14px;
}

/* ─── Dock widgets — frosted-glass panels ────────────────────────── */
QDockWidget {
    color: #18203A;
    font-size: 14px;
    font-weight: 600;
}
QDockWidget::title {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255,0.97),
        stop:1 rgba(235,242,255,0.91)
    );
    padding: 9px 14px;
    border-top:    1.5px solid rgba(255,255,255,0.92);
    border-left:   1.5px solid rgba(255,255,255,0.92);
    border-right:  1px   solid rgba(130,158,215,0.42);
    border-bottom: 1px   solid rgba(150,178,225,0.45);
    border-radius: 14px 14px 0 0;
    font-weight: 600;
    font-size: 14px;
    letter-spacing: 0.2px;
}
QDockWidget > QWidget {
    background: #FFFFFF;
    border: 1px solid #D8E4F8;
    border-radius: 0 0 14px 14px;
}

/* ─── Toolbar — bright floating pill ────────────────────────────── */
QToolBar {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255,0.97),
        stop:1 rgba(238,244,255,0.93)
    );
    border:      1px   solid rgba(150,175,225,0.50);
    border-top:  1.5px solid rgba(255,255,255,0.97);
    border-left: 1.5px solid rgba(255,255,255,0.97);
    border-radius: 12px;
    spacing: 5px;
    padding: 5px 12px;
    margin: 5px 8px 3px 8px;
}
QToolBar::separator {
    background: rgba(155,180,230,0.45);
    width: 1px;
    margin: 5px 4px;
}

/* ─── Toolbar buttons ────────────────────────────────────────────── */
QPushButton#tb_btn {
    background: rgba(255,255,255,0.65);
    color: #18203A;
    border:     1px solid rgba(150,175,225,0.55);
    border-top: 1px solid rgba(255,255,255,0.88);
    border-radius: 8px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#tb_btn:hover {
    background: #E8EEFF;
    border-color: #3D5FD9;
    color: #3D5FD9;
}
QPushButton#tb_btn:pressed {
    background: #D0D8F8;
    border-color: #2A46C8;
}

/* ─── Zoom slider ────────────────────────────────────────────────── */
QSlider#zoom_slider::groove:horizontal {
    background: rgba(155,180,225,0.48);
    height: 4px;
    border-radius: 2px;
}
QSlider#zoom_slider::sub-page:horizontal {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #6B8EF5, stop:1 #3D5FD9);
    border-radius: 2px;
}
QSlider#zoom_slider::handle:horizontal {
    background: #3D5FD9;
    border: 2.5px solid #FFFFFF;
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}
QSlider#zoom_slider::handle:horizontal:hover { background: #2A46C8; }
QLabel#zoom_label {
    color: #3D5FD9;
    font-size: 12px;
    font-weight: 700;
    background: rgba(61,95,217,0.12);
    border: 1px solid rgba(61,95,217,0.22);
    border-radius: 6px;
    padding: 2px 6px;
}

/* ─── Opacity / alpha slider ─────────────────────────────────────── */
QSlider#alpha_slider::groove:horizontal {
    background: rgba(155,180,225,0.48);
    height: 4px;
    border-radius: 2px;
}
QSlider#alpha_slider::sub-page:horizontal {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #A0B0F0, stop:1 #3D5FD9);
    border-radius: 2px;
}
QSlider#alpha_slider::handle:horizontal {
    background: #3D5FD9;
    border: 2.5px solid #FFFFFF;
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}

/* ─── Left tool palette buttons ──────────────────────────────────── */
QPushButton[objectName^="tool_"], QPushButton[objectName="tool_action"] {
    text-align: center;
    padding: 8px 4px;
    border: 1px solid rgba(130,158,215,0.40);
    border-radius: 6px;
    color: #18203A;
    background: rgba(245,248,255,0.95);
    font-size: 14px;
    font-weight: 500;
    min-height: 36px;
}
QPushButton[objectName^="tool_"]:hover, QPushButton[objectName="tool_action"]:hover {
    background: #FFFFFF;
    border: 1px solid #3D5FD9;
    color: #3D5FD9;
}
QPushButton[objectName^="tool_"]:checked {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #4F75F8, stop:1 #3249CC);
    border: 1px solid #3D5FD9;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton[objectName^="tool_"]:checked:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #5A7EFF, stop:1 #3D5CE0);
}

/* ─── Layer panel buttons ────────────────────────────────────────── */
QPushButton#layer_btn {
    background: rgba(255,255,255,0.65);
    border:     1px solid rgba(150,175,225,0.55);
    border-top: 1px solid rgba(255,255,255,0.88);
    border-radius: 8px;
    color: #18203A;
    font-size: 14px;
}
QPushButton#layer_btn:hover {
    background: #E8EEFF;
    border-color: #3D5FD9;
    color: #3D5FD9;
}
QPushButton#layer_btn:pressed { background: #D0D8F8; }

/* ─── Layer list ─────────────────────────────────────────────────── */
QListWidget#layer_list {
    background: rgba(235,242,255,0.55);
    color: #18203A;
    border: 1px solid rgba(150,175,225,0.38);
    border-radius: 10px;
    font-size: 13px;
    outline: none;
}
QListWidget#layer_list::item {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(150,175,225,0.22);
    border-radius: 7px;
    margin: 1px 4px;
}
QListWidget#layer_list::item:hover {
    background: rgba(61,95,217,0.10);
}
QListWidget#layer_list::item:selected {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #3D5FD9, stop:1 #5278F8);
    color: #FFFFFF;
    border-color: transparent;
}

/* ─── Gallery strip ──────────────────────────────────────────────── */
QListWidget#gallery {
    background: rgba(235,242,255,0.84);
    border: none;
    border-top: 1.5px solid rgba(255,255,255,0.78);
    outline: none;
    padding: 5px;
}
QListWidget#gallery::item {
    background: rgba(255,255,255,0.90);
    border:     1px solid rgba(150,175,225,0.45);
    border-top: 1px solid rgba(255,255,255,0.92);
    border-radius: 9px;
    padding: 5px;
    color: #5A6480;
    font-size: 11px;
}
QListWidget#gallery::item:hover {
    border: 1.5px solid rgba(61,95,217,0.45);
    background: #EEF2FF;
}
QListWidget#gallery::item:selected {
    border: 1.5px solid #3D5FD9;
    background: rgba(61,95,217,0.12);
    color: #3D5FD9;
}

/* ─── Scrollbars ─────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: rgba(255,255,255,0.20);
    width: 7px;
    margin: 3px 2px;
    border-radius: 4px;
}
QScrollBar:horizontal {
    background: rgba(255,255,255,0.20);
    height: 7px;
    margin: 2px 3px;
    border-radius: 4px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: rgba(61,95,217,0.35);
    min-height: 28px;
    min-width: 28px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: rgba(61,95,217,0.62);
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: none; height: 0; width: 0;
}

/* ─── Status bar ─────────────────────────────────────────────────── */
QStatusBar {
    background: rgba(235,242,255,0.80);
    border-top: 1px solid rgba(255,255,255,0.65);
    color: #5A6480;
    font-size: 12px;
    padding: 2px 10px;
}

/* ─── Tooltips ───────────────────────────────────────────────────── */
QToolTip {
    background: rgba(245,248,255,0.98);
    color: #18203A;
    border: 1px solid rgba(150,175,225,0.62);
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 12px;
}

/* ─── Context menus ──────────────────────────────────────────────── */
QMenu[objectName="context_menu"] {
    background: rgba(248,250,255,0.98);
    border: 1px solid rgba(150,175,225,0.55);
    border-radius: 12px;
    padding: 5px 0;
}
QMenu[objectName="context_menu"]::item {
    padding: 7px 20px;
    border-radius: 6px;
    margin: 1px 5px;
}
QMenu[objectName="context_menu"]::item:selected {
    background: #E8EEFF;
    color: #3D5FD9;
}

/* ─── Dialogs / inputs ───────────────────────────────────────────── */
QInputDialog, QFileDialog, QMessageBox {
    background: rgba(245,248,255,0.97);
    color: #18203A;
}
QLineEdit {
    background: rgba(255,255,255,0.90);
    color: #18203A;
    border: 1.5px solid rgba(150,175,225,0.70);
    border-radius: 9px;
    padding: 6px 12px;
    selection-background-color: #3D5FD9;
    font-size: 13px;
}
QLineEdit:focus {
    border-color: #3D5FD9;
    background: #FFFFFF;
}
QDialogButtonBox QPushButton, QMessageBox QPushButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #5278F8, stop:1 #3249CC);
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 7px 22px;
    font-weight: 600;
    font-size: 13px;
}
QDialogButtonBox QPushButton:hover, QMessageBox QPushButton:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #6387FF, stop:1 #3D5CE0);
}
QDialogButtonBox QPushButton:pressed, QMessageBox QPushButton:pressed {
    background: #2A46C8;
}
QColorDialog { background: rgba(245,248,255,0.97); }
"""

_THEMES = {
    "dark": DARK_STYLESHEET,
    "light": LIGHT_STYLESHEET,
    "glass": GLASS_STYLESHEET,
}


def apply_theme(app, theme_name: str) -> None:
    """Apply a named theme ('dark', 'light', or 'glass') to *app* at runtime."""
    sheet = _THEMES.get(theme_name, LIGHT_STYLESHEET)
    app.setStyleSheet(sheet)


def main() -> None:
    # --- Parse CLI arguments (used by .desktop right-click integration) ---
    parser = argparse.ArgumentParser(
        prog="crabture",
        description="Crabture — Image Editor",
    )
    parser.add_argument(
        "image",
        nargs="?",
        default=None,
        help="Image file to open on launch (used by 'Edit with Crabture' context menu).",
    )
    parser.add_argument(
        "--directory",
        "-d",
        default=None,
        metavar="DIR",
        help="Directory to populate the gallery with on launch "
             "(used by 'Open Directory with Crabture' context menu).",
    )
    # argparse must not consume Qt's own flags; parse only known args.
    args, _qt_args = parser.parse_known_args()

    app = QApplication(sys.argv)
    app.setApplicationName("Crabture")
    app.setOrganizationName("YawnByte")
    
    app.setWindowIcon(QIcon(get_resource("logo.png")))
    
    apply_theme(app, "light")

    window = CrabtureMainWindow(initial_image=args.image, initial_directory=args.directory)
    window.show()

    # Load the image after the event loop is ready.
    if args.image:
        window.load_image_to_editor(args.image)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
