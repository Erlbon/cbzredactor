"""History is OPT-IN (owner decision 2026-10-14): nothing is recorded, and no journal file is created,
until the user turns it on (Tools > Preferences > History, or Yes in Tools > History). The command line
obeys the same setting. Real small CBZ files; the journal and settings live in tmp_path (conftest.py)."""

import os
import sys

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core import history
from core.cbz_file import CbzBook
from gui import app_settings, preferences
from test_redact_steps import _cbz, _run, env  # noqa: F401 -- env is a fixture

_app = QApplication.instance() or QApplication(sys.argv)


def _no_file(journal):
    return not os.path.exists(journal.path)


def _edit_and_save(path, series):
    book = CbzBook(path)
    book.metadata.series = series
    book.save()


def test_history_is_off_by_default_and_a_garbled_setting_is_off():
    assert history.history_enabled() is False
    app_settings._settings().setValue("history/enabled", "banana")
    assert history.history_enabled() is False


def test_off_save_records_nothing_and_creates_no_file(tmp_path, journal):
    _edit_and_save(_cbz(tmp_path / "a.cbz"), "Other")
    assert _no_file(journal)


def test_off_redact_records_nothing(env, tmp_path, journal, monkeypatch):  # noqa: F811
    import core.redact_steps as rs

    path = _cbz(tmp_path / "Saga 001.cbz", extra=[("Thumbs.db", b"junk")])
    original_begin = rs._journal_begin

    def with_an_edit(ctx):
        ctx.work.metadata.notes = "tagged"
        return original_begin(ctx)

    monkeypatch.setattr(rs, "_journal_begin", with_an_edit)
    _run(env, [CbzBook(path)])
    assert CbzBook(path).metadata.notes == "tagged" and _no_file(journal)


def test_off_command_line_records_nothing(tmp_path, journal):
    from cbzcli.main import main

    assert main(["set", _cbz(tmp_path / "a.cbz"), "-s", "Series=Else"]) == 0
    assert _no_file(journal)


def test_off_save_all_in_the_window_records_nothing(tmp_path, journal):
    from gui.main_window import MainWindow

    window = MainWindow()
    window._load_paths([_cbz(tmp_path / "a.cbz"), _cbz(tmp_path / "b.cbz")])
    for book in window.books:
        book.metadata.series = "Other"
        book.dirty = True
    window.save_all_changed()
    assert CbzBook(window.books[0].path).metadata.series == "Other" and _no_file(journal)


def test_turning_it_on_records_and_off_again_stops(tmp_path, journal):
    path = _cbz(tmp_path / "a.cbz")
    history.set_history_enabled(True)
    _edit_and_save(path, "One")
    assert len(journal.entries()) == 1
    history.set_history_enabled(False)
    _edit_and_save(path, "Two")
    assert len(journal.entries()) == 1


# --- Tools > Preferences > History ---------------------------------------------------------------


def test_preferences_checkbox_round_trips_and_cancel_changes_nothing():
    dlg = preferences.make_dialog()
    assert "History" in dlg.page_titles()
    assert dlg.value(preferences.KEY_HISTORY_ENABLED) is False
    dlg.set_value(preferences.KEY_HISTORY_ENABLED, True)
    dlg.reject()  # Cancel
    assert app_settings.load_history_enabled() is False

    dlg = preferences.make_dialog()
    dlg.set_value(preferences.KEY_HISTORY_ENABLED, True)
    dlg.apply()
    assert app_settings.load_history_enabled() is True and history.history_enabled() is True
    assert preferences.make_dialog().value(preferences.KEY_HISTORY_ENABLED) is True


def test_preferences_help_says_what_how_much_and_where():
    spec = next(
        s for section in preferences.preference_sections() for s in section.specs
        if s.key == preferences.KEY_HISTORY_ENABLED
    )
    assert spec.default is False
    assert "never page images" in spec.help and "90 days" in spec.help and "50,000" in spec.help
    assert history.journal_file_path() in spec.help and "switch it off" in spec.help


def test_delete_history_asks_and_removes_the_file(tmp_path, journal, monkeypatch):
    history.set_history_enabled(True)
    _edit_and_save(_cbz(tmp_path / "a.cbz"), "Other")
    assert os.path.exists(journal.path)
    dlg = preferences.make_dialog()
    button = dlg.delete_history_button
    assert button.isEnabled()

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    button.click()
    assert os.path.exists(journal.path)  # Cancel keeps it

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    button.click()
    assert _no_file(journal) and not button.isEnabled()
    assert app_settings.load_history_enabled() is True  # the setting is not changed


# --- Tools > History ---------------------------------------------------------------------------------


@pytest.fixture
def window():
    from gui.main_window import MainWindow

    return MainWindow()


def _capture_dialogs(monkeypatch):
    from redactor_common.gui.history_dialog import HistoryDialog

    titles = []
    monkeypatch.setattr(HistoryDialog, "exec", lambda self: titles.append(self.windowTitle()) or 0)
    return titles


def test_tools_history_when_off_and_empty_asks_first_no_does_nothing(window, journal, monkeypatch):
    titles = _capture_dialogs(monkeypatch)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No),
    )
    window.actions_["history"].trigger()
    assert "History is off. Turn it on?" in asked[0] and "Nothing has been recorded so far" in asked[0]
    assert titles == [] and history.history_enabled() is False and _no_file(journal)


def test_tools_history_when_off_and_empty_yes_turns_it_on(window, journal, monkeypatch):
    titles = _capture_dialogs(monkeypatch)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window.actions_["history"].trigger()
    assert titles == ["History"] and history.history_enabled() is True


def test_tools_history_when_off_with_a_file_opens_marked_off(window, tmp_path, journal, monkeypatch):
    history.set_history_enabled(True)
    _edit_and_save(_cbz(tmp_path / "a.cbz"), "Other")
    history.set_history_enabled(False)
    titles = _capture_dialogs(monkeypatch)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: pytest.fail("must not ask")))
    window.actions_["history"].trigger()
    assert titles == ["History (recording is off)"]


def test_tools_history_when_on_opens_normally(window, journal, monkeypatch):
    history.set_history_enabled(True)
    titles = _capture_dialogs(monkeypatch)
    window.actions_["history"].trigger()
    assert titles == ["History"]
