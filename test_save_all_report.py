"""Save All reports its result after EVERY run (status line with counts or the reason nothing was saved, a box listing
each problem file, an unexpected error in the "Something went wrong" box), never stops at the first bad file, and
names a file left marked unsaved without an error. Real MainWindow offscreen."""

import os
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core import crash_log, history
from core.cbz_file import CbzBook, CbzError
from gui import app_settings
from gui.main_window import MainWindow
from redactor_common.gui import crash_dialog

_app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "settings.ini"))
    monkeypatch.setattr(crash_log, "log_path", lambda: str(tmp_path / "logs" / "cbzredactor_crash.log"))
    monkeypatch.setattr(crash_dialog, "_reporter", None)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def reporter(tmp_path):
    rep = crash_dialog.install(tmp_path / "logs" / "cbzredactor_crash.log")
    yield rep
    if rep.dialog is not None:
        rep.dialog.close()
    _app.processEvents()


@pytest.fixture
def boxes(monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: seen.append((title, text))))
    return seen


def _cbz(path, title="T") -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", f"<ComicInfo><Title>{title}</Title></ComicInfo>")
        zf.writestr("001.jpg", b"x")
    return str(path)


@pytest.fixture
def window(tmp_path):
    win = MainWindow()
    win.resize(1500, 600)
    win.show()
    win._load_paths([_cbz(tmp_path / n) for n in ("A.cbz", "B.cbz", "C.cbz")])
    yield win
    for book in win.books:
        book.dirty = False
    win.close()


def _dirty(book, title):
    book.metadata.title = title
    book.dirty = True


def _on_disk(book):
    return CbzBook(book.path).metadata.title


def test_all_clean_says_why_nothing_was_saved(window):
    window.save_all_changed()
    assert window.statusBar().currentMessage() == "Nothing to save: no file has unsaved changes"


def test_all_changed_files_with_load_errors_name_them(window):
    for book in window.books[:2]:
        book.dirty = True
        book.load_error = "broken"
    window.save_all_changed()
    message = window.statusBar().currentMessage()
    assert message.startswith("Nothing to save: all 2 changed file(s) have load errors")
    assert "A.cbz" in message and "B.cbz" in message


def test_a_failing_file_is_listed_in_the_box_and_the_others_are_saved(window, boxes, monkeypatch):
    for i, book in enumerate(window.books):
        _dirty(book, f"New {i}")
    real = CbzBook.save

    def flaky(self, *a, **k):
        if self.path.endswith("B.cbz"):
            raise CbzError("disk says no")
        return real(self, *a, **k)

    monkeypatch.setattr(CbzBook, "save", flaky)
    window.save_all_changed()
    assert window.statusBar().currentMessage() == "Saved 2 file(s), 1 failed"
    assert len(boxes) == 1 and "B.cbz: disk says no" in boxes[0][1]
    assert _on_disk(window.books[0]) == "New 0" and _on_disk(window.books[2]) == "New 2"


def test_an_unexpected_exception_in_the_middle_shows_the_box_and_the_rest_is_saved(window, boxes, reporter, monkeypatch):
    for i, book in enumerate(window.books):
        _dirty(book, f"New {i}")
    real = CbzBook.save

    def explode(self, *a, **k):
        if self.path.endswith("B.cbz"):
            raise ValueError("kaboom")
        return real(self, *a, **k)

    monkeypatch.setattr(CbzBook, "save", explode)
    window.save_all_changed()
    _app.processEvents()
    assert reporter.dialog is not None and "kaboom" in reporter.dialog.details_text()
    assert _on_disk(window.books[0]) == "New 0" and _on_disk(window.books[2]) == "New 2"  # the loop went on
    assert window.statusBar().currentMessage() == "Saved 2 file(s), 1 failed"
    assert "B.cbz: unexpected error: kaboom" in boxes[0][1]


def test_an_exception_outside_the_per_file_loop_is_reported_and_shown_in_the_status_bar(window, reporter, monkeypatch):
    _dirty(window.books[0], "x")

    def boom(*a, **k):
        raise RuntimeError("no way")

    monkeypatch.setattr(window, "_save_rows", boom)
    window.save_all_changed()
    _app.processEvents()
    assert reporter.dialog is not None
    assert reporter.dialog.summary_label.text().startswith("Save All stopped because of an unexpected error")
    assert "no way" in window.statusBar().currentMessage()


def test_the_invariant_names_a_file_left_unsaved_without_an_error(window, boxes, monkeypatch):
    _dirty(window.books[0], "x")
    monkeypatch.setattr(CbzBook, "save", lambda self, *a, **k: None)  # "saves" but leaves dirty, no error
    window.save_all_changed()
    assert "still marked unsaved" in window.statusBar().currentMessage()
    assert boxes and "1 file(s) are still marked unsaved after saving: A.cbz" in boxes[0][1]


def test_the_save_log_is_opt_in_and_has_no_file_names(window, tmp_path):
    log = tmp_path / "logs" / "save_log.txt"
    _dirty(window.books[0], "x")
    window.save_all_changed()
    assert not log.exists()  # data logging is off by default
    history.set_history_enabled(True)
    _dirty(window.books[1], "y")
    window.save_all_changed()
    text = log.read_text(encoding="utf-8")
    assert text.count("\n") == 1 and "saved=1" in text and "B.cbz" not in text
