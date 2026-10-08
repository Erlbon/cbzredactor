"""Tests for gui/comicvine_browse_dialog.py -- the two-step Comic Vine browser.
Every network call is replaced at the name the dialog imports it under; the
covers are real images so the cover-hash preselection really runs."""

import io
import sys
import zipfile

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

import gui.comicvine_browse_dialog as dlg_mod
from core.cbz_file import CbzBook
from core.comicvine_browse import ComicVineIssue, ComicVineVolume
from core.comicvine_lookup import ComicVineIssueDetails, ComicVineLookupError
from gui.comicvine_browse_dialog import STEP_ISSUES, STEP_SERIES, ComicVineBrowseDialog

_app = QApplication.instance() or QApplication(sys.argv)


def _gradient(rising: bool) -> bytes:
    image = Image.new("RGB", (90, 120))
    for x in range(90):
        value = int(255 * x / 89)
        image.paste((value if rising else 255 - value,) * 3, (x, 0, x + 1, 120))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


RISING, FALLING = _gradient(True), _gradient(False)  # opposite hashes: two different covers

VOLUMES = [
    ComicVineVolume(volume_id="1", name="Batman", start_year="2016", issue_count=140, publisher="DC Comics",
                    image_url="https://x/v1.png", detail_url="https://x/volume/1/"),
    ComicVineVolume(volume_id="2", name="Batman", start_year="2011", issue_count=52, publisher="DC Comics",
                    image_url="https://x/v2.png", detail_url="https://x/volume/2/"),
]
ISSUES = {
    "1": [ComicVineIssue(issue_id=str(100 + n), number=str(n), name=f"Story {n}", cover_date="2016-01-01",
                         image_url=f"https://x/i{n}.png", detail_url=f"https://x/issue/{100 + n}/")
          for n in (1, 2, 3, 5)],
    "2": [ComicVineIssue(issue_id="201", number="1", image_url="https://x/i201.png", detail_url="https://x/issue/201/")],
}
IMAGES = {"https://x/v1.png": FALLING, "https://x/v2.png": RISING}  # the file's cover is RISING


def _book(tmp_path, name="Batman 002 (2017).cbz", series="Batman", number="2") -> CbzBook:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", f"<ComicInfo><Series>{series}</Series><Number>{number}</Number></ComicInfo>")
        zf.writestr("001.png", RISING)
    return CbzBook(str(path))


@pytest.fixture
def net(monkeypatch):
    calls = {"search": 0, "issues": []}

    def search_volumes(key, series, fetch=None, max_results=100):
        calls["search"] += 1
        return list(VOLUMES)

    def fetch_volume_issues(key, volume_id, fetch=None):
        calls["issues"].append(volume_id)
        return list(ISSUES[volume_id])

    def download_image(url, fetch=None):
        if url not in IMAGES:
            raise ComicVineLookupError("no image")
        return IMAGES[url]

    def issue_details(key, detail_url, fetch=None):
        return ComicVineIssueDetails(volume_name="Batman", issue_number="2", name=f"details of {detail_url}")

    monkeypatch.setattr("gui.app_settings.load_comicvine_api_key", lambda: "KEY")
    monkeypatch.setattr(dlg_mod, "call_in_background", lambda fn, *a, **k: fn(*a))
    monkeypatch.setattr(dlg_mod, "search_volumes", search_volumes)
    monkeypatch.setattr(dlg_mod, "fetch_volume_issues", fetch_volume_issues)
    monkeypatch.setattr(dlg_mod, "download_image", download_image)
    monkeypatch.setattr(dlg_mod, "fetch_issue_details", issue_details)
    return calls


def _selected_series(dialog):
    return dialog._selected_volume()


def _select_volume(dialog, volume_id):
    for row in range(dialog.volume_table.rowCount()):
        if dialog.volume_table.item(row, 0).data(0x0100).volume_id == volume_id:
            dialog.volume_table.selectRow(row)
            return
    raise AssertionError(volume_id)


def test_the_volume_whose_cover_matches_is_preselected_but_not_applied(tmp_path, net):
    dialog = ComicVineBrowseDialog([_book(tmp_path)])
    assert dialog.stack.currentIndex() == STEP_SERIES
    assert dialog.volume_table.rowCount() == 2
    # Name/year/count rank the 2016 volume first, but the 2011 volume has the same cover.
    assert dialog.volume_table.item(0, 0).data(0x0100).volume_id == "1"
    assert _selected_series(dialog).volume_id == "2"
    assert dialog.volume_table.item(1, 4).text() == "100%"
    assert "same cover" in dialog.match_label.text()
    assert dialog.accepted_metadata() == {}  # nothing applied by looking


def test_without_a_matching_cover_the_best_ranked_volume_is_selected(tmp_path, net, monkeypatch):
    monkeypatch.setitem(IMAGES, "https://x/v2.png", FALLING)
    dialog = ComicVineBrowseDialog([_book(tmp_path)])
    assert _selected_series(dialog).volume_id == "1"


def test_choosing_a_series_lists_its_issues_in_order_with_the_files_number_selected(tmp_path, net):
    dialog = ComicVineBrowseDialog([_book(tmp_path)])
    dialog.use_btn.click()  # choose the preselected series (volume 2... which has only #1)
    assert dialog.stack.currentIndex() == STEP_ISSUES
    assert net["issues"] == ["2"]
    assert dialog._selected_issue() is None
    assert "No issue #2 in this series. It has: 1." in dialog.info_label.text()

    dialog.back_btn.click()
    _select_volume(dialog, "1")
    dialog.use_btn.click()
    assert [dialog.issue_table.item(r, 0).text() for r in range(4)] == ["1", "2", "3", "5"]
    assert dialog._selected_issue().number == "2"


def test_using_the_issue_applies_its_details_and_the_volumes_publisher(tmp_path, net):
    dialog = ComicVineBrowseDialog([_book(tmp_path)])
    _select_volume(dialog, "1")
    dialog.use_btn.click()
    dialog.use_btn.click()  # "Use This Issue"
    fields = dialog.accepted_metadata()[0]
    assert fields["publisher"] == "DC Comics"
    assert fields["title"] == "details of https://x/issue/102/"
    assert dialog.result() == dialog.DialogCode.Accepted  # the only file: done


def test_the_chosen_series_is_remembered_for_the_next_file_of_the_same_series(tmp_path, net):
    books = [_book(tmp_path, "Batman 002 (2017).cbz", number="2"), _book(tmp_path, "Batman 003 (2017).cbz", number="3")]
    dialog = ComicVineBrowseDialog(books)
    _select_volume(dialog, "1")
    dialog.use_btn.click()
    dialog.use_btn.click()
    assert dialog._index == 1
    assert dialog.stack.currentIndex() == STEP_ISSUES  # straight to the issue, no second series search
    assert net["search"] == 1
    assert dialog._selected_issue().number == "3"
    dialog.use_btn.click()
    assert sorted(dialog.accepted_metadata()) == [0, 1]


def test_back_to_series_forgets_the_remembered_choice(tmp_path, net):
    books = [_book(tmp_path, "Batman 002 (2017).cbz"), _book(tmp_path, "Batman 003 (2017).cbz", number="3")]
    dialog = ComicVineBrowseDialog(books)
    _select_volume(dialog, "1")
    dialog.use_btn.click()
    dialog.use_btn.click()
    dialog.back_btn.click()
    assert dialog.stack.currentIndex() == STEP_SERIES
    assert dialog._last_volume is None


def test_skip_moves_on_without_applying_and_the_last_skip_closes(tmp_path, net):
    dialog = ComicVineBrowseDialog([_book(tmp_path, "a.cbz"), _book(tmp_path, "b.cbz")])
    dialog.skip_btn.click()
    assert dialog._index == 1 and dialog.result() != dialog.DialogCode.Accepted
    dialog.skip_btn.click()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.accepted_metadata() == {}


def test_a_series_with_no_matches_says_so(tmp_path, net, monkeypatch):
    monkeypatch.setattr(dlg_mod, "search_volumes", lambda *a, **k: [])
    dialog = ComicVineBrowseDialog([_book(tmp_path)])
    assert dialog.volume_table.rowCount() == 0
    assert "no series matching" in dialog.info_label.text()


def test_an_api_error_is_shown_in_a_warning_not_raised(tmp_path, net, monkeypatch):
    shown = []
    monkeypatch.setattr(dlg_mod.QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))

    def broken(*a, **k):
        raise ComicVineLookupError("Comic Vine error: Invalid API key")

    monkeypatch.setattr(dlg_mod, "search_volumes", broken)
    dialog = ComicVineBrowseDialog([_book(tmp_path)])
    assert shown and "Invalid API key" in shown[0]
    assert dialog.volume_table.rowCount() == 0


def test_editing_the_series_and_searching_again_shows_the_new_results(tmp_path, net):
    dialog = ComicVineBrowseDialog([_book(tmp_path)])
    dialog.series_edit.setText("Detective Comics")
    dialog.search_btn.click()
    assert net["search"] == 2
    assert dialog.volume_table.rowCount() == 2
