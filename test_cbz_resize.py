"""Tests for CbzBook.resize_images() (core/cbz_file.py) -- built
against real temporary .cbz files with real Pillow-generated page
images, since this is the archive-level plumbing around
core/image_resize.py's per-page logic (already covered on its own in
test_image_resize.py): does it resize the right entries, leave
everything else untouched, support both in-place and export-to-a-new-
file modes, and report an accurate summary."""

import io
import zipfile

from PIL import Image

from core.cbz_file import CbzBook

COMICINFO_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Title>Test Book</Title>
  <PageCount>2</PageCount>
</ComicInfo>
"""


def _page_bytes(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color=(100, 110, 120)).save(out, format="JPEG")
    return out.getvalue()


def _make_cbz(path, pages: dict[str, bytes], comicinfo=COMICINFO_XML) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        if comicinfo is not None:
            zf.writestr("ComicInfo.xml", comicinfo)
        for name, data in pages.items():
            zf.writestr(name, data)
    return str(path)


def test_resize_in_place_shrinks_oversized_pages_only(tmp_path):
    small_page = _page_bytes(800, 1200)
    large_page = _page_bytes(3000, 4500)
    cbz_path = _make_cbz(tmp_path / "book.cbz", {"001.jpg": small_page, "002.jpg": large_page})

    book = CbzBook(cbz_path)
    summary = book.resize_images(max_width=1500)

    assert summary.pages_resized == 1
    assert summary.pages_skipped == 1
    assert summary.pages_failed == 0
    assert book.path == cbz_path  # in-place: path unchanged

    with zipfile.ZipFile(cbz_path) as zf:
        assert Image.open(io.BytesIO(zf.read("001.jpg"))).size == (800, 1200)  # untouched
        new_size = Image.open(io.BytesIO(zf.read("002.jpg"))).size
        assert new_size == (1500, 2250)  # resized, aspect ratio preserved


def test_resize_preserves_comicinfo_and_page_count(tmp_path):
    cbz_path = _make_cbz(tmp_path / "book.cbz", {"001.jpg": _page_bytes(3000, 4500)})
    book = CbzBook(cbz_path)
    book.resize_images(max_width=1000)

    with zipfile.ZipFile(cbz_path) as zf:
        assert zf.read("ComicInfo.xml") == COMICINFO_XML  # byte-for-byte, resize never touches metadata
    assert book.actual_page_count == 1  # resizing never adds/removes pages


def test_resize_export_mode_leaves_original_untouched(tmp_path):
    original_bytes = _page_bytes(3000, 4500)
    cbz_path = _make_cbz(tmp_path / "book.cbz", {"001.jpg": original_bytes})
    output_path = str(tmp_path / "resized.cbz")

    book = CbzBook(cbz_path)
    summary = book.resize_images(max_width=1000, output_path=output_path)

    assert summary.pages_resized == 1
    assert book.path == cbz_path  # export mode: book's own path is untouched

    with zipfile.ZipFile(cbz_path) as zf:
        assert zf.read("001.jpg") == original_bytes  # original file completely untouched

    with zipfile.ZipFile(output_path) as zf:
        assert Image.open(io.BytesIO(zf.read("001.jpg"))).size[0] == 1000


def test_double_page_spread_gets_double_width_through_the_full_archive_path(tmp_path):
    spread = _page_bytes(4000, 3000)  # landscape -- a spread
    cbz_path = _make_cbz(tmp_path / "book.cbz", {"001.jpg": spread})

    book = CbzBook(cbz_path)
    book.resize_images(max_width=1500)

    with zipfile.ZipFile(cbz_path) as zf:
        new_size = Image.open(io.BytesIO(zf.read("001.jpg"))).size
        assert new_size == (3000, 2250)  # 2x max_width (3000), not max_width (1500)


def test_non_image_entries_are_never_touched(tmp_path):
    cbz_path = _make_cbz(
        tmp_path / "book.cbz",
        {"001.jpg": _page_bytes(3000, 4500), "notes.txt": b"scanner notes, not a page"},
    )
    book = CbzBook(cbz_path)
    book.resize_images(max_width=1000)

    with zipfile.ZipFile(cbz_path) as zf:
        assert zf.read("notes.txt") == b"scanner notes, not a page"


def test_summary_reports_accurate_byte_totals(tmp_path):
    small_page = _page_bytes(800, 1200)
    cbz_path = _make_cbz(tmp_path / "book.cbz", {"001.jpg": small_page})
    book = CbzBook(cbz_path)
    summary = book.resize_images(max_width=4000)  # nothing needs resizing

    assert summary.pages_resized == 0
    assert summary.pages_skipped == 1
    assert summary.original_bytes == summary.new_bytes  # nothing changed size
