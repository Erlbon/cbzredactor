"""Auto-Number opens on Number, not Title (2026-11-25: "title" was a
bewildering default; the comic's "track" is its issue Number)."""

import sys

from PyQt6.QtWidgets import QApplication

import gui.main_window as main_window_module
from core.cbz_file import CbzBook
from core.comicinfo import ComicInfoMetadata
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _fake_book(name: str) -> CbzBook:
    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = f"{name}.cbz"
    book.metadata = ComicInfoMetadata()
    book.page_names = []
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    return book


def test_auto_number_dialog_starts_on_number(monkeypatch):
    window = MainWindow()
    for i in range(3):
        book = _fake_book(f"f{i}")
        window.books.append(book)
        window._add_table_row(book)
    window._show_idle_panel()

    seen = {}

    def fake_exec(dialog):
        seen["field"] = dialog.field_combo.currentData()
        seen["labels"] = [dialog.field_combo.itemText(i) for i in range(dialog.field_combo.count())]
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(main_window_module.AutoNumberingDialog, "exec", fake_exec)
    window.open_auto_numbering_dialog()

    assert seen["field"] == "number"
    assert seen["labels"][0] == "Number"
    assert "Title" in seen["labels"]  # still selectable, just not the default
    assert seen["labels"].count("Number") == 1
