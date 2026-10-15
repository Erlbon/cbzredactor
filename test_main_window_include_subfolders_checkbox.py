"""The toolbar's Include Subfolders checkbox: right after Open Folder, mirrors the File menu
action both ways, one saved setting, and it changes what Open Folder loads."""

import sys
import zipfile

from PyQt6.QtWidgets import QApplication, QCheckBox, QFileDialog, QMessageBox

from gui import app_settings
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _toolbar(window):
    from PyQt6.QtWidgets import QToolBar

    return window.findChildren(QToolBar)[0]


def test_checkbox_sits_right_after_open_folder():
    window = MainWindow()
    toolbar = _toolbar(window)
    items = toolbar.actions()
    open_folder = window.actions_["open_folder"]
    after = items[items.index(open_folder) + 1]
    assert toolbar.widgetForAction(after) is window.include_subfolders_box
    assert isinstance(window.include_subfolders_box, QCheckBox)


def test_checkbox_and_menu_action_stay_in_step_with_one_saved_setting():
    app_settings.save_include_subfolders(False)
    window = MainWindow()
    box, action = window.include_subfolders_box, window.actions_["include_subfolders"]
    assert not box.isChecked() and not action.isChecked()
    box.setChecked(True)
    assert action.isChecked() and app_settings.load_include_subfolders()
    action.setChecked(False)
    assert not box.isChecked() and not app_settings.load_include_subfolders()


def test_checkbox_decides_whether_open_folder_finds_a_comic_in_a_subfolder(tmp_path, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()
    with zipfile.ZipFile(sub / "a.cbz", "w") as z:
        z.writestr("001.png", b"x")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    window = MainWindow()
    loaded = []
    monkeypatch.setattr(window, "_load_paths", lambda paths: loaded.extend(paths))
    window.include_subfolders_box.setChecked(False)
    window.load_folder_dialog()
    assert loaded == [] and shown == ["No Files Found"]
    window.include_subfolders_box.setChecked(True)
    window.load_folder_dialog()
    assert len(loaded) == 1 and loaded[0].endswith("a.cbz")
