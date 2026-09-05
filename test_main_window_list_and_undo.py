"""Tests for MainWindow's list-management (remove/clear) and undo
wiring -- same "worth testing despite the general GUI-isn't-unit-
tested convention" reasoning as test_main_window_overwrite.py: a
regression here silently loses data or crashes outright."""

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _fake_book(**metadata_kwargs) -> CbzBook:
    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = "/x/fake.cbz"
    book.metadata = ComicInfoMetadata(**metadata_kwargs)
    book.page_names = []
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    return book


@pytest.fixture
def window():
    return MainWindow()


def test_remove_selected_does_not_crash_on_unhashable_book(window):
    """CbzBook is a plain @dataclass -- it has a value-based __eq__ and
    is therefore unhashable. remove_selected() must exclude by row
    index, never by putting CbzBook instances in a set/dict key -- this
    is a regression test for exactly that TypeError."""
    window.books = [_fake_book(title="A"), _fake_book(title="B"), _fake_book(title="C")]
    window._rebuild_table()
    window._selected_rows = [1]

    window.remove_selected()

    assert [b.metadata.title for b in window.books] == ["A", "C"]
    assert window.table.rowCount() == 2


def test_remove_selected_with_identical_books_removes_only_the_selected_one(window):
    """Two books with identical in-memory state would compare equal
    under CbzBook's default __eq__ -- a set-membership-based removal
    would incorrectly remove BOTH. Index-based removal must only
    remove the one actually selected."""
    same_a = _fake_book(title="Same")
    same_b = _fake_book(title="Same")
    window.books = [same_a, same_b]
    window._rebuild_table()
    window._selected_rows = [0]

    window.remove_selected()

    assert window.books == [same_b]


def test_clear_list_prompts_when_dirty_and_respects_no(window, monkeypatch):
    window.books = [_fake_book(title="Dirty")]
    window.books[0].dirty = True
    window._rebuild_table()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    window.clear_list()

    assert len(window.books) == 1  # aborted -- nothing cleared


def test_clear_list_proceeds_when_confirmed(window, monkeypatch):
    window.books = [_fake_book(title="Dirty")]
    window.books[0].dirty = True
    window._rebuild_table()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window.clear_list()

    assert window.books == []
    assert window.table.rowCount() == 0


def test_clear_list_skips_prompt_when_nothing_dirty(window, monkeypatch):
    window.books = [_fake_book(title="Clean")]
    window._rebuild_table()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: pytest.fail("should not prompt when nothing is dirty")
    )

    window.clear_list()

    assert window.books == []


def test_undo_restores_metadata_and_dirty_flag(window):
    book = _fake_book(series="Original")
    window.books = [book]
    window._rebuild_table()

    window._push_undo("Test edit", [book])
    book.metadata.series = "Changed"
    book.dirty = True
    assert window.actions_["undo"].isEnabled() is True

    window.undo_last_action()

    assert book.metadata.series == "Original"
    assert book.dirty is False
    assert window.actions_["undo"].isEnabled() is False


def test_undo_stack_cleared_by_remove_selected(window):
    book_a = _fake_book(title="A")
    book_b = _fake_book(title="B")
    window.books = [book_a, book_b]
    window._rebuild_table()
    window._push_undo("Edit", [book_a])

    window._selected_rows = [1]  # remove book_b, unrelated to the undo entry
    window.remove_selected()

    # Cleared regardless -- a stale entry could otherwise reference a
    # book object no longer in the list at all if further removals
    # happened, so the whole stack is dropped rather than trying to
    # track which entries are still valid.
    assert window.undo_manager.can_undo() is False
    assert window.actions_["undo"].isEnabled() is False
