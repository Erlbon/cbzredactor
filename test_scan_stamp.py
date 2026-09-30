"""Tests for the persisted validation-scan stamp: a <RedactorScan> element in
ComicInfo.xml (core/comicinfo.py), held in memory after Validate / Fix Issues
and written only on Save; CbzBook.record_scan / stamp_text / staleness; the
page fingerprint (core/cbz_fingerprint.py) and the window's Status column."""

import datetime
import io
import sys
import zipfile

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMessageBox

from redactor_common.core.scan_stamp import make_stamp

from core.cbz_file import CbzBook, CbzError
from core.cbz_fingerprint import cbz_fingerprint
from core.comicinfo import parse_comicinfo_xml, serialize_comicinfo_xml

_app = QApplication.instance() or QApplication(sys.argv)

INFO = (
    b'<?xml version="1.0"?><ComicInfo><Series>Saga</Series><Number>1</Number>'
    b"<FutureThing>keep me</FutureThing>"
    b'<Pages><Page Image="0" Type="FrontCover"/><Page Image="1"/></Pages></ComicInfo>'
)


def _cbz(path, info=INFO, pages=(("001.jpg", b"page one"), ("002.jpg", b"page two")), comment=b"", extra=()):
    with zipfile.ZipFile(path, "w") as zf:
        zf.comment = comment
        if info is not None:
            zf.writestr("ComicInfo.xml", info)
        for name, data in (*pages, *extra):
            zf.writestr(name, data)
    return str(path)


def _jpeg(width):
    out = io.BytesIO()
    Image.new("RGB", (width, width * 2), (90, 100, 110)).save(out, "JPEG")
    return out.getvalue()


# --- storage ------------------------------------------------------------------------


def test_stamp_round_trips_through_save_and_foreign_content_survives(tmp_path):
    path = _cbz(tmp_path / "a.cbz", comment=b"someone else's comment")
    book = CbzBook(path)
    assert book.stamp is None and not book.dirty
    assert book.record_scan("OK")
    book.save()
    assert not book.dirty
    again = CbzBook(path)
    assert again.stamp is not None and again.stamp.status == "OK" and again.stamp_stale is False
    assert not again.dirty  # loading a stamp never marks the book unsaved
    with zipfile.ZipFile(path) as zf:
        assert zf.comment == b"someone else's comment"
        info = zf.read("ComicInfo.xml")
    assert b"<FutureThing>keep me</FutureThing>" in info and b'<Page Image="0" Type="FrontCover"/>' in info
    assert info.count(b"<RedactorScan>") == 1


def test_garbage_stamp_is_ignored_but_not_lost():
    xml = b"<ComicInfo><Series>S</Series><RedactorScan>not a stamp</RedactorScan></ComicInfo>"
    meta = parse_comicinfo_xml(xml)
    assert meta.scan_stamp == "not a stamp" and meta.extra_elements == []
    assert b"<RedactorScan>not a stamp</RedactorScan>" in serialize_comicinfo_xml(meta)


def test_garbage_stamp_shows_as_unscanned(tmp_path):
    book = CbzBook(_cbz(tmp_path / "a.cbz", info=b"<ComicInfo><RedactorScan>zzz</RedactorScan></ComicInfo>"))
    assert book.stamp is None and book.stamp_text() == "" and book.scan_status() == ""


def test_no_stamp_element_when_unscanned(tmp_path):
    assert b"RedactorScan" not in serialize_comicinfo_xml(CbzBook(_cbz(tmp_path / "a.cbz")).metadata)


# --- fingerprint ----------------------------------------------------------------------


def test_fingerprint_is_stable_across_metadata_saves_and_changes_with_pages(tmp_path):
    path = _cbz(tmp_path / "a.cbz")
    before = cbz_fingerprint(path)
    assert before
    book = CbzBook(path)
    book.metadata.title = "Edited"
    book.save()
    assert cbz_fingerprint(path) == before
    changed = _cbz(tmp_path / "b.cbz", pages=(("001.jpg", b"page one"), ("002.jpg", b"page TWO")))
    assert cbz_fingerprint(changed) != before
    added = _cbz(tmp_path / "c.cbz", extra=(("003.jpg", b"x"),))
    assert cbz_fingerprint(added) != before
    assert cbz_fingerprint(str(tmp_path / "missing.cbz")) == ""


def test_fingerprint_ignores_entry_order_and_comicinfo_case(tmp_path):
    a = _cbz(tmp_path / "a.cbz")
    with zipfile.ZipFile(tmp_path / "b.cbz", "w") as zf:
        zf.writestr("002.jpg", b"page two")
        zf.writestr("001.jpg", b"page one")
        zf.writestr("comicinfo.xml", b"<ComicInfo/>")
    assert cbz_fingerprint(a) == cbz_fingerprint(str(tmp_path / "b.cbz"))


# --- stamping rules -------------------------------------------------------------------


def test_record_scan_marks_dirty_as_stamp_only_until_another_edit(tmp_path):
    book = CbzBook(_cbz(tmp_path / "a.cbz"))
    assert book.record_scan("ISSUES")
    assert book.dirty and book.stamp_only_dirty and book.stamp.status == "ISSUES" and book.stamp_stale is False
    assert book.scan_status() == "ISSUES"
    book.dirty = True  # any other assignment is a real edit
    assert not book.stamp_only_dirty
    assert book.record_scan("OK") and not book.stamp_only_dirty  # an edit stays an edit


def test_failed_scans_are_never_stamped(tmp_path):
    broken = CbzBook(_cbz(tmp_path / "a.cbz", info=b"<ComicInfo><Series>"))
    assert broken.load_error and not broken.record_scan("OK")
    assert broken.stamp is None and not broken.dirty
    book = CbzBook(_cbz(tmp_path / "b.cbz"))
    assert not book.record_scan("")  # no result, no stamp
    (tmp_path / "b.cbz").write_bytes(b"not a zip any more")
    assert not book.record_scan("OK") and book.stamp is None and not book.dirty


# --- every rewrite path ---------------------------------------------------------------


def _stamped(path):
    book = CbzBook(path)
    book.record_scan("OK")
    book.save()
    return CbzBook(path)


def test_remove_pages_keeps_the_stamp_but_it_is_now_stale(tmp_path):
    book = _stamped(_cbz(tmp_path / "a.cbz"))
    assert book.stamp_stale is False
    book.remove_pages(["002.jpg"], dispose_original=lambda _p: None)
    assert book.stamp is not None and book.stamp_stale is True
    assert "(changed since)" in book.stamp_text() and book.scan_status() == ""
    assert CbzBook(book.path).stamp_stale is True


def test_clean_contents_and_resize_make_the_stamp_stale(tmp_path):
    clean = _stamped(_cbz(tmp_path / "a.cbz", pages=(("Scan/p1.jpg", b"one"), ("Scan/p2.jpg", b"two"))))
    clean.clean_contents(dispose_original=lambda _p: None)
    assert clean.stamp is not None and clean.stamp_stale is True

    big = _stamped(_cbz(tmp_path / "b.cbz", pages=(("001.jpg", _jpeg(1600)), ("002.jpg", _jpeg(1600)))))
    big.resize_images(800)
    assert big.stamp is not None and big.stamp_stale is True
    assert CbzBook(big.path).stamp_stale is True


def test_resize_to_another_file_and_zip_comment_are_preserved(tmp_path):
    book = _stamped(_cbz(tmp_path / "a.cbz", pages=(("001.jpg", _jpeg(1600)),), comment=b"keep"))
    book.resize_images(800, output_path=str(tmp_path / "out.cbz"))
    out = CbzBook(str(tmp_path / "out.cbz"))
    assert out.stamp is not None and out.stamp_stale is True
    with zipfile.ZipFile(out.path) as zf:
        assert zf.comment == b"keep"


def test_stamp_only_dirty_does_not_block_rewrites_and_is_cleared_by_them(tmp_path):
    book = CbzBook(_cbz(tmp_path / "a.cbz"))
    book.record_scan("OK")
    assert book.remove_pages(["002.jpg"], dispose_original=lambda _p: None) == 1
    assert not book.dirty and book.stamp_stale is True
    real_edit = CbzBook(_cbz(tmp_path / "b.cbz"))
    real_edit.record_scan("OK")
    real_edit.dirty = True
    with pytest.raises(CbzError, match="unsaved"):
        real_edit.remove_pages(["002.jpg"])
    with pytest.raises(CbzError, match="unsaved"):
        real_edit.clean_contents()


def test_metadata_only_save_keeps_a_current_stamp_current(tmp_path):
    book = _stamped(_cbz(tmp_path / "a.cbz"))
    book.metadata.title = "New"
    book.save()
    assert book.stamp_stale is False and CbzBook(book.path).stamp_stale is False


# --- display --------------------------------------------------------------------------


def test_stamp_text_variants(tmp_path):
    book = _stamped(_cbz(tmp_path / "a.cbz"))
    assert book.stamp_text().startswith("OK · 20") and "(" not in book.stamp_text()
    book.stamp_stale = None
    assert book.stamp_text().endswith("(unverified)")
    book.stamp_stale = True
    assert book.stamp_text().endswith("(changed since)")
    # a stamp with no fingerprint can't be checked
    old = make_stamp("OK", now=datetime.datetime(2026, 9, 30, 12, 0, tzinfo=datetime.timezone.utc)).to_text()
    info = f"<ComicInfo><RedactorScan>{old}</RedactorScan></ComicInfo>".encode()
    plain = CbzBook(_cbz(tmp_path / "b.cbz", info=info))
    assert plain.stamp_stale is None and plain.stamp_text().endswith("(unverified)") and plain.scan_status() == ""


# --- the window -----------------------------------------------------------------------


@pytest.fixture
def window(tmp_path, monkeypatch):
    from gui import app_settings

    monkeypatch.setattr(app_settings, "credit_pages_path", lambda: str(tmp_path / "known.json"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from gui.main_window import MainWindow

    return MainWindow()


def _status(window, row):
    return window.table.item(row, window._col_index["status"])


def test_loading_a_stamped_file_shows_it_without_marking_dirty(window, tmp_path):
    path = str(_stamped(_cbz(tmp_path / "a.cbz")).path)
    window._load_paths([path])
    book = window.books[0]
    assert not book.dirty
    assert _status(window, 0).text().startswith("OK · 20")
    assert "Scanned" in _status(window, 0).toolTip()


def test_unscanned_file_keeps_the_plain_ok_text(window, tmp_path):
    window._load_paths([_cbz(tmp_path / "a.cbz")])
    assert _status(window, 0).text() == "OK" and not window.books[0].dirty


def test_clean_validate_stamps_ok_and_marks_unsaved(window, tmp_path):
    path = _cbz(tmp_path / "a.cbz", info=b"<ComicInfo><Series>Clean</Series><Number>2</Number></ComicInfo>")
    window._load_paths([path])
    window.open_validate_fix_dialog()
    book = window.books[0]
    assert book.dirty and book.stamp_only_dirty and book.stamp.status == "OK"
    assert _status(window, 0).text().startswith("Modified · OK · 20")
    book.save()
    assert CbzBook(path).stamp.status == "OK"


def test_validate_with_issues_stamps_what_is_left(window, tmp_path, monkeypatch):
    from gui import validate_fix_dialog

    # Count lower than Number is reported only (no fix), so ISSUES remains after Apply.
    path = _cbz(tmp_path / "a.cbz", info=b"<ComicInfo><Series>Saga (Zone-Empire)</Series><Number>5</Number><Count>3</Count></ComicInfo>")
    window._load_paths([path])
    monkeypatch.setattr(validate_fix_dialog.ValidateFixDialog, "exec", lambda d: d.DialogCode.Accepted)
    window.open_validate_fix_dialog()
    book = window.books[0]
    assert book.metadata.series == "Saga" and book.dirty and not book.stamp_only_dirty
    assert book.stamp.status == "ISSUES"


def test_applying_every_fix_stamps_ok_and_cancel_stamps_issues(window, tmp_path, monkeypatch):
    from gui import validate_fix_dialog

    a = _cbz(tmp_path / "a.cbz", info=b"<ComicInfo><Series>Saga (Zone-Empire)</Series></ComicInfo>")
    b = _cbz(tmp_path / "b.cbz", info=b"<ComicInfo><Series>Other (Zone-Empire)</Series></ComicInfo>")
    window._load_paths([a])
    monkeypatch.setattr(validate_fix_dialog.ValidateFixDialog, "exec", lambda d: d.DialogCode.Accepted)
    window.open_validate_fix_dialog()
    assert window.books[0].stamp.status == "OK"
    window._load_paths([b])
    window.table.clearSelection()
    window.books = window.books[1:]  # only b is left to check
    window._rebuild_table()
    monkeypatch.setattr(validate_fix_dialog.ValidateFixDialog, "exec", lambda d: d.DialogCode.Rejected)
    window.open_validate_fix_dialog()
    cancelled = window.books[0]
    assert cancelled.metadata.series == "Other (Zone-Empire)" and cancelled.stamp.status == "ISSUES"


def test_stamp_survives_undo_and_is_written_by_save(window, tmp_path, monkeypatch):
    from gui import validate_fix_dialog

    path = _cbz(tmp_path / "a.cbz", info=b"<ComicInfo><Series>Saga (Zone-Empire)</Series></ComicInfo>")
    window._load_paths([path])
    monkeypatch.setattr(validate_fix_dialog.ValidateFixDialog, "exec", lambda d: d.DialogCode.Accepted)
    window.open_validate_fix_dialog()
    window.undo_last_action()
    book = window.books[0]
    assert book.metadata.series == "Saga (Zone-Empire)" and book.stamp is not None
    book.save()
    assert CbzBook(path).stamp is not None


def test_issues_row_is_tinted_and_stale_stamp_is_flagged(window, tmp_path):
    from redactor_common.gui.colors import SAVE_FAILED_COLOR

    path = _cbz(tmp_path / "a.cbz", info=b"<ComicInfo><Series>X (Digital)</Series></ComicInfo>")
    book = CbzBook(path)
    book.record_scan("ISSUES")
    book.save()
    window._load_paths([path])
    assert window.table.item(0, 0).background().color() == SAVE_FAILED_COLOR
    # pages change behind the stamp's back (another tool rewrote a page)
    with zipfile.ZipFile(path) as zf:
        info = zf.read("ComicInfo.xml")
    _cbz(tmp_path / "a.cbz", info=info, pages=(("001.jpg", b"different"), ("002.jpg", b"page two")))
    window.books[0] = CbzBook(path)
    window._refresh_table_row(0, window.books[0])
    text = _status(window, 0).text()
    assert "ISSUES" in text and text.endswith("(changed since)")
    assert window.table.item(0, 0).background().color() != SAVE_FAILED_COLOR  # stale: no longer asserted
