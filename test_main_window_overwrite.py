"""Tests for MainWindow._resolve_overwrite_conflicts() -- the single
place every lookup dialog's "Apply" funnels through (see
gui/main_window.py's _run_lookup_dialog()), so this is worth covering
even though the GUI layer generally isn't unit-tested elsewhere in
this app: a regression here would silently start clobbering (or
silently stop filling) real user data across every lookup source at
once.

QMessageBox's modal exec() is faked via monkeypatching -- clicking a
button synchronously (QPushButton.click()) fires the same signal a
real click would, without needing the dialog actually shown."""

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.main_window import MainWindow

# A QApplication is required to construct any QWidget (including
# MainWindow and QMessageBox) -- created once per test session.
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


def _autoclick(button_text: str):
    """Replaces QMessageBox.exec with one that immediately clicks
    whichever button's text contains `button_text`, instead of
    actually blocking on a real click."""

    def _fake_exec(self):
        for button in self.buttons():
            if button_text in button.text():
                button.click()
                return 0
        self.buttons()[-1].click()  # fallback: last button (Cancel)
        return 0

    return _fake_exec


@pytest.fixture
def window():
    return MainWindow()


def test_no_conflict_applies_everything_without_prompting(window, monkeypatch):
    # If QMessageBox.exec were called here, the test would hang waiting
    # for a real click -- asserting it's never called IS the check that
    # nothing was prompted for a no-conflict case.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("should not prompt"))

    book = _fake_book()  # every field blank
    changes = {0: {"series": "New Series", "writer": "New Writer"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == changes


def test_identical_value_is_not_a_conflict(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("should not prompt"))

    book = _fake_book(series="Same Series")
    changes = {0: {"series": "Same Series"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == changes


def test_overwrite_all_applies_everything(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", _autoclick("Overwrite All"))

    book = _fake_book(series="Old Series")
    changes = {0: {"series": "New Series", "writer": "New Writer"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == changes


def test_keep_existing_only_fills_blank_fields(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", _autoclick("Keep Existing"))

    book = _fake_book(series="Old Series")  # writer is blank
    changes = {0: {"series": "New Series", "writer": "New Writer"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == {0: {"writer": "New Writer"}}


def test_cancel_returns_none(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", _autoclick("Cancel"))

    book = _fake_book(series="Old Series")
    changes = {0: {"series": "New Series"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result is None


def test_conflict_across_multiple_books_only_prompts_once(window, monkeypatch):
    call_count = 0

    def _counting_autoclick(self):
        nonlocal call_count
        call_count += 1
        for button in self.buttons():
            if "Overwrite All" in button.text():
                button.click()
                return 0
        return 0

    monkeypatch.setattr(QMessageBox, "exec", _counting_autoclick)

    book_a = _fake_book(series="A Old")
    book_b = _fake_book(series="B Old")
    changes = {0: {"series": "A New"}, 1: {"series": "B New"}}
    result = window._resolve_overwrite_conflicts([book_a, book_b], changes)
    assert result == changes
    assert call_count == 1  # one prompt covering every affected file, not one per file
