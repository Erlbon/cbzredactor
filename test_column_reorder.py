"""Tools > Columns... reorders the table's columns (redactor_common's ColumnSettingsDialog + apply_column_order),
in the real MainWindow: the dialog lists the table's CURRENT visual order (a header drag included), the new order is
applied to the header and persisted by key, survives a second window and Export/Import Settings, and the hidden
set and widths are kept."""

import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

import gui.main_window as mw
from gui import app_settings
from gui.settings_adapter import CbzSettingsAdapter
from redactor_common.core import settings_bundle as sb
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog

_app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def temp_ini(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "settings.ini"))


def _visual(window):
    header = window.table.horizontalHeader()
    return [window._column_keys[header.logicalIndex(v)] for v in range(header.count())]


def _run_dialog(monkeypatch, window, edit):
    """Open Tools > Columns... with exec patched: `edit(dialog)` plays the user."""
    seen = {}

    def fake_exec(self):
        seen["order"] = self.column_order()
        seen["dialog"] = self
        edit(self)
        return 1

    monkeypatch.setattr(ColumnSettingsDialog, "exec", fake_exec)
    window.open_column_settings_dialog()
    return seen


def _select(dialog, key):
    dialog.list_widget.setCurrentRow(dialog.column_order().index(key))


def test_dialog_lists_visual_order_including_a_header_drag(monkeypatch):
    window = mw.MainWindow()
    try:
        header = window.table.horizontalHeader()
        header.moveSection(header.visualIndex(window._col_index["series"]), 3)  # the user drags a header
        expected = _visual(window)
        seen = _run_dialog(monkeypatch, window, lambda d: None)
        assert seen["order"] == expected
        assert seen["order"] != window._column_keys  # the logical order is NOT what is shown
    finally:
        window.close()


def test_move_in_dialog_reorders_table_and_persists(monkeypatch):
    window = mw.MainWindow()
    try:
        window.table.setColumnHidden(window._col_index["genre"], True)
        header = window.table.horizontalHeader()
        header.resizeSection(window._col_index["series"], 222)
        before = _visual(window)
        target = "series"

        def edit(dialog):
            _select(dialog, target)
            for _ in range(2):
                dialog.up_button.click()

        _run_dialog(monkeypatch, window, edit)
        after = _visual(window)
        expected = list(before)
        expected.remove(target)
        expected.insert(before.index(target) - 2, target)
        assert after == expected
        assert app_settings.load_column_order() == expected
        assert window.table.isColumnHidden(window._col_index["genre"])  # hidden state kept
        assert header.sectionSize(window._col_index["series"]) == 222  # width kept
    finally:
        window.close()
    second = mw.MainWindow()
    try:
        assert _visual(second) == expected  # same order in a new window (restored by key)
    finally:
        second.close()


def test_header_drag_then_dialog_shows_it_and_unchecking_still_works(monkeypatch):
    window = mw.MainWindow()
    try:
        header = window.table.horizontalHeader()
        header.moveSection(header.visualIndex(window._col_index["title"]), 1)
        assert app_settings.load_column_order() == _visual(window)  # header drag persisted
        order_after_drag = _visual(window)

        def edit(dialog):
            for i in range(dialog.list_widget.count()):
                if dialog.list_widget.item(i).data(Qt.ItemDataRole.UserRole) == "genre":
                    dialog.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

        _run_dialog(monkeypatch, window, edit)
        assert _visual(window) == order_after_drag  # nothing moved
        assert window.table.isColumnHidden(window._col_index["genre"])
    finally:
        window.close()


def test_order_travels_through_export_import():
    window = mw.MainWindow()
    try:
        header = window.table.horizontalHeader()
        header.moveSection(header.visualIndex(window._col_index["title"]), 1)
        wanted = _visual(window)
        adapter = CbzSettingsAdapter("x")
        keys = {s.key for s in adapter.sections() if s.portable}
        bundle = sb.parse_bundle(sb.dump_bundle(sb.build_bundle(adapter, keys)), "cbzredactor")
        app_settings.save_column_order([])
        header.moveSection(header.visualIndex(window._col_index["title"]), 6)
        sb.apply_bundle(adapter, bundle, keys)
        window._on_settings_imported()
        assert _visual(window) == wanted
    finally:
        window.close()
