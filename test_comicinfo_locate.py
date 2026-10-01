"""One rule for finding ComicInfo.xml (core/comicinfo_locate.py), used by the
editor, the collection scan, the fingerprint and every rewrite."""

import posixpath
import zipfile

import pytest

import core.collection_scan as scan
from core.cbz_file import CbzBook
from core.cbz_fingerprint import cbz_fingerprint
from core.comicinfo_locate import find_comicinfo_entry, locate_comicinfo


def _xml(series="Saga", title="T"):
    return f"<?xml version='1.0'?><ComicInfo><Title>{title}</Title><Series>{series}</Series></ComicInfo>".encode()


def _make(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return str(path)


def _info_entries(path):
    with zipfile.ZipFile(path) as zf:
        return [n for n in zf.namelist() if posixpath.basename(n).lower() == "comicinfo.xml"]


# --- the rule ------------------------------------------------------------------


def test_root_wins_in_any_case_and_any_position():
    assert find_comicinfo_entry(["a/ComicInfo.xml", "001.jpg", "comicinfo.XML"]) == "comicinfo.XML"
    assert find_comicinfo_entry(["ComicInfo.xml", "a/ComicInfo.xml"]) == "ComicInfo.xml"
    assert locate_comicinfo(["a/ComicInfo.xml", "ComicInfo.xml"]).nested is False


def test_a_single_nested_one_is_used_for_reading():
    found = locate_comicinfo(["pages/1.jpg", "Book/ComicInfo.xml"])
    assert found.name == "Book/ComicInfo.xml" and found.nested and found.others == ()


def test_several_nested_ones_shallowest_then_alphabetical():
    names = ["b/c/ComicInfo.xml", "z/ComicInfo.xml", "a/comicinfo.xml", "1.jpg"]
    found = locate_comicinfo(names)
    assert found.name == "a/comicinfo.xml"
    assert found.others == ("z/ComicInfo.xml", "b/c/ComicInfo.xml")


def test_non_candidates_are_ignored():
    assert find_comicinfo_entry([]) is None
    assert find_comicinfo_entry(["001.jpg", "notComicInfo.xml", "ComicInfo.xml.bak", "ComicInfo.xml/"]) is None
    assert find_comicinfo_entry(["__MACOSX/Book/ComicInfo.xml", "Book/__MACOSX/ComicInfo.xml"]) is None
    assert find_comicinfo_entry(["__MACOSX/Book/ComicInfo.xml", "Book/ComicInfo.xml"]) == "Book/ComicInfo.xml"


# --- the editor ------------------------------------------------------------------


def test_editor_reads_a_nested_comicinfo(tmp_path):
    path = _make(tmp_path / "a.cbz", [("Book/001.jpg", b"x"), ("Book/ComicInfo.xml", _xml("Nested"))])
    book = CbzBook(path)
    assert book.comicinfo_name == "Book/ComicInfo.xml"
    assert book.metadata.series == "Nested"
    assert book.load_warning == ""


def test_several_nested_ones_raise_a_load_warning(tmp_path):
    path = _make(tmp_path / "a.cbz", [
        ("001.jpg", b"x"), ("b/ComicInfo.xml", _xml("B")), ("a/ComicInfo.xml", _xml("A")),
    ])
    book = CbzBook(path)
    assert book.metadata.series == "A" and book.comicinfo_name == "a/ComicInfo.xml"
    assert "a/ComicInfo.xml" in book.load_warning and "b/ComicInfo.xml" in book.load_warning
    assert not book.load_error


def test_root_beside_nested_is_not_ambiguous(tmp_path):
    path = _make(tmp_path / "a.cbz", [("ComicInfo.xml", _xml("Root")), ("a/ComicInfo.xml", _xml("A"))])
    book = CbzBook(path)
    assert book.metadata.series == "Root" and book.load_warning == ""


# --- save --------------------------------------------------------------------------


def test_save_promotes_a_nested_comicinfo_to_the_root_without_a_duplicate(tmp_path):
    path = _make(tmp_path / "a.cbz", [("Book/001.jpg", b"x"), ("Book/ComicInfo.xml", _xml("Nested"))])
    book = CbzBook(path)
    book.metadata.title = "Edited"
    book.save()
    assert _info_entries(path) == ["ComicInfo.xml"]
    again = CbzBook(path)
    assert again.comicinfo_name == "ComicInfo.xml" and again.metadata.title == "Edited"
    assert again.metadata.series == "Nested"
    assert again.page_names == ["Book/001.jpg"]
    assert book.comicinfo_name == "ComicInfo.xml" and book.load_warning == ""


def test_save_with_ambiguous_nested_ones_drops_only_the_source(tmp_path):
    path = _make(tmp_path / "a.cbz", [
        ("001.jpg", b"x"), ("b/ComicInfo.xml", _xml("B")), ("a/ComicInfo.xml", _xml("A")),
    ])
    book = CbzBook(path)
    book.save()
    assert sorted(_info_entries(path)) == ["ComicInfo.xml", "b/ComicInfo.xml"]
    assert CbzBook(path).load_warning == ""  # the root one now wins


def test_save_leaves_nested_copies_alone_when_a_root_one_exists(tmp_path):
    path = _make(tmp_path / "a.cbz", [("ComicInfo.xml", _xml("Root")), ("a/ComicInfo.xml", _xml("A"))])
    book = CbzBook(path)
    book.metadata.title = "Edited"
    book.save()
    assert sorted(_info_entries(path)) == ["ComicInfo.xml", "a/ComicInfo.xml"]
    with zipfile.ZipFile(path) as zf:
        assert b"<Series>A</Series>" in zf.read("a/ComicInfo.xml")
    assert CbzBook(path).metadata.title == "Edited"


def test_remove_pages_promotes_a_nested_comicinfo_too(tmp_path):
    path = _make(tmp_path / "a.cbz", [
        ("Book/1.jpg", b"a"), ("Book/2.jpg", b"b"), ("Book/ComicInfo.xml", _xml("Nested")),
    ])
    book = CbzBook(path)
    book.remove_pages(["Book/2.jpg"])
    assert _info_entries(path) == ["ComicInfo.xml"]
    assert book.comicinfo_name == "ComicInfo.xml"
    assert CbzBook(path).metadata.page_count == "1"


def test_metadata_only_save_keeps_the_stamp_valid_after_promotion(tmp_path):
    path = _make(tmp_path / "a.cbz", [("Book/001.jpg", b"x"), ("Book/ComicInfo.xml", _xml("Nested"))])
    before = cbz_fingerprint(path)
    book = CbzBook(path)
    book.save()
    assert cbz_fingerprint(path) == before


# --- the collection report agrees with the editor ------------------------------------------


@pytest.mark.parametrize("entries", [
    [("001.jpg", b"x")],
    [("001.jpg", b"x"), ("ComicInfo.xml", _xml("Root"))],
    [("Book/001.jpg", b"x"), ("Book/ComicInfo.xml", _xml("Nested"))],
    [("001.jpg", b"x"), ("z/ComicInfo.xml", _xml("Z")), ("a/b/ComicInfo.xml", _xml("Deep")), ("m/ComicInfo.xml", _xml("M"))],
    [("ComicInfo.xml", _xml("Root")), ("a/ComicInfo.xml", _xml("A"))],
    [("__MACOSX/ComicInfo.xml", _xml("Junk")), ("001.jpg", b"x")],
])
def test_report_and_editor_agree(tmp_path, entries):
    path = _make(tmp_path / "a.cbz", entries)
    book = CbzBook(path)
    (item,) = list(scan.list_comics(str(tmp_path)))
    row = scan.read_comic(str(tmp_path), item)
    assert (row.comicinfo == "yes") == (book.comicinfo_name is not None)
    assert row.series == book.metadata.series
