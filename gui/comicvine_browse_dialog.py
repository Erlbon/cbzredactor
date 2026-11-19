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

Nothing here waits for Comic Vine (2026-11-19). Every request runs on a
worker thread and its answer is handed back to the GUI thread; the dialog
stays usable and says "Loading..." where the answer will go. Before this,
each search, issue list and cover ran under a modal busy box with a Cancel
button: a slow Comic Vine looked like a freeze, and the Cancel clicked to
get out of it cancelled a request that belonged to the next file. Answers
that arrive for a file or a row no longer on screen are only cached, never
shown. "Use" fetches the issue's details in the background too and moves on
at once; Stop or the last file waits for those before closing.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QPointF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
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
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


from redactor_common.core.error_summary import wrapped_errors
from redactor_common.gui.image_label import AspectRatioImageLabel
from redactor_common.gui.table_columns import make_columns_resizable
from redactor_common.gui.sortable_table import NumericTableWidgetItem, suspend_sorting

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
from redactor_common.gui.owned_dialog import OwnedDialog

# How many of the best-ranked volumes get their cover fetched up front so
# the "Cover match" column can be filled (and the best look-alike preselected).
HASH_TOP_VOLUMES = 8

STEP_SERIES, STEP_ISSUES = 0, 1
_GOOD = QColor(30, 130, 60)
COVER_MIN_SIZE = (200, 300)

# A series row reached with the arrow keys is looked up only once the selection has rested this
# long, so running down the list does not send Comic Vine one request per row passed.
SELECTION_DELAY_MS = 250

LOADING = "Loading..."


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


ICON_SIZE = 26
GROUP_GAP = 28


def _media_icon(shape: str, color: QColor) -> QIcon:
    """A player-style icon ("back", "play", "forward", "stop", "cross") drawn in `color`."""
    size = 64  # drawn large, Qt scales it down to ICON_SIZE
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)

    def triangle(x: float, width: float, pointing_right: bool) -> None:
        tip, base = (x + width, x) if pointing_right else (x, x + width)
        painter.drawPolygon(QPolygonF([QPointF(base, 10), QPointF(base, 54), QPointF(tip, 32)]))

    if shape == "play":
        triangle(16, 36, True)
    elif shape == "forward":
        triangle(6, 26, True)
        triangle(32, 26, True)
    elif shape == "back":
        triangle(6, 26, False)
        triangle(32, 26, False)
    elif shape == "stop":
        painter.drawRect(14, 14, 36, 36)
    elif shape == "cross":
        pen = QPen(color, 8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(16, 16, 48, 48)
        painter.drawLine(48, 16, 16, 48)
    painter.end()
    return QIcon(pixmap)


class _Mailbox(QObject):
    """Lives on the GUI thread. A worker thread posts a callable here and it runs on the GUI thread."""

    posted = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.posted.connect(self._deliver)

    def _deliver(self, deliver) -> None:
        deliver()


def start_background(mailbox: _Mailbox, fn: Callable, args: tuple, on_done: Callable) -> None:
    """Runs fn(*args) on a daemon thread. on_done(result, error) runs later on the GUI thread; error is the
    exception fn raised, or None. Never waits. (Tests replace this with a direct call.)"""

    def work() -> None:
        try:
            result, error = fn(*args), None
        except Exception as exc:  # noqa: BLE001 -- handed to on_done on the GUI thread
            result, error = None, exc
        try:
            mailbox.posted.emit(lambda: on_done(result, error))
        except RuntimeError:  # the dialog, and its mailbox, is already gone
            pass

    threading.Thread(target=work, name="comicvine-request", daemon=True).start()


class ComicVineBrowseDialog(OwnedDialog):
    def __init__(self, books: list[CbzBook], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Look Up via Comic Vine")
        self.resize(1080, 680)

        self._books = books
        self._index = 0
        self._results: dict[int, dict] = {}
        self._series_issue: Optional[ComicVineIssue] = None  # the selected series' issue with the file's number

        self._api_key = app_settings.load_comicvine_api_key()
        if not self._api_key:
            self._api_key = self._prompt_for_key(self._api_key)

        # Caches for the whole session: a batch of one series asks Comic Vine once.
        self._volume_cache: dict[str, list[ComicVineVolume]] = {}
        self._issue_cache: dict[str, list[ComicVineIssue]] = {}
        self._image_cache: dict[str, bytes] = {}
        self._hash_cache: dict[str, Optional[int]] = {}
        # What could not be fetched (tried again on the next Search, not on every redraw).
        self._issue_failed: dict[str, str] = {}
        self._image_failed: set[str] = set()

        self._prior_volume_ids: set[str] = set()
        self._last_volume: Optional[ComicVineVolume] = None
        self._current_volume: Optional[ComicVineVolume] = None
        self._issues_wanted: Optional[ComicVineVolume] = None  # step 2 waiting for this volume's issue list
        self._last_series_key = ""
        self._local_hash: Optional[int] = None

        # Background requests.
        self._mailbox = _Mailbox(self)
        self._waiting: dict[object, list[Callable]] = {}  # request key -> callbacks waiting for its answer
        self._gen = 0  # bumped for every file: an answer that belongs to an earlier file is never shown
        self._shut = False  # the dialog has closed: late answers are dropped
        self._token = 0
        self._answers: dict[int, int] = {}  # file index -> token of its latest "Use"
        self._pending: dict[int, int] = {}  # file index -> token, issue details still on their way
        self._errors: dict[int, str] = {}
        self._used: dict[int, str] = {}  # file index -> "Series #N" it was given with ▶ (shown when going back to it)
        self._finishing = False
        self._selecting = 0  # > 0 while the code (not the user) changes a table's selection
        self._user_moved = False  # the user picked a series row himself: covers arriving late don't move it

        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.timeout.connect(self._render_volume)

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
        self.publisher_edit = QLineEdit()
        self.publisher_edit.setMaximumWidth(140)
        self.publisher_edit.setToolTip("Optional: volumes from this publisher rank higher")
        self.search_btn = QPushButton("Search")
        for caption, edit in (("Series", self.series_edit), ("Number", self.number_edit), ("Year", self.year_edit),
                              ("Publisher", self.publisher_edit)):
            query_row.addWidget(QLabel(caption))
            query_row.addWidget(edit, 1 if edit is self.series_edit else 0)
        query_row.addWidget(self.search_btn)
        self.key_btn = QPushButton("Change API Key…")
        query_row.addWidget(self.key_btn)
        root.addLayout(query_row)
        self.series_edit.returnPressed.connect(self._search_volumes)
        self.number_edit.returnPressed.connect(self._search_volumes)
        self.year_edit.returnPressed.connect(self._search_volumes)
        self.publisher_edit.returnPressed.connect(self._search_volumes)
        self.search_btn.clicked.connect(self._search_volumes)
        self.key_btn.clicked.connect(self._change_key)

        step_row = QHBoxLayout()
        self.step_label = QLabel("")
        self.step_label.setWordWrap(True)
        step_row.addWidget(self.step_label, 1)
        self.browse_btn = QPushButton("Show Issues of This Series")
        self.browse_btn.setToolTip("List every issue of the selected series, to pick another one")
        step_row.addWidget(self.browse_btn)
        root.addLayout(step_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        self.stack = QStackedWidget()
        self.volume_table = self._make_table(["Series", "Year", "Issues", "Publisher", "Cover Match"])
        self.issue_table = self._make_table(["#", "Title", "Cover Date", "Cover Match"])
        self.stack.addWidget(self.volume_table)
        self.stack.addWidget(self.issue_table)
        splitter.addWidget(self.stack)
        self.volume_table.itemSelectionChanged.connect(self._on_volume_selected)
        self.issue_table.itemSelectionChanged.connect(self._render_issue)
        self.volume_table.itemDoubleClicked.connect(self._on_volume_double_clicked)
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

        # Player-style controls (the user's choice, 2026-11-19): ◀◀ back, ▶ use this issue, ■ stop and keep
        # what was used, ✕ close and use nothing, ▶▶ next file without using one.
        buttons = QHBoxLayout()
        self.back_btn = QPushButton("◀◀")
        self.use_btn = QPushButton("▶")
        self.use_btn.setToolTip("Use this issue for this file and go to the next file (Enter)")
        self.use_btn.setDefault(True)
        self.skip_btn = QPushButton("▶▶")
        self.skip_btn.setToolTip("Next file, without using a match for this one")
        self.finish_btn = QPushButton("■")
        self.finish_btn.setToolTip(
            "Stop here. Files you used an issue for (▶) keep it; this file and the rest are left alone."
        )
        self.cancel_btn = QPushButton("✕")
        self.cancel_btn.setToolTip("Close and use nothing, not even the files you already used an issue for.")
        # Kept APART on purpose (user, 2026-11-19): ◀◀ on the outer left, ▶▶ on the outer right, ▶ ■ ✕ in
        # the middle, so back, use and forward can't be hit by mistake for one another.
        # The text triangles came out tiny next to ■, and Qt's own media icons are black on the dark theme:
        # the shapes are drawn here, in the theme's text colour, all the same size. The glyph stays as the
        # accessible name (what tests and screen readers read).
        shapes = {self.back_btn: "back", self.use_btn: "play", self.finish_btn: "stop",
                  self.cancel_btn: "cross", self.skip_btn: "forward"}
        for button, shape in shapes.items():
            button.setAccessibleName(button.text())
            button.setText("")
            button.setIcon(_media_icon(shape, button.palette().color(QPalette.ColorRole.ButtonText)))
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            button.setMinimumSize(84, 46)
        # One group in the middle (user, 2026-11-19): apart enough to tell and hit each one, near enough to
        # work through a long list without moving the mouse. ■ and ✕ are rare: out of the way on the right.
        buttons.addStretch(1)
        buttons.addWidget(self.back_btn)
        buttons.addSpacing(GROUP_GAP)
        buttons.addWidget(self.use_btn)
        buttons.addSpacing(GROUP_GAP)
        buttons.addWidget(self.skip_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.finish_btn)
        buttons.addSpacing(GROUP_GAP)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)
        self.back_btn.clicked.connect(self._back)
        self.skip_btn.clicked.connect(self._next_book)
        self.use_btn.clicked.connect(self._use_current)
        self.browse_btn.clicked.connect(self._use_volume)
        self.finish_btn.clicked.connect(self._stop)
        self.cancel_btn.clicked.connect(self.reject)

    @staticmethod
    def _reset_sort(table: QTableWidget) -> None:
        """Back to the ranked / numbered order the rows are filled in."""
        table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    @staticmethod
    def _set_score(item: QTableWidgetItem, score: float) -> None:
        item.setText(f"{score:.0%}")
        item._sort_value = score  # the NumericTableWidgetItem sorts on this, not the text
        if score >= MATCH_THRESHOLD:
            item.setForeground(_GOOD)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        # Click a header to sort. The rows start in the ranked order (indicator -1 = unsorted), and
        # every repopulate goes back to it (see _reset_sort).
        table.setSortingEnabled(True)
        table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        make_columns_resizable(table, wide_column=1 if len(headers) == 4 else 0)
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
    def _read_only(text: str, align_center: bool = False, numeric: bool = False,
                   sort_value: Optional[float] = None) -> QTableWidgetItem:
        item = NumericTableWidgetItem(text, sort_value) if numeric else QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if align_center:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _select_row(self, table: QTableWidget, row: int) -> None:
        """Selects a row as the code, not the user (see _user_moved)."""
        self._selecting += 1
        try:
            table.selectRow(row)
        finally:
            self._selecting -= 1

    # ------------------------------------------------------------------
    # Network: every request on a worker thread, nothing waits for it

    def _request(self, key, fn: Callable, *args, then: Callable) -> None:
        """Starts fn(*args) in the background unless the same request (key) is already on its way;
        then(result, error) runs on the GUI thread when it answers."""
        waiting = self._waiting.get(key)
        if waiting is not None:
            waiting.append(then)
            return
        self._waiting[key] = [then]

        def done(result, error) -> None:
            for callback in self._waiting.pop(key, []):
                if self._shut:
                    return
                callback(result, error)

        start_background(self._mailbox, fn, args, done)

    def done(self, result: int) -> None:  # QDialog.done: every way of closing ends here
        self._shut = True
        self._selection_timer.stop()
        super().done(result)

    def _warn(self, exc: Exception) -> None:
        QMessageBox.warning(self, "Comic Vine", wrapped_errors([str(exc)]))

    def _cover(self, url: str) -> tuple[Optional[bytes], bool]:
        """(the cover's bytes or None, still loading). Starts the download when needed; when it
        lands, whatever is on screen then is drawn again."""
        if not url or url in self._image_failed:
            return None, False
        if url not in self._image_cache:
            self._load_cover(url, self._redraw)
        if url in self._image_cache:
            return self._image_cache[url], False
        return None, url not in self._image_failed

    def _load_cover(self, url: str, then: Callable[[], None]) -> None:
        def store(images, _error) -> None:
            if images and url in images:
                self._image_cache[url] = images[url]
            else:
                self._image_failed.add(url)
            then()

        self._request(("image", url), _fetch_images, [url], then=store)

    def _issues(self, volume: ComicVineVolume) -> tuple[Optional[list[ComicVineIssue]], bool]:
        """(the volume's issues or None, still loading). Same pattern as _cover."""
        volume_id = volume.volume_id
        if volume_id not in self._issue_cache and volume_id not in self._issue_failed:
            def store(issues, error) -> None:
                if error is None:
                    self._issue_cache[volume_id] = issues
                else:
                    self._issue_failed[volume_id] = str(error)
                self._redraw()

            self._request(("issues", volume_id), fetch_volume_issues, self._api_key, volume_id, then=store)
        if volume_id in self._issue_cache:
            return self._issue_cache[volume_id], False
        return None, volume_id not in self._issue_failed

    def _hash_for(self, url: str) -> Optional[int]:
        if url not in self._hash_cache:
            self._hash_cache[url] = cover_hash(self._image_cache.get(url))
        return self._hash_cache[url]

    def _redraw(self) -> None:
        """Draws the current view again from the caches (called when an answer arrives)."""
        if self._shut or self._finishing:
            return
        if self.stack.currentIndex() == STEP_SERIES:
            if not self._selection_timer.isActive():
                self._render_volume()
        elif self._issues_wanted is not None:
            self._show_issues(self._issues_wanted)
        else:
            self._render_issue()

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
        self._gen += 1
        self._series_issue = None
        self._issues_wanted = None
        self._user_moved = False
        self._selection_timer.stop()
        book = self._book
        used = self._used.get(self._index)
        self.header.setText(
            f"File {self._index + 1} of {len(self._books)}: {os.path.basename(book.path)}"
            + (f"   (already using {used}: ▶ replaces it, ▶▶ keeps it)" if used else "")
        )
        series, number = guess_series_and_number(book.path, book.metadata.series, book.metadata.number)
        self.series_edit.setText(series)
        self.number_edit.setText(number)
        self.year_edit.setText(guess_year(book.path, book.metadata.year))
        self.publisher_edit.setText((book.metadata.publisher or "").strip())

        try:
            local = book.read_first_page_bytes()
        except Exception:  # noqa: BLE001 -- an unreadable archive just means no cover to compare
            local = None
        self._local_hash = cover_hash(local)
        self._show_cover(self.cover_yours, local, "No cover")
        self._show_cover(self.cover_found, None, "")
        self.match_label.setText("")
        self.info_label.setText("")

        if self._last_volume is not None and series and _series_key(series) == self._last_series_key:
            self._show_issues(self._last_volume)
        else:
            self._search_volumes()

    def _next_book(self) -> None:
        if self._finishing:
            return
        self._index += 1
        if self._index >= len(self._books):
            self._finish()
        else:
            self._load_book()

    # ------------------------------------------------------------------
    # Step 1: the series

    def _search_volumes(self) -> None:
        if self._finishing:
            return
        self._set_step(STEP_SERIES)
        self._series_issue = None
        self._selection_timer.stop()
        series = self.series_edit.text().strip()
        self._selecting += 1
        try:
            self.volume_table.setRowCount(0)
        finally:
            self._selecting -= 1
        self._show_cover(self.cover_found, None, "")
        self.match_label.setText("")
        self._update_buttons()
        if not series:
            self.info_label.setText("Type a series name and press Search.")
            return
        if not self._api_key:
            self.info_label.setText('No Comic Vine API key set -- click "Change API Key…".')
            return
        # A new search tries again what could not be fetched before (a timeout is often passing).
        self._issue_failed.clear()
        self._image_failed.clear()

        key = series.lower()
        if key in self._volume_cache:
            self._fill_volumes()
            return
        self.info_label.setText("Searching Comic Vine...")
        gen = self._gen

        def then(volumes, error) -> None:
            if error is None:
                self._volume_cache[key] = volumes
            still_wanted = (gen == self._gen and not self._finishing and self.stack.currentIndex() == STEP_SERIES
                            and self.series_edit.text().strip().lower() == key)
            if not still_wanted:
                return  # the answer is cached; this file or search is no longer on screen
            if error is not None:
                self.info_label.setText("")
                self._warn(error)
                return
            self._fill_volumes()

        self._request(("search", key), search_volumes, self._api_key, series, then=then)

    def _fill_volumes(self) -> None:
        series = self.series_edit.text().strip()
        volumes = rank_volumes(
            self._volume_cache.get(series.lower(), []), series, self.number_edit.text().strip(),
            self.year_edit.text().strip(), frozenset(self._prior_volume_ids),
            publisher_hint=self.publisher_edit.text().strip(),
        )
        if not volumes:
            self.info_label.setText(f'Comic Vine has no series matching "{series}". Change the name and search again.')
            return

        self._user_moved = False
        self._reset_sort(self.volume_table)
        self._selecting += 1  # filling the rows is not the user picking one
        with suspend_sorting(self.volume_table):
            self.volume_table.setRowCount(len(volumes))
            for row, volume in enumerate(volumes):
                name_item = self._read_only(volume.name)
                name_item.setData(Qt.ItemDataRole.UserRole, volume)
                self.volume_table.setItem(row, 0, name_item)
                self.volume_table.setItem(row, 1, self._read_only(volume.start_year, True, numeric=True))
                self.volume_table.setItem(row, 2, self._read_only(str(volume.issue_count), True, numeric=True))
                self.volume_table.setItem(row, 3, self._read_only(volume.publisher))
                self.volume_table.setItem(row, 4, self._read_only("–", True, numeric=True, sort_value=0.0))
        self._selecting -= 1
        make_columns_resizable(self.volume_table, wide_column=0)
        # Preselect only: the look-alike cover if one is known already, else the best-fitting name.
        best_row = self._refresh_volume_scores()
        self._select_row(self.volume_table, best_row if best_row >= 0 else 0)
        self.volume_table.setFocus()

        # The covers of the best-ranked series, to fill "Cover Match". Each arrives on its own; while the
        # user has not picked a row himself, the selection moves to a look-alike cover when one turns up.
        if self._local_hash is None:
            return
        gen = self._gen

        def arrived() -> None:
            if gen != self._gen or self._finishing or self.stack.currentIndex() != STEP_SERIES:
                return
            best = self._refresh_volume_scores()
            if best >= 0 and not self._user_moved and self.volume_table.currentRow() != best:
                self._select_row(self.volume_table, best)

        for volume in volumes[:HASH_TOP_VOLUMES]:
            url = volume.image_url
            if url and url not in self._image_cache and url not in self._image_failed:
                self._load_cover(url, arrived)

    def _refresh_volume_scores(self) -> int:
        """Fills "Cover Match" for every series whose cover is here. Returns the row of the best
        look-alike (MATCH_THRESHOLD or better), or -1."""
        if self._local_hash is None:
            return -1
        best_row, best = -1, 0.0
        with suspend_sorting(self.volume_table):
            for row in range(self.volume_table.rowCount()):
                volume = self.volume_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                cell = self.volume_table.item(row, 4)
                url = volume.image_url
                if url not in self._image_cache or getattr(cell, "_from_issue", False):
                    continue
                score = similarity(self._local_hash, self._hash_for(url))
                if score:
                    self._set_score(cell, score)
                if score >= MATCH_THRESHOLD and score > best:
                    best_row, best = row, score
        return best_row

    def _selected_volume(self) -> Optional[ComicVineVolume]:
        rows = self.volume_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.volume_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _on_volume_selected(self) -> None:
        if not self._selecting:
            self._user_moved = True
        # Nothing of the previous row may stay on screen, or be applied, while this one loads.
        self._series_issue = None
        self._show_cover(self.cover_found, None, "")
        self.match_label.setText("")
        self._update_buttons()
        if SELECTION_DELAY_MS and not self._selecting:
            self._selection_timer.start(SELECTION_DELAY_MS)
        else:
            self._render_volume()

    def _on_volume_double_clicked(self, _item) -> None:
        if self._selection_timer.isActive():
            self._render_volume()
        if self._series_issue is not None:
            self._use_current()
        else:
            self._use_volume()

    def _render_volume(self) -> None:
        """Step 1's right-hand side for the selected series: the cover of its issue with the file's
        number (so the series can be judged without choosing it first), else the series' own cover."""
        self._selection_timer.stop()
        if self._finishing or self.stack.currentIndex() != STEP_SERIES:
            return
        volume = self._selected_volume()
        self._series_issue = None
        if volume is None:
            self._update_buttons()
            return
        bits = [bit for bit in (volume.publisher, f"started {volume.start_year}" if volume.start_year else "") if bit]
        bits.append(f"{volume.issue_count} issue(s)")
        summary = " · ".join(bits)

        number = self.number_edit.text().strip()
        issue, note, image_url, waiting = None, "", volume.image_url, False
        if number and self._api_key:
            issues, waiting = self._issues(volume)
            if self._selected_volume() is not volume:
                return  # an answer that arrived just now redrew another row already
            if issues is not None:
                issue = find_issue_by_number(issues, number)
                if issue is None:
                    note = f"No issue #{number} in this series. It has: {number_summary(issues) or 'none'}."
            elif waiting:
                image_url, note = "", "Loading the issue list..."
            else:
                note = f"Could not fetch the issue list: {self._issue_failed.get(volume.volume_id, '')}"
        if issue is not None and issue.image_url:
            image_url = issue.image_url
            note = f"{volume.name} #{issue.number}" + (f' – "{issue.name}"' if issue.name else "")
        self._series_issue = issue

        data, loading = self._cover(image_url)
        if self._selected_volume() is not volume:
            return
        self._show_cover(self.cover_found, data, LOADING if (loading or waiting) else "No cover available")
        self._show_match(image_url if data else None)
        if issue is not None and data and self._local_hash is not None:
            rows = self.volume_table.selectionModel().selectedRows()
            if rows:  # the table's Cover column now says how this series' #N compares
                cell = self.volume_table.item(rows[0].row(), 4)
                with suspend_sorting(self.volume_table):
                    self._set_score(cell, similarity(self._local_hash, self._hash_for(image_url)))
                cell._from_issue = True
        self.info_label.setText(summary + ("\n" + note if note else ""))
        self._update_buttons()

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
        if self._finishing:
            return
        self._current_volume = volume
        self._prior_volume_ids.add(volume.volume_id)
        self._last_volume = volume
        self._last_series_key = _series_key(self.series_edit.text())
        self._set_step(STEP_ISSUES)

        issues, waiting = self._issues(volume)
        if issues is None:
            self._issues_wanted = volume if waiting else None
            self.issue_table.setRowCount(0)
            self._show_cover(self.cover_found, None, LOADING if waiting else "")
            self._show_match(None)
            self.info_label.setText(
                "Loading the issue list..." if waiting
                else f"Could not fetch the issue list: {self._issue_failed.get(volume.volume_id, '')}"
            )
            self._update_buttons()
            return
        self._issues_wanted = None

        number = self.number_edit.text().strip()
        match = find_issue_by_number(issues, number) if number else None
        self._reset_sort(self.issue_table)
        self._selecting += 1
        try:
            with suspend_sorting(self.issue_table):
                self.issue_table.setRowCount(len(issues))
                match_row = -1
                for row, issue in enumerate(issues):
                    number_item = self._read_only(issue.number, True, numeric=True)
                    number_item.setData(Qt.ItemDataRole.UserRole, issue)
                    self.issue_table.setItem(row, 0, number_item)
                    self.issue_table.setItem(row, 1, self._read_only(issue.name))
                    self.issue_table.setItem(row, 2, self._read_only(issue.cover_date, True))
                    self.issue_table.setItem(row, 3, self._read_only("", True, numeric=True, sort_value=0.0))
                    if issue is match:
                        match_row = row
            make_columns_resizable(self.issue_table, wide_column=1)
            self.issue_table.clearSelection()
            if match_row >= 0:
                self.issue_table.selectRow(match_row)
                self.issue_table.scrollToItem(self.issue_table.item(match_row, 0))
        finally:
            self._selecting -= 1
        self._render_issue()
        self.issue_table.setFocus()

    def _selected_issue(self) -> Optional[ComicVineIssue]:
        rows = self.issue_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.issue_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _render_issue(self) -> None:
        """Step 2's right-hand side: the selected issue's cover, or the series' cover and the numbers it
        has when nothing is selected."""
        if self._selecting or self._finishing or self.stack.currentIndex() != STEP_ISSUES:
            return
        volume = self._current_volume
        issue = self._selected_issue()
        if volume is None or self._issues_wanted is not None:
            return
        if issue is None:
            data, loading = self._cover(volume.image_url)
            if self._selected_issue() is not None or self.stack.currentIndex() != STEP_ISSUES:
                return
            self._show_cover(self.cover_found, data, LOADING if loading else "No cover available")
            self._show_match(None)
            number = self.number_edit.text().strip()
            have = number_summary(self._issue_cache.get(volume.volume_id, [])) or "none"
            self.info_label.setText(
                f"No issue #{number} in this series. It has: {have}." if number else f"Numbers in this series: {have}."
            )
            self._update_buttons()
            return
        data, loading = self._cover(issue.image_url)
        if self._selected_issue() is not issue:
            return
        self._show_cover(self.cover_found, data, LOADING if loading else "No cover available")
        self._show_match(issue.image_url if data else None)
        rows = self.issue_table.selectionModel().selectedRows()
        if rows and data and self._local_hash is not None:
            with suspend_sorting(self.issue_table):
                self._set_score(self.issue_table.item(rows[0].row(), 3),
                                similarity(self._local_hash, self._hash_for(issue.image_url)))
        self.info_label.setText(
            f"{volume.name} #{issue.number}" + (f' – "{issue.name}"' if issue.name else "")
            + (f"\n{issue.cover_date}" if issue.cover_date else "")
        )
        self._update_buttons()

    def _back(self) -> None:
        """◀◀: from the issue list back to the series list; from the series list back to the previous file."""
        if self._finishing:
            return
        if self.stack.currentIndex() == STEP_ISSUES:
            self._back_to_series()
        elif self._index > 0:
            self._index -= 1
            self._load_book()

    def _back_to_series(self) -> None:
        self._last_volume = None  # the remembered choice was wrong for this file
        self._last_series_key = ""
        self._issues_wanted = None
        if self._current_volume is not None:
            self._prior_volume_ids.discard(self._current_volume.volume_id)  # and no longer a favourite
        self._search_volumes()

    def _use_issue(self) -> None:
        issue = self._selected_issue()
        if issue is not None:
            self._apply(self._current_volume, issue)

    def _apply(self, volume: Optional[ComicVineVolume], issue: Optional[ComicVineIssue]) -> None:
        """Records the answer and moves straight on to the next file; the issue's details (credits,
        summary...) are fetched in the background. Stop / the last file waits for them."""
        if volume is None or issue is None or self._finishing:
            return
        index = self._index
        self._token += 1
        token = self._token
        self._answers[index] = token
        self._pending[index] = token
        self._results.pop(index, None)
        self._errors.pop(index, None)
        self._used[index] = f"{volume.name} #{issue.number}"
        name = os.path.basename(self._book.path)
        candidate = candidate_for(volume, issue)

        def then(details, error) -> None:
            if self._answers.get(index) != token:
                return  # answered again since
            self._pending.pop(index, None)
            if error is not None:
                self._errors[index] = f"{name}: {error}"
            else:
                details.publisher = volume.publisher
                self._results[index] = details.as_dict()
            self._finish_if_done()

        self._prior_volume_ids.add(volume.volume_id)  # the chosen series is remembered for the next file
        self._last_volume = volume
        self._last_series_key = _series_key(self.series_edit.text())
        self._request(("details", token), fetch_issue_details, self._api_key, candidate.detail_url, then=then)
        self._next_book()

    def _use_current(self) -> None:
        if self.stack.currentIndex() == STEP_SERIES:
            if self._series_issue is not None:  # Yes straight from the series list
                self._apply(self._selected_volume(), self._series_issue)
        else:
            self._use_issue()

    # ------------------------------------------------------------------
    # Finishing

    def _stop(self) -> None:
        """Stop here: keep the files already answered Yes, leave this one and the rest alone."""
        self._finish()

    def _finish(self) -> None:
        """Closes once every Yes has its details. Cancel stays live meanwhile (and discards all)."""
        self._finishing = True
        self._selection_timer.stop()
        for widget in (self.stack, self.search_btn, self.key_btn, self.back_btn, self.browse_btn, self.use_btn,
                       self.skip_btn, self.finish_btn):
            widget.setEnabled(False)
        self._finish_if_done()

    def _finish_if_done(self) -> None:
        if not self._finishing or self._shut:
            return
        if self._pending:
            count = len(self._pending)
            self.step_label.setText(
                f"Waiting for Comic Vine: the details of {count} issue(s) are still on their way. "
                "The window closes by itself; Cancel discards everything."
            )
            return
        if self._errors:
            QMessageBox.warning(
                self, "Comic Vine",
                "These files were answered Yes, but Comic Vine did not send their details, so they are left alone:\n\n"
                + "\n".join(self._errors[i] for i in sorted(self._errors)),
            )
        self.accept()

    # ------------------------------------------------------------------
    # Step switching

    def _set_step(self, step: int) -> None:
        self.stack.setCurrentIndex(step)
        in_issues = step == STEP_ISSUES
        self.browse_btn.setVisible(not in_issues)
        self.back_btn.setToolTip("Back to the series list" if in_issues else "Back to the previous file")
        self.step_label.setText(
            "Choose the issue (the covers are side by side), then ▶ to use it. ◀◀ goes back to the series list."
            if in_issues
            else "Is this the right issue? ▶ uses it. Pick another series in the list, or Show Issues to choose "
                 "a different issue. ▶▶ goes on without one."
        )
        if not in_issues:
            self.match_label.setText("")
        self._update_buttons()

    def _update_buttons(self) -> None:
        if self._finishing:
            return
        self.back_btn.setEnabled(self.stack.currentIndex() == STEP_ISSUES or self._index > 0)
        if self.stack.currentIndex() == STEP_ISSUES:
            self.use_btn.setEnabled(self._selected_issue() is not None)
        else:
            self.use_btn.setEnabled(self._series_issue is not None)
            self.browse_btn.setEnabled(self._selected_volume() is not None)

    # ------------------------------------------------------------------
    # Result, read by main_window._run_lookup_dialog

    def accepted_metadata(self) -> dict[int, dict]:
        return dict(self._results)
