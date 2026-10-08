"""
gui/comicvine_browse_dialog.py

Look Up via Comic Vine, one file at a time, deciding while you look at the
covers (the old tick-a-list-afterwards dialog is gone). Modelled on the
ComicRack "Comic Vine Scraper" plugin's two steps (cbanack/comic-vine-scraper,
Apache 2.0; the code is our own):

  1. SERIES: the Comic Vine volumes matching the file's name, best fit first
     (core/comicvine_browse.py's score_volume()), with the file's own cover
     beside the cover of the selected volume.
  2. ISSUE: that volume's issues in number order, the issue with the file's
     number preselected, again with the two covers side by side. A number
     the volume doesn't have is said so, with the numbers it does have.

Cover matching (core/cover_hash.py) only PRESELECTS: the volume whose cover
looks like the file's is selected first, and a "Cover match" figure is shown;
nothing is applied until "Yes – Use This Issue" is pressed.

Within a batch the chosen volume is remembered: the next file with the same
series goes straight to step 2 with that volume (Back to Series changes it).

The caller (main_window._run_lookup_dialog) reads accepted_metadata() --
{index into the books list: {ComicInfo field: value}} -- exactly as it did
with the dialog this replaces, so the overwrite review and undo are unchanged.
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from redactor_common.core.error_summary import wrapped_errors
from redactor_common.gui.background_call import call_in_background
from redactor_common.gui.image_label import AspectRatioImageLabel

from core.cbz_file import CbzBook
from core.comicvine_browse import (
    ComicVineIssue,
    ComicVineVolume,
    candidate_for,
    download_image,
    fetch_volume_issues,
    find_issue_by_number,
    number_summary,
    rank_volumes,
    search_volumes,
)
from core.comicvine_lookup import ComicVineLookupError, _split_words, fetch_issue_details
from core.cover_hash import MATCH_THRESHOLD, cover_hash, similarity
from core.filename_guess import guess_series_and_number, guess_year
from gui import app_settings

# How many of the best-ranked volumes get their cover fetched up front so
# the "Cover match" column can be filled (and the best look-alike preselected).
HASH_TOP_VOLUMES = 8

STEP_SERIES, STEP_ISSUES = 0, 1
_GOOD = QColor(30, 130, 60)
COVER_MIN_SIZE = (200, 300)


def _series_key(series: str) -> str:
    return " ".join(_split_words(series))


def _fetch_images(urls: list[str]) -> dict[str, bytes]:
    """Downloads each url, skipping the ones that fail (a missing thumbnail
    must never fail the whole step)."""
    images: dict[str, bytes] = {}
    for url in urls:
        try:
            images[url] = download_image(url)
        except ComicVineLookupError:
            pass
    return images


class ComicVineBrowseDialog(QDialog):
    def __init__(self, books: list[CbzBook], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Look Up via Comic Vine")
        self.resize(1080, 680)

        self._books = books
        self._index = 0
        self._results: dict[int, dict] = {}

        self._api_key = app_settings.load_comicvine_api_key()
        if not self._api_key:
            self._api_key = self._prompt_for_key(self._api_key)

        # Caches for the whole session: a batch of one series asks Comic Vine once.
        self._volume_cache: dict[str, list[ComicVineVolume]] = {}
        self._issue_cache: dict[str, list[ComicVineIssue]] = {}
        self._image_cache: dict[str, bytes] = {}
        self._hash_cache: dict[str, Optional[int]] = {}

        self._prior_volume_ids: set[str] = set()
        self._last_volume: Optional[ComicVineVolume] = None
        self._current_volume: Optional[ComicVineVolume] = None
        self._last_series_key = ""
        self._local_hash: Optional[int] = None

        self._build_ui()
        self._load_book()

    # ------------------------------------------------------------------
    # UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.header = QLabel("")
        self.header.setStyleSheet("font-weight: bold;")
        root.addWidget(self.header)

        query_row = QHBoxLayout()
        self.series_edit = QLineEdit()
        self.number_edit = QLineEdit()
        self.number_edit.setMaximumWidth(80)
        self.year_edit = QLineEdit()
        self.year_edit.setMaximumWidth(80)
        self.search_btn = QPushButton("Search")
        for caption, edit in (("Series", self.series_edit), ("Number", self.number_edit), ("Year", self.year_edit)):
            query_row.addWidget(QLabel(caption))
            query_row.addWidget(edit, 1 if edit is self.series_edit else 0)
        query_row.addWidget(self.search_btn)
        self.key_btn = QPushButton("Change API Key…")
        query_row.addWidget(self.key_btn)
        root.addLayout(query_row)
        self.series_edit.returnPressed.connect(self._search_volumes)
        self.number_edit.returnPressed.connect(self._search_volumes)
        self.year_edit.returnPressed.connect(self._search_volumes)
        self.search_btn.clicked.connect(self._search_volumes)
        self.key_btn.clicked.connect(self._change_key)

        self.step_label = QLabel("")
        root.addWidget(self.step_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        self.stack = QStackedWidget()
        self.volume_table = self._make_table(["Series", "Year", "Issues", "Publisher", "Cover Match"])
        self.issue_table = self._make_table(["#", "Title", "Cover Date", "Cover Match"])
        self.stack.addWidget(self.volume_table)
        self.stack.addWidget(self.issue_table)
        splitter.addWidget(self.stack)
        self.volume_table.itemSelectionChanged.connect(self._on_volume_selected)
        self.issue_table.itemSelectionChanged.connect(self._on_issue_selected)
        self.volume_table.itemDoubleClicked.connect(lambda _item: self._use_volume())
        self.issue_table.itemDoubleClicked.connect(lambda _item: self._use_issue())

        covers = QWidget()
        covers_layout = QVBoxLayout(covers)
        covers_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.cover_yours = self._cover_slot(row, "This File")
        self.cover_found = self._cover_slot(row, "Comic Vine")
        covers_layout.addLayout(row, 1)
        self.match_label = QLabel("")
        self.match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        covers_layout.addWidget(self.match_label)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        covers_layout.addWidget(self.info_label)
        splitter.addWidget(covers)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        buttons = QHBoxLayout()
        self.back_btn = QPushButton("◀ Back to Series")
        self.skip_btn = QPushButton("No – Skip This File")
        self.use_btn = QPushButton("Choose This Series ▶")
        self.use_btn.setDefault(True)
        self.finish_btn = QPushButton("Stop Here")
        self.cancel_btn = QPushButton("Cancel")
        for button in (self.back_btn, self.skip_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        for button in (self.use_btn, self.finish_btn, self.cancel_btn):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.back_btn.clicked.connect(self._back_to_series)
        self.skip_btn.clicked.connect(self._next_book)
        self.use_btn.clicked.connect(self._use_current)
        self.finish_btn.clicked.connect(self._stop)
        self.finish_btn.setToolTip(
            "Stop the lookup here. Files you answered Yes to keep their metadata; this file and the rest are left alone."
        )
        self.cancel_btn.clicked.connect(self.reject)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(headers)):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        return table

    @staticmethod
    def _cover_slot(row: QHBoxLayout, caption: str) -> AspectRatioImageLabel:
        column = QVBoxLayout()
        title = QLabel(caption)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight: bold;")
        column.addWidget(title)
        image = AspectRatioImageLabel()
        image.setMinimumSize(*COVER_MIN_SIZE)
        image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setStyleSheet("background-color: palette(base); border: 1px solid palette(mid);")
        column.addWidget(image, 1)
        row.addLayout(column, 1)
        return image

    @staticmethod
    def _show_cover(label: AspectRatioImageLabel, data: Optional[bytes], empty: str) -> None:
        pixmap = None
        if data:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                pixmap = None
        label.set_original_pixmap(pixmap)
        label.setText("" if pixmap else empty)

    @staticmethod
    def _read_only(text: str, align_center: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if align_center:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    # ------------------------------------------------------------------
    # Network, kept off the GUI thread

    def _bg(self, fn, *args):
        """Runs `fn` on a worker thread while the window stays painted but
        unclickable; re-raises whatever `fn` raised."""
        self.setEnabled(False)
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return call_in_background(fn, *args)
        finally:
            QGuiApplication.restoreOverrideCursor()
            self.setEnabled(True)

    def _warn(self, exc: Exception) -> None:
        QMessageBox.warning(self, "Comic Vine", wrapped_errors([str(exc)]))

    def _image_for(self, url: str) -> Optional[bytes]:
        if not url:
            return None
        if url not in self._image_cache:
            fetched = self._bg(_fetch_images, [url])
            if url in fetched:
                self._image_cache[url] = fetched[url]
        return self._image_cache.get(url)

    def _hash_for(self, url: str) -> Optional[int]:
        if url not in self._hash_cache:
            self._hash_cache[url] = cover_hash(self._image_cache.get(url))
        return self._hash_cache[url]

    # ------------------------------------------------------------------
    # API key

    def _prompt_for_key(self, current: str) -> str:
        from gui.comicvine_key_dialog import ComicVineKeyDialog

        if ComicVineKeyDialog(self.parent()).exec():
            return app_settings.load_comicvine_api_key()
        return current

    def _change_key(self) -> None:
        self._api_key = self._prompt_for_key(self._api_key)
        self._search_volumes()

    # ------------------------------------------------------------------
    # One file

    @property
    def _book(self) -> CbzBook:
        return self._books[self._index]

    def _load_book(self) -> None:
        book = self._book
        self.header.setText(f"File {self._index + 1} of {len(self._books)}: {os.path.basename(book.path)}")
        series, number = guess_series_and_number(book.path, book.metadata.series, book.metadata.number)
        self.series_edit.setText(series)
        self.number_edit.setText(number)
        self.year_edit.setText(guess_year(book.path, book.metadata.year))

        try:
            local = book.read_first_page_bytes()
        except Exception:  # noqa: BLE001 -- an unreadable archive just means no cover to compare
            local = None
        self._local_hash = cover_hash(local)
        self._show_cover(self.cover_yours, local, "No cover")
        self._show_cover(self.cover_found, None, "")
        self.match_label.setText("")

        if self._last_volume is not None and series and _series_key(series) == self._last_series_key:
            self._show_issues(self._last_volume)
        else:
            self._search_volumes()

    def _next_book(self) -> None:
        self._index += 1
        if self._index >= len(self._books):
            self.accept()
        else:
            self._load_book()

    # ------------------------------------------------------------------
    # Step 1: the series

    def _search_volumes(self) -> None:
        self._set_step(STEP_SERIES)
        series = self.series_edit.text().strip()
        self.volume_table.setRowCount(0)
        if not series:
            self.info_label.setText("Type a series name and press Search.")
            return
        if not self._api_key:
            self.info_label.setText('No Comic Vine API key set -- click "Change API Key…".')
            return

        key = series.lower()
        try:
            if key not in self._volume_cache:
                self._volume_cache[key] = self._bg(search_volumes, self._api_key, series)
            volumes = rank_volumes(
                self._volume_cache[key], series, self.number_edit.text().strip(),
                self.year_edit.text().strip(), frozenset(self._prior_volume_ids),
            )
            top = volumes[:HASH_TOP_VOLUMES]
            wanted = [v.image_url for v in top if v.image_url and v.image_url not in self._image_cache]
            if wanted and self._local_hash is not None:
                self._image_cache.update(self._bg(_fetch_images, wanted))
        except ComicVineLookupError as exc:
            self._warn(exc)
            return

        if not volumes:
            self.info_label.setText(f'Comic Vine has no series matching "{series}". Change the name and search again.')
            return

        best_row, best_similarity = 0, 0.0
        self.volume_table.setRowCount(len(volumes))
        for row, volume in enumerate(volumes):
            name_item = self._read_only(volume.name)
            name_item.setData(Qt.ItemDataRole.UserRole, volume)
            self.volume_table.setItem(row, 0, name_item)
            self.volume_table.setItem(row, 1, self._read_only(volume.start_year, True))
            self.volume_table.setItem(row, 2, self._read_only(str(volume.issue_count), True))
            self.volume_table.setItem(row, 3, self._read_only(volume.publisher))
            score = similarity(self._local_hash, self._hash_for(volume.image_url)) if row < HASH_TOP_VOLUMES else 0.0
            shown = f"{score:.0%}" if row < HASH_TOP_VOLUMES and self._local_hash is not None and score else "–"
            cell = self._read_only(shown, True)
            if score >= MATCH_THRESHOLD:
                cell.setForeground(_GOOD)
            self.volume_table.setItem(row, 4, cell)
            if score > best_similarity:
                best_row, best_similarity = row, score
        # Preselect only: the look-alike cover if there is one, else the best-fitting name.
        self.volume_table.selectRow(best_row if best_similarity >= MATCH_THRESHOLD else 0)
        self.volume_table.setFocus()

    def _selected_volume(self) -> Optional[ComicVineVolume]:
        rows = self.volume_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.volume_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _on_volume_selected(self) -> None:
        volume = self._selected_volume()
        if volume is None:
            return
        bits = [bit for bit in (volume.publisher, f"started {volume.start_year}" if volume.start_year else "") if bit]
        bits.append(f"{volume.issue_count} issue(s)")
        summary = " · ".join(bits)
        # Show the cover of the issue with the file's number straight away, so the series can be
        # judged (and another one picked) without choosing it first. Falls back to the series cover.
        issue, have = self._issue_for_file(volume)
        number = self.number_edit.text().strip()
        if issue is not None and issue.image_url:
            image_url, note = issue.image_url, f"{volume.name} #{issue.number}" + (f' – "{issue.name}"' if issue.name else "")
        else:
            image_url = volume.image_url
            note = f"No issue #{number} in this series. It has: {have}." if number and have is not None else ""
        try:
            data = self._image_for(image_url)
        except ComicVineLookupError:
            data = None
        self._show_cover(self.cover_found, data, "No cover available")
        self._show_match(data and image_url)
        if issue is not None and data and self._local_hash is not None:
            rows = self.volume_table.selectionModel().selectedRows()
            if rows:  # the table's Cover column now says how this series' #N compares
                score = similarity(self._local_hash, self._hash_for(image_url))
                self.volume_table.item(rows[0].row(), 4).setText(f"{score:.0%}")
        self.info_label.setText(summary + ("\n" + note if note else ""))

    def _issue_for_file(self, volume: ComicVineVolume):
        """(the volume's issue with the file's number or None, a summary of the numbers it
        does have or None if they could not be fetched). Issues are cached per volume."""
        number = self.number_edit.text().strip()
        if not number or not self._api_key:
            return None, None
        try:
            if volume.volume_id not in self._issue_cache:
                self._issue_cache[volume.volume_id] = self._bg(fetch_volume_issues, self._api_key, volume.volume_id)
        except ComicVineLookupError:
            return None, None
        issues = self._issue_cache[volume.volume_id]
        return find_issue_by_number(issues, number), (number_summary(issues) or "none")

    def _show_match(self, url) -> None:
        if not url or self._local_hash is None:
            self.match_label.setText("")
            return
        score = similarity(self._local_hash, self._hash_for(url))
        good = score >= MATCH_THRESHOLD
        self.match_label.setText(f"Cover match {score:.0%}" + ("  – looks like the same cover" if good else ""))
        self.match_label.setStyleSheet(f"color: {_GOOD.name()}; font-weight: bold;" if good else "")

    def _use_volume(self) -> None:
        volume = self._selected_volume()
        if volume is not None:
            self._show_issues(volume)

    # ------------------------------------------------------------------
    # Step 2: the issue

    def _show_issues(self, volume: ComicVineVolume) -> None:
        try:
            if volume.volume_id not in self._issue_cache:
                self._issue_cache[volume.volume_id] = self._bg(fetch_volume_issues, self._api_key, volume.volume_id)
        except ComicVineLookupError as exc:
            self._warn(exc)
            return
        issues = self._issue_cache[volume.volume_id]

        self._current_volume = volume
        self._prior_volume_ids.add(volume.volume_id)
        self._last_volume = volume
        self._last_series_key = _series_key(self.series_edit.text())
        self._set_step(STEP_ISSUES)

        number = self.number_edit.text().strip()
        match = find_issue_by_number(issues, number) if number else None
        self.issue_table.setRowCount(len(issues))
        match_row = -1
        for row, issue in enumerate(issues):
            number_item = self._read_only(issue.number, True)
            number_item.setData(Qt.ItemDataRole.UserRole, issue)
            self.issue_table.setItem(row, 0, number_item)
            self.issue_table.setItem(row, 1, self._read_only(issue.name))
            self.issue_table.setItem(row, 2, self._read_only(issue.cover_date, True))
            self.issue_table.setItem(row, 3, self._read_only("", True))
            if issue is match:
                match_row = row

        self.issue_table.clearSelection()
        if match_row >= 0:
            self.issue_table.selectRow(match_row)
            self.issue_table.scrollToItem(self.issue_table.item(match_row, 0))
        else:
            self._show_cover(self.cover_found, self._image_for(volume.image_url), "No cover available")
            self._show_match(None)
            have = number_summary(issues) or "none"
            wanted = f"No issue #{number} in this series. It has: {have}." if number else f"Numbers in this series: {have}."
            self.info_label.setText(wanted)
        self.issue_table.setFocus()

    def _selected_issue(self) -> Optional[ComicVineIssue]:
        rows = self.issue_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.issue_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _on_issue_selected(self) -> None:
        issue = self._selected_issue()
        if issue is None:
            return
        try:
            data = self._image_for(issue.image_url)
        except ComicVineLookupError:
            data = None
        self._show_cover(self.cover_found, data, "No cover available")
        self._show_match(data and issue.image_url)
        rows = self.issue_table.selectionModel().selectedRows()
        if rows and data and self._local_hash is not None:
            score = similarity(self._local_hash, self._hash_for(issue.image_url))
            self.issue_table.item(rows[0].row(), 3).setText(f"{score:.0%}")
        volume = self._current_volume
        self.info_label.setText(
            f"{volume.name} #{issue.number}" + (f' – "{issue.name}"' if issue.name else "")
            + (f"\n{issue.cover_date}" if issue.cover_date else "")
        )

    def _back_to_series(self) -> None:
        self._last_volume = None  # the remembered choice was wrong for this file
        self._last_series_key = ""
        if self._current_volume is not None:
            self._prior_volume_ids.discard(self._current_volume.volume_id)  # and no longer a favourite
        self._search_volumes()

    def _stop(self) -> None:
        """Stop here: keep the files already answered Yes, leave this one and the rest alone."""
        self.accept()

    def _use_issue(self, advance: bool = True) -> bool:
        issue = self._selected_issue()
        if issue is None:
            QMessageBox.information(self, "Comic Vine", "Select an issue first, or press No.")
            return False
        volume = self._current_volume
        candidate = candidate_for(volume, issue)
        try:
            details = self._bg(fetch_issue_details, self._api_key, candidate.detail_url)
        except ComicVineLookupError as exc:
            self._warn(exc)
            return False
        details.publisher = volume.publisher
        self._results[self._index] = details.as_dict()
        if advance:
            self._next_book()
        return True

    def _use_current(self) -> None:
        if self.stack.currentIndex() == STEP_SERIES:
            self._use_volume()
        else:
            self._use_issue()

    # ------------------------------------------------------------------
    # Step switching

    def _set_step(self, step: int) -> None:
        self.stack.setCurrentIndex(step)
        in_issues = step == STEP_ISSUES
        self.back_btn.setVisible(in_issues)
        self.use_btn.setText("Yes – Use This Issue" if in_issues else "Choose This Series ▶")
        self.step_label.setText(
            "Step 2 of 2: choose the issue (the covers are side by side)." if in_issues
            else "Step 1 of 2: choose the series (best fit first; the covers are side by side)."
        )
        if not in_issues:
            self.match_label.setText("")

    # ------------------------------------------------------------------
    # Result, read by main_window._run_lookup_dialog

    def accepted_metadata(self) -> dict[int, dict]:
        return dict(self._results)
