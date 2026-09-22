"""Tests for gui/main_window.py's open_rename_dialog() wiring onto
redactor_common's RenamePatternDialog -- specifically the two padding
rules this project needs (see CHANGELOG.md):

- %month% must always render as 2 digits ("02", not "2"), regardless
  of the zero-pad checkbox -- wired via `always_pad_fields={"month": 2}`.
- %number% zero-padding is opt-in (the checkbox) but its width (2, 3
  or 4 digits) is user-selectable -- wired via `zero_pad_field="number"`.

This only exercises the dialog with the same keyword arguments
open_rename_dialog() passes; RenamePatternDialog's own padding logic is
covered in redactor_common's test suite."""

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from redactor_common.gui.rename_pattern_dialog import RenamePatternDialog

_app = QApplication.instance() or QApplication(sys.argv)

PLACEHOLDERS = [("series", "Series"), ("number", "Number"), ("month", "Month"), ("title", "Title")]


class _FakeBook:
    def __init__(self, path: str, values: dict[str, str]):
        self.path = path
        self.values = values


def _make_dialog(books: list[_FakeBook]) -> RenamePatternDialog:
    return RenamePatternDialog(
        books,
        PLACEHOLDERS,
        get_values=lambda book: book.values,
        get_current_path=lambda book: book.path,
        pattern_history=[],
        default_pattern="%series% %number% (%month%) - %title%",
        title="Rename / Export by Metadata Pattern",
        item_noun="file",
        zero_pad_field="number",
        always_pad_fields={"month": 2},
    )


def test_month_is_always_padded_even_with_number_padding_off(tmp_path):
    book = _FakeBook(
        str(tmp_path / "Amazing Spider-Man 012.cbz"),
        {"series": "Amazing Spider-Man", "number": "12", "month": "2", "title": "Nothing Can Stop"},
    )
    dialog = _make_dialog([book])
    assert dialog.zero_pad_cb.isChecked() is False  # off by default
    new_name = dialog.preview_table.item(0, 1).text()
    assert "(02)" in new_name
    assert "(2)" not in new_name


def test_number_padding_defaults_to_2_digits_when_enabled(tmp_path):
    book = _FakeBook(
        str(tmp_path / "book.cbz"),
        {"series": "Saga", "number": "7", "month": "1", "title": "Chapter One"},
    )
    dialog = _make_dialog([book])
    dialog.zero_pad_cb.setChecked(True)
    new_name = dialog.preview_table.item(0, 1).text()
    assert "Saga 07 " in new_name


@pytest.mark.parametrize("width, expected", [(2, "07"), (3, "007"), (4, "0007")])
def test_number_padding_width_is_selectable(tmp_path, width, expected):
    book = _FakeBook(
        str(tmp_path / "book.cbz"),
        {"series": "Saga", "number": "7", "month": "1", "title": "Chapter One"},
    )
    dialog = _make_dialog([book])
    dialog.zero_pad_cb.setChecked(True)
    index = dialog.zero_pad_width_combo.findData(width)
    assert index != -1
    dialog.zero_pad_width_combo.setCurrentIndex(index)
    new_name = dialog.preview_table.item(0, 1).text()
    assert f"Saga {expected} " in new_name


def test_number_padding_off_leaves_number_unpadded(tmp_path):
    book = _FakeBook(
        str(tmp_path / "book.cbz"),
        {"series": "Saga", "number": "7", "month": "1", "title": "Chapter One"},
    )
    dialog = _make_dialog([book])
    new_name = dialog.preview_table.item(0, 1).text()
    assert "Saga 7 " in new_name
