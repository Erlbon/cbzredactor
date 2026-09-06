"""Tests for MainWindow's click-a-header-to-sort behavior
(_on_header_clicked/_sort_key_for) -- same "worth testing despite the
general GUI-isn't-unit-tested convention" reasoning as
test_main_window_overwrite.py: a regression here would silently break
the "table row N is self.books[N]" invariant that save/remove/bulk-
edit/lookups/etc. all depend on throughout gui/main_window.py."""

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _fake_book(path: str, page_count: int = 1, **metadata_kwargs) -> CbzBook:
    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = path
    book.metadata = ComicInfoMetadata(**metadata_kwargs)
    book.page_names = [f"{i:03}.jpg" for i in range(page_count)]
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    return book


@pytest.fixture
def window():
    return MainWindow()


def _load(window, books):
    window.books = books
    window._rebuild_table()


def test_clicking_a_string_column_sorts_ascending_then_descending(window):
    books = [
        _fake_book("/x/c.cbz", series="Charlie"),
        _fake_book("/x/a.cbz", series="Alpha"),
        _fake_book("/x/b.cbz", series="Bravo"),
    ]
    _load(window, books)
    series_col = window._col_index["series"]

    window._on_header_clicked(series_col)
    assert [b.metadata.series for b in window.books] == ["Alpha", "Bravo", "Charlie"]

    window._on_header_clicked(series_col)  # same column again -- reverses
    assert [b.metadata.series for b in window.books] == ["Charlie", "Bravo", "Alpha"]


def test_sort_is_case_insensitive(window):
    books = [_fake_book("/x/a.cbz", series="banana"), _fake_book("/x/b.cbz", series="Apple")]
    _load(window, books)
    window._on_header_clicked(window._col_index["series"])
    assert [b.metadata.series for b in window.books] == ["Apple", "banana"]


def test_numeric_column_sorts_numerically_not_lexicographically(window):
    """"9" must sort before "10" -- a plain string sort would put "10"
    first, which is exactly the kind of bug this dedicated numeric-key
    path (NUMERIC_FILENAME_FIELDS) exists to avoid."""
    books = [
        _fake_book("/x/a.cbz", number="10"),
        _fake_book("/x/b.cbz", number="2"),
        _fake_book("/x/c.cbz", number="9"),
    ]
    _load(window, books)
    window._on_header_clicked(window._col_index["number"])
    assert [b.metadata.number for b in window.books] == ["2", "9", "10"]


def test_non_numeric_value_in_a_numeric_column_sorts_after_numeric_ones(window):
    books = [
        _fake_book("/x/a.cbz", number="3"),
        _fake_book("/x/b.cbz", number=""),  # blank -- doesn't parse as a number
        _fake_book("/x/c.cbz", number="1"),
    ]
    _load(window, books)
    window._on_header_clicked(window._col_index["number"])
    assert [b.metadata.number for b in window.books] == ["1", "3", ""]


def test_pages_column_sorts_numerically(window):
    books = [
        _fake_book("/x/a.cbz", page_count=20),
        _fake_book("/x/b.cbz", page_count=3),
        _fake_book("/x/c.cbz", page_count=9),
    ]
    _load(window, books)
    window._on_header_clicked(window._col_index["pages"])
    assert [b.actual_page_count for b in window.books] == [3, 9, 20]


def test_filename_column_sorts_by_basename(window):
    books = [_fake_book("/z/charlie.cbz"), _fake_book("/a/alpha.cbz"), _fake_book("/m/bravo.cbz")]
    _load(window, books)
    window._on_header_clicked(window._col_index["filename"])
    assert [b.path for b in window.books] == ["/a/alpha.cbz", "/m/bravo.cbz", "/z/charlie.cbz"]


def test_table_rows_stay_in_lockstep_with_books_after_sort(window):
    """The whole point of sorting self.books + rebuilding rather than
    letting Qt reorder QTableWidgetItems in place: row N must always be
    self.books[N], since every other MainWindow method (save, remove,
    bulk edit, lookups...) assumes exactly that."""
    books = [
        _fake_book("/x/c.cbz", series="Charlie"),
        _fake_book("/x/a.cbz", series="Alpha"),
        _fake_book("/x/b.cbz", series="Bravo"),
    ]
    _load(window, books)
    window._on_header_clicked(window._col_index["series"])

    for row, book in enumerate(window.books):
        assert window.table.item(row, window._col_index["series"]).text() == book.metadata.series
        assert window.table.item(row, window._col_index["filename"]).text() == book.path.rsplit("/", 1)[-1]


def test_sort_clears_selection_rather_than_leaving_a_stale_highlight(window):
    books = [_fake_book("/x/c.cbz", series="Charlie"), _fake_book("/x/a.cbz", series="Alpha")]
    _load(window, books)
    window._selected_rows = [0]

    window._on_header_clicked(window._col_index["series"])
    assert window._selected_rows == []


def test_newly_loaded_files_are_folded_into_an_active_sort(window, tmp_path):
    """A column sort is active when more files get loaded (Load Files/
    Load Folder, or Refresh List / Convert CBR / Resize Images loading
    their own results back in, all of which route through
    _load_paths()) -- the new arrivals shouldn't just get tacked onto
    the end, breaking the sort the user asked for."""
    import zipfile

    def _make_real_cbz(path, series):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ComicInfo.xml", f"<ComicInfo><Series>{series}</Series></ComicInfo>".encode())
            zf.writestr("001.jpg", b"\xff\xd8\xff\xe0fake")
        return str(path)

    books = [_fake_book("/x/c.cbz", series="Charlie"), _fake_book("/x/a.cbz", series="Alpha")]
    _load(window, books)
    window._on_header_clicked(window._col_index["series"])  # sort by series, ascending
    assert [b.metadata.series for b in window.books] == ["Alpha", "Charlie"]

    new_path = _make_real_cbz(tmp_path / "bravo.cbz", "Bravo")
    window._load_paths([new_path])

    assert [b.metadata.series for b in window.books] == ["Alpha", "Bravo", "Charlie"]
