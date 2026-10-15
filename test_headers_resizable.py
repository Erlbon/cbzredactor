"""House rule (owner, 2026-10-15): every table column can be resized by dragging.

(The repo keeps its tests next to the sources, so this file is at the root, not in tests/.)"""

import sys
import zipfile
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QHeaderView, QTableWidget
from redactor_common.gui.table_columns import lint_resizable_headers

from core.cbz_file import CbzBook
from core.comicinfo_check import Finding

_app = QApplication.instance() or QApplication(sys.argv)
ROOT = Path(__file__).parent


def _book(tmp_path, name="Some Comic 001.cbz", extra=()):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo><Series> Bad </Series></ComicInfo>")
        zf.writestr("001.png", b"x")
        for entry, data in extra:
            zf.writestr(entry, data)
    return CbzBook(str(path))


def _assert_draggable(table: QTableWidget) -> None:
    header = table.horizontalHeader()
    assert header.count() >= 2
    for section in range(header.count() - 1):
        assert header.sectionResizeMode(section) == QHeaderView.ResizeMode.Interactive, section
    before = header.sectionSize(0)
    header.resizeSection(0, before + 37)
    assert header.sectionSize(0) == before + 37


def test_no_undraggable_header_modes_in_gui():
    assert lint_resizable_headers(ROOT / "gui") == []


def test_validate_fix_dialog_columns_draggable(tmp_path):
    from gui.validate_fix_dialog import ValidateFixDialog

    book = _book(tmp_path)
    dialog = ValidateFixDialog([(book, Finding("series", "Series", "has stray spaces", "Bad"))])
    _assert_draggable(dialog.table)


def test_archive_cleanup_dialog_columns_draggable(tmp_path):
    from core.archive_contents import CleanupOptions
    from gui.archive_cleanup_dialog import ArchiveCleanupDialog

    book = _book(tmp_path, extra=[("Thumbs.db", b"x")])
    dialog = ArchiveCleanupDialog([(book, book.entry_names())], CleanupOptions(), "", None)
    assert dialog.table.rowCount() == 1
    _assert_draggable(dialog.table)
    dragged = dialog.table.horizontalHeader().sectionSize(0)
    dialog.checkboxes["remove_junk"].setChecked(False)
    dialog.checkboxes["remove_junk"].setChecked(True)  # a refill must not undo the user's width
    assert dialog.table.horizontalHeader().sectionSize(0) == dragged


def test_comicvine_browse_dialog_columns_draggable(tmp_path, monkeypatch):
    import gui.comicvine_browse_dialog as dlg_mod
    from core.comicvine_browse import ComicVineVolume

    volumes = [ComicVineVolume(volume_id="1", name="Batman", start_year="2016", issue_count=140,
                               publisher="DC Comics", image_url="", detail_url="")]
    monkeypatch.setattr("gui.app_settings.load_comicvine_api_key", lambda: "KEY")
    monkeypatch.setattr(dlg_mod, "run_busy", lambda parent, label, fn, *a, **k: fn(*a))
    monkeypatch.setattr(dlg_mod, "search_volumes", lambda *a, **k: list(volumes))
    dialog = dlg_mod.ComicVineBrowseDialog([_book(tmp_path)])
    assert dialog.volume_table.rowCount() == 1
    _assert_draggable(dialog.volume_table)
    _assert_draggable(dialog.issue_table)


def test_main_window_table_columns_draggable():
    from gui.main_window import MainWindow

    window = MainWindow()
    _assert_draggable(window.table)
