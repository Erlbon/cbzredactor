"""Tools > Preferences...: the shared dialog over gui/app_settings.py's
existing storage. The ini keys must not change (Export/Import Settings and
older installs depend on them) and secrets must never appear."""

import sys

import pytest
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication

from gui import app_settings, preferences
from gui.main_window import MainWindow
from gui.settings_adapter import CbzSettingsAdapter
from redactor_common.core import preferences as core_prefs
from redactor_common.gui.preferences_dialog import PreferencesDialog

_app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def _ini(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "s.ini"))


def _dialog():
    return preferences.make_dialog()


def test_sections_are_valid_and_have_the_expected_pages():
    sections = preferences.preference_sections()
    core_prefs.validate_sections(sections)
    assert [s.title for s in sections] == ["Filenames", "Conversion", "Resize defaults", "Paths"]
    keys = {spec.key for s in sections for spec in s.specs}
    assert not any("key" in k or "password" in k for k in keys)  # secrets stay in API Keys
    zero_width = next(sp for s in sections for sp in s.specs if sp.key == core_prefs.KEY_ZERO_PAD_WIDTH)
    assert zero_width.maximum == 10


def test_fresh_install_shows_the_defaults():
    dlg = _dialog()
    assert dlg.value(core_prefs.KEY_ASCII_FILENAMES) is False
    assert dlg.value(core_prefs.KEY_ZERO_PAD_WIDTH) == 2
    assert dlg.value(preferences.KEY_ON_LOAD) == app_settings.FOREIGN_LOAD_UNCONVERTED
    assert dlg.value(preferences.KEY_RESIZE_MAX_WIDTH) == app_settings._DEFAULT_RESIZE_MAX_WIDTH
    assert dlg.value(preferences.KEY_RESIZE_OUTPUT_FORMAT) == ""
    assert dlg.changed_values() == {}


def test_existing_settings_are_shown():
    app_settings.save_ascii_filenames(True)
    app_settings.save_rename_zero_pad(True, 4)
    app_settings.save_auto_number_padding(3)
    app_settings.save_foreign_load_behavior(app_settings.FOREIGN_LOAD_ASK)
    app_settings.save_recycle_originals(True)
    app_settings.save_resize_max_width(1920)
    app_settings.save_resize_max_height(2500)
    app_settings.save_resize_jpeg_quality(75)
    app_settings.save_resize_output_format("WEBP")
    app_settings.save_gcd_local_database("C:/db/gcd.db")
    app_settings.save_comicrack_database("C:/db/cr.db")
    app_settings.save_library_root("C:/Comics")
    dlg = _dialog()
    assert dlg.value(core_prefs.KEY_ASCII_FILENAMES) is True
    assert dlg.value(core_prefs.KEY_ZERO_PAD_NUMBERS) is True
    assert dlg.value(core_prefs.KEY_ZERO_PAD_WIDTH) == 4
    assert dlg.value(core_prefs.KEY_AUTO_NUMBER_PADDING) == 3
    assert dlg.value(preferences.KEY_ON_LOAD) == "ask"
    assert dlg.value(preferences.KEY_RECYCLE_ORIGINALS) is True
    assert dlg.value(preferences.KEY_RESIZE_MAX_WIDTH) == 1920
    assert dlg.value(preferences.KEY_RESIZE_MAX_HEIGHT) == 2500
    assert dlg.value(preferences.KEY_RESIZE_JPEG_QUALITY) == 75
    assert dlg.value(preferences.KEY_RESIZE_OUTPUT_FORMAT) == "WEBP"
    assert dlg.value(preferences.KEY_GCD_LOCAL_DB) == "C:/db/gcd.db"
    assert dlg.value(preferences.KEY_COMICRACK_DB) == "C:/db/cr.db"
    assert dlg.value(preferences.KEY_LIBRARY_ROOT) == "C:/Comics"
    assert dlg.changed_values() == {}


def test_ok_writes_through_the_existing_storage_functions():
    dlg = _dialog()
    dlg.set_value(core_prefs.KEY_ASCII_FILENAMES, True)
    dlg.set_value(core_prefs.KEY_ZERO_PAD_NUMBERS, True)
    dlg.set_value(core_prefs.KEY_ZERO_PAD_WIDTH, 10)
    dlg.set_value(core_prefs.KEY_AUTO_NUMBER_PADDING, 5)
    dlg.set_value(preferences.KEY_ON_LOAD, "convert")
    dlg.set_value(preferences.KEY_RECYCLE_ORIGINALS, True)
    dlg.set_value(preferences.KEY_RESIZE_MAX_WIDTH, 2560)
    dlg.set_value(preferences.KEY_RESIZE_MAX_HEIGHT, 3000)
    dlg.set_value(preferences.KEY_RESIZE_JPEG_QUALITY, 80)
    dlg.set_value(preferences.KEY_RESIZE_OUTPUT_FORMAT, "JPEG")
    dlg.set_value(preferences.KEY_LIBRARY_ROOT, "D:/Lib")
    dlg.accept()
    assert app_settings.load_ascii_filenames() is True
    assert app_settings.load_rename_zero_pad() == (True, 10)
    assert app_settings.load_auto_number_padding() == 5
    assert app_settings.load_foreign_load_behavior() == "convert"
    assert app_settings.load_recycle_originals() is True
    assert app_settings.load_resize_max_width() == 2560
    assert app_settings.load_resize_max_height() == 3000
    assert app_settings.load_resize_jpeg_quality() == 80
    assert app_settings.load_resize_output_format() == "JPEG"
    assert app_settings.load_library_root() == "D:/Lib"


def test_changing_only_the_zero_pad_width_keeps_the_enabled_flag():
    app_settings.save_rename_zero_pad(True, 2)
    dlg = _dialog()
    dlg.set_value(core_prefs.KEY_ZERO_PAD_WIDTH, 6)
    dlg.accept()
    assert app_settings.load_rename_zero_pad() == (True, 6)


def test_cancel_writes_nothing():
    dlg = _dialog()
    dlg.set_value(preferences.KEY_RESIZE_JPEG_QUALITY, 60)
    dlg.reject()
    assert app_settings.load_resize_jpeg_quality() == 90


def test_unused_settings_are_not_written():
    dlg = _dialog()
    dlg.set_value(core_prefs.KEY_ASCII_FILENAMES, True)
    dlg.accept()
    ini = app_settings._settings()
    assert ini.contains("rename/ascii_only")
    assert not ini.contains("resize/max_width")
    assert not ini.contains("conversion/on_load")


def test_a_garbled_ini_value_falls_back_to_the_default():
    app_settings._settings().setValue("resize/max_width", "banana")
    app_settings._settings().setValue("rename/zero_pad_width", "99")
    dlg = _dialog()
    assert dlg.value(preferences.KEY_RESIZE_MAX_WIDTH) == app_settings._DEFAULT_RESIZE_MAX_WIDTH
    assert dlg.value(core_prefs.KEY_ZERO_PAD_WIDTH) == 2


def test_export_import_bundle_sees_what_the_dialog_wrote():
    dlg = _dialog()
    dlg.set_value(core_prefs.KEY_ZERO_PAD_NUMBERS, True)
    dlg.set_value(preferences.KEY_RESIZE_OUTPUT_FORMAT, "WEBP")
    dlg.accept()
    adapter = CbzSettingsAdapter("x")
    data = {s.key: adapter.read_section(s.key) for s in adapter.sections()}
    assert data["rename_defaults"]["zero_pad_enabled"] is True
    assert data["resize"]["output_format"] == "WEBP"


def test_tools_menu_has_preferences_on_ctrl_comma():
    window = MainWindow()
    action = window.actions_["preferences"]
    assert action.shortcut() == QKeySequence("Ctrl+,")
    tools = next(a for a in window.menuBar().actions() if a.text().replace("&", "") == "Tools").menu()
    assert action in tools.actions()
    assert action.menuRole() == type(action).MenuRole.PreferencesRole


def test_menu_entry_opens_the_dialog(monkeypatch):
    opened = []
    monkeypatch.setattr(PreferencesDialog, "exec", lambda self: opened.append(self.windowTitle()) or 0)
    MainWindow().open_preferences_dialog()
    assert opened == ["Preferences"]
