"""Delete Files (Recycle Bin), and the Delete key asking Remove from List / Delete Files / Cancel
(Erlend, 2026-11-26: Delete only took the comic off the list, which is not what most people expect).
Offscreen, tmp_path only."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from redactor_common.gui.remove_or_delete import DELETE, REMOVE  # noqa: E402
from redactor_common.gui.standard_menus import get_action_registry  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.cbz_file import CbzBook  # noqa: E402
from core.comicinfo import ComicInfoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _book(path) -> CbzBook:
    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = str(path)
    book.metadata = ComicInfoMetadata(title=path.stem)
    book.page_names = []
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    return book


@pytest.fixture
def window(tmp_path):
    w = mw.MainWindow()
    books = []
    for n in "ab":
        p = tmp_path / f"{n}.cbz"
        p.write_bytes(b"x")
        books.append(_book(p))
    w.books = books
    w._rebuild_table()
    return w


def _select_all(window):
    window._selected_rows = list(range(len(window.books)))


def _fake_trash(trashed):
    return lambda p: (trashed.append(p), os.remove(p))


def test_delete_key_is_the_question_and_remove_from_list_has_no_shortcut(window):
    reg = get_action_registry(window)
    assert reg["remove_or_delete"].shortcut() == QKeySequence("Delete")
    assert reg["remove_from_list"].shortcut().isEmpty()
    assert reg["delete_files"].shortcut() == QKeySequence("Shift+Delete")
    assert "Recycle Bin" in reg["delete_files"].text()
    assert "Recycle Bin" in reg["delete_files"].toolTip()


def test_answer_remove_takes_the_comics_off_the_list_and_keeps_the_files(window, monkeypatch):
    _select_all(window)
    paths = [b.path for b in window.books]
    monkeypatch.setattr(mw, "ask_remove_or_delete", lambda *a, **k: REMOVE)
    monkeypatch.setattr(mw, "_trash_retrying", lambda p: pytest.fail("trashed on Remove"))
    window.on_delete_key()
    assert window.books == [] and all(os.path.exists(p) for p in paths)


def test_answer_delete_trashes_without_a_second_question(window, monkeypatch):
    _select_all(window)
    paths = [b.path for b in window.books]
    trashed = []
    monkeypatch.setattr(mw, "ask_remove_or_delete", lambda *a, **k: DELETE)
    monkeypatch.setattr(mw, "_trash_retrying", _fake_trash(trashed))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("asked twice"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail("warned"))
    window.on_delete_key()
    assert sorted(trashed) == sorted(paths) and window.books == []
    assert "Recycle Bin" in window.statusBar().currentMessage()


def test_cancel_changes_nothing(window, monkeypatch):
    _select_all(window)
    before = list(window.books)
    monkeypatch.setattr(mw, "ask_remove_or_delete", lambda *a, **k: None)
    monkeypatch.setattr(mw, "_trash_retrying", lambda p: pytest.fail("trashed on Cancel"))
    window.on_delete_key()
    assert window.books == before


def test_nothing_selected_asks_nothing(window, monkeypatch):
    window._selected_rows = []
    monkeypatch.setattr(mw, "ask_remove_or_delete", lambda *a, **k: pytest.fail("asked with nothing selected"))
    window.on_delete_key()
    assert "select the comics" in window.statusBar().currentMessage()


def test_the_menu_item_still_confirms_on_its_own(window, monkeypatch):
    _select_all(window)
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: (asked.append(a[1]), QMessageBox.StandardButton.No)[1])
    monkeypatch.setattr(mw, "_trash_retrying", lambda p: pytest.fail("trashed after No"))
    window.delete_files()
    assert asked == ["Delete Files (Recycle Bin)?"] and len(window.books) == 2


def test_the_menu_item_trashes_after_yes(window, monkeypatch):
    _select_all(window)
    trashed = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mw, "_trash_retrying", _fake_trash(trashed))
    window.delete_files()
    assert len(trashed) == 2 and window.books == []


def test_unsaved_comic_is_refused_and_stays_in_the_list(window, monkeypatch):
    _select_all(window)
    dirty, clean = window.books
    dirty.dirty = True
    trashed = []
    warnings = []
    monkeypatch.setattr(mw, "_trash_retrying", _fake_trash(trashed))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    window.delete_files(confirm=False)
    assert trashed == [clean.path]
    assert window.books == [dirty] and os.path.exists(dirty.path)
    assert len(warnings) == 1 and "unsaved changes" in warnings[0] and "a.cbz" in warnings[0]


def test_trash_failure_is_reported_in_one_warning(window, monkeypatch):
    _select_all(window)

    def boom(path):
        raise mw.TrashError("locked")

    warnings = []
    monkeypatch.setattr(mw, "_trash_retrying", boom)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    window.delete_files(confirm=False)
    assert len(window.books) == 2 and len(warnings) == 1 and "locked" in warnings[0]
