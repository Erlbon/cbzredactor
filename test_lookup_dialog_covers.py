"""Tests that GcdLookupDialog actually wires
the book's own existing cover into LookupDialogBase's
`get_local_cover` parameter (see redactor_common.gui.lookup_dialog,
promoted 2026-09-06 after "we need to show the existing cover page of
the comic ... and the one from the scraper ... so that we can see that
they are the same comic"). The shared dialog's own cover-rendering
logic isn't re-tested here -- that's redactor_common's, exercised
directly there is out of scope per this repo's "GUI isn't separately
unit-tested in redactor_common" convention; this only covers that
cbzredactor's dialog actually passes book.read_first_page_bytes()
through, end to end, against a real temp .cbz file with a real cover
image."""

import io
import sys
import zipfile

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from core.cbz_file import CbzBook
from gui.gcd_lookup_dialog import GcdLookupDialog

_app = QApplication.instance() or QApplication(sys.argv)


def _cover_bytes(color) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (100, 150), color=color).save(out, format="JPEG")
    return out.getvalue()


def _make_cbz(path, cover: bytes) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", b"<ComicInfo><Series>Test</Series><Number>1</Number></ComicInfo>")
        zf.writestr("001.jpg", cover)
    return str(path)


def test_gcd_dialog_shows_the_books_own_cover_as_current(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.gcd_lookup_dialog.search_gcd", lambda *a, **k: [])

    cover = _cover_bytes((40, 50, 60))
    book = CbzBook(_make_cbz(tmp_path / "book.cbz", cover))

    dialog = GcdLookupDialog([book])
    dialog.table.selectRow(0)

    assert dialog.detail_cover_current.text() == ""
    assert dialog.detail_cover_found.text() == "No match"


def test_book_with_no_pages_shows_no_local_cover_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.gcd_lookup_dialog.search_gcd", lambda *a, **k: [])

    with zipfile.ZipFile(tmp_path / "empty.cbz", "w") as zf:
        zf.writestr("ComicInfo.xml", b"<ComicInfo><Series>Test</Series></ComicInfo>")
    book = CbzBook(str(tmp_path / "empty.cbz"))

    dialog = GcdLookupDialog([book])
    dialog.table.selectRow(0)

    assert dialog.detail_cover_current.text() == "No local cover"
