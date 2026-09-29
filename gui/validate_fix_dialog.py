"""
gui/validate_fix_dialog.py

Operations > Validate / Fix Issues...: the review step for
core/comicinfo_check.py -- one row per finding (file, field, what's
wrong, current value, proposed fix), fixable ones ticked by default,
report-only ones listed without a tickbox. The main window applies the
ticked fixes as ordinary edits (one Undo step, written on Save).
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

FILE_COL, FIELD_COL, PROBLEM_COL, CURRENT_COL, FIX_COL = range(5)


def _shown(value: str) -> str:
    return "(clear)" if value == "" else value


class ValidateFixDialog(QDialog):
    def __init__(self, found: list, skipped_note: str = "", parent=None):
        """`found`: (book, Finding) pairs."""
        super().__init__(parent)
        self.setWindowTitle("Validate / Fix Issues")
        self.resize(1100, 480)
        self._found = found
        fixable = sum(1 for _book, f in found if f.fixable)
        files = len({id(book) for book, _f in found})

        layout = QVBoxLayout(self)
        intro = QLabel(
            f"{len(found)} issue(s) in {files} file(s), {fixable} with a fix. Untick anything you "
            "don't want changed, then Apply: the fixes are ordinary edits -- one Undo step, written "
            "to the files on Save." + (f"<br>{skipped_note}" if skipped_note else "")
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(found), 5)
        self.table.setHorizontalHeaderLabels(["File", "Field", "Problem", "Current", "Fix"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(PROBLEM_COL, QHeaderView.ResizeMode.Stretch)
        for column, width in ((FILE_COL, 250), (FIELD_COL, 110), (CURRENT_COL, 170), (FIX_COL, 150)):
            self.table.setColumnWidth(column, width)
        for row, (book, finding) in enumerate(found):
            file_item = QTableWidgetItem(os.path.basename(book.path))
            file_item.setToolTip(book.path)
            if finding.fixable:
                file_item.setFlags(file_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                file_item.setCheckState(Qt.CheckState.Checked)
            current = getattr(book.metadata, finding.field, "")
            cells = (
                file_item, QTableWidgetItem(finding.label), QTableWidgetItem(finding.message),
                QTableWidgetItem(repr(current) if current != current.strip() else current),
                QTableWidgetItem(_shown(finding.fix) if finding.fixable else "(check by hand)"),
            )
            for column, item in enumerate(cells):
                if column:
                    item.setToolTip(item.text())
                self.table.setItem(row, column, item)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.apply_button = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.table.itemChanged.connect(self._update_button)
        layout.addWidget(buttons)
        self._update_button()

    def _update_button(self, *_args) -> None:
        count = len(self.ticked())
        self.apply_button.setText(f"Apply {count} Fix(es)")
        self.apply_button.setEnabled(count > 0)

    def ticked(self) -> list:
        """(book, Finding) for every ticked fix."""
        return [
            self._found[row] for row in range(self.table.rowCount())
            if self._found[row][1].fixable and self.table.item(row, FILE_COL).checkState() == Qt.CheckState.Checked
        ]
