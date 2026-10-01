"""
gui/archive_cleanup_dialog.py

Operations > Clean Up Archive Contents...: the review step -- every
selected file whose insides would change under the chosen options (three
checkboxes: junk files, folders, page names), what would change
(core/archive_contents.py), and a tickbox each. The main window applies the
ticked ones (CbzBook.clean_contents, originals to the Recycle Bin).
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.archive_contents import CleanupOptions, plan_cleanup

EXPLANATION = (
    "Tidies the names <b>inside</b> each archive. Choose below what to do; the list shows the files "
    "that would change. The pages themselves, their order and ComicInfo.xml's content are never "
    "changed, and each original goes to the Recycle Bin."
)

# (CleanupOptions field, checkbox text, plain-language help)
OPTION_TEXTS = (
    ("remove_junk", "Remove junk files",
     "Deletes Thumbs.db, desktop.ini, .DS_Store, __MACOSX folders and release-group extras "
     "(.nfo, .sfv, .url, .txt ...). These are not comic pages."),
    ("flatten_folders", "Move pages out of folders",
     "Takes pages out of subfolders (such as a release-group folder) so paths stay short enough for "
     "Windows and other tools. Page order never changes. A page keeps its own name unless two pages "
     "would clash, then a short number is put in front."),
    ("rename_pages", "Rename pages to 001, 002, ...",
     "Gives the pages plain numbers in reading order, which drops release-group tags and long text "
     "from the names. Alone, pages stay in their folders."),
)


class ArchiveCleanupDialog(QDialog):
    def __init__(self, candidates: list, options: CleanupOptions = CleanupOptions(), skipped_note: str = "",
                 parent=None):
        """`candidates`: (book, entry names) for every file that could be cleaned. The table
        lists those the chosen options would change, and follows the checkboxes."""
        super().__init__(parent)
        self.setWindowTitle("Clean Up Archive Contents")
        self.resize(980, 560)
        self._candidates = candidates
        self._unticked: set[int] = set()  # ids of books the user unticked, kept across option changes
        self._rows: list = []  # the book shown in each table row

        layout = QVBoxLayout(self)
        intro = QLabel(EXPLANATION + (f"<br><br>{skipped_note}" if skipped_note else ""))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        group = QGroupBox("What to clean up")
        group_layout = QVBoxLayout(group)
        self.checkboxes: dict[str, QCheckBox] = {}
        for field, text, help_text in OPTION_TEXTS:
            box = QCheckBox(text)
            box.setChecked(getattr(options, field))
            box.setToolTip(help_text)
            box.toggled.connect(self._options_changed)
            help_label = QLabel(help_text)
            help_label.setWordWrap(True)
            help_label.setContentsMargins(24, 0, 0, 6)
            help_label.setEnabled(False)  # greyed: secondary text under its checkbox
            self.checkboxes[field] = box
            group_layout.addWidget(box)
            group_layout.addWidget(help_label)
        layout.addWidget(group)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["File", "Changes"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 330)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton("Clean Up", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.itemChanged.connect(self._item_changed)
        self._fill_table()

    def options(self) -> CleanupOptions:
        return CleanupOptions(**{field: box.isChecked() for field, box in self.checkboxes.items()})

    def _options_changed(self, *_args) -> None:
        self._fill_table()

    def _fill_table(self) -> None:
        options = self.options()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._rows = []
        for book, names in self._candidates:
            plan = plan_cleanup(names, book.page_names, book.comicinfo_name, options) if options.any else None
            if plan is None or not plan.needed:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(os.path.basename(book.path))
            name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name_item.setCheckState(
                Qt.CheckState.Unchecked if id(book) in self._unticked else Qt.CheckState.Checked
            )
            name_item.setToolTip(book.path)
            summary = plan.summary() + (f" -- {'; '.join(plan.notes)}" if plan.notes else "")
            changes = QTableWidgetItem(summary)
            changes.setToolTip(_example(plan) or summary)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, changes)
            self._rows.append(book)
        self.table.blockSignals(False)
        self._update_button()

    def _item_changed(self, item) -> None:
        if item.column() == 0 and item.row() < len(self._rows):
            book_id = id(self._rows[item.row()])
            if item.checkState() == Qt.CheckState.Checked:
                self._unticked.discard(book_id)
            else:
                self._unticked.add(book_id)
        self._update_button()

    def _update_button(self, *_args) -> None:
        count = len(self.ticked())
        self.apply_button.setText(f"Clean Up {count} File(s)")
        self.apply_button.setEnabled(count > 0)

    def ticked(self) -> list:
        return [
            self._rows[row] for row in range(self.table.rowCount())
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
