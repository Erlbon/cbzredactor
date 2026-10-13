"""
gui/collection_report_dialog.py

Collection > Collection Report...: core/collection_report.py's findings,
one tab per check. The report is read-only -- it never moves, renames,
converts or rewrites a file. Each finding names the app action that
fixes it ("Fix with"), to use after opening those files with File >
Open Files / Open Folder. Save List... writes every finding to a CSV to
work through elsewhere.
"""

from __future__ import annotations

import csv
import html

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.collection_report import (
    FIX_DUPLICATES, FIX_MISMATCH, FIX_MOVE, FIX_RENAME, LIST_COLUMNS, Report, fix_summary, format_fix_with, report_list,
)
from core.collection_scan import ScanInfo

_LAZY_ROWS = 2000  # a tab with more rows is filled when first shown; filling 100,000 rows takes seconds


def _cells(row: list[str]) -> list[QTableWidgetItem]:
    items = []
    for text in row:
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        items.append(item)
    return items


class CollectionReportDialog(QDialog):
    def __init__(self, info: ScanInfo, file_count: int, report: Report, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Collection Report")
        self.resize(1200, 620)
        self.report = report
        self._pending: dict[QTableWidget, object] = {}

        layout = QVBoxLayout(self)
        lines = [f"Scan of <b>{html.escape(info.root)}</b> on {html.escape(info.computer or '?')}, "
                 f"{html.escape(info.scanned or '?')}: {file_count:,} file(s)."]
        if not info.complete:
            lines.append("<b>This scan was stopped before it finished</b> -- files it didn't reach "
                         "are missing or from the scan before. Scan again to complete it.")
        lines.append("This report changes nothing. To fix a finding, open those files with File &gt; Open Files "
                     "or Open Folder and use the action in its \"Fix with\" column.")
        summary = fix_summary(report)
        if summary:
            lines.append("<br>".join(f"Use {html.escape(action)} on {count:,} file(s)." for action, count in summary))
        self.intro = QLabel("<br>".join(lines))
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.moves_table = self._add_tab(
            "Moves", ["File", "Now in", "Suggested folder", "Why", "Fix with"],
            [[m.path.rsplit("/", 1)[-1], m.path.rpartition("/")[0], m.target_folder or "(which one?)", m.reason,
              FIX_MOVE] for m in report.moves],
        )
        self._add_tab(
            "Split series", ["Series", "Folders", "Note", "Fix with"],
            [[s.series, "; ".join(f"{f} ({n})" if n else f for f, n in s.folders), s.note, FIX_MOVE]
             for s in report.splits],
        )
        self.names_table = self._add_tab(
            "File names", ["File", "Folder", "Problem", "Suggested name", "Fix with"],
            [[n.path.rsplit("/", 1)[-1], n.path.rpartition("/")[0], "; ".join(n.problems), n.suggested, FIX_RENAME]
             for n in report.names],
        )
        dupes = []
        for group in report.duplicates:
            for row in group.rows:
                dupes.append([f"{group.series} {group.number}", row.file, row.folder,
                              f"{row.size / 1048576:.1f}", row.pages or "?", group.covers, FIX_DUPLICATES])
        self._add_tab("Duplicates", ["Comic", "File", "Folder", "MB", "Pages", "Covers", "Fix with"], dupes)
        self._add_tab(
            "Name vs ComicInfo", ["File", "Folder", "Field", "In the name", "In ComicInfo", "Fix with"],
            [[m.path.rsplit("/", 1)[-1], m.path.rpartition("/")[0], m.field, m.in_name, m.in_comicinfo, FIX_MISMATCH]
             for m in report.mismatches],
        )
        self.formats_table = self._add_tab(
            "Formats & missing", ["File", "Folder", "Problem", "Fix with"],
            [[f.path.rsplit("/", 1)[-1], f.path.rpartition("/")[0], f.problem, format_fix_with(f.problem)]
             for f in report.formats],
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        save = buttons.addButton("Save List...", QDialogButtonBox.ButtonRole.ActionRole)
        save.clicked.connect(self._save_list)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tabs.currentChanged.connect(self._fill_pending)
        QTimer.singleShot(0, self._fill_first_tab)  # once the window is up

    def _fill_first_tab(self) -> None:
        self._fill_pending(self.tabs.currentIndex())

    def _fill_pending(self, index: int) -> None:
        """Fills a big tab the first time it is shown."""
        page = self.tabs.widget(index)
        for table in [t for t in self._pending if page is not None and page.isAncestorOf(t)]:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._pending.pop(table)()
            finally:
                QApplication.restoreOverrideCursor()

    def _add_tab(self, title: str, headers: list[str], rows: list[list[str]]) -> QTableWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(4, 4, 4, 4)
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setTextElideMode(Qt.TextElideMode.ElideMiddle)  # deep folders: keep the series folder at the end

        def fill() -> None:
            table.setUpdatesEnabled(False)
            table.setRowCount(len(rows))
            for index, cells in enumerate(rows):
                for column, item in enumerate(_cells(cells)):
                    table.setItem(index, column, item)
            table.setSortingEnabled(True)
            table.setUpdatesEnabled(True)

        if len(rows) > _LAZY_ROWS:
            # A big tab is filled when it is first shown, not while the report opens.
            self._pending[table] = fill
        else:
            fill()
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column in range(len(headers) - 1):
            table.setColumnWidth(column, 300 if column in (0, 1) else 220)
        page_layout.addWidget(table)
        self.tabs.addTab(page, f"{title} ({len(rows)})".replace("&", "&&"))
        return table

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
