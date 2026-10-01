"""The table's right-click menu: which of the menu-bar commands that work on
the selected files it offers, for no / one / many selected files and for a
file still needing conversion; and that it opens from every visible column
(including Size and Ext) and from a row waiting for Convert to CBZ."""

import io
import sys
import tarfile
import zipfile

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu

import gui.main_window as mw
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        zf.writestr("001.jpg", b"x")
    return str(path)


def _cbt(path) -> str:
    with tarfile.open(path, "w") as tf:
        data = b"x"
        info = tarfile.TarInfo("001.jpg")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return str(path)


@pytest.fixture
def window(tmp_path):
    win = MainWindow()
    win.resize(1800, 600)
    win.show()
    win._load_paths([_cbz(tmp_path / "A.cbz"), _cbz(tmp_path / "B.cbz")])
    return win


def _plain(text: str) -> str:
    return text.replace("&", "")


def _layout(items) -> list:
    """Plain texts of the menu rows ('---' separators), submenus as (title, [entries])."""
    out = []
    for item in items:
        if isinstance(item, mw.Separator):
            out.append("---")
        elif isinstance(item, mw.Submenu):
            out.append((_plain(item.text), [_plain(i.text()) if isinstance(i, QAction) else _plain(i.text) for i in item.items]))
        elif isinstance(item, QAction):
            out.append(_plain(item.text()))
        else:
            out.append(_plain(item.text))
    return out


def _items(window, rows):
    window.table.clearSelection()
    window._selected_rows = []
    for row in rows:
        window.table.selectRow(row) if len(rows) == 1 else window.table.selectionModel().select(
            window.table.model().index(row, 0),
            window.table.selectionModel().SelectionFlag.Select | window.table.selectionModel().SelectionFlag.Rows,
        )
    return window._context_menu_items(window._context_menu_books())


REPAIR = ["Validate and Fix…", "Resize Images…", "Tag Low-Res Scans", "Remove Credit Pages…",
          "Clean Up Archive Contents…"]
BULK = ["Search and Replace…", "Change Case…", "Auto-Number…"]
LOOK_UP = ["Comic Vine…", "Grand Comics Database…", "GCD Local Database…", "ComicRack Library…", "Bedetheque…"]


def test_one_file_selected_offers_every_selection_command(window):
    layout = _layout(_items(window, [0]))
    assert layout == [
        "---", "Rename File…", "---",
        ("Look Up", LOOK_UP),
        ("Organize", ["Rename / Export / Move…", "Parse Filename…", "Read Filename Tags",
                      "Number Issues…", "Credit Pages…"]),
        ("Bulk Edit", BULK),
        ("Repair", REPAIR),
        "---", "Redact", "---", "Remove from List",
    ]


def test_several_files_selected_drops_the_single_file_entries(window):
    layout = _layout(_items(window, [0, 1]))
    assert "Rename File…" not in layout
    organize = next(i for i in layout if isinstance(i, tuple) and i[0] == "Organize")
    assert "Credit Pages…" not in organize[1]
    assert "Number Issues…" in organize[1]
    assert ("Repair", REPAIR) in layout and ("Bulk Edit", BULK) in layout
    assert layout[-1] == "Remove from List"


def test_nothing_selected_still_offers_the_all_files_commands(window):
    layout = _layout(_items(window, []))
    organize = next(i for i in layout if isinstance(i, tuple) and i[0] == "Organize")
    # Number Issues / Credit Pages need a real selection; the rest work on all files.
    assert organize[1] == ["Rename / Export / Move…", "Parse Filename…", "Read Filename Tags"]
    assert ("Repair", REPAIR) in layout
    assert "Rename File…" not in layout
    assert not any(isinstance(i, str) and i.startswith("Convert ") for i in layout)


def test_menu_entries_are_the_real_menu_bar_actions(window):
    items = _items(window, [0])
    entries = []
    for item in items:
        if isinstance(item, mw.Submenu):
            entries.extend(i for i in item.items if isinstance(i, QAction))
        elif isinstance(item, QAction):
            entries.append(item)
    keys = {key: window.actions_[key] for key in (
        "rename_file", "redact", "remove_from_list", "rename_export_move", "parse_filename",
        "read_filename_tags", "number_issues", "credit_pages", "search_replace", "change_case",
        "auto_number", "validate", "resize_images", "tag_low_res", "remove_credit_pages",
        "clean_contents", "comicvine_lookup", "bedetheque_lookup",
    )}
    for key, action in keys.items():
        assert any(action is entry for entry in entries), key


def test_whole_library_commands_are_not_in_the_menu(window):
    texts = []

    def walk(items):
        for item in items:
            if isinstance(item, mw.Submenu):
                walk(item.items)
            elif isinstance(item, QAction):
                texts.append(_plain(item.text()))

    walk(_items(window, [0]))
    for left_out in ("Find Duplicates…", "Compare Library with GCD…", "Scan Collection Folder…",
                     "Save As…", "Save All", "Undo", "Redo"):
        assert left_out not in texts


def test_book_needing_conversion_gets_convert_and_no_editing_entries(tmp_path):
    win = MainWindow()
    win._load_paths([_cbt(tmp_path / "old.cbt")])
    assert win.books[0].needs_conversion
    layout = _layout(_items(win, [0]))
    assert layout == [
        "---", "Rename File…", "---",
        ("Organize", ["Rename / Export / Move…"]),
        "---", "Redact", "Convert 1 File(s) to CBZ", "---", "Remove from List",
    ]


def test_mixed_selection_offers_editing_and_convert(tmp_path):
    win = MainWindow()
    win._load_paths([_cbz(tmp_path / "A.cbz"), _cbt(tmp_path / "old.cbt")])
    layout = _layout(_items(win, [0, 1]))
    assert ("Repair", REPAIR) in layout
    assert "Convert 1 File(s) to CBZ" in layout
    assert layout.index("Redact") < layout.index("Convert 1 File(s) to CBZ")


def _open_menu_at(window, x, y):
    shown = []
    original = QMenu.exec
    QMenu.exec = lambda self, *a: shown.append([i.text() for i in self.actions()])
    try:
        window.table.clearSelection()
        window._selected_rows = []
        window._show_table_context_menu(QPoint(x, y))
    finally:
        QMenu.exec = original
    return shown


def test_right_click_opens_the_menu_in_every_visible_column(window):
    _app.processEvents()
    y = window.table.rowViewportPosition(0) + 5
    visible = [k for k in window._column_keys if not window.table.isColumnHidden(window._col_index[k])]
    assert "size" in visible and "ext" in visible
    for key in visible:
        x = window.table.columnViewportPosition(window._col_index[key]) + 3
        shown = _open_menu_at(window, x, y)
        assert shown and "Open in Default App" in shown[0], key


def test_right_click_opens_the_menu_in_every_column_of_an_unconverted_row(tmp_path):
    """The reported bug: with a CBR/CBT/CB7 row under the cursor the shared
    helper got an empty selection (editable rows only) and showed nothing."""
    win = MainWindow()
    win.resize(1800, 600)
    win.show()
    win._load_paths([_cbt(tmp_path / "old.cbt")])
    _app.processEvents()
    y = win.table.rowViewportPosition(0) + 5
    for key in [k for k in win._column_keys if not win.table.isColumnHidden(win._col_index[k])]:
        x = win.table.columnViewportPosition(win._col_index[key]) + 3
        shown = _open_menu_at(win, x, y)
        assert shown, key
        assert any("Convert 1 File(s) to CBZ" in text for text in shown[0]), key
