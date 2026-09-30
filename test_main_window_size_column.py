"""Tests for MainWindow's Size / File Size columns: measured values land
in the right cell with the right band color, and sorting by Size
measures every book first (not only the rows that happened to be
visible) and orders numerically."""

import io
import sys
import zipfile

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from gui.main_window import SIZE_CATEGORY_COLORS, MainWindow
from core.page_dimensions import SIZE_LOW, SIZE_OK, SIZE_OVERSIZED

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path, width: int, height: int, pages: int = 3) -> str:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color=(50, 60, 70)).save(out, format="JPEG")
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(pages):
            zf.writestr(f"{i:03}.jpg", out.getvalue())
    return str(path)


@pytest.fixture
def window():
    return MainWindow()


def _size_cell(window, row):
    return window.table.item(row, window._col_index["size"])


def test_measured_size_shows_width_and_band_color(window, tmp_path):
    paths = [
        _cbz(tmp_path / "a_low.cbz", 800, 1200),
        _cbz(tmp_path / "b_ok.cbz", 1440, 2160),
        _cbz(tmp_path / "c_big.cbz", 2000, 3000),
    ]
    window._load_paths(paths)
    window._ensure_page_sizes(window.books)
    window._rebuild_table()

    expected = [("800px", SIZE_LOW), ("1440px", SIZE_OK), ("2000px", SIZE_OVERSIZED)]
    for row, (text, category) in enumerate(expected):
        item = _size_cell(window, row)
        assert item.text() == text
        assert item.background().color() == SIZE_CATEGORY_COLORS[category]
        assert "typical page width" in item.toolTip()


def test_sort_by_size_measures_everything_and_sorts_numerically(window, tmp_path):
    paths = [
        _cbz(tmp_path / "a.cbz", 2000, 3000),
        _cbz(tmp_path / "b.cbz", 900, 1350),
        _cbz(tmp_path / "c.cbz", 1440, 2160),
    ]
    window._load_paths(paths)
    size_col = window._col_index["size"]

    window._on_header_clicked(size_col)
    assert [_size_cell(window, r).text() for r in range(3)] == ["900px", "1440px", "2000px"]

    window._on_header_clicked(size_col)
    assert [_size_cell(window, r).text() for r in range(3)] == ["2000px", "1440px", "900px"]


def test_file_size_column_is_filled_and_sorts_by_bytes(window, tmp_path):
    small = _cbz(tmp_path / "small.cbz", 200, 300, pages=1)
    large = _cbz(tmp_path / "large.cbz", 1500, 2250, pages=5)
    window._load_paths([large, small])
    col = window._col_index["filesize"]
    assert window.table.item(0, col).text().endswith("KB")

    window._on_header_clicked(col)
    assert window.books[0].path == small


def test_resize_in_place_forgets_the_old_measurement(window, tmp_path):
    path = _cbz(tmp_path / "big.cbz", 2400, 3600)
    window._load_paths([path])
    book = window.books[0]
    before = window._ensure_page_sizes([book])[id(book)]
    assert before.category == SIZE_OVERSIZED

    book.resize_images(1440)
    window._size_source.pop(book)
    import os
    os.utime(path, (1, 1))  # a fresh mtime, even on a coarse-clock filesystem
    after = window._ensure_page_sizes([book])[id(book)]
    assert after.representative_width == 1440
    assert after.category == SIZE_OK


def test_resize_images_runs_off_thread_and_updates_rows(window, tmp_path, monkeypatch):
    import gui.main_window as mw
    from PyQt6.QtWidgets import QMessageBox

    paths = [_cbz(tmp_path / f"{n}.cbz", 1500, 2200, pages=4) for n in "ab"]
    window._load_paths(paths)
    window.table.selectAll()

    class FakeDialog:
        DialogCode = mw.ResizeImagesDialog.DialogCode
        def __init__(self, *a, **k): pass
        def exec(self): return self.DialogCode.Accepted
        def max_width(self): return 1000
        def max_height(self): return None
        def output_format(self): return None
        def jpeg_quality(self): return 85
        def is_export_mode(self): return False
        def oversized_only(self): return False
        def output_path_for(self, p): return None

    monkeypatch.setattr(mw, "ResizeImagesDialog", FakeDialog)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window.open_resize_images_dialog()
    for p in paths:
        with zipfile.ZipFile(p) as zf:
            im = Image.open(io.BytesIO(zf.read("000.jpg")))
            assert im.width == 1000
