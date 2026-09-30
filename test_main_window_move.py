"""Window-level tests for the Rename / Export dialog's third mode, "Move into
folders": the library root is remembered in the settings, the planned moves
run through redactor_common's runner, the rows follow the files, and File >
Undo Last Rename puts everything back (the log is redirected to a temporary
folder by conftest.py). Rename and Export keep working as before."""

import os
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from gui import app_settings

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path, series, number, publisher="Image"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", b"p")
        zf.writestr(
            "ComicInfo.xml",
            f"<ComicInfo><Series>{series}</Series><Number>{number}</Number><Publisher>{publisher}</Publisher></ComicInfo>",
        )
    return str(path)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "s.ini"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    from gui import main_window as mw

    w = mw.MainWindow()
    w.mw = mw
    return w


def _choose(monkeypatch, mw, pattern, move_root=None):
    """Drive the dialog: pick the mode, type the pattern, accept."""

    def accept(dialog):
        if move_root is not None:
            dialog.move_radio.setChecked(True)
            monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: move_root)
            dialog._choose_root()
        dialog.pattern_edit.setText(pattern)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RenamePatternDialog, "exec", accept)


def test_move_into_folders_moves_files_and_updates_rows(window, tmp_path, monkeypatch):
    root = tmp_path / "Library"
    root.mkdir()
    a = _cbz(tmp_path / "scene.one.cbz", "Saga", "1")
    b = _cbz(tmp_path / "scene.two.cbz", "Saga", "2", publisher="Other")
    window._load_paths([a, b])
    _choose(monkeypatch, window.mw, "%publisher%/%series%/%series% %number%", str(root))
    window.open_rename_dialog()

    first, second = root / "Image" / "Saga" / "Saga 1.cbz", root / "Other" / "Saga" / "Saga 2.cbz"
    assert first.exists() and second.exists() and not os.path.exists(a) and not os.path.exists(b)
    assert [book.path for book in window.books] == [str(first), str(second)]
    assert window.table.item(0, window._col_index["filename"]).text() == "Saga 1.cbz"
    assert app_settings.load_library_root() == str(root)


def test_the_library_root_is_remembered_by_the_next_dialog(window, tmp_path, monkeypatch):
    root = tmp_path / "Library"
    root.mkdir()
    window._load_paths([_cbz(tmp_path / "scene.one.cbz", "Saga", "1")])
    _choose(monkeypatch, window.mw, "%series%/%series% %number%", str(root))
    window.open_rename_dialog()

    seen = {}

    def peek(dialog):
        seen["root"] = dialog.library_root()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(window.mw.RenamePatternDialog, "exec", peek)
    window.open_rename_dialog()
    assert seen["root"] == str(root)


def test_move_is_logged_and_undo_puts_files_and_rows_back(window, tmp_path, monkeypatch):
    root = tmp_path / "Library"
    root.mkdir()
    a = _cbz(tmp_path / "scene.one.cbz", "Saga", "1")
    window._load_paths([a])
    _choose(monkeypatch, window.mw, "%series%/%series% %number%", str(root))
    window.open_rename_dialog()
    moved = root / "Saga" / "Saga 1.cbz"
    assert moved.exists()

    batch = window.mw._rename_log().last_batch()
    assert batch.label == "Move into Folders" and batch.renames == [(a, str(moved))]
    assert batch.root == str(root) and batch.created_dirs == [str(root / "Saga")]

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.undo_last_rename()
    assert os.path.exists(a) and not moved.exists()
    assert window.books[0].path == a
    assert window.table.item(0, window._col_index["filename"]).text() == "scene.one.cbz"


def test_a_move_never_overwrites_an_existing_file(window, tmp_path, monkeypatch):
    root = tmp_path / "Library"
    (root / "Saga").mkdir(parents=True)
    existing = _cbz(root / "Saga" / "Saga 1.cbz", "Saga", "1")
    before = open(existing, "rb").read()
    window._load_paths([_cbz(tmp_path / "scene.one.cbz", "Saga", "1")])
    _choose(monkeypatch, window.mw, "%series%/%series% %number%", str(root))
    window.open_rename_dialog()
    assert open(existing, "rb").read() == before
    assert window.books[0].path == str(root / "Saga" / "Saga 1 (2).cbz")


def test_plain_rename_does_not_use_the_move_runner(window, tmp_path, monkeypatch):
    a = _cbz(tmp_path / "scene.one.cbz", "Saga", "1")
    window._load_paths([a])
    called = []
    monkeypatch.setattr(window, "_move_into_folders", lambda planned: called.append(planned))
    _choose(monkeypatch, window.mw, "%series% %number%")
    window.open_rename_dialog()
    assert called == [] and os.path.exists(tmp_path / "Saga 1.cbz")
    assert window.books[0].path == str(tmp_path / "Saga 1.cbz")
