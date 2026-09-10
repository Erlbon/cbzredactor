"""Tests for MainWindow._resolve_overwrite_conflicts() -- the single
place every lookup dialog's "Apply" (and Parse Filename) funnels
through, so this is worth covering even though the GUI layer generally
isn't unit-tested elsewhere in this app: a regression here would
silently start clobbering (or silently stop filling) real user data
across every metadata-writing path at once.

2026-09-10: replaced the old all-or-nothing "Overwrite All / Keep
Existing / Cancel" QMessageBox with a per-file, per-field review
(gui/overwrite_review_dialog.OverwriteReviewDialog) -- "There could be
instances where we want some fields, but not all." OverwriteReviewDialog's
own exec() is faked via monkeypatching (never actually shown), same
reasoning as the old QMessageBox fakes -- but its accepted_changes()
is exercised for REAL wherever a test doesn't care about a specific
selection, so the actual default-checked business logic (a safe fill
starts ticked, a real overwrite starts unticked) is what's under test,
not a mocked stand-in for it."""

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.main_window import MainWindow
from gui.overwrite_review_dialog import OverwriteReviewDialog

# A QApplication is required to construct any QWidget (including
# MainWindow and the review dialog) -- created once per test session.
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


def _fake_exec_accept(self):
    return QDialog.DialogCode.Accepted


def _fake_exec_reject(self):
    return QDialog.DialogCode.Rejected


@pytest.fixture
def window():
    return MainWindow()


def test_no_conflict_applies_everything_without_prompting(window, monkeypatch):
    # If the dialog's exec() were called here, the test would hang (or
    # fail outright) waiting for a real interaction -- asserting it's
    # never called IS the check that nothing was prompted for a
    # no-conflict case.
    monkeypatch.setattr(OverwriteReviewDialog, "exec", lambda self: pytest.fail("should not prompt"))

    book = _fake_book()  # every field blank
    changes = {0: {"series": "New Series", "writer": "New Writer"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == changes


def test_identical_value_is_not_a_conflict(window, monkeypatch):
    monkeypatch.setattr(OverwriteReviewDialog, "exec", lambda self: pytest.fail("should not prompt"))

    book = _fake_book(series="Same Series")
    changes = {0: {"series": "Same Series"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == changes


def test_conflict_opens_the_review_dialog_exactly_once(window, monkeypatch):
    call_count = 0

    def _counting_accept(self):
        nonlocal call_count
        call_count += 1
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OverwriteReviewDialog, "exec", _counting_accept)

    book_a = _fake_book(series="A Old")
    book_b = _fake_book(series="B Old")
    changes = {0: {"series": "A New"}, 1: {"series": "B New"}}
    window._resolve_overwrite_conflicts([book_a, book_b], changes)
    assert call_count == 1  # one review covering every affected file, not one per file


def test_a_genuine_overwrite_starts_unticked_and_is_excluded_by_default(window, monkeypatch):
    """The actual default-checked business logic, exercised through a
    real (unshown) dialog rather than a mock of accepted_changes()."""
    monkeypatch.setattr(OverwriteReviewDialog, "exec", _fake_exec_accept)

    book = _fake_book(series="Old Series")
    changes = {0: {"series": "New Series"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == {}  # nothing ticked by default -- a real overwrite needs an opt-in


def test_a_safe_fill_starts_ticked_even_when_a_sibling_field_conflicts(window, monkeypatch):
    """The whole point of per-field review: one field in the same file
    can conflict (and default off) while another, currently-blank
    field in that SAME change still defaults on -- "some fields, but
    not all", not an all-or-nothing choice for the file."""
    monkeypatch.setattr(OverwriteReviewDialog, "exec", _fake_exec_accept)

    book = _fake_book(series="Old Series")  # writer is blank
    changes = {0: {"series": "New Series", "writer": "New Writer"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result == {0: {"writer": "New Writer"}}  # series excluded (conflict), writer kept (safe fill)


def test_reviewer_can_accept_some_fields_but_not_others(window, monkeypatch):
    """Directly proves per-field granularity end to end: simulates the
    user re-ticking the one conflicting field the default left off,
    while a second conflicting field in the same file stays off."""
    book = _fake_book(series="Old Series", writer="Old Writer")
    changes = {0: {"series": "New Series", "writer": "New Writer"}}

    captured_dialog = {}

    def _fake_exec_and_capture(self):
        captured_dialog["dialog"] = self
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OverwriteReviewDialog, "exec", _fake_exec_and_capture)
    original_result = window._resolve_overwrite_conflicts([book], changes)
    assert original_result == {}  # both conflict, both off by default -- confirms the starting state

    # Re-run, this time flipping just the "series" row's checkbox on
    # before reading accepted_changes() -- exactly what ticking one box
    # in the real dialog would do.
    monkeypatch.setattr(OverwriteReviewDialog, "exec", _fake_exec_and_capture)
    window._resolve_overwrite_conflicts([book], changes)
    dialog = captured_dialog["dialog"]
    dialog._controller._checkboxes[(0, "series")].setChecked(True)
    result = {}
    for (index, attr), value in dialog.accepted_changes().items():
        result.setdefault(index, {})[attr] = value
    assert result == {0: {"series": "New Series"}}  # series accepted, writer still excluded


def test_cancel_returns_none(window, monkeypatch):
    monkeypatch.setattr(OverwriteReviewDialog, "exec", _fake_exec_reject)

    book = _fake_book(series="Old Series")
    changes = {0: {"series": "New Series"}}
    result = window._resolve_overwrite_conflicts([book], changes)
    assert result is None
