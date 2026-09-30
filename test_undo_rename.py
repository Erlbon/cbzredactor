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


def test_multi_save_and_export_run_under_progress(tmp_path, monkeypatch):
    """Review finding M6: multi-selection Save and Export by Pattern loop
    over files, so they go through run_with_progress (cancellable: they
    write to disk)."""
    from gui import main_window as mw

    calls = []
    real = mw.run_with_progress

    def spy(parent, items, step, label, **kwargs):
        items = list(items)
        calls.append((label, len(items), kwargs.get("cancellable")))
        return real(parent, items, step, label, **kwargs)

    monkeypatch.setattr(mw, "run_with_progress", spy)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    a = _cbz(tmp_path / "one.cbz", "Saga", "1")
    b = _cbz(tmp_path / "two.cbz", "Saga", "2")
    window = mw.MainWindow()
    window._load_paths([a, b])
    window.table.selectAll()
    for book in window.books:
        book.dirty = True
    window.save_all_changed()
    assert ("Saving files...", 2, True) in calls

    def export(dialog):
        dialog.pattern_edit.setText("copy %series% %number%")
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RenamePatternDialog, "exec", export)
    monkeypatch.setattr(mw.RenamePatternDialog, "is_export_mode", lambda self: True)
    window._selected_rows = []
    window.open_rename_dialog()
    assert any(label == "Exporting files..." and count == 2 and c is True for label, count, c in calls)
    assert sorted(f for f in os.listdir(tmp_path) if f.endswith(".cbz")) == [
        "copy Saga 1.cbz", "copy Saga 2.cbz", "one.cbz", "two.cbz",
    ]
