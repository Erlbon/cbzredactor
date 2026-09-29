"""The menu bar actually shows every menu main_window.py defines. The
Collection menu (Scan Collection Folder..., Collection Report...)
shipped in 2026-09-29#06 but never appeared: it was a key in the specs
dict, which build_menu_bar() ignored, so neither action was reachable."""

import sys

from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _menus(window):
    return {a.text(): a.menu() for a in window.menuBar().actions() if a.menu()}


def test_collection_menu_sits_between_operations_and_settings():
    window = MainWindow()
    assert list(_menus(window)) == ["&File", "&Import", "&Operations", "&Collection", "&Settings", "&Help"]


def test_collection_menu_holds_the_scan_and_the_report():
    window = MainWindow()
    items = [a.text().replace("&", "") for a in _menus(window)["&Collection"].actions()]
    assert items == ["Scan Collection Folder...", "Collection Report..."]
    assert window.actions_["scan_collection"] in _menus(window)["&Collection"].actions()
