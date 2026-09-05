"""Tests for core/cbz_file.py -- built against real temporary .cbz
files on disk (via tmp_path), since zip I/O is the whole point of this
module."""

import zipfile

import pytest

from core.cbz_file import CbzBook, CbzError

COMICINFO_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Title>Test Book</Title>
  <Series>Test Series</Series>
  <PageCount>99</PageCount>
</ComicInfo>
"""


def _make_cbz(path, comicinfo=COMICINFO_XML, page_names=("001.jpg", "002.jpg", "003.jpg")):
    with zipfile.ZipFile(path, "w") as zf:
        if comicinfo is not None:
            zf.writestr("ComicInfo.xml", comicinfo)
        for name in page_names:
            zf.writestr(name, b"\xff\xd8\xff\xe0fakejpegbytes")  # JPEG-ish magic bytes, content doesn't matter
    return path


def test_load_reads_metadata_and_pages(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz")
    book = CbzBook(str(cbz_path))
    assert not book.load_error
    assert book.metadata.title == "Test Book"
    assert book.metadata.series == "Test Series"
    assert book.page_names == ["001.jpg", "002.jpg", "003.jpg"]
    assert book.actual_page_count == 3
    assert book.first_page_name == "001.jpg"


def test_load_with_no_comicinfo_xml(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz", comicinfo=None)
    book = CbzBook(str(cbz_path))
    assert not book.load_error
    assert book.metadata.title == ""
    assert book.actual_page_count == 3


def test_page_count_mismatch_detected(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz")  # metadata says 99, archive has 3
    book = CbzBook(str(cbz_path))
    assert book.page_count_mismatch is True


def test_page_count_mismatch_false_when_correct(tmp_path):
    xml = COMICINFO_XML.replace(b"<PageCount>99</PageCount>", b"<PageCount>3</PageCount>")
    cbz_path = _make_cbz(tmp_path / "book.cbz", comicinfo=xml)
    book = CbzBook(str(cbz_path))
    assert book.page_count_mismatch is False


def test_load_error_on_corrupt_zip(tmp_path):
    bad_path = tmp_path / "bad.cbz"
    bad_path.write_bytes(b"not a zip file at all")
    book = CbzBook(str(bad_path))
    assert book.load_error


def test_save_updates_metadata_and_recomputes_page_count(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz")
    book = CbzBook(str(cbz_path))
    book.metadata.title = "Updated Title"
    book.save()

    reloaded = CbzBook(str(cbz_path))
    assert reloaded.metadata.title == "Updated Title"
    assert reloaded.metadata.page_count == "3"  # recomputed from actual pages, not left at 99
    assert reloaded.page_names == ["001.jpg", "002.jpg", "003.jpg"]
    assert book.dirty is False


def test_save_preserves_page_bytes_exactly(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz")
    with zipfile.ZipFile(cbz_path, "r") as zf:
        original_page_bytes = zf.read("001.jpg")

    book = CbzBook(str(cbz_path))
    book.metadata.title = "Changed"
    book.save()

    with zipfile.ZipFile(cbz_path, "r") as zf:
        saved_page_bytes = zf.read("001.jpg")
    assert saved_page_bytes == original_page_bytes


def test_save_as_leaves_original_untouched(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz")
    other_path = tmp_path / "copy.cbz"
    book = CbzBook(str(cbz_path))
    book.metadata.title = "Copy Title"
    book.save(output_path=str(other_path))

    original = CbzBook(str(cbz_path))
    assert original.metadata.title == "Test Book"  # unchanged
    copy = CbzBook(str(other_path))
    assert copy.metadata.title == "Copy Title"
    assert book.path == str(cbz_path)  # save-as doesn't adopt the new path


def test_save_creates_comicinfo_when_missing(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz", comicinfo=None)
    book = CbzBook(str(cbz_path))
    book.metadata.title = "New Title"
    book.save()

    with zipfile.ZipFile(cbz_path, "r") as zf:
        assert "ComicInfo.xml" in zf.namelist()

    reloaded = CbzBook(str(cbz_path))
    assert reloaded.metadata.title == "New Title"


def test_save_raises_when_load_failed(tmp_path):
    bad_path = tmp_path / "bad.cbz"
    bad_path.write_bytes(b"not a zip file")
    book = CbzBook(str(bad_path))
    with pytest.raises(CbzError):
        book.save()
