"""Right-click > Save Selected (N): saves only the selected files that have unsaved changes, through the same
path as File > Save All; greyed when none of the selection is changed; a failing file does not block the others."""

import sys
import zipfile

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox

from core.cbz_file import CbzBook, CbzError
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path, title="T") -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", f"<ComicInfo><Title>{title}</Title></ComicInfo>")
        zf.writestr("001.jpg", b"x")
    return str(path)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    win = MainWindow()
    win.resize(1800, 600)
    win.show()
    win._load_paths([_cbz(tmp_path / n) for n in ("A.cbz", "B.cbz", "C.cbz")])
    yield win
    for book in win.books:
        book.dirty = False
    win.close()


def _select(window, rows):
    window.table.clearSelection()
    window._selected_rows = []
    sm = window.table.selectionModel()
    for row in rows:
        sm.select(window.table.model().index(row, 0), sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
    _app.processEvents()


def _menu(window, rows):
    _select(window, rows)
    shown = []
    original = QMenu.exec
    QMenu.exec = lambda self, *a: shown.append([(i.text(), i.isEnabled(), i.toolTip()) for i in self.actions()])
    try:
        pos = QPoint(5, window.table.rowViewportPosition(rows[0]) + 5)
        window._show_table_context_menu(pos)
    finally:
        QMenu.exec = original
    return shown[0]


def _save_item(entries):
    return next(e for e in entries if e[0].startswith("Save Selected"))


def _dirty(window, row, title):
    window.books[row].metadata.title = title
    window.books[row].dirty = True


def _on_disk(path):
    return CbzBook(str(path)).metadata.title


def test_count_counts_only_changed_selected_files(window):
    _dirty(window, 0, "New A")
    _dirty(window, 2, "New C")
    entries = _menu(window, [0, 1])
    assert _save_item(entries)[:2] == ("Save Selected (1)", True)


def test_saves_only_the_selected_changed_files(window, tmp_path):
    _dirty(window, 0, "New A")
    _dirty(window, 1, "New B")
    _dirty(window, 2, "New C")
    _select(window, [0, 1])
    window.save_selected_books([window.books[0], window.books[1]])
    assert _on_disk(tmp_path / "A.cbz") == "New A"
    assert _on_disk(tmp_path / "B.cbz") == "New B"
    assert _on_disk(tmp_path / "C.cbz") == "T"  # not selected: untouched on disk
    assert not window.books[0].dirty and not window.books[1].dirty
    assert window.books[2].dirty  # and still changed in the list


def test_menu_item_triggers_the_save(window, tmp_path):
    _dirty(window, 0, "New A")
    _dirty(window, 2, "New C")
    _select(window, [0, 1])
    actions = []
    original = QMenu.exec
    QMenu.exec = lambda self, *a: actions.extend(self.actions())
    try:
        window._show_table_context_menu(QPoint(5, window.table.rowViewportPosition(0) + 5))
    finally:
        QMenu.exec = original
    next(a for a in actions if a.text().startswith("Save Selected")).trigger()
    assert _on_disk(tmp_path / "A.cbz") == "New A"
    assert _on_disk(tmp_path / "C.cbz") == "T"


def test_greyed_when_nothing_selected_is_changed(window):
    _dirty(window, 2, "New C")
    item = _save_item(_menu(window, [0, 1]))
    assert item[0] == "Save Selected (0)" and not item[1]
    assert item[2] == "The selected files have no unsaved changes"


def test_a_failing_file_does_not_block_the_others_and_shows_the_failure_box(window, tmp_path, monkeypatch):
    for row in range(3):
        _dirty(window, row, f"New {row}")
    real_save = CbzBook.save

    def flaky(self, *a, **k):
        if self.path.endswith("B.cbz"):
            raise CbzError("disk says no")
        return real_save(self, *a, **k)

    monkeypatch.setattr(CbzBook, "save", flaky)
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: boxes.append((title, text))))
    window.save_selected_books([window.books[0], window.books[1], window.books[2]])
    assert _on_disk(tmp_path / "A.cbz") == "New 0"
    assert _on_disk(tmp_path / "C.cbz") == "New 2"
    assert _on_disk(tmp_path / "B.cbz") == "T"
    assert window.books[1].dirty
    assert len(boxes) == 1 and boxes[0][0] == "Some Files Failed to Save"
    assert "B.cbz" in boxes[0][1] and "disk says no" in boxes[0][1]
