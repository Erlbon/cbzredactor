"""
gui/archive_cleanup_dialog.py

Operations > Clean Up Archive Contents...: the review step -- every
selected file whose insides would change, what would change
(core/archive_contents.py), and a tickbox each. The main window applies
the ticked ones (CbzBook.clean_contents, originals to the Recycle Bin).
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

EXPLANATION = (
    "Tidies the names <b>inside</b> each archive: pages are renamed to plain numbers in reading "
    "order (001.webp, 002.webp, ...) and taken out of folders -- which drops release-group names "
    "and over-long paths -- and junk files (Thumbs.db, .DS_Store, __MACOSX, .nfo, .sfv, .url, "
    ".txt ...) are removed. The pages themselves, their order and ComicInfo.xml are unchanged. "
    "Each original goes to the Recycle Bin."
)


class ArchiveCleanupDialog(QDialog):
    def __init__(self, work: list, skipped_note: str = "", parent=None):
        """`work`: (book, plan) pairs to offer, plan.needed True for each."""
        super().__init__(parent)
        self.setWindowTitle("Clean Up Archive Contents")
        self.resize(980, 460)
        self._work = work

        layout = QVBoxLayout(self)
        intro = QLabel(EXPLANATION + (f"<br><br>{skipped_note}" if skipped_note else ""))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(work), 2)
        self.table.setHorizontalHeaderLabels(["File", "Changes"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 330)
        for row, (book, plan) in enumerate(work):
            name_item = QTableWidgetItem(os.path.basename(book.path))
            name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name_item.setCheckState(Qt.CheckState.Checked)
            name_item.setToolTip(book.path)
            changes = QTableWidgetItem(plan.summary())
            changes.setToolTip(_example(plan) or plan.summary())
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, changes)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton(f"Clean Up {len(work)} File(s)", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.table.itemChanged.connect(self._update_button)
        layout.addWidget(buttons)

    def _update_button(self, *_args) -> None:
        count = len(self.ticked())
        self.apply_button.setText(f"Clean Up {count} File(s)")
        self.apply_button.setEnabled(count > 0)

    def ticked(self) -> list:
        return [
            self._work[row][0] for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]


def _example(plan) -> str:
    """A before/after sample for the tooltip."""
    pages = [(old, new) for old, new in plan.renames.items() if new != "ComicInfo.xml"]
    if not pages:
        return ""
    lines = [f"{old}  →  {new}" for old, new in pages[:3]]
    if len(pages) > 3:
        lines.append("...")
    return "\n".join(lines)
