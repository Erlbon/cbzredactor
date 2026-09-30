"""Window-level tests for Parse Filename's path mode: the dialog gets the
same library root Rename / Export's "Move into folders" keeps, remembers a
root chosen in it, and a '/' pattern fills ComicInfo from the folders."""

import os
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from gui import app_settings

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", b"p")
        zf.writestr("ComicInfo.xml", "<ComicInfo/>")
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


def test_the_dialog_starts_with_the_saved_library_root(window, tmp_path, monkeypatch):
    app_settings.save_library_root(str(tmp_path / "Library"))
    window._load_paths([_cbz(tmp_path / "Library" / "Saga" / "Saga 001.cbz")])
    seen = {}

    def peek(dialog):
        dialog.pattern_edit.setText("%series%/%title% %number%")
        seen["root"] = dialog._library_root
        seen["path_mode"] = dialog.is_path_mode()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(window.mw.ParseFilenameDialog, "exec", peek)
    window.open_parse_filename_dialog()
    assert seen == {"root": str(tmp_path / "Library"), "path_mode": True}


def test_path_pattern_fills_fields_and_the_chosen_root_is_persisted(window, tmp_path, monkeypatch):
    root = tmp_path / "Library"
    a = _cbz(root / "Image" / "Saga" / "Saga 001.cbz")
    b = _cbz(root / "Image" / "Saga" / "Saga 002.cbz")
    window._load_paths([a, b])
    assert app_settings.load_library_root() == ""

    def accept(dialog):
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(root))
        dialog.pattern_edit.setText("%publisher%/%series%/%series% %number%")
        dialog._choose_root()
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(window.mw.ParseFilenameDialog, "exec", accept)
    window.open_parse_filename_dialog()

    assert app_settings.load_library_root() == str(root)
    meta = [book.metadata for book in window.books]
    assert [(m.publisher, m.series, m.number) for m in meta] == [("Image", "Saga", "1"), ("Image", "Saga", "2")]
    assert all(book.dirty for book in window.books)
    assert "%publisher%/%series%/%series% %number%" in app_settings.load_pattern_history()


def test_filename_only_pattern_still_works_without_a_root(window, tmp_path, monkeypatch):
    window._load_paths([_cbz(tmp_path / "Saga 003.cbz")])

    def accept(dialog):
        dialog.pattern_edit.setText("%series% %number%")
        assert not dialog.is_path_mode()
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(window.mw.ParseFilenameDialog, "exec", accept)
    window.open_parse_filename_dialog()
    assert (window.books[0].metadata.series, window.books[0].metadata.number) == ("Saga", "3")
