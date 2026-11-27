"""Tests for the 2026-11-27 review cards (one test per fixed card)."""

import io
import os
import zipfile

import pytest
from PIL import Image

from core import collection_scan, credit_pages, duplicates
from core.cbz_file import CbzBook, CbzError


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.effect_noise((width, height), 80).save(out, "PNG")
    return out.getvalue()


def _cbz_with_pages(path, pages: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in pages.items():
            zf.writestr(name, data)
    return str(path)


def test_card15_control_character_in_field_is_a_cbz_error(tmp_path):
    path = _cbz_with_pages(tmp_path / "book.cbz", {"001.png": _png(8, 8), "002.png": _png(8, 8)})
    book = CbzBook(path)
    before = open(path, "rb").read()
    book.metadata.summary = "page one" + chr(12) + "page two"
    book.dirty = True
    with pytest.raises(CbzError):
        book.save()
    assert open(path, "rb").read() == before  # the file is untouched


@pytest.fixture
def bomb_limit(monkeypatch):
    """Makes a 100x100 page count as a decompression bomb (limit 1000 pixels, error above twice that)."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)


def test_card16_cover_fingerprint_survives_a_huge_page(tmp_path, bomb_limit):
    names = ["001.png", "002.png"]
    path = _cbz_with_pages(tmp_path / "big.cbz", {name: _png(100, 100) for name in names})
    with zipfile.ZipFile(path) as archive:
        assert collection_scan.cover_fingerprint(archive, names, None)[0] == ""


def test_card16_credit_scan_survives_a_huge_page(tmp_path, bomb_limit):
    names = [f"{i:03d}.png" for i in range(8)]
    path = _cbz_with_pages(tmp_path / "big.cbz", {name: _png(100, 100) for name in names})
    candidates = credit_pages.scan_book(path, names)
    assert all(c.hash is None for c in candidates)


def test_card16_duplicate_fingerprint_survives_a_huge_page(tmp_path, bomb_limit):
    names = [f"{i:03d}.png" for i in range(12)]
    path = _cbz_with_pages(tmp_path / "big.cbz", {name: _png(100, 100) for name in names})
    result = duplicates.fingerprint_book(path, names)
    assert result.cover is None
    assert result.page_count == 12


def test_card16_read_comics_turns_an_unexpected_error_into_that_files_row(tmp_path, monkeypatch):
    items = [collection_scan.Listed("a.cbz", 1, "m"), collection_scan.Listed("b.cbz", 1, "m")]
    real = collection_scan.read_comic

    def flaky(root, listed, *args, **kwargs):
        if listed.path == "a.cbz":
            raise MemoryError("boom")
        return real(root, listed, *args, **kwargs)

    monkeypatch.setattr(collection_scan, "read_comic", flaky)
    rows, finished = collection_scan.read_comics(str(tmp_path), items, workers=1)
    assert finished
    assert set(rows) == {"a.cbz", "b.cbz"}
    assert "boom" in rows["a.cbz"].error


# --- Batch 2: save and archive -------------------------------------------------------------------------------

def _cbz_with_info(path, xml: bytes, pages=("001.png", "002.png")):
    entries = {name: _png(8, 8) for name in pages}
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        zf.writestr("ComicInfo.xml", xml)
    return str(path)


def _info_text(path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("ComicInfo.xml").decode("utf-8")


NAMESPACED_XML = (
    b'<?xml version="1.0"?><ComicInfo xmlns="http://example.com/ci"><Title>A</Title><Pages>'
    b'<Page Image="0" Type="FrontCover" ImageWidth="2000" ImageHeight="3000" ImageSize="12345"/>'
    b'<Page Image="1" DoublePage="true"/></Pages></ComicInfo>'
)


def test_card02_namespaced_root_keeps_pages_unprefixed_on_save(tmp_path):
    path = _cbz_with_info(tmp_path / "ns.cbz", NAMESPACED_XML)
    book = CbzBook(path)
    book.metadata.title = "B"
    book.dirty = True
    book.save()
    text = _info_text(path)
    assert "ns0" not in text
    assert "<Pages>" in text and 'Type="FrontCover"' in text


def test_card08_modern_image_formats_count_as_pages(tmp_path):
    names = ["001.avif", "002.jxl", "003.heic", "004.tif"]
    path = _cbz_with_pages(tmp_path / "modern.cbz", {name: b"not decoded here" for name in names})
    book = CbzBook(path)
    assert book.page_names == names
    book.save()
    assert CbzBook(path).metadata.page_count == "4"


def test_card11_first_copy_of_a_repeated_field_wins(tmp_path):
    xml = b"<ComicInfo><Series>First</Series><Series>Second</Series></ComicInfo>"
    path = _cbz_with_info(tmp_path / "dup.cbz", xml)
    book = CbzBook(path)
    assert book.metadata.series == "First"
    book.dirty = True
    book.save()
    text = _info_text(path)
    assert "First" in text and "Second" not in text


def test_card12_resize_drops_the_stale_page_sizes(tmp_path):
    out = io.BytesIO()
    Image.new("RGB", (400, 600), (90, 90, 90)).save(out, "JPEG")
    xml = (b'<ComicInfo><Pages><Page Image="0" Type="FrontCover" ImageWidth="400" ImageHeight="600" '
           b'ImageSize="1"/></Pages></ComicInfo>')
    path = tmp_path / "big.cbz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", out.getvalue())
        zf.writestr("ComicInfo.xml", xml)
    summary = CbzBook(str(path)).resize_images(200)
    assert summary.pages_resized == 1
    text = _info_text(path)
    assert "ImageWidth" not in text and "ImageHeight" not in text and "ImageSize" not in text
    assert 'Type="FrontCover"' in text  # the rest of the page entry is kept


def test_card23_circled_digits_do_not_crash_names_or_checks(tmp_path):
    from core.cbz_file import page_sort_key
    from core.comicinfo import ComicInfoMetadata
    from core.comicinfo_check import check_metadata

    assert page_sort_key("①1.jpg")  # no ValueError
    path = _cbz_with_pages(tmp_path / "odd.cbz", {"①1.png": _png(8, 8), "002.png": _png(8, 8)})
    book = CbzBook(path)
    assert not book.load_error and len(book.page_names) == 2
    findings = check_metadata(ComicInfoMetadata(year="²", month="²", day="²", number="²", count="²"))
    assert any(f.field == "year" for f in findings)
    book.metadata.page_count = "²"
    assert book.page_count_mismatch is False


def test_card24_deeply_nested_unknown_element_is_a_load_error_not_a_crash(tmp_path):
    depth = 3000
    xml = b"<ComicInfo><Title>T</Title>" + b"<x>" * depth + b"</x>" * depth + b"</ComicInfo>"
    path = _cbz_with_info(tmp_path / "deep.cbz", xml)
    book = CbzBook(path)
    assert "deep" in book.load_error
    with pytest.raises(CbzError):
        book.save()


def test_card25_save_as_onto_own_file_with_other_spelling_is_an_in_place_save(tmp_path):
    path = _cbz_with_pages(tmp_path / "Own.cbz", {"001.png": _png(8, 8)})
    book = CbzBook(path)
    book.metadata.title = "Changed"
    book.dirty = True
    other = path.replace(os.sep, "/")
    if os.name == "nt":
        other = other.swapcase()  # Windows ignores letter case in paths
    book.save(other, overwrite=True)
    assert book.dirty is False
    assert book.path == path
    assert CbzBook(path).metadata.title == "Changed"


def test_card44_oversized_comicinfo_is_refused_before_unpacking(tmp_path):
    xml = b"<ComicInfo><Summary>" + b"a" * (3 * 1024 * 1024) + b"</Summary></ComicInfo>"
    path = _cbz_with_info(tmp_path / "bomb.cbz", xml)
    assert "too large" in CbzBook(path).load_error


def test_card44_first_page_over_the_cap_is_not_read(tmp_path, monkeypatch):
    import core.cbz_file as cbz_file

    path = _cbz_with_pages(tmp_path / "p.cbz", {"001.png": _png(64, 64)})
    book = CbzBook(path)
    assert book.read_first_page_bytes() is not None
    monkeypatch.setattr(cbz_file, "MAX_PREVIEW_PAGE_BYTES", 10)
    assert book.read_first_page_bytes() is None
