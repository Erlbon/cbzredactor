"""Tests for MainWindow._apply_row_status_color() -- row tinting for a
load error / unsaved change / page-count mismatch, added 2026-09-07 to
match epub/mp3/video's own use of redactor_common.gui.colors (a cross-
repo review found this app was the only one of the four with no row
color-tinting at all -- its Status column was plain text only)."""

import sys

from PyQt6.QtWidgets import QApplication
from redactor_common.gui.colors import DIRTY_COLOR, ERROR_COLOR

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _fake_book(**metadata_kwargs) -> CbzBook:
    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = "/x/fake.cbz"
    book.metadata = ComicInfoMetadata(**metadata_kwargs)
    book.page_names = ["001.jpg"]
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    return book


def _row_colors(window, row):
    colors = set()
    for col in range(window.table.columnCount()):
        item = window.table.item(row, col)
        if item is not None:
            colors.add(item.background().color().name())
    return colors


def test_clean_row_has_no_background_override():
    window = MainWindow()
    window.books = [_fake_book(title="A")]
    window._rebuild_table()

    # An unset background reports as the invalid/transparent color, not
    # ERROR_COLOR/DIRTY_COLOR's actual hex values.
    assert ERROR_COLOR.name() not in _row_colors(window, 0)
    assert DIRTY_COLOR.name() not in _row_colors(window, 0)


def test_dirty_row_gets_tinted():
    window = MainWindow()
    book = _fake_book(title="A")
    book.dirty = True
    window.books = [book]
    window._rebuild_table()

    assert _row_colors(window, 0) == {DIRTY_COLOR.name()}


def test_load_error_row_gets_tinted_and_takes_priority_over_dirty():
    window = MainWindow()
    book = _fake_book(title="A")
    book.dirty = True
    book.load_error = "truncated zip"
    window.books = [book]
    window._rebuild_table()

    assert _row_colors(window, 0) == {ERROR_COLOR.name()}


def test_page_count_mismatch_row_gets_tinted():
    window = MainWindow()
    book = _fake_book(title="A", page_count="99")  # actual_page_count is 1 (one fake page)
    window.books = [book]
    window._rebuild_table()

    assert book.page_count_mismatch is True
    assert _row_colors(window, 0) == {DIRTY_COLOR.name()}


def test_color_clears_once_saved():
    window = MainWindow()
    book = _fake_book(title="A")
    book.dirty = True
    window.books = [book]
    window._rebuild_table()
    assert _row_colors(window, 0) == {DIRTY_COLOR.name()}

    book.dirty = False
    window._refresh_table_row(0, book)
    assert ERROR_COLOR.name() not in _row_colors(window, 0)
    assert DIRTY_COLOR.name() not in _row_colors(window, 0)
