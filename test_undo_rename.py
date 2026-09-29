"""Window-level test: Rename by Pattern, then File > Undo Last Rename puts
the files -- and the app's own list -- back (redactor_common's rename log;
the log itself is redirected to a temporary folder by conftest.py)."""

import os
import sys
import zipfile

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path, series, number):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", b"p")
        zf.writestr("ComicInfo.xml", f"<ComicInfo><Series>{series}</Series><Number>{number}</Number></ComicInfo>")
    return str(path)


def test_rename_by_pattern_then_undo(tmp_path, monkeypatch):
    from gui import main_window as mw

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    a = _cbz(tmp_path / "scene.name.one.cbz", "Saga", "1")
    b = _cbz(tmp_path / "scene.name.two.cbz", "Saga", "2")
    window = mw.MainWindow()
    window._load_paths([a, b])
    window._selected_rows = []

    def accept_pattern(dialog):
        dialog.pattern_edit.setText("%series% %number%")
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RenamePatternDialog, "exec", accept_pattern)
    window.open_rename_dialog()
    assert sorted(f for f in os.listdir(tmp_path) if f.endswith(".cbz")) == ["Saga 1.cbz", "Saga 2.cbz"]

    window.undo_last_rename()
    assert sorted(f for f in os.listdir(tmp_path) if f.endswith(".cbz")) == ["scene.name.one.cbz", "scene.name.two.cbz"]
    assert sorted(os.path.basename(book.path) for book in window.books) == ["scene.name.one.cbz", "scene.name.two.cbz"]
