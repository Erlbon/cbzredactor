"""Tests for gui/overwrite_review_dialog.build_overwrite_review_rows()
-- the pure logic behind the per-file, per-field review dialog:
turning a {book_index: {attr: value}} change set into PreviewRow
entries, with the "safe fill starts ticked, real overwrite starts
unticked" default split. Doesn't need a QApplication -- PreviewRow is
a plain dataclass and this function does no Qt work itself."""

import os

from redactor_common.gui.preview_table import PreviewRow

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.overwrite_review_dialog import build_overwrite_review_rows


def _fake_book(path="/x/fake.cbz", **metadata_kwargs) -> CbzBook:
    book = CbzBook.__new__(CbzBook)
    book.path = path
    book.metadata = ComicInfoMetadata(**metadata_kwargs)
    book.page_names = []
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    return book


def _label(attr: str) -> str:
    return attr.replace("_", " ").title()


def test_blank_field_defaults_checked():
    book = _fake_book()  # series is blank
    changes = {0: {"series": "Batman"}}
    rows = build_overwrite_review_rows([book], changes, _label)

    assert len(rows) == 1
    assert rows[0] == PreviewRow(
        item_index=(0, "series"), display_name="Series", old_value="", new_value="Batman",
        group="fake.cbz", default_checked=True,
    )


def test_real_overwrite_defaults_unchecked():
    book = _fake_book(series="Old Series")
    changes = {0: {"series": "New Series"}}
    rows = build_overwrite_review_rows([book], changes, _label)

    assert rows[0].default_checked is False
    assert rows[0].old_value == "Old Series"
    assert rows[0].new_value == "New Series"


def test_identical_value_defaults_checked_not_treated_as_a_conflict():
    book = _fake_book(series="Same Series")
    changes = {0: {"series": "Same Series"}}
    rows = build_overwrite_review_rows([book], changes, _label)

    assert rows[0].default_checked is True


def test_rows_are_grouped_by_the_files_basename_not_full_path():
    book = _fake_book(path="/some/deep/folder/comic.cbz")
    changes = {0: {"series": "Batman"}}
    rows = build_overwrite_review_rows([book], changes, _label)

    assert rows[0].group == os.path.basename("/some/deep/folder/comic.cbz")


def test_one_row_per_field_across_multiple_files():
    book_a = _fake_book(path="/x/a.cbz", series="A Old")
    book_b = _fake_book(path="/x/b.cbz")  # writer blank
    changes = {0: {"series": "A New"}, 1: {"writer": "New Writer"}}
    rows = build_overwrite_review_rows([book_a, book_b], changes, _label)

    by_key = {row.item_index: row for row in rows}
    assert len(rows) == 2
    assert by_key[(0, "series")].group == "a.cbz"
    assert by_key[(0, "series")].default_checked is False  # A Old -> A New is a real overwrite
    assert by_key[(1, "writer")].group == "b.cbz"
    assert by_key[(1, "writer")].default_checked is True  # blank -> New Writer is a safe fill
