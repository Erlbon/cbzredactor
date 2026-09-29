"""
gui/collection_report_dialog.py

Collection > Collection Report...: core/collection_report.py's findings,
one tab per check, plus a Fixes tab for what can be repaired
mechanically (core/collection_fix.py). Suggested moves, renames and
fixes start unticked -- nothing changes unless the user ticks it -- and
can only be applied on a computer
where the scanned folder exists (the collection's own); anywhere else
the report is read-only and says why. Save List... writes every finding
to a CSV to work through elsewhere.
"""

from __future__ import annotations

import csv

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.collection_fix import Fix
from core.collection_report import LIST_COLUMNS, Report, report_list
from core.collection_scan import ScanInfo

_PAIR_ROLE = Qt.ItemDataRole.UserRole + 1
_FIX_ROLE = Qt.ItemDataRole.UserRole + 2


def _cells(row: list[str]) -> list[QTableWidgetItem]:
    items = []
    for text in row:
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        items.append(item)
    return items


class CollectionReportDialog(QDialog):
    def __init__(self, info: ScanInfo, file_count: int, report: Report, can_apply: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Collection Report")
        self.resize(1200, 620)
        self.report = report
        self.can_apply = can_apply
        self._checkable_tables: list[QTableWidget] = []
        self.fixes_table: QTableWidget | None = None

        layout = QVBoxLayout(self)
        lines = [f"Scan of <b>{info.root}</b> on {info.computer or '?'}, {info.scanned or '?'}: "
                 f"{file_count:,} file(s)."]
        if not info.complete:
            lines.append("<b>This scan was stopped before it finished</b> -- files it didn't reach "
                         "are missing or from the scan before. Scan again to complete it.")
        if can_apply:
            lines.append("Tick the moves, renames and fixes you want, then Apply. Nothing is overwritten, and "
                         "File &gt; Undo Last Rename takes renames back; <b>Fixes that change ComicInfo "
                         "rewrite the archive and can't be undone</b>. Where a file's name and ComicInfo "
                         "disagree, tick which one is right -- only one of the two can be ticked.")
        else:
            lines.append("The scanned folder isn't on this computer, so moves and renames can't be applied "
                         "here: run the report on the collection's computer, or Save List to work through it.")
        intro = QLabel("<br>".join(lines))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._add_fixes_tab(report.fixes)
        self.moves_table = self._add_tab(
            "Moves", ["File", "Now in", "Suggested folder", "Why"],
            [([m.path.rsplit("/", 1)[-1], m.path.rpartition("/")[0], m.target_folder or "(which one?)", m.reason],
              (m.path, m.target) if m.target_folder else None) for m in report.moves],
        )
        self._add_tab(
            "Split series", ["Series", "Folders", "Note"],
            [([s.series, "; ".join(f"{f} ({n})" if n else f for f, n in s.folders), s.note], None)
             for s in report.splits],
        )
        self.names_table = self._add_tab(
            "File names", ["File", "Folder", "Problem", "Suggested name"],
            [([n.path.rsplit("/", 1)[-1], n.path.rpartition("/")[0], "; ".join(n.problems), n.suggested],
              (n.path, n.target) if n.suggested else None) for n in report.names],
        )
        dupes = []
        for group in report.duplicates:
            for row in group.rows:
                dupes.append(([f"{group.series} {group.number}", row.file, row.folder,
                               f"{row.size / 1048576:.1f}", row.pages or "?"], None))
        self._add_tab("Duplicates", ["Comic", "File", "Folder", "MB", "Pages"], dupes)
        self._add_tab(
            "Name vs ComicInfo", ["File", "Folder", "Field", "In the name", "In ComicInfo"],
            [([m.path.rsplit("/", 1)[-1], m.path.rpartition("/")[0], m.field, m.in_name, m.in_comicinfo], None)
             for m in report.mismatches],
        )
        self._add_tab(
            "Formats & missing", ["File", "Folder", "Problem"],
            [([f.path.rsplit("/", 1)[-1], f.path.rpartition("/")[0], f.problem], None) for f in report.formats],
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        save = buttons.addButton("Save List...", QDialogButtonBox.ButtonRole.ActionRole)
        save.clicked.connect(self._save_list)
        self.apply_button = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setVisible(can_apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_button()

    def _add_fixes_tab(self, fixes: list[Fix]) -> None:
        rows = [([f.path.rsplit("/", 1)[-1], f.path.rpartition("/")[0], f.kind, f.what], None) for f in fixes]
        table = self._add_tab("Fixes", ["File", "Folder", "Fix", "What changes"], rows, register=False)
        self.fixes_table = table
        if not self.can_apply:
            table.setSortingEnabled(True)
            return
        for index, fix in enumerate(fixes):
            item = table.item(index, 0)  # sorting is off until _add_tab ends, but rows keep their index here
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(_FIX_ROLE, fix)
        table.itemChanged.connect(self._fix_changed)
        table.setSortingEnabled(True)
        page_layout = self.tabs.widget(self.tabs.count() - 1).layout()
        row = QHBoxLayout()
        for text, state, kind in (("Tick All PageCount", Qt.CheckState.Checked, "PageCount"),
                                  ("Tick All New ComicInfo", Qt.CheckState.Checked, "New ComicInfo"),
                                  ("Tick All Extension", Qt.CheckState.Checked, "Extension"),
                                  ("Untick All", Qt.CheckState.Unchecked, "")):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, s=state, k=kind: self._tick_fixes(s, k))
            row.addWidget(button)
        row.addStretch(1)
        page_layout.addLayout(row)

    def _tick_fixes(self, state: Qt.CheckState, kind: str) -> None:
        table = self.fixes_table
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            fix = item.data(_FIX_ROLE)
            if fix and (not kind or fix.kind == kind):
                item.setCheckState(state)
        table.blockSignals(False)
        self._update_button()

    def _fix_changed(self, changed: QTableWidgetItem) -> None:
        """Of the two ways to settle a name/ComicInfo disagreement, only one."""
        fix = changed.data(_FIX_ROLE)
        if fix and fix.choice and changed.checkState() == Qt.CheckState.Checked:
            table = self.fixes_table
            table.blockSignals(True)
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                other = item.data(_FIX_ROLE)
                if item is not changed and other and other.choice == fix.choice:
                    item.setCheckState(Qt.CheckState.Unchecked)
            table.blockSignals(False)
        self._update_button()

    def ticked_fixes(self) -> list[Fix]:
        if self.fixes_table is None:
            return []
        table = self.fixes_table
        return [table.item(row, 0).data(_FIX_ROLE) for row in range(table.rowCount())
                if table.item(row, 0).data(_FIX_ROLE) and table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _add_tab(self, title: str, headers: list[str], rows: list[tuple[list[str], tuple | None]],
                 register: bool = True) -> QTableWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(4, 4, 4, 4)
        table = QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setTextElideMode(Qt.TextElideMode.ElideMiddle)  # deep folders: keep the series folder at the end
        checkable = False
        for index, (cells, pair) in enumerate(rows):
            items = _cells(cells)
            if pair is not None and self.can_apply:
                items[0].setFlags(items[0].flags() | Qt.ItemFlag.ItemIsUserCheckable)
                items[0].setCheckState(Qt.CheckState.Unchecked)
                items[0].setData(_PAIR_ROLE, pair)
                checkable = True
            for column, item in enumerate(items):
                table.setItem(index, column, item)
        table.setSortingEnabled(register)  # the Fixes tab sorts after its rows are tagged
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column in range(len(headers) - 1):
            table.setColumnWidth(column, 300 if column in (0, 1) else 220)
        page_layout.addWidget(table)
        if checkable and register:
            self._checkable_tables.append(table)
            table.itemChanged.connect(self._update_button)
            row = QHBoxLayout()
            for text, state in (("Tick All", Qt.CheckState.Checked), ("Untick All", Qt.CheckState.Unchecked)):
                button = QPushButton(text)
                button.clicked.connect(lambda _=False, t=table, s=state: self._tick_all(t, s))
                row.addWidget(button)
            row.addStretch(1)
            page_layout.addLayout(row)
        self.tabs.addTab(page, f"{title} ({len(rows)})".replace("&", "&&"))
        return table

    def _tick_all(self, table: QTableWidget, state: Qt.CheckState) -> None:
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item.data(_PAIR_ROLE):
                item.setCheckState(state)
        table.blockSignals(False)
        self._update_button()

    def _update_button(self, *_args) -> None:
        count = len(self.ticked()) + len(self.ticked_fixes())
        self.apply_button.setText(f"Apply {count} Change(s)")
        self.apply_button.setEnabled(count > 0)

    def ticked(self) -> list[tuple[str, str]]:
        """(path now, new path) for every ticked row, relative to the
        scanned folder. A file ticked on both tabs moves only."""
        pairs, seen = [], set()
        for table in self._checkable_tables:
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                pair = item.data(_PAIR_ROLE)
                if pair and item.checkState() == Qt.CheckState.Checked and pair[0] not in seen:
                    seen.add(pair[0])
                    pairs.append(tuple(pair))
        return pairs

    def _save_list(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save List", "collection_report.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(LIST_COLUMNS)
                writer.writerows(report_list(self.report))
        except OSError as exc:
            QMessageBox.warning(self, "Save List", f"Couldn't save the list:\n{exc}")
