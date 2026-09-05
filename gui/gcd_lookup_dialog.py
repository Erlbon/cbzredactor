"""
gui/gcd_lookup_dialog.py

Look Up via Grand Comics Database: for each target book, searches GCD
using that book's own Series + Number (falling back to a filename
guess, same as the Comic Vine dialog) and shows the match -- series,
story title, credits, genre, characters, publisher, and a cover
thumbnail for visual confirmation only (see core/gcd_lookup.py's
module docstring for why it's never written into the archive).

No API key needed, unlike Comic Vine -- GCD's API is open. But GCD's
own endpoint shape requires BOTH a series name and an issue number
(no free-text search like Comic Vine's), so a book with a blank
Series and a filename that doesn't parse into "name + number" simply
can't be searched here; that row is marked "(needs Series + Number)"
rather than silently guessing something wrong.

Same one-checkbox-per-book, review-before-Apply pattern as the sibling
lookup dialogs.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.cbz_file import CbzBook
from core.filename_guess import guess_series_and_number
from core.gcd_lookup import GcdLookupError, download_cover_image, fetch_issue_details, search_gcd

BOOK_COL, COVER_COL, FOUND_COL, APPLY_COL = range(4)
THUMB_SIZE = QSize(50, 70)


class GcdLookupDialog(QDialog):
    def __init__(self, books: list[CbzBook], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Look Up via Grand Comics Database")
        self.resize(900, 520)
        self.books = books
        self._checkboxes: dict[int, QCheckBox] = {}
        self._results: dict[int, dict[str, str]] = {}

        self._build_ui()
        self._run_search()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.info_label = QLabel(
            f"Searching the Grand Comics Database for {len(self.books)} file(s) by Series + "
            "Number (guessed from the filename when Series is blank; GCD needs both to "
            "search, unlike Comic Vine). Untick anything you don't trust, then Apply. Cover "
            "images are shown for confirmation only -- they are never written into the archive."
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["File", "Cover", "Found", "Apply"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(FOUND_COL, QHeaderView.ResizeMode.Stretch)
        self.table.setIconSize(THUMB_SIZE)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.retry_btn = QPushButton("Search Again")
        self.retry_btn.clicked.connect(self._run_search)
        btn_row.addWidget(self.retry_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    # ------------------------------------------------------------------
    # Running the lookup

    def _run_search(self) -> None:
        self.table.setRowCount(len(self.books))
        self._checkboxes = {}
        self._results = {}
        progress = QProgressDialog("Searching the Grand Comics Database…", "Cancel", 0, len(self.books), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        found_count = 0
        errors: list[str] = []

        for row, book in enumerate(self.books):
            if progress.wasCanceled():
                self.table.setRowCount(row)
                break
            progress.setValue(row)
            progress.setLabelText(f"Searching: {os.path.basename(book.path)}")
            QApplication.processEvents()

            self.table.setItem(row, BOOK_COL, self._readonly_item(os.path.basename(book.path)))
            cover_item = self._readonly_item("")
            self.table.setItem(row, COVER_COL, cover_item)
            self.table.setRowHeight(row, THUMB_SIZE.height() + 6)

            cb = QCheckBox()
            fields: dict = {}
            series, number = guess_series_and_number(book.path, book.metadata.series, book.metadata.number)

            if not series or not number:
                self.table.setItem(row, FOUND_COL, self._readonly_item("(needs Series + Number)"))
                cb.setEnabled(False)
                self._checkboxes[row] = cb
                self.table.setCellWidget(row, APPLY_COL, cb)
                continue

            try:
                candidates = search_gcd(series, number)
            except GcdLookupError as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
                candidates = []

            if candidates:
                best = candidates[0]
                try:
                    details = fetch_issue_details(best.detail_url)
                    fields = details.as_dict()
                except GcdLookupError as exc:
                    errors.append(f"{os.path.basename(book.path)}: {exc}")
                    details = None

                summary = "; ".join(f"{k}: {v}" for k, v in fields.items()) or "(matched, but no detail fetched)"
                self.table.setItem(row, FOUND_COL, self._readonly_item(summary))

                if details and details.cover_image_url:
                    try:
                        image_bytes = download_cover_image(details)
                        pixmap = QPixmap()
                        if pixmap.loadFromData(image_bytes):
                            scaled = pixmap.scaled(
                                THUMB_SIZE,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                            cover_item.setIcon(QIcon(scaled))
                    except GcdLookupError:
                        pass  # cover is a nice-to-have preview only

                if fields:
                    cb.setChecked(True)
                    self._results[row] = fields
                    found_count += 1
                else:
                    cb.setEnabled(False)
            else:
                self.table.setItem(row, FOUND_COL, self._readonly_item("(no match)"))
                cb.setEnabled(False)

            self._checkboxes[row] = cb
            self.table.setCellWidget(row, APPLY_COL, cb)

        progress.setValue(len(self.books))
        self.table.resizeColumnsToContents()

        msg = f"Found something for {found_count} of {len(self.books)} file(s)."
        if errors:
            from redactor_common.core.error_summary import summarize_errors
            msg += f" {len(errors)} error(s): {summarize_errors(errors)}"
        self.status_label.setText(msg)

    # ------------------------------------------------------------------
    # Result accessor, read by the caller after exec() returns Accepted

    def accepted_metadata(self) -> dict[int, dict[str, str]]:
        return {
            row: self._results[row]
            for row, cb in self._checkboxes.items()
            if cb.isChecked() and cb.isEnabled() and row in self._results
        }
