"""Tests for gui/comicvine_lookup_dialog.py's "Other Matches Found"
wiring -- picking a lower-ranked candidate should replace the row's
applied fields without re-running the search (see
redactor_common.gui.lookup_dialog's `resolve_alternative`, and this
project's own core/comicvine_lookup.py ranking that decides which
candidate is the row's default top pick vs an alternative).

Network calls (search + per-candidate detail/publisher/cover fetches)
are all monkeypatched at the names gui/comicvine_lookup_dialog.py
imports them under -- this only exercises the dialog's own wiring, not
core/comicvine_lookup.py's parsing/ranking (covered separately in
test_comicvine_lookup.py)."""

import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication

from core.cbz_file import CbzBook
from core.comicvine_lookup import ComicVineCandidate, ComicVineIssueDetails
from gui.comicvine_lookup_dialog import ComicVineLookupDialog

_app = QApplication.instance() or QApplication(sys.argv)

CANDIDATES = [
    ComicVineCandidate(
        issue_id="1",
        volume_name="Amazing Spider-Man Annual",
        issue_number="1",
        cover_date="1962-01-01",
        detail_url="https://x/issue/1/",
        volume_detail_url="https://x/volume/1/",
    ),
    ComicVineCandidate(
        issue_id="2",
        volume_name="Amazing Spider-Man",
        issue_number="12",
        cover_date="2016-03-01",
        detail_url="https://x/issue/2/",
        volume_detail_url="https://x/volume/2/",
    ),
    ComicVineCandidate(
        issue_id="3",
        volume_name="Amazing Spider-Man",
        issue_number="13",
        cover_date="2016-04-01",
        detail_url="https://x/issue/3/",
        volume_detail_url="https://x/volume/3/",
    ),
]


def _fake_fetch_issue_details(api_key, detail_url, fetch=None):
    # Each candidate's own URL round-trips into a distinguishable
    # series name, so a test can tell which one actually got applied.
    return ComicVineIssueDetails(volume_name=f"Details for {detail_url}", issue_number="1")


def _make_book(tmp_path) -> CbzBook:
    path = tmp_path / "Amazing Spider-Man 012.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", b"<ComicInfo><Series>Amazing Spider-Man</Series><Number>12</Number></ComicInfo>")
    return CbzBook(str(path))


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.app_settings.load_comicvine_api_key", lambda: "fake-key")
    monkeypatch.setattr("gui.comicvine_lookup_dialog.search_comicvine", lambda *a, **k: list(CANDIDATES))
    monkeypatch.setattr("gui.comicvine_lookup_dialog.fetch_issue_details", _fake_fetch_issue_details)
    monkeypatch.setattr("gui.comicvine_lookup_dialog.fetch_publisher", lambda *a, **k: "")

    book = _make_book(tmp_path)
    return ComicVineLookupDialog([book])


def test_alternatives_list_shows_the_runner_up_candidates(dialog):
    dialog.table.selectRow(0)
    # 3 candidates total: candidate[0] is the row's default "Found"
    # result, candidate[1:] are offered as alternatives.
    assert dialog.alt_list.count() == 2
    labels = [dialog.alt_list.item(i).text() for i in range(dialog.alt_list.count())]
    assert labels == [CANDIDATES[1].display_label(), CANDIDATES[2].display_label()]


def test_top_candidate_is_applied_by_default(dialog):
    result = dialog._row_results[0]
    assert result.fields["series"] == "Details for https://x/issue/1/"


def test_picking_an_alternative_replaces_the_rows_applied_fields(dialog):
    dialog.table.selectRow(0)
    dialog.alt_list.setCurrentRow(1)  # the second alternative -> candidate[2] ("#13")

    result = dialog._row_results[0]
    assert result.fields["series"] == "Details for https://x/issue/3/"
    # the row stays checked/applicable after switching
    assert dialog._checkboxes[0].isChecked() is True
    assert dialog._checkboxes[0].isEnabled() is True


def test_picking_an_alternative_keeps_the_alternatives_list_intact(dialog):
    dialog.table.selectRow(0)
    dialog.alt_list.setCurrentRow(0)

    # still browsable afterwards -- picking one doesn't consume the list
    assert dialog.alt_list.count() == 2
