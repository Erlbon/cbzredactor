"""Window-level tests for Operations > Redact: the menu/toolbar wiring, the
selection / ask-about-all rules, unsaved and unreadable files skipped, the
recipe stored as JSON in the settings file, and the list refreshed from
disk afterwards with the Undo stack cleared."""

import os
import shutil
import sys
import zipfile

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui import app_settings
from redactor_common.gui.redact_dialog import RecipeEditorDialog, RedactResultsDialog

_app = QApplication.instance() or QApplication(sys.argv)

INFO = b"<ComicInfo><Series>  Saga  </Series><Number>1</Number></ComicInfo>"


def _cbz(path, extra=()):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", INFO)
        for i in range(1, 4):
            zf.writestr(f"{i:03}.jpg", b"\xff\xd8\xff\xe0fakejpeg")
        for name, data in extra:
            zf.writestr(name, data)
    return str(path)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "s.ini"))
    monkeypatch.setattr(app_settings, "credit_pages_path", lambda: str(tmp_path / "known.json"))
    bin_dir = tmp_path / "_bin"
    bin_dir.mkdir()
    trashed = []

    def fake_trash(path):
        trashed.append(path)
        shutil.move(path, str(bin_dir / f"{len(trashed)}-{os.path.basename(path)}"))

    monkeypatch.setattr("gui.main_window.move_to_trash", fake_trash)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    results = []
    monkeypatch.setattr(RedactResultsDialog, "exec", lambda self: results.append(self) or 0)
    from gui.main_window import MainWindow

    w = MainWindow()
    w.trashed, w.shown, w.results = trashed, shown, results
    return w


def test_redact_is_in_the_operations_menu_and_the_toolbar(window):
    action = window.actions_["redact"]
    assert action.shortcut().toString() == "Ctrl+Shift+E"
    assert "redact_recipe" in window.actions_
    toolbar_actions = window.findChildren(type(window.addToolBar("x")))[0].actions()
    assert action in toolbar_actions


def test_redact_selected_files_in_place_and_refreshes_the_list(window, tmp_path):
    a = _cbz(tmp_path / "A 001.cbz", extra=[("Thumbs.db", b"x")])
    b = _cbz(tmp_path / "B 001.cbz", extra=[("Thumbs.db", b"x")])
    window._load_paths([a, b])
    window.table.selectRow(0)
    window._push_undo("edit", [window.books[0]])
    window.redact_files()

    with zipfile.ZipFile(a) as zf:
        assert "Thumbs.db" not in zf.namelist()
    with zipfile.ZipFile(b) as zf:
        assert "Thumbs.db" in zf.namelist()  # not selected, not touched
    assert len(window.trashed) == 1
    assert window.books[0].metadata.series == "Saga"
    assert window.table.item(0, window._col_index["series"]).text() == "Saga"
    assert not window.undo_manager.can_undo()
    assert window._selected_rows == [0]  # the selection came back, showing the fresh values
    report = window.results[0].report
    assert len(report.entries) == 1 and report.entries[0].applied
    assert "Recycle Bin" in window.results[0].header_label.text()


def test_nothing_selected_asks_before_redacting_all(window, tmp_path, monkeypatch):
    path = _cbz(tmp_path / "A 001.cbz", extra=[("Thumbs.db", b"x")])
    window._load_paths([path])
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel
    )
    window.redact_files()
    assert asked and "Redact all 1 loaded" in asked[0] and window.trashed == []

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.redact_files()
    assert len(window.trashed) == 1


def test_no_files_loaded_says_so(window):
    window.redact_files()
    assert any("Load some files" in m for m in window.shown)


def test_unsaved_file_is_skipped_and_named_in_the_report(window, tmp_path, monkeypatch):
    path = _cbz(tmp_path / "A 001.cbz", extra=[("Thumbs.db", b"x")])
    window._load_paths([path])
    window.table.selectRow(0)
    window.books[0].dirty = True
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes
    )
    before = open(path, "rb").read()
    window.redact_files()
    assert asked and "unsaved edits" in asked[0]
    assert open(path, "rb").read() == before and window.trashed == []
    entry = window.results[0].report.entries[0]
    assert entry.skips and "unsaved edits" in entry.skips[0]
    assert "SKIPPED" in window.results[0].report.to_text()


def test_recipe_is_stored_as_json_in_the_settings_file(window, monkeypatch, tmp_path):
    seen = {}

    def accept(dialog):
        seen["steps"] = [dialog.list.item(r).data(Qt.ItemDataRole.UserRole) for r in range(dialog.list.count())]
        dialog.list.item(0).setCheckState(Qt.CheckState.Unchecked)  # untick the first step
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(RecipeEditorDialog, "exec", accept)
    window.edit_redact_recipe()
    assert "guard" not in seen["steps"] and seen["steps"][-1] == "move_into_folders"
    from core.redact_steps import recipe_from_setting

    stored = app_settings.load_redact_recipe()
    recipe = recipe_from_setting(stored)
    assert recipe.enabled["convert_to_cbz"] is False and recipe.enabled["clean_contents"] is True
    assert "api_key" not in stored and "password" not in stored.lower()  # options only, no secrets
    # The stored recipe is what the next run uses: with cleaning on but conversion off, a CBZ is still cleaned.
    path = _cbz(tmp_path / "A 001.cbz", extra=[("Thumbs.db", b"x")])
    window._load_paths([path])
    window.table.selectRow(0)
    window.redact_files()
    with zipfile.ZipFile(path) as zf:
        assert "Thumbs.db" not in zf.namelist()


# --- pattern trail in the editor -------------------------------------------------------


def _accept_editor(monkeypatch):
    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(RecipeEditorDialog, "exec", lambda self: QDialog.DialogCode.Accepted)


def test_first_save_pins_the_current_patterns(window, monkeypatch):
    from core.redact_steps import recipe_from_setting

    app_settings.save_pattern_used("%series% %number%")
    _accept_editor(monkeypatch)
    window.edit_redact_recipe()
    saved = recipe_from_setting(app_settings.load_redact_recipe())
    assert saved.options["rename"]["pattern"] == "%series% %number%"
    app_settings.save_pattern_used("%title%")  # a later Rename / Export must not steer Redact
    from core.redact_steps import RedactEnv, build_catalogue
    from redactor_common.core.pipeline import effective_option_source

    spec = next(s for s in build_catalogue(window._redact_env()) if s.key == "rename").options[0]
    assert effective_option_source(spec, saved.options["rename"]["pattern"]) == ("%series% %number%", "set in this recipe")
    # opening and accepting again keeps what was stored as typed
    window.edit_redact_recipe()
    assert recipe_from_setting(app_settings.load_redact_recipe()).options["rename"]["pattern"] == "%series% %number%"


def test_saved_empty_pattern_stays_following(window, monkeypatch):
    from core.redact_steps import recipe_from_setting, recipe_to_setting

    app_settings.save_pattern_used("%series% %number%")
    recipe = recipe_from_setting("")
    recipe.options["rename"] = {"pattern": ""}
    app_settings.save_redact_recipe(recipe_to_setting(recipe))
    _accept_editor(monkeypatch)
    window.edit_redact_recipe()
    assert recipe_from_setting(app_settings.load_redact_recipe()).options["rename"]["pattern"] == ""


def test_editor_shows_the_trail(window):
    from PyQt6.QtWidgets import QComboBox, QLabel
    from core.redact_steps import build_catalogue, recipe_from_setting

    app_settings.save_pattern_used("%series%/%number%")
    app_settings.save_pattern_used("%series% %number%")
    dialog = RecipeEditorDialog(build_catalogue(window._redact_env()), recipe_from_setting(""))
    for row in range(dialog.list.count()):
        if dialog.list.item(row).data(Qt.ItemDataRole.UserRole) == "rename":
            dialog.list.setCurrentRow(row)
    combo = dialog.findChild(QComboBox)
    assert [combo.itemText(i) for i in range(combo.count())] == ["%series% %number%", "%series%/%number%"]
    caption = dialog.findChild(QLabel, "pattern_caption").text()
    assert "%series% %number%" in caption and "follows:" in caption
    assert "Series 001" in dialog.findChild(QLabel, "pattern_preview").text()
    combo.setCurrentText("%title%")
    assert "set in this recipe" in dialog.findChild(QLabel, "pattern_caption").text()
