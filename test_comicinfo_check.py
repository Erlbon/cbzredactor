"""Tests for Validate / Fix Issues (core/comicinfo_check.py, its dialog,
the window flow) and the stale-panel fix it came with: an edit made
behind the panel's back to the one selected file must survive clicking
another file."""

import datetime
import os
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.comicinfo import ComicInfoMetadata
from core.comicinfo_check import check_metadata

_app = QApplication.instance() or QApplication(sys.argv)
TODAY = datetime.date(2026, 9, 29)


def _fixes(meta, pages=None):
    return {f.field: f.fix for f in check_metadata(meta, pages, TODAY)}


def test_a_clean_record_has_nothing_to_report():
    meta = ComicInfoMetadata(series="The Maxx", title="Madness", number="1", count="35", year="1993",
                             month="3", day="31", language_iso="en", page_count="24")
    assert check_metadata(meta, 24, TODAY) == []  # "Maxx"/"Madness" are tags only in brackets


def test_scene_junk_numbers_and_whitespace():
    fixes = _fixes(ComicInfoMetadata(
        series="  Batman  (Zone-Empire) (Digital).webp", title="The Gift (2 covers)", number="#045",
        writer=" Tom King ", summary="  A story.  ",
    ))
    assert fixes["series"] == "Batman" and fixes["title"] == "The Gift"
    assert fixes["number"] == "45" and fixes["writer"] == "Tom King" and fixes["summary"] == "A story."
    assert _fixes(ComicInfoMetadata(number="[nn]"))["number"] == ""
    assert "number" not in _fixes(ComicInfoMetadata(number="0")) and "number" not in _fixes(ComicInfoMetadata(number="0.5"))
    # A year in brackets is part of the name, not a scene tag.
    assert "series" not in _fixes(ComicInfoMetadata(series="Batman (2016)"))


def test_dates_page_count_language_and_suggestions():
    fixes = _fixes(ComicInfoMetadata(year="2108", month="13", day="40", language_iso="en-US", page_count="30",
                                     tags="b&w", genre="Manga, Action"), pages=28)
    assert fixes == {"year": "", "month": "", "day": "", "language_iso": "en", "page_count": "28",
                     "black_and_white": "Yes", "manga": "Yes"}
    assert _fixes(ComicInfoMetadata(year="2023", month="2", day="29"))["day"] == ""  # not a leap year
    assert "day" not in _fixes(ComicInfoMetadata(year="2024", month="2", day="29"))
    assert "black_and_white" not in _fixes(ComicInfoMetadata(tags="b&w", black_and_white="No"))  # already set


def test_report_only_findings_and_one_line_per_field():
    findings = check_metadata(ComicInfoMetadata(number="12", count="6"), None, TODAY)
    assert [(f.field, f.fixable) for f in findings] == [("count", False)]
    merged = check_metadata(ComicInfoMetadata(series=" Saga  (Zone-Empire)"), None, TODAY)
    assert len(merged) == 1 and merged[0].fix == "Saga" and "stray spaces" in merged[0].message


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def _cbz(path, comicinfo):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", b"p")
        zf.writestr("ComicInfo.xml", f"<ComicInfo>{comicinfo}</ComicInfo>")
    return str(path)


@pytest.fixture
def window(tmp_path, monkeypatch):
    from gui import app_settings

    monkeypatch.setattr(app_settings, "credit_pages_path", lambda: str(tmp_path / "known.json"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from gui.main_window import MainWindow

    return MainWindow()


def test_validate_fix_applies_ticked_fixes_as_one_undo_step(window, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt

    from gui import validate_fix_dialog

    a = _cbz(tmp_path / "a.cbz", "<Series>Saga (Zone-Empire)</Series><Number>#007</Number>")
    b = _cbz(tmp_path / "b.cbz", "<Series>Clean</Series><Number>2</Number><Year>2108</Year>")
    window._load_paths([a, b])  # nothing selected: every loaded file is checked
    seen = {}

    def untick_the_year(dialog):
        seen["rows"] = [(dialog.table.item(r, 1).text(), dialog.table.item(r, 4).text()) for r in range(dialog.table.rowCount())]
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 1).text() == "Year":
                dialog.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(validate_fix_dialog.ValidateFixDialog, "exec", untick_the_year)
    window.open_validate_fix_dialog()
    assert ("Series", "Saga") in seen["rows"] and ("Number", "7") in seen["rows"] and ("Year", "(clear)") in seen["rows"]
    first, second = window.books
    assert (first.metadata.series, first.metadata.number) == ("Saga", "7") and first.dirty
    assert second.metadata.year == "2108" and not second.dirty  # unticked: untouched
    window.undo_last_action()
    assert (first.metadata.series, first.metadata.number) == ("Saga (Zone-Empire)", "#007")


@pytest.mark.parametrize("flow", ["validate", "search_replace"])
def test_an_edit_to_the_selected_file_survives_clicking_another(window, tmp_path, monkeypatch, flow):
    """Regression: the panel kept the old values and wrote them back on
    the next row change (_commit_current_edits writes every field)."""
    import gui.main_window as mw

    a = _cbz(tmp_path / "a.cbz", "<Series>Saga (Zone-Empire)</Series><Number>1</Number>")
    b = _cbz(tmp_path / "b.cbz", "<Series>Other</Series><Number>2</Number>")
    window._load_paths([a, b])
    window.table.selectRow(0)
    _app.processEvents()
    if flow == "validate":
        from gui import validate_fix_dialog

        monkeypatch.setattr(validate_fix_dialog.ValidateFixDialog, "exec", lambda d: d.DialogCode.Accepted)
        window.open_validate_fix_dialog()
        expected = "Saga"
    else:
        monkeypatch.setattr(mw.SearchReplaceDialog, "exec", lambda d: d.DialogCode.Accepted)
        monkeypatch.setattr(mw.SearchReplaceDialog, "result_field_key", lambda d: "series")
        monkeypatch.setattr(mw.SearchReplaceDialog, "accepted_changes", lambda d: {0: "New Name"})
        window.open_search_replace_dialog()
        expected = "New Name"
    assert window.books[0].metadata.series == expected
    window.table.selectRow(1)
    _app.processEvents()
    assert window.books[0].metadata.series == expected
