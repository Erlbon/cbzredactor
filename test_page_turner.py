"""The side panel's page turner: turn through a book's pages in the preview.

Pages are loaded lazily -- only the entry asked for is ever read, on a
worker thread, with a small LRU of decoded pages; nothing is preloaded.
"""

import os
import sys
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import cbz_file  # noqa: E402
from core.cbz_file import CbzBook  # noqa: E402
from core.page_cache import LruCache  # noqa: E402
from core.zip_names import ArchiveReader  # noqa: E402
from gui import main_window as mw  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.metadata_panel import ComicInfoPanel  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


def _jpeg(shade: int) -> bytes:
    img = QImage(120, 180, QImage.Format.Format_RGB32)
    img.fill(QColor(shade, 90, 200))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG")
    return bytes(data)


def _make_cbz(path, pages=8, corrupt=()):
    with zipfile.ZipFile(path, "w") as zf:
        # written out of order on purpose: reading order is the natural sort of the names
        for i in reversed(range(pages)):
            data = b"not an image" if i in corrupt else _jpeg(20 + i * 10)
            zf.writestr(f"p{i + 1:03}.jpg", data)
    return CbzBook(str(path))


def _pump(window, seconds=0.0):
    window._cover_preview.wait_for_done()
    window._page_preview.wait_for_done()
    deadline = time.monotonic() + seconds
    _app.processEvents()
    while time.monotonic() < deadline:
        _app.processEvents()
        time.sleep(0.01)
    _app.processEvents()


@pytest.fixture
def reads(monkeypatch, tmp_path):
    log = []
    original = ArchiveReader.read

    def _read(self, name, *a, **k):
        # only this test's own files: background loads leaked from other tests also read
        if os.path.dirname(str(self.filename)) == str(tmp_path):
            log.append(os.path.basename(str(self.filename)) + ":" + (name if isinstance(name, str) else name.filename))
        return original(self, name, *a, **k)

    monkeypatch.setattr(ArchiveReader, "read", _read)
    return log


@pytest.fixture
def window():
    w = MainWindow()
    yield w
    w._cover_preview.shutdown()
    w._page_preview.shutdown()
    w.close()


def _load(window, books):
    window.books = books
    window._rebuild_table()


# ---------------------------------------------------------------- core


def test_read_page_bytes_reads_only_the_requested_entry(tmp_path, reads):
    book = _make_cbz(tmp_path / "a.cbz")
    reads.clear()
    data = book.read_page_bytes(2)
    assert data == _jpeg(20 + 2 * 10)
    assert reads == ["a.cbz:p003.jpg"]


def test_pages_are_in_the_sorted_natural_order(tmp_path):
    book = _make_cbz(tmp_path / "a.cbz", pages=12)
    assert book.page_names == [f"p{i:03}.jpg" for i in range(1, 13)]
    assert book.read_page_bytes(11) == _jpeg(20 + 11 * 10)


@pytest.mark.parametrize("index", [-1, 8, 100])
def test_read_page_bytes_out_of_range_is_none(tmp_path, index):
    assert _make_cbz(tmp_path / "a.cbz").read_page_bytes(index) is None


def test_read_page_bytes_refuses_a_huge_entry(tmp_path, monkeypatch, reads):
    book = _make_cbz(tmp_path / "a.cbz")
    monkeypatch.setattr(cbz_file, "MAX_PREVIEW_PAGE_BYTES", 10)
    reads.clear()
    assert book.read_page_bytes(1) is None
    assert reads == []  # refused from the directory entry, never read


def test_read_page_bytes_survives_a_vanished_file(tmp_path):
    book = _make_cbz(tmp_path / "a.cbz")
    os.remove(book.path)
    assert book.read_page_bytes(1) is None


def test_lru_cache_is_bounded_and_evicts_least_recent():
    cache = LruCache(3)
    for key in "abc":
        cache.put(key, key.upper())
    assert cache.get("a") == "A"  # a is now most recent
    cache.put("d", "D")  # evicts b
    assert len(cache) == 3 and "b" not in cache and "a" in cache
    cache.clear()
    assert len(cache) == 0
    with pytest.raises(ValueError):
        LruCache(0)


# --------------------------------------------------------------- panel


def test_panel_without_pages_has_disabled_controls():
    panel = ComicInfoPanel()
    assert not panel.prev_page_btn.isEnabled() and not panel.next_page_btn.isEnabled()
    assert panel.page_label.text() == ""


def test_panel_single_page_book_cannot_turn():
    panel = ComicInfoPanel()
    panel.set_pages(1)
    assert panel.page_label.text() == "Page 1 / 1"
    assert not panel.prev_page_btn.isEnabled() and not panel.next_page_btn.isEnabled()
    asked = []
    panel.pageRequested.connect(asked.append)
    panel.turn_page(1)
    assert asked == []


def test_panel_bounds_and_label():
    panel = ComicInfoPanel()
    asked = []
    panel.pageRequested.connect(asked.append)
    panel.set_pages(120)
    assert panel.page_label.text() == "Page 1 / 120"
    assert not panel.prev_page_btn.isEnabled() and panel.next_page_btn.isEnabled()
    panel.turn_page(-1)  # before the first: nothing
    assert asked == [] and panel.page_index == 0
    panel.turn_page(1)
    panel.turn_page(1)
    assert panel.page_label.text() == "Page 3 / 120" and asked == [1, 2]
    panel.go_to_page(500)  # clamped to the last page
    assert panel.page_index == 119 and not panel.next_page_btn.isEnabled()
    panel.turn_page(1)
    assert asked == [1, 2, 119]
    panel.go_to_page(-5)
    assert panel.page_index == 0 and panel.cover_box.title() == "Cover (First Page)"


def test_panel_arrow_keys_and_ctrl_wheel_on_the_preview():
    panel = ComicInfoPanel()
    panel.set_pages(5)

    def key(k):
        return QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier)

    def wheel(dy, mods):
        return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, dy),
                           Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)

    assert panel.eventFilter(panel.cover_label, key(Qt.Key.Key_Right))
    assert panel.eventFilter(panel.cover_label, key(Qt.Key.Key_Right))
    assert panel.page_index == 2
    assert panel.eventFilter(panel.cover_label, key(Qt.Key.Key_Left))
    assert panel.page_index == 1
    panel.eventFilter(panel.cover_label, wheel(-120, Qt.KeyboardModifier.ControlModifier))
    assert panel.page_index == 2
    panel.eventFilter(panel.cover_label, wheel(120, Qt.KeyboardModifier.ControlModifier))
    assert panel.page_index == 1
    panel.eventFilter(panel.cover_label, wheel(-120, Qt.KeyboardModifier.NoModifier))  # plain wheel: no turn
    assert panel.page_index == 1


def test_panel_bulk_and_disabled_modes_turn_the_controls_off():
    panel = ComicInfoPanel()
    panel.set_pages(5, 3)
    panel.set_bulk_mode(3)
    assert panel.page_total == 0 and not panel.next_page_btn.isEnabled()
    panel.set_pages(5)
    panel.set_enabled(False)
    assert panel.page_total == 0 and panel.page_label.text() == ""


# -------------------------------------------------------- main window


def test_nothing_but_the_cover_is_read_on_selection(window, tmp_path, reads):
    _load(window, [_make_cbz(tmp_path / "a.cbz")])
    reads.clear()
    window.table.selectRow(0)
    _pump(window)
    assert reads == ["a.cbz:p001.jpg"]  # no page 2, no preloading
    assert window.panel.page_label.text() == "Page 1 / 8"
    assert window.panel.cover_label.text() == ""  # image shown


def test_turning_reads_just_that_page_and_caches_it(window, tmp_path, reads):
    _load(window, [_make_cbz(tmp_path / "a.cbz")])
    window.table.selectRow(0)
    _pump(window)
    reads.clear()
    window.panel.turn_page(1)
    assert window.panel.cover_label.text() == "Loading cover…"  # placeholder while it loads
    _pump(window)
    assert reads == ["a.cbz:p002.jpg"]
    assert window.panel.cover_label.text() == "" and window.panel.page_label.text() == "Page 2 / 8"
    window.panel.turn_page(-1)  # the cover came from the cache
    window.panel.turn_page(1)  # and so does page 2
    _pump(window)
    assert reads == ["a.cbz:p002.jpg"]
    assert window.panel.cover_label.text() == ""


def test_cache_stays_bounded(window, tmp_path, reads):
    _load(window, [_make_cbz(tmp_path / "a.cbz", pages=12)])
    window.table.selectRow(0)
    _pump(window)
    for _ in range(11):
        window.panel.turn_page(1)
        _pump(window)
    assert len(window._page_cache) <= mw.PAGE_CACHE_SIZE
    assert window.panel.page_label.text() == "Page 12 / 12"


def test_selection_change_resets_to_page_one(window, tmp_path):
    _load(window, [_make_cbz(tmp_path / "a.cbz"), _make_cbz(tmp_path / "b.cbz", pages=3)])
    window.table.selectRow(0)
    _pump(window)
    window.panel.go_to_page(4)
    _pump(window)
    assert window.panel.page_index == 4
    window.table.selectRow(1)
    _pump(window)
    assert window.panel.page_index == 0 and window.panel.page_label.text() == "Page 1 / 3"
    window.table.selectRow(0)
    _pump(window)
    assert window.panel.page_index == 0 and window.panel.page_label.text() == "Page 1 / 8"


def test_multi_selection_and_no_selection_disable_the_controls(window, tmp_path):
    _load(window, [_make_cbz(tmp_path / "a.cbz"), _make_cbz(tmp_path / "b.cbz")])
    window.table.selectRow(0)
    _pump(window)
    assert window.panel.next_page_btn.isEnabled()
    window.table.selectAll()
    _pump(window)
    assert window.panel.page_total == 0 and not window.panel.next_page_btn.isEnabled()
    assert len(window._page_cache) == 0
    window.table.clearSelection()
    _pump(window)
    assert window.panel.page_total == 0 and not window.panel.next_page_btn.isEnabled()


def test_unconverted_book_has_no_page_controls(window, tmp_path):
    book = _make_cbz(tmp_path / "a.cbz")
    book.path = str(tmp_path / "a.cbr")  # looks foreign: read-only, panel off
    _load(window, [book])
    window.table.selectRow(0)
    _pump(window)
    assert window.panel.page_total == 0 and not window.panel.next_page_btn.isEnabled()


def test_corrupt_page_shows_a_message_not_a_crash(window, tmp_path):
    _load(window, [_make_cbz(tmp_path / "a.cbz", pages=4, corrupt=(2,))])
    window.table.selectRow(0)
    _pump(window)
    window.panel.go_to_page(2)
    _pump(window)
    assert window.panel.cover_label.text() == "Could not read page 3"
    assert len(window._page_cache) == 1  # only the good cover; the failure is not cached
    window.panel.turn_page(1)
    _pump(window)
    assert window.panel.cover_label.text() == "" and window.panel.page_label.text() == "Page 4 / 4"


def test_stale_load_is_dropped_when_the_user_already_moved_on(window, tmp_path):
    _load(window, [_make_cbz(tmp_path / "a.cbz")])
    window.table.selectRow(0)
    _pump(window)
    window.panel.go_to_page(3)
    window.panel.go_to_page(5)  # superseded request: only page 6 is delivered
    _pump(window)
    assert window.panel.page_label.text() == "Page 6 / 8"
    assert window.panel.cover_label.text() == ""
