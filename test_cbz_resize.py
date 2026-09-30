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


def _png_bytes(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color=(10, 20, 30)).save(out, format="PNG")
    return out.getvalue()


def test_output_format_renames_entries_and_keeps_order(tmp_path):
    path = _make_cbz(tmp_path / "book.cbz", {
        "001.png": _png_bytes(800, 1200),
        "002.jpeg": _page_bytes(800, 1200),
        "003.png": _png_bytes(3000, 4500),
    })
    book = CbzBook(path)
    summary = book.resize_images(1440, output_format="WEBP", workers=2)

    assert summary.pages_failed == 0
    assert book.page_names == ["001.webp", "002.webp", "003.webp"]
    with zipfile.ZipFile(path) as zf:
        assert "ComicInfo.xml" in zf.namelist()
        assert Image.open(io.BytesIO(zf.read("003.webp"))).size == (1440, 2160)


def test_jpeg_output_keeps_a_jpeg_extension_name(tmp_path):
    path = _make_cbz(tmp_path / "book.cbz", {"001.jpeg": _png_bytes(800, 1200)})  # misnamed PNG
    book = CbzBook(path)
    book.resize_images(1440, output_format="JPEG")
    assert book.page_names == ["001.jpeg"]
    with zipfile.ZipFile(path) as zf:
        assert Image.open(io.BytesIO(zf.read("001.jpeg"))).format == "JPEG"


def test_conversion_that_would_collide_keeps_the_original_format(tmp_path):
    path = _make_cbz(tmp_path / "book.cbz", {
        "001.png": _png_bytes(800, 1200),
        "001.gif": _png_bytes(800, 1200),  # both would become 001.webp
        "002.png": _png_bytes(800, 1200),
    })
    book = CbzBook(path)
    book.resize_images(1440, output_format="WEBP")
    assert book.page_names == ["001.gif", "001.png", "002.webp"]


def test_many_pages_parallel_keep_source_order(tmp_path):
    pages = {f"{i:03}.jpg": _page_bytes(1600 + i, 2400) for i in range(20)}
    path = _make_cbz(tmp_path / "book.cbz", pages)
    CbzBook(path).resize_images(1440, workers=4)
    with zipfile.ZipFile(path) as zf:
        assert [n for n in zf.namelist() if n.endswith(".jpg")] == sorted(pages)


def test_resize_reports_progress_and_can_be_cancelled(tmp_path):
    import pytest
    from core.cbz_file import ResizeCancelled

    pages = {f"{i:03d}.png": _png_bytes(3000, 200) for i in range(6)}
    path = _make_cbz(tmp_path / "a.cbz", pages)
    calls = []
    CbzBook(path).resize_images(1000, workers=2, progress=lambda d, t: calls.append((d, t)))
    assert calls[-1][0] == calls[-1][1] >= 6

    before = open(path, "rb").read()
    with pytest.raises(ResizeCancelled):
        CbzBook(path).resize_images(500, workers=2, should_cancel=lambda: True)
    assert open(path, "rb").read() == before
    assert not (tmp_path / "a.cbz.tmp_resize").exists()
