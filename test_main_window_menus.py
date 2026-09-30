"""The menu bar follows the shared menu skeleton (redactor_common's
gui/standard_menus.py, section C1 of the family's menu-skeleton proposal):
File, Edit, View, Metadata, Repair, Tools, Help. The former top-level
Collection menu is a "Collection" submenu of Tools. Every action the old
Import / Operations / Settings layout offered is still reachable, and the
toolbar and the right-click menu reuse the same QAction objects."""

import sys

from PyQt6.QtGui import QAction
import zipfile

from PyQt6.QtWidgets import QApplication, QToolBar, QToolButton

import gui.main_window as mw
from gui.main_window import LEGACY_ACTION_KEYS, MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _plain(text):
    return text.replace("&", "")


def _menus(window):
    return {_plain(a.text()): a.menu() for a in window.menuBar().actions() if a.menu()}


def _texts(menu):
    """Plain texts of a menu's entries in order, '---' for a separator."""
    return ["---" if a.isSeparator() else _plain(a.text()) for a in menu.actions()]


def _all_actions(menu):
    for act in menu.actions():
        if act.menu() is not None:
            yield from _all_actions(act.menu())
        elif not act.isSeparator():
            yield act


def test_top_level_menus_follow_the_skeleton():
    window = MainWindow()
    assert list(_menus(window)) == ["File", "Edit", "View", "Metadata", "Repair", "Tools", "Help"]


def test_file_menu_structure():
    menu = _menus(MainWindow())["File"]
    assert _texts(menu) == [
        "Open Files…", "Open Folder…", "---",
        "Save", "Save As…", "Save All", "---",
        "Rename File…", "Undo Last Rename", "Rename / Export / Move…", "---",
        "Export Settings…", "Import Settings…", "---",
        "Remove from List", "Clear List", "---",
        "Exit",
    ]


def test_edit_menu_structure():
    menu = _menus(MainWindow())["Edit"]
    assert _texts(menu) == [
        "Undo", "Redo", "---",
        "Apply to Selected", "---",
        "Redact", "Edit Redact Recipe…", "---",
        "Search and Replace…", "Change Case…", "Auto-Number…",
    ]


def test_view_menu_structure():
    menu = _menus(MainWindow())["View"]
    assert _texts(menu) == [
        "Show Metadata Panel", "---",
        "Zoom In", "Zoom Out", "Reset Zoom", "---",
        "Refresh List", "Command Palette…",
    ]


def test_metadata_menu_structure():
    menu = _menus(MainWindow())["Metadata"]
    assert _texts(menu) == [
        "Parse Filename…", "Read Filename Tags", "---",
        "Look Up", "---",
        "Credit Pages…", "Number Issues…",
    ]
    look_up = next(a for a in menu.actions() if _plain(a.text()) == "Look Up").menu()
    assert _texts(look_up) == [
        "Comic Vine…", "Grand Comics Database…", "GCD Local Database…", "ComicRack Library…", "Bedetheque…",
    ]


def test_repair_menu_structure():
    menu = _menus(MainWindow())["Repair"]
    assert _texts(menu) == [
        "Validate and Fix…", "Resize Images…", "Tag Low-Res Scans", "Remove Credit Pages…",
        "Clean Up Archive Contents…", "Convert to CBZ…", "---", "Find Duplicates…",
    ]


def test_tools_menu_structure_with_the_collection_submenu():
    window = MainWindow()
    menu = _menus(window)["Tools"]
    assert _texts(menu) == [
        "API Keys…", "---",
        "Known Credit Pages…", "Conversion Settings…", "---",
        "GCD Local Database…", "ComicRack Library Database…", "---",
        "Collection", "---",
        "Columns…", "Genres…", "Languages…",
    ]
    collection = next(a for a in menu.actions() if _plain(a.text()) == "Collection").menu()
    assert _texts(collection) == ["Scan Collection Folder…", "Collection Report…", "Compare Library with GCD…"]
    assert window.actions_["scan_collection"] in collection.actions()


def test_help_menu_structure():
    menu = _menus(MainWindow())["Help"]
    assert _texts(menu) == ["Changelog…", "Credits…", "---", "About The ƆBZ Redactor"]


def test_no_top_level_collection_menu_any_more():
    assert "Collection" not in _menus(MainWindow())


def test_every_old_feature_is_still_reachable_from_a_menu():
    """Each action the old menus held is still in some menu (Collection and
    Look Up are submenus now)."""
    window = MainWindow()
    in_menus = {id(a) for menu in _menus(window).values() for a in _all_actions(menu)}
    old_keys = [
        "load_files", "load_folder", "save", "save_as", "save_all", "rename_file", "undo_rename",
        "rename_files", "remove_files", "refresh_list", "clear_list", "exit",
        "parse_filename", "read_filename_tags", "convert_foreign",
        "comicvine_lookup", "gcd_lookup", "gcd_local_lookup", "comicrack_lookup", "compare_with_gcd",
        "bedetheque_lookup", "apply_bulk_edit", "redact", "redact_recipe", "search_replace",
        "case_conversion", "auto_numbering", "validate", "resize_images", "tag_low_res",
        "remove_credit_pages", "clean_contents", "find_duplicates", "undo", "redo",
        "known_credit_pages", "gcd_local_settings", "comicrack_settings", "conversion_settings",
        "column_settings", "genre_settings", "language_settings", "about", "changelog", "credits",
        "scan_collection", "collection_report",
    ]
    # Keys the skeleton renamed resolve through the legacy aliases; the two
    # Comic Vine / GCD dialogs merged into the one API Keys entry.
    renamed = {"undo_rename": "undo_last_rename", "column_settings": "columns",
               "genre_settings": "genres", "language_settings": "languages"}
    missing = []
    for key in old_keys:
        act = window.actions_.get(key) or window.actions_.get(renamed.get(key, key))
        if act is None or id(act) not in in_menus:
            missing.append(key)
    assert missing == []
    assert "api_keys" in window.actions_


def test_legacy_action_keys_alias_the_same_actions():
    window = MainWindow()
    for old, new in LEGACY_ACTION_KEYS.items():
        assert window.actions_[old] is window.actions_[new]


def test_every_action_is_connected_to_a_slot():
    """Each menu action has a receiver on triggered (the checkable panel
    toggle included), so none of them is a dead entry."""
    window = MainWindow()
    for key, act in window.actions_.items():
        assert act.receivers(act.triggered) > 0, key


def _main_toolbar(window):
    return next(t for t in window.findChildren(QToolBar) if t.windowTitle() == "Main")


def test_toolbar_holds_the_c1_actions_in_order():
    window = MainWindow()
    by_action = {id(v): k for k, v in window.actions_.items() if k not in LEGACY_ACTION_KEYS}
    keys = [by_action[id(a)] for a in _main_toolbar(window).actions() if id(a) in by_action]
    assert keys == ["open_files", "open_folder", "save", "save_all", "apply", "redact", "undo", "redo"]


def test_redact_toolbar_button_is_prominent():
    window = MainWindow()
    button = _main_toolbar(window).widgetForAction(window.actions_["redact"])
    assert isinstance(button, QToolButton)
    assert "bold" in button.styleSheet()


def test_show_metadata_panel_follows_the_panel():
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    _app.processEvents()
    act = window.actions_["show_metadata_panel"]
    assert act.isCheckable() and act.isChecked()
    act.trigger()
    _app.processEvents()
    assert window._panel_collapser.is_collapsed()
    assert not act.isChecked()
    window._toggle_panel()  # the toolbar button path
    _app.processEvents()
    assert act.isChecked()


def test_credit_pages_and_number_issues_follow_the_selection(tmp_path):
    window = MainWindow()
    assert not window.actions_["credit_pages"].isEnabled()
    assert not window.actions_["number_issues"].isEnabled()
    paths = []
    for name in ("A.cbz", "B.cbz"):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
            zf.writestr("001.jpg", b"x")
        paths.append(str(path))
    window._load_paths(paths)
    window.table.selectRow(0)
    assert window.actions_["credit_pages"].isEnabled()
    assert window.actions_["number_issues"].isEnabled()
    window.table.selectAll()
    assert not window.actions_["credit_pages"].isEnabled()  # exactly one file only
    assert window.actions_["number_issues"].isEnabled()
    window.table.clearSelection()
    assert not window.actions_["number_issues"].isEnabled()


def _context_items(window, monkeypatch):
    seen = {}

    def fake_show(win, table, pos, get_selected_items, get_path, extra_items=None):
        seen["items"] = extra_items([])

    monkeypatch.setattr(mw, "show_table_context_menu", fake_show)
    window._show_table_context_menu(None)
    return seen["items"]


def test_every_lookup_is_in_the_right_click_menu(monkeypatch):
    window = MainWindow()
    items = _context_items(window, monkeypatch)
    sub = next(i for i in items if isinstance(i, mw.Submenu) and "Look Up" in i.text)
    assert [a.text() for a in sub.items] == [
        window.actions_[k].text() for k in (
            "comicvine_lookup", "gcd_lookup", "gcd_local_lookup", "comicrack_lookup", "bedetheque_lookup"
        )
    ]


def test_right_click_menu_is_the_short_core_plus_submenus(monkeypatch):
    """Redact and Remove from List are the real menu actions; Organize holds
    Rename / Export / Move (and Number Issues / Credit Pages when they apply)."""
    window = MainWindow()
    items = _context_items(window, monkeypatch)
    actions = [i for i in items if isinstance(i, QAction)]
    assert window.actions_["redact"] in actions
    assert window.actions_["remove_from_list"] in actions
    organize = next(i for i in items if isinstance(i, mw.Submenu) and "Organize" in i.text)
    assert window.actions_["rename_export_move"] in organize.items
    # Redact comes before Remove from List, which is last.
    assert actions[-1] is window.actions_["remove_from_list"]
    assert actions.index(window.actions_["redact"]) < actions.index(window.actions_["remove_from_list"])
