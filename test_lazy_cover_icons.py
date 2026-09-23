"""
Cover images off the GUI thread (2026-09-23):

- table thumbnails in the Filename cell load lazily -- only rows on
  screen ever read/decode a cover (redactor_common's VisibleRowsWatcher +
  AsyncIconCache, epub's mechanism);
- the side panel's cover is read and decoded on a worker thread
  (AsyncPreviewLoader) instead of a full-resolution decode on every
  selection change.
"""

import os
import sys
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.cbz_file import CbzBook  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


def _jpeg() -> bytes:
    img = QImage(300, 450, QImage.Format.Format_RGB32)
    img.fill(QColor(30, 90, 200))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG")
    return bytes(data)


def _make_cbz(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.jpg", _jpeg())
    return CbzBook(str(path))


def _pump_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _app.processEvents()
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def window():
    w = MainWindow()
    w.resize(900, 400)
    w.show()
    yield w
    w.close()


def _load(window, books):
    window.books = books
    window._rebuild_table()


def test_only_visible_rows_read_their_cover(window, tmp_path, monkeypatch):
    books = [_make_cbz(tmp_path / f"{i:03}.cbz") for i in range(150)]
    reads = []
    for book in books:
        original = book.read_first_page_bytes
        monkeypatch.setattr(book, "read_first_page_bytes",
                            lambda original=original, book=book: (reads.append(book), original())[1])
    _load(window, books)
    window._visible_rows.check_now()
    col = window._col_index["filename"]
    assert _pump_until(lambda: not window.table.item(0, col).icon().isNull())
    # far fewer than 150: just the viewport plus the buffer
    assert 0 < len(reads) < 80
    assert window.table.item(149, col).icon().isNull()

    window.table.scrollToBottom()
    assert _pump_until(lambda: not window.table.item(149, col).icon().isNull())


def test_rebuild_reuses_cached_icons_without_rereading(window, tmp_path, monkeypatch):
    books = [_make_cbz(tmp_path / f"{i}.cbz") for i in range(3)]
    _load(window, books)
    window._visible_rows.check_now()
    col = window._col_index["filename"]
    assert _pump_until(lambda: all(not window.table.item(r, col).icon().isNull() for r in range(3)))

    def must_not_read():
        raise AssertionError("cover re-read for an unchanged file")

    for book in books:
        monkeypatch.setattr(book, "read_first_page_bytes", must_not_read)
    window._rebuild_table()
    assert all(not window.table.item(r, col).icon().isNull() for r in range(3))
    window._visible_rows.check_now()
    for _ in range(10):
        _app.processEvents()


def test_panel_cover_loads_in_the_background(window, tmp_path, monkeypatch):
    book = _make_cbz(tmp_path / "a.cbz")
    original = book.read_first_page_bytes

    def slow_read():
        time.sleep(0.4)
        return original()

    monkeypatch.setattr(book, "read_first_page_bytes", slow_read)
    _load(window, [book])
    started = time.monotonic()
    window.table.selectRow(0)
    _app.processEvents()
    assert time.monotonic() - started < 0.3  # selection didn't wait for the read
    label = window.panel.cover_label
    window._cover_preview.wait_for_done()
    assert _pump_until(lambda: label._original_pixmap is not None)
    assert label._original_pixmap.height() <= 1350
