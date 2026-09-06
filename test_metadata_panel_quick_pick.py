"""Tests for the Genre/Language '+' quick-pick wiring in
gui/metadata_panel.py -- specifically that it's actually driving
redactor_common.gui.quick_pick_dialog.QuickPickDialog correctly (multi-
select for Genre so several can be appended at once, single-select for
Language so picking one replaces the field), not the flat QMenu this
replaced (see CHANGELOG for why: "the genre list gets too long to see
the apply button"). QuickPickDialog itself isn't re-tested here --
that's redactor_common's own module, exercised on its own logic
(filtering/selection) wherever it's actually used, per this repo's
established "GUI isn't separately unit-tested in redactor_common"
convention; this file only covers cbzredactor's wiring onto it."""

import sys

from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit

from gui.metadata_panel import ComicInfoPanel

_app = QApplication.instance() or QApplication(sys.argv)


def _genre_edit(panel: ComicInfoPanel) -> QLineEdit:
    return panel._line_edits["genre"]


def _language_edit(panel: ComicInfoPanel) -> QLineEdit:
    return panel._line_edits["language_iso"]


def test_genre_quick_pick_is_multi_select_and_appends(monkeypatch):
    panel = ComicInfoPanel()
    edit = _genre_edit(panel)
    edit.setText("Action")

    from redactor_common.gui import quick_pick_dialog

    captured = {}

    class _FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, title, load_entries_fn, multi_select, add_custom_fn=None, parent=None):
            captured["title"] = title
            captured["multi_select"] = multi_select
            captured["entries"] = load_entries_fn()

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_keys(self):
            return ["Fantasy", "Horror"]

    monkeypatch.setattr(quick_pick_dialog, "QuickPickDialog", _FakeDialog)
    panel._show_quick_pick_dialog("genre", edit)

    assert captured["multi_select"] is True
    assert edit.text() == "Action, Fantasy, Horror"  # appended, existing value preserved


def test_language_quick_pick_is_single_select_and_replaces(monkeypatch):
    panel = ComicInfoPanel()
    edit = _language_edit(panel)
    edit.setText("en")

    from redactor_common.gui import quick_pick_dialog

    class _FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, title, load_entries_fn, multi_select, add_custom_fn=None, parent=None):
            self._multi_select = multi_select

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_keys(self):
            return ["ja"]

    monkeypatch.setattr(quick_pick_dialog, "QuickPickDialog", _FakeDialog)
    panel._show_quick_pick_dialog("language_iso", edit)

    assert edit.text() == "ja"  # replaced outright, not appended


def test_cancelling_the_dialog_leaves_the_field_untouched(monkeypatch):
    panel = ComicInfoPanel()
    edit = _genre_edit(panel)
    edit.setText("Action")

    from redactor_common.gui import quick_pick_dialog

    class _FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

        def selected_keys(self):
            raise AssertionError("selected_keys() should not be read after Cancel")

    monkeypatch.setattr(quick_pick_dialog, "QuickPickDialog", _FakeDialog)
    panel._show_quick_pick_dialog("genre", edit)

    assert edit.text() == "Action"
