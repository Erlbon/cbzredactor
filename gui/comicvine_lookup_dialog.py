"""
gui/comicvine_lookup_dialog.py

Look Up via Comic Vine: for each target book, searches Comic Vine using
that book's own Series + Number (falling back to the archive's
filename, parsed loosely, when Series is blank), shows the best match
-- series, issue number/title, credits, characters, publisher, and a
cover thumbnail for visual confirmation -- and lets the user apply it.

Same one-checkbox-per-book, review-before-Apply pattern as epubredactor's
Google Books / Calibre / Open Library lookup dialogs. Cover images are
shown but never applied (see core/comicvine_lookup.py's module
docstring for why: a CBZ's "cover" is page 1 of the archive, and
replacing archive content is a different feature than metadata lookup).

Prompts for a Comic Vine API key (stored via gui/app_settings.py) the
first time none is set, the same "ask once, remember it" pattern as
epubredactor's Calibre-location prompt.
"""

from __future__ import annotations

import os
import re

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
    QInputDialog,
    QLabel,
    QLineEdit,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.cbz_file import CbzBook
from core.comicvine_lookup import (
    ComicVineLookupError,
    download_cover_image,
    fetch_issue_details,
    fetch_publisher,
    search_comicvine,
)
from gui import app_settings

BOOK_COL, COVER_COL, FOUND_COL, APPLY_COL = range(4)
THUMB_SIZE = QSize(50, 70)

# A loose "Series Name 12" / "Series Name #12" filename guess, used only
# when a book has no Series set yet to search with -- good enough to
# seed a search query, not meant to be a general-purpose comic filename
# parser (the user can always search again after checking the result).
_FILENAME_GUESS_RE = re.compile(r"^(.*?)[\s_.-]+#?0*(\d+)\s*$")


def _guess_series_and_number(book: CbzBook) -> tuple[str, str]:
    if book.metadata.series.strip():
        return book.metadata.series.strip(), book.metadata.number.strip()
    stem = os.path.splitext(os.path.basename(book.path))[0]
    match = _FILENAME_GUESS_RE.match(stem.replace("_", " "))
    if match:
        return match.group(1).strip(" -_."), match.group(2)
    return stem, ""


class ComicVineLookupDialog(QDialog):
    def __init__(self, books: list[CbzBook], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Look Up via Comic Vine")
        self.resize(900, 520)
        self.books = books
        self._checkboxes: dict[int, QCheckBox] = {}
        self._results: dict[int, dict[str, str]] = {}
        self._api_key = app_settings.load_comicvine_api_key()

        self._build_ui()
        self._resolve_key_and_run()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.info_label = QLabel(
            f"Searching Comic Vine for {len(self.books)} file(s) by Series + Number "
            "(guessed from the filename when Series is blank). Untick anything you "
            "don't trust, then Apply. Cover images are shown for confirmation only -- "
            "they are never written into the archive."
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
        self.retry_btn.clicked.connect(self._resolve_key_and_run)
        btn_row.addWidget(self.retry_btn)
        self.change_key_btn = QPushButton("Change API Key…")
        self.change_key_btn.clicked.connect(self._prompt_for_key)
        btn_row.addWidget(self.change_key_btn)
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
    # API key

    def _resolve_key_and_run(self) -> None:
        self._api_key = app_settings.load_comicvine_api_key()
        if not self._api_key:
            self._prompt_for_key()
            return
        self._run_search()

    def _prompt_for_key(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Comic Vine API Key",
            "Enter your Comic Vine API key (free -- register at "
            "comicvine.gamespot.com/api/):",
            QLineEdit.EchoMode.Normal,
            self._api_key,
        )
        if not ok:
            if not self._api_key:
                self.status_label.setText("No API key set -- click \"Change API Key…\" to add one.")
                self.table.setRowCount(0)
            return
        app_settings.save_comicvine_api_key(text)
        self._api_key = text.strip()
        if self._api_key:
            self._run_search()

    # ------------------------------------------------------------------
    # Running the lookup

    def _run_search(self) -> None:
        self.table.setRowCount(len(self.books))
        self._checkboxes = {}
        self._results = {}
        progress = QProgressDialog("Searching Comic Vine…", "Cancel", 0, len(self.books), self)
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
            series, number = _guess_series_and_number(book)

            try:
                candidates = search_comicvine(self._api_key, series, number)
            except ComicVineLookupError as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
                candidates = []

            if candidates:
                best = candidates[0]
                try:
                    details = fetch_issue_details(self._api_key, best.detail_url)
                    details.publisher = fetch_publisher(self._api_key, best.volume_detail_url)
                    fields = details.as_dict()
                except ComicVineLookupError as exc:
                    errors.append(f"{os.path.basename(book.path)}: {exc}")

                summary = "; ".join(f"{k}: {v}" for k, v in fields.items()) or "(matched, but no detail fetched)"
                self.table.setItem(row, FOUND_COL, self._readonly_item(summary))

                if best.image_url:
                    try:
                        image_bytes = download_cover_image(best)
                        pixmap = QPixmap()
                        if pixmap.loadFromData(image_bytes):
                            scaled = pixmap.scaled(
                                THUMB_SIZE,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                            cover_item.setIcon(QIcon(scaled))
                    except ComicVineLookupError:
                        pass  # cover is a nice-to-have preview only; never worth failing the row over

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
        """book index -> {field_key: value}, for every checked row that
        found something."""
        return {
            row: self._results[row]
            for row, cb in self._checkboxes.items()
            if cb.isChecked() and cb.isEnabled() and row in self._results
        }
