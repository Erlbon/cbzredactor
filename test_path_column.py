"""The shared Path column (redactor_common.core.path_column), new in this app, in the real MainWindow: directly after
Filename, visible by default, shows the folder, full path in the tooltip and for Ctrl+C, sorts as text (this app
sorts its own list), is moved and un-hidden once for old saved column settings, and follows a rename and a move
into folders."""

import os
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from redactor_common.core.path_column import lint_path_column
from redactor_common.gui.path_column import selected_column_text

from gui import app_settings

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path, series="Saga", number="1", publisher="Image"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", b"p")
        zf.writestr(
            "ComicInfo.xml",
            f"<ComicInfo><Series>{series}</Series><Number>{number}</Number><Publisher>{publisher}</Publisher></ComicInfo>",
        )
    return str(path)


@pytest.fixture
def mw(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "s.ini"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    from gui import main_window

    return main_window


def _col(w, key="path"):
    return w._col_index[key]


def test_path_is_directly_after_filename_and_visible_by_default(mw):
    assert lint_path_column(mw._ALL_COLUMN_KEYS) == []
    w = mw.MainWindow()
    assert lint_path_column(w._column_keys) == []
    assert w.table.horizontalHeaderItem(_col(w)).text() == "Path"
    assert not w.table.isColumnHidden(_col(w))
    assert "path" not in mw.DEFAULT_HIDDEN_COLUMNS
    w.close()


def test_cell_shows_the_folder_and_the_tooltip_the_full_path(mw, tmp_path):
    w = mw.MainWindow()
    path = _cbz(tmp_path / "Sub" / "a.cbz")
    w._load_paths([path])
    item = w.table.item(0, _col(w))
    assert item.text() == str(tmp_path / "Sub") and item.toolTip() == path
    assert w.table.item(0, _col(w, "filename")).text() == "a.cbz"
    w.close()


def test_ctrl_c_text_is_the_full_path(mw, tmp_path):
    # The key handling itself (real Ctrl+C, real clipboard) is tested in redactor_common.
    w = mw.MainWindow()
    path = _cbz(tmp_path / "Sub" / "a.cbz")
    w._load_paths([path])
    assert getattr(w.table, "_cell_copy_filter", None) is not None
    w.table.selectRow(0)
    w.table.setCurrentCell(0, _col(w))
    assert selected_column_text(w.table) == path
    w.close()


def test_sorting_the_path_column_is_natural_text_order_and_toggles(mw, tmp_path):
    w = mw.MainWindow()
    w._load_paths([_cbz(tmp_path / "Vol 10" / "a.cbz"), _cbz(tmp_path / "vol 2" / "b.cbz"),
                   _cbz(tmp_path / "Vol 1" / "c.cbz")])
    w._on_header_clicked(_col(w))
    shown = [os.path.basename(w.table.item(r, _col(w)).text()) for r in range(w.table.rowCount())]
    assert shown == ["Vol 1", "vol 2", "Vol 10"]
    w._on_header_clicked(_col(w))
    shown = [os.path.basename(w.table.item(r, _col(w)).text()) for r in range(w.table.rowCount())]
    assert shown == ["Vol 10", "vol 2", "Vol 1"]
    w.close()


def test_old_saved_order_and_hidden_set_are_migrated_once(mw):
    old_order = [k for k in mw._ALL_COLUMN_KEYS if k != "path"]  # a saved order from before the column existed
    app_settings.save_column_order(old_order)
    app_settings.save_hidden_columns({"series", "path"})  # a saved hidden choice (path hidden by some accident)
    assert not app_settings.path_column_seeded()
    first = mw.MainWindow()
    try:
        assert first._column_keys.index("path") == first._column_keys.index("filename") + 1
        assert not first.table.isColumnHidden(_col(first))
        assert first.table.isColumnHidden(_col(first, "series"))  # the other choices stay
        assert app_settings.path_column_seeded()
        first._on_column_visibility_toggled("path", False)  # the user hides it again
    finally:
        first.close()
    second = mw.MainWindow()
    try:
        assert second.table.isColumnHidden(_col(second))  # now the user's choice wins
        assert second._column_keys.index("path") == second._column_keys.index("filename") + 1
    finally:
        second.close()


def test_without_a_saved_order_the_default_has_the_column_in_place(mw):
    w = mw.MainWindow()
    assert app_settings.load_column_order() == []  # nothing was invented for a fresh install
    assert w._column_keys.index("path") == w._column_keys.index("filename") + 1
    w.close()


def test_a_path_cell_follows_a_rename(mw, tmp_path, monkeypatch):
    w = mw.MainWindow()
    w._load_paths([_cbz(tmp_path / "a.cbz")])
    book = w.books[0]
    new_dir = tmp_path / "renamed"
    new_dir.mkdir()
    new_path = str(new_dir / "z.cbz")

    def fake_prompt(parent, old, setter, log=None):
        setter(new_path)
        return True

    monkeypatch.setattr(mw, "prompt_rename_single_file", fake_prompt)
    w.rename_single_file(book)
    item = w.table.item(0, _col(w))
    assert item.text() == str(new_dir) and item.toolTip() == new_path
    w.close()


def test_a_path_cell_follows_a_move_into_folders(mw, tmp_path, monkeypatch):
    root = tmp_path / "Library"
    root.mkdir()
    w = mw.MainWindow()
    w._load_paths([_cbz(tmp_path / "scene.one.cbz")])

    def accept(dialog):
        dialog.move_radio.setChecked(True)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(root))
        dialog._choose_root()
        dialog.pattern_edit.setText("%publisher%/%series%/%series% %number%")
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RenamePatternDialog, "exec", accept)
    w.open_rename_dialog()
    moved = root / "Image" / "Saga" / "Saga 1.cbz"
    item = w.table.item(0, _col(w))
    assert item.text() == str(moved.parent) and item.toolTip() == str(moved)
    w.close()
