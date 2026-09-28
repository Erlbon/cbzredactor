"""Tests for core/page_dimensions.py -- the Size column's measuring and
banding, against real Pillow-generated pages in real .cbz files."""

import io
import zipfile

from PIL import Image

from core.page_dimensions import (
    SIZE_LOW,
    SIZE_OK,
    SIZE_OVERSIZED,
    PageSizeStats,
    classify_width,
    scan_page_sizes,
)


def _page(width: int, height: int, fmt: str = "JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color=(90, 100, 110)).save(out, format=fmt)
    return out.getvalue()


def _cbz(tmp_path, pages: dict[str, bytes]) -> str:
    path = tmp_path / "book.cbz"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in pages.items():
            zf.writestr(name, data)
    return str(path)


def test_bands_match_the_agreed_thresholds():
    assert classify_width(999) == SIZE_LOW
    assert classify_width(1000) == SIZE_OK  # exactly 1000 counts as acceptable
    assert classify_width(1599) == SIZE_OK
    assert classify_width(1600) == SIZE_OVERSIZED


def test_scan_reads_every_page_size(tmp_path):
    path = _cbz(tmp_path, {"001.jpg": _page(1200, 1800), "002.png": _page(1210, 1800, "PNG")})
    stats = scan_page_sizes(path, ["001.jpg", "002.png"])
    assert stats.sizes == [(1200, 1800), (1210, 1800)]
    assert stats.unreadable == 0


def test_spreads_do_not_push_a_book_into_oversized(tmp_path):
    pages = {f"{i:03}.jpg": _page(1400, 2100) for i in range(5)}
    pages["003.jpg"] = _page(2800, 2100)  # a double-page spread
    path = _cbz(tmp_path, pages)
    stats = scan_page_sizes(path, sorted(pages))
    assert stats.spread_count == 1
    assert stats.representative_width == 1400
    assert stats.category == SIZE_OK


def test_one_huge_cover_does_not_decide_the_band():
    stats = PageSizeStats(sizes=[(3000, 4500)] + [(900, 1350)] * 20)
    assert stats.representative_width == 900
    assert stats.category == SIZE_LOW


def test_landscape_only_album_falls_back_to_all_pages():
    stats = PageSizeStats(sizes=[(2000, 1400)] * 3)
    assert stats.representative_width == 2000
    assert stats.category == SIZE_OVERSIZED


def test_unreadable_page_is_counted_not_fatal(tmp_path):
    path = _cbz(tmp_path, {"001.jpg": _page(1000, 1500), "002.jpg": b"not an image"})
    stats = scan_page_sizes(path, ["001.jpg", "002.jpg", "missing.jpg"])
    assert stats.sizes == [(1000, 1500)]
    assert stats.unreadable == 2


def test_no_readable_pages_has_no_category():
    stats = PageSizeStats(unreadable=3)
    assert stats.representative_width is None
    assert stats.category is None
    assert "No readable" in stats.describe()


def test_describe_mentions_width_and_spreads():
    text = PageSizeStats(sizes=[(1988, 3056)] * 4 + [(3976, 3056)]).describe()
    assert "Oversized" in text
    assert "1988px (4)" in text
    assert "spreads: 1" in text
