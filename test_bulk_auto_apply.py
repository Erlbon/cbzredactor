"""Typed-but-unapplied BULK edits are applied to the previous selection when
the selection changes (decided 2026-11-04).

Before, a bulk edit (2+ files selected) was deliberately not committed on a
selection change, so values typed into the panel were silently lost the
moment the user clicked another row. Now _commit_current_edits() applies
them to the previous selection's editable books, through the same core as
the Apply button (one undo entry, dirty flags, rows refreshed). Never in
"apply all" mode (nothing selected): that would write every loaded file
without Apply's confirmation.
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _fake_book(name="a", **metadata_kwargs) -> CbzBook:
    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = f"{name}.cbz"
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


def _select(window, *rows):
    window.table.clearSelection()
    model = window.table.selectionModel()
    for row in rows:
        model.select(
            window.table.model().index(row, 0), model.SelectionFlag.Select | model.SelectionFlag.Rows
        )
    assert window._selected_rows == sorted(rows)


def _type(window, **fields):
    for attr, value in fields.items():
        window.panel._line_edits[attr].setText(value)


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: pytest.fail("unexpected info box"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("unexpected question"))


def test_selecting_another_row_applies_pending_bulk_edit_to_previous_selection():
    window = _window(3)
    a, b, c = window.books
    _select(window, 0, 1)
    assert window.panel.bulk_mode and not window.panel.apply_all_mode
    _type(window, genre="Horror")
    window.table.selectRow(2)
    assert (a.metadata.genre, b.metadata.genre, c.metadata.genre) == ("Horror", "Horror", "")
    assert a.dirty and b.dirty and not c.dirty
    assert window.statusBar().currentMessage() == "Applied pending edits to 2 files"


def test_one_undo_restores_the_auto_applied_books():
    window = _window(3)
    a, b, _ = window.books
    _select(window, 0, 1)
    _type(window, genre="Horror")
    window.table.selectRow(2)
    assert a.dirty and b.dirty
    window.undo_last_action()
    assert a.metadata.genre == "" and b.metadata.genre == ""
    assert not a.dirty and not b.dirty
    assert not window.undo_manager.can_undo()


def test_clearing_the_selection_applies_to_previous_selection_not_to_all():
    window = _window(3)
    _select(window, 0, 1)
    _type(window, genre="Horror")
    window.table.clearSelection()
    assert [book.metadata.genre for book in window.books] == ["Horror", "Horror", ""]
    assert window.panel.apply_all_mode
    assert window.panel._line_edits["genre"].text() == ""


def test_apply_all_mode_is_never_auto_applied():
    window = _window(3)
    assert window.panel.apply_all_mode
    _type(window, genre="Horror")
    window.table.selectRow(1)
    assert all(book.metadata.genre == "" and not book.dirty for book in window.books)
    assert not window.undo_manager.can_undo()


def test_untouched_bulk_panel_applies_nothing():
    window = _window(3)
    _select(window, 0, 1)
    window.table.selectRow(2)
    assert not any(book.dirty for book in window.books)
    assert not window.undo_manager.can_undo()
    assert window.statusBar().currentMessage() != "Applied pending edits to 2 files"


def test_sorting_applies_to_the_right_books_before_reordering():
    window = _window(3)
    a, b, c = window.books
    a.metadata.series, b.metadata.series, c.metadata.series = "Zed", "Mid", "Alpha"
    _select(window, 0, 1)
    _type(window, genre="Horror")
    window._on_header_clicked(window._column_keys.index("series"))
    assert (a.metadata.genre, b.metadata.genre, c.metadata.genre) == ("Horror", "Horror", "")


def test_sorting_with_one_row_selected_does_not_copy_it_onto_another_book():
    # The rebuild's selectionChanged used to commit the panel into whichever
    # book had moved into the old row number.
    window = _window(3)
    a, b, c = window.books
    a.metadata.series, b.metadata.series, c.metadata.series = "Zed", "Mid", "Alpha"
    window.table.selectRow(0)
    window._on_header_clicked(window._column_keys.index("series"))
    assert (a.metadata.series, b.metadata.series, c.metadata.series) == ("Zed", "Mid", "Alpha")


def test_remove_from_list_asks_about_the_pending_bulk_edit(monkeypatch):
    window = _window(3)
    _select(window, 0, 1)
    _type(window, genre="Horror")
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No
    )
    window.remove_selected()
    assert asked and "2 with unsaved edits" in asked[0]
    assert len(window.books) == 3


def test_clear_list_asks_about_the_pending_bulk_edit(monkeypatch):
    window = _window(3)
    _select(window, 0, 1)
    _type(window, genre="Horror")
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No
    )
    window.clear_list()
    assert asked and len(window.books) == 3
    assert [book.metadata.genre for book in window.books] == ["Horror", "Horror", ""]


def test_explicit_apply_still_works_and_is_not_applied_twice():
    window = _window(3)
    _select(window, 0, 1)
    _type(window, genre="Horror")
    window._apply_bulk_edit()
    window.table.selectRow(2)
    assert [book.metadata.genre for book in window.books] == ["Horror", "Horror", ""]
    window.undo_last_action()
    assert not window.undo_manager.can_undo()  # one Apply = one undo entry, no second auto one
