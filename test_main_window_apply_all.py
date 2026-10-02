"""Apply with NOTHING selected writes the panel's edits to every loaded file.

Before 2026-10-02 the panel was disabled and Apply read 'Apply to 0
Selected' (disabled) whenever no row was selected, so typing tags into the
left-hand panel and pressing Apply did nothing. Now 'nothing selected =
every loaded book', the same rule every other action uses (_target_books).
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui import main_window as mw
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _fake_book(name="a", **metadata_kwargs) -> CbzBook:
    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = f"/x/{name}.cbz"
    book.metadata = ComicInfoMetadata(**metadata_kwargs)
    book.page_names = []
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    return book


def _window(count: int) -> MainWindow:
    window = MainWindow()
    for i in range(count):
        book = _fake_book(f"f{i}")
        window.books.append(book)
        window._add_table_row(book)
    window._show_idle_panel()
    return window


def _type(window, **fields):
    for attr, value in fields.items():
        window.panel._line_edits[attr].setText(value)


def _no_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: pytest.fail("unexpected info box"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("unexpected question"))


def test_nothing_selected_panel_and_apply_are_live(monkeypatch):
    window = _window(3)
    assert window._selected_rows == []
    assert window.panel.isEnabled() and window.panel.bulk_mode and window.panel.apply_all_mode
    apply = window.actions_["apply"]
    assert apply.isEnabled()
    assert "All 3 Files" in apply.text()


def test_apply_with_nothing_selected_writes_every_book(monkeypatch):
    _no_dialogs(monkeypatch)
    window = _window(3)
    _type(window, genre="Horror", writer="Someone")
    window._apply_bulk_edit()
    for book in window.books:
        assert book.metadata.genre == "Horror"
        assert book.metadata.writer == "Someone"
        assert book.dirty
    assert window.statusBar().currentMessage() == "Applied to all 3 files"
    assert window.undo_manager.can_undo()
    # fields cleared for another round, still in all-files mode
    assert window.panel.apply_all_mode
    assert window.panel._line_edits["genre"].text() == ""


def test_untouched_fields_are_not_written(monkeypatch):
    _no_dialogs(monkeypatch)
    window = _window(2)
    window.books[0].metadata.series = "Keep Me"
    window.books[1].metadata.series = "Me Too"
    _type(window, genre="Horror")
    window._apply_bulk_edit()
    assert [b.metadata.series for b in window.books] == ["Keep Me", "Me Too"]


def test_undo_restores_every_book(monkeypatch):
    _no_dialogs(monkeypatch)
    window = _window(2)
    _type(window, genre="Horror")
    window._apply_bulk_edit()
    window.undo_last_action()
    assert all(b.metadata.genre == "" and not b.dirty for b in window.books)


def test_single_loaded_file_nothing_selected_still_applies(monkeypatch):
    _no_dialogs(monkeypatch)
    window = _window(1)
    assert window.panel.apply_all_mode and window.actions_["apply"].isEnabled()
    _type(window, genre="Horror")
    window._apply_bulk_edit()
    assert window.books[0].metadata.genre == "Horror"


def test_large_apply_asks_first_and_names_the_fields(monkeypatch):
    window = _window(mw.APPLY_ALL_CONFIRM_ABOVE + 1)
    _type(window, genre="Horror", publisher="Acme")
    seen = {}

    def _ask(parent, title, text, *a, **k):
        seen["text"] = text
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", _ask)
    window._apply_bulk_edit()
    assert "Genre" in seen["text"] and "Publisher" in seen["text"]
    assert str(mw.APPLY_ALL_CONFIRM_ABOVE + 1) in seen["text"]
    assert all(b.metadata.genre == "" for b in window.books)  # declined: nothing written

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._apply_bulk_edit()
    assert all(b.metadata.genre == "Horror" for b in window.books)


def test_small_apply_does_not_ask(monkeypatch):
    _no_dialogs(monkeypatch)
    window = _window(mw.APPLY_ALL_CONFIRM_ABOVE)
    _type(window, genre="Horror")
    window._apply_bulk_edit()
    assert all(b.metadata.genre == "Horror" for b in window.books)


def test_empty_apply_says_so_rather_than_silently_nothing(monkeypatch):
    window = _window(2)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    window._apply_bulk_edit()
    assert shown == ["Nothing to Apply"]


def test_selected_rows_only_unchanged(monkeypatch):
    _no_dialogs(monkeypatch)
    window = _window(4)
    window.table.selectRow(0)
    window.table.selectionModel().select(
        window.table.model().index(1, 0), window.table.selectionModel().SelectionFlag.Select
        | window.table.selectionModel().SelectionFlag.Rows,
    )
    assert window._selected_rows == [0, 1]
    assert not window.panel.apply_all_mode and window.panel.bulk_mode
    assert "2 Selected" in window.actions_["apply"].text()
    _type(window, genre="Horror")
    window._apply_bulk_edit()
    assert [b.metadata.genre for b in window.books] == ["Horror", "Horror", "", ""]
    assert window.statusBar().currentMessage() != "Applied to all 2 files"


def test_one_selected_label_is_neutral_not_zero(monkeypatch):
    window = _window(3)
    window.table.selectRow(1)
    apply = window.actions_["apply"]
    assert not apply.isEnabled()
    assert "0" not in apply.text() and apply.text() == "&Apply to Selected"


def test_deselecting_returns_to_all_files_mode():
    window = _window(3)
    window.table.selectRow(0)
    assert not window.panel.apply_all_mode
    window.table.clearSelection()
    assert window.panel.apply_all_mode and "All 3 Files" in window.actions_["apply"].text()


def test_no_files_loaded_panel_off_and_label_neutral():
    window = _window(0)
    assert not window.panel.isEnabled()
    assert not window.actions_["apply"].isEnabled()
    assert window.actions_["apply"].text() == "&Apply to Selected"


def test_removing_all_files_turns_panel_off():
    window = _window(2)
    window.clear_list()
    assert not window.panel.apply_all_mode and not window.actions_["apply"].isEnabled()


def test_typed_text_survives_loaded_count_change():
    window = _window(2)
    _type(window, genre="Horror")
    book = _fake_book("late")
    window.books.append(book)
    window._add_table_row(book)
    window._show_idle_panel()
    assert window.panel._line_edits["genre"].text() == "Horror"
    assert "All 3 Files" in window.actions_["apply"].text()


def test_unconverted_books_are_not_targets(monkeypatch):
    _no_dialogs(monkeypatch)
    window = _window(2)
    foreign = _fake_book("old")
    foreign.path = "/x/old.cbr"
    window.books.append(foreign)
    window._add_table_row(foreign)
    window._show_idle_panel()
    _type(window, genre="Horror")
    window._apply_bulk_edit()
    assert foreign.metadata.genre == ""
    assert window.statusBar().currentMessage() == "Applied to all 2 files"
