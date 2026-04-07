import os
import re
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QCheckBox, QTableWidget, QTableWidgetItem, QPushButton, 
    QHeaderView, QComboBox, QTabWidget, QWidget, QSpinBox
)

class PowerRenameDialog(QDialog):
    """
    A PowerToys-style bulk rename dialog.
    Allows basic sequential renaming or advanced search and replacing 
    using regular expressions across selected files.
    """
    def __init__(self, filepaths: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Power Rename")
        self.resize(700, 550)
        self.filepaths = filepaths
        self.new_names = [Path(fp).name for fp in filepaths]
        self._build_ui()
        self._preview()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # -- Tabs for Simple / Advanced --
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._preview)

        # TAB 1: Sequential Rename (Simple)
        seq_tab = QWidget()
        seq_layout = QVBoxLayout(seq_tab)
        
        base_name_layout = QHBoxLayout()
        base_name_layout.addWidget(QLabel("Base Name:"))
        self.base_name_edit = QLineEdit()
        self.base_name_edit.setPlaceholderText("e.g. vacation_photo")
        self.base_name_edit.textChanged.connect(self._preview)
        base_name_layout.addWidget(self.base_name_edit)
        seq_layout.addLayout(base_name_layout)

        num_layout = QHBoxLayout()
        num_layout.addWidget(QLabel("Start Number:"))
        self.start_num_spin = QSpinBox()
        self.start_num_spin.setRange(0, 999999)
        self.start_num_spin.setValue(1)
        self.start_num_spin.valueChanged.connect(self._preview)
        num_layout.addWidget(self.start_num_spin)
        
        num_layout.addSpacing(20)
        num_layout.addWidget(QLabel("Number Padding (Zeros):"))
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(1, 10)
        self.padding_spin.setValue(1)
        self.padding_spin.valueChanged.connect(self._preview)
        num_layout.addWidget(self.padding_spin)

        num_layout.addSpacing(20)
        num_layout.addWidget(QLabel("Separator:"))
        self.separator_combo = QComboBox()
        self.separator_combo.addItems(["_ (Underscore)", "- (Dash)", "  (Space)", "None"])
        self.separator_combo.currentIndexChanged.connect(self._preview)
        num_layout.addWidget(self.separator_combo)

        num_layout.addStretch()
        seq_layout.addLayout(num_layout)
        seq_layout.addStretch()

        self.tabs.addTab(seq_tab, "Sequential Rename")

        # TAB 2: Search & Replace (Advanced)
        sr_tab = QWidget()
        sr_layout = QVBoxLayout(sr_tab)
        
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search for:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._preview)
        search_layout.addWidget(self.search_edit)
        sr_layout.addLayout(search_layout)

        replace_layout = QHBoxLayout()
        replace_layout.addWidget(QLabel("Replace with:"))
        self.replace_edit = QLineEdit()
        self.replace_edit.textChanged.connect(self._preview)
        replace_layout.addWidget(self.replace_edit)
        sr_layout.addLayout(replace_layout)

        opts_layout = QHBoxLayout()
        self.regex_cb = QCheckBox("Use Regular Expressions")
        self.regex_cb.stateChanged.connect(self._preview)
        self.case_cb = QCheckBox("Match Case")
        self.case_cb.stateChanged.connect(self._preview)
        
        self.apply_to_combo = QComboBox()
        self.apply_to_combo.addItems(["Filename + Extension", "Filename only", "Extension only"])
        self.apply_to_combo.currentIndexChanged.connect(self._preview)
        
        opts_layout.addWidget(self.regex_cb)
        opts_layout.addWidget(self.case_cb)
        opts_layout.addSpacing(20)
        opts_layout.addWidget(QLabel("Apply to:"))
        opts_layout.addWidget(self.apply_to_combo)
        opts_layout.addStretch()
        sr_layout.addLayout(opts_layout)
        sr_layout.addStretch()

        self.tabs.addTab(sr_tab, "Search & Replace (Advanced)")

        layout.addWidget(self.tabs)

        # -- Table --
        self.table = QTableWidget(len(self.filepaths), 2)
        self.table.setHorizontalHeaderLabels(["Original", "Renamed"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        
        for i, fp in enumerate(self.filepaths):
            name = Path(fp).name
            self.table.setItem(i, 0, QTableWidgetItem(name))
            self.table.setItem(i, 1, QTableWidgetItem(name))

        layout.addWidget(self.table)

        # -- Buttons --
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.clicked.connect(self.accept)
        
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_apply)
        layout.addLayout(btn_layout)

    def _preview(self):
        if not hasattr(self, 'search_edit') or not hasattr(self, 'separator_combo'):
            return

        is_sequential = (self.tabs.currentIndex() == 0)

        # Advanced Tab values
        search_term = self.search_edit.text()
        replace_term = self.replace_edit.text()
        use_regex = self.regex_cb.isChecked()
        match_case = self.case_cb.isChecked()
        apply_to = self.apply_to_combo.currentIndex()

        flags = 0 if match_case else re.IGNORECASE

        # Sequential Tab values
        base_name = self.base_name_edit.text()
        start_num = self.start_num_spin.value()
        padding = self.padding_spin.value()
        sep_idx = self.separator_combo.currentIndex()
        sep = "_" if sep_idx == 0 else "-" if sep_idx == 1 else " " if sep_idx == 2 else ""

        for i, fp in enumerate(self.filepaths):
            original_path = Path(fp)
            original = original_path.name
            stem = original_path.stem
            ext = original_path.suffix

            new_name = original

            if is_sequential:
                if base_name:
                    num_str = str(start_num + i).zfill(padding)
                    new_name = f"{base_name}{sep}{num_str}{ext}"
            else:
                target = original
                if apply_to == 1:
                    target = stem
                elif apply_to == 2:
                    target = ext[1:] if ext else ""
                    
                new_target = target

                if search_term:
                    try:
                        if use_regex:
                            new_target = re.sub(search_term, replace_term, target, flags=flags)
                        else:
                            if match_case:
                                new_target = target.replace(search_term, replace_term)
                            else:
                                pattern = re.compile(re.escape(search_term), re.IGNORECASE)
                                new_target = pattern.sub(lambda m: replace_term, target)
                    except re.error:
                        pass  # Invalid regex being typed
                
                # Reassemble based on mode
                if apply_to == 1:
                    new_name = new_target + ext
                elif apply_to == 2:
                    new_name = stem + ("." + new_target if new_target else "")
                else:
                    new_name = new_target

            self.new_names[i] = new_name
            
            # Update UI
            item = self.table.item(i, 1)
            item.setText(new_name)
            
            # Highlight changes
            if new_name != original:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            else:
                font = item.font()
                font.setBold(False)
                item.setFont(font)

    def apply_renames(self) -> int:
        """Execute renames and return the number of successful renames."""
        count = 0
        for i, fp in enumerate(self.filepaths):
            original = Path(fp).name
            new_name = self.new_names[i]
            if original != new_name:
                parent_dir = Path(fp).parent
                new_path = parent_dir / new_name
                try:
                    if not new_path.exists():
                        os.rename(fp, new_path)
                        count += 1
                except Exception as e:
                    print(f"Failed to rename {fp} to {new_path}: {e}")
        return count
