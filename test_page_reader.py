"""File > Preview...: the read-only page reader window and its pure navigation."""

import os
import sys
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.cbz_file import CbzBook  # noqa: E402
from core.page_reader import PageNavigator, clamp_index  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.page_reader_dialog import PageReaderDialog  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


def _jpeg(shade: int) -> bytes:
    img = QImage(120, 180, QImage.Format.Format_RGB32)
    img.fill(QColor(shade, 90, 200))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG")
    return bytes(data)


def _make_cbz(path, pages=5, corrupt=(), with_xml=True):
    with zipfile.ZipFile(path, "w") as zf:
        if with_xml:
            zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        for i in reversed(range(pages)):
            zf.writestr(f"p{i + 1:03}.jpg", b"not an image" if i in corrupt else _jpeg(20 + i * 10))
    return CbzBook(str(path))


def _pump(reader, seconds=0.3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        reader._loader.wait_for_done()
        reader._prefetch.wait_for_done()
        _app.processEvents()
        time.sleep(0.01)


def _open(book):
    reader = PageReaderDialog(book)
    reader.show()
    _pump(reader)
    return reader


def _close(reader):
    reader._loader.shutdown()
    reader._prefetch.shutdown()
    reader.close()


# ---------------------------------------------------------------- pure logic


@pytest.mark.parametrize("index,count,want", [(-3, 5, 0), (0, 5, 0), (4, 5, 4), (9, 5, 4), (3, 0, 0)])
def test_clamp_index(index, count, want):
    assert clamp_index(index, count) == want


def test_navigator_moves_and_never_leaves_the_list():
    nav = PageNavigator(["a", "b", "c"])
    assert nav.label() == "Page 1 / 3" and nav.at_first and not nav.at_last
    assert not nav.previous() and nav.index == 0
    assert nav.next() and nav.next() and nav.current_name == "c" and nav.at_last
    assert not nav.next() and nav.index == 2
    assert nav.first() and nav.index == 0
    assert nav.last() and nav.label() == "Page 3 / 3"
    assert PageNavigator(["a", "b"], start=99).index == 1


def test_navigator_with_no_pages():
    nav = PageNavigator([])
    assert nav.label() == "No pages" and nav.current_name == "" and not nav.has_pages
    assert not (nav.next() or nav.previous() or nav.first() or nav.last())


def test_navigator_copies_the_names():
    names = ["a", "b"]
    nav = PageNavigator(names)
    names.append("c")
    assert nav.count == 2


# ---------------------------------------------------------------------- GUI


def test_reader_shows_pages_in_reading_order_and_navigates(tmp_path):
    book = _make_cbz(tmp_path / "a.cbz", pages=12)
    reader = _open(book)
    try:
        assert reader.page_label.text() == "Page 1 / 12"
        assert reader.name_label.text() == "p001.jpg"
        assert reader.image_label.pixmap() is not None and not reader.image_label.pixmap().isNull()
        assert not reader.prev_button.isEnabled() and reader.next_button.isEnabled()
        QTest.keyClick(reader, Qt.Key.Key_Right)
        _pump(reader)
        assert reader.page_label.text() == "Page 2 / 12" and reader.name_label.text() == "p002.jpg"
        QTest.keyClick(reader, Qt.Key.Key_End)
        _pump(reader)
        assert reader.page_label.text() == "Page 12 / 12" and reader.name_label.text() == "p012.jpg"
        assert not reader.next_button.isEnabled()
        QTest.keyClick(reader, Qt.Key.Key_Right)  # clamped
        assert reader.page_label.text() == "Page 12 / 12"
        QTest.keyClick(reader, Qt.Key.Key_Left)
        assert reader.page_label.text() == "Page 11 / 12"
        QTest.keyClick(reader, Qt.Key.Key_Home)
        assert reader.page_label.text() == "Page 1 / 12"
        reader.last_button.click()
        assert reader.nav.index == 11
        reader.first_button.click()
        reader.next_button.click()
        reader.prev_button.click()
        assert reader.nav.index == 0
    finally:
        _close(reader)


def test_reader_reads_only_what_it_shows_plus_the_next_page(tmp_path, monkeypatch):
    book = _make_cbz(tmp_path / "a.cbz", pages=10)
    asked = []
    real = CbzBook.read_page_bytes
    monkeypatch.setattr(CbzBook, "read_page_bytes", lambda self, i: (asked.append(i), real(self, i))[1])
    reader = _open(book)
    try:
        assert sorted(set(asked)) == [0, 1]
    finally:
        _close(reader)


def test_corrupt_page_shows_a_message_and_the_reader_keeps_working(tmp_path):
    book = _make_cbz(tmp_path / "a.cbz", pages=4, corrupt=(1,))
    reader = _open(book)
    try:
        reader.next_button.click()
        _pump(reader)
        assert "could not be read" in reader.image_label.text()
        assert "p002.jpg" in reader.image_label.text()
        reader.next_button.click()
        _pump(reader)
        assert reader.image_label.text() == "" and not reader.image_label.pixmap().isNull()
    finally:
        _close(reader)


def test_archive_without_images(tmp_path):
    book = _make_cbz(tmp_path / "a.cbz", pages=0)
    reader = _open(book)
    try:
        assert reader.page_label.text() == "No pages"
        assert "no images" in reader.image_label.text()
        assert not any(b.isEnabled() for b in (reader.first_button, reader.prev_button, reader.next_button, reader.last_button))
        QTest.keyClick(reader, Qt.Key.Key_Right)  # nothing happens, nothing crashes
        assert reader.page_label.text() == "No pages"
    finally:
        _close(reader)


def test_unreadable_book_shows_its_error(tmp_path):
    path = tmp_path / "bad.cbz"
    path.write_bytes(b"this is not a zip")
    reader = _open(CbzBook(str(path)))
    try:
        assert reader.page_label.text() == "No pages"
        assert reader.image_label.text()
    finally:
        _close(reader)


def test_refits_on_resize_and_fit_width_toggle(tmp_path):
    book = _make_cbz(tmp_path / "a.cbz")
    reader = _open(book)
    try:
        reader.resize(400, 300)
        _pump(reader)
        small = reader.image_label.pixmap()
        assert small.height() <= reader.scroll.viewport().height()
        reader.resize(900, 1000)
        _pump(reader)
        big = reader.image_label.pixmap()
        assert big.height() > small.height()
        reader.fit_width.setChecked(True)
        _pump(reader)
        wide = reader.image_label.pixmap()
        assert wide.width() == reader.scroll.viewport().width()
        assert wide.height() > wide.width()  # a portrait page: taller than the window is wide
    finally:
        _close(reader)


def test_reading_never_modifies_the_archive(tmp_path):
    path = tmp_path / "a.cbz"
    book = _make_cbz(path, pages=6)
    before = (os.stat(path).st_mtime_ns, path.read_bytes())
    reader = _open(book)
    try:
        reader.last_button.click()
        _pump(reader)
    finally:
        _close(reader)
    assert (os.stat(path).st_mtime_ns, path.read_bytes()) == before


def test_main_window_preview_needs_exactly_one_selected_comic(tmp_path):
    win = MainWindow()
    try:
        paths = [str(tmp_path / "A.cbz"), str(tmp_path / "B.cbz")]
        _make_cbz(paths[0])
        _make_cbz(paths[1])
        win.show()
        win._load_paths(paths)
        win.table.clearSelection()
        win.actions_["preview"].trigger()
        assert "exactly one" in win.statusBar().currentMessage()
        assert not win.findChildren(PageReaderDialog)
        win.table.selectRow(0)
        _app.processEvents()
        win.actions_["preview"].trigger()
        readers = win.findChildren(PageReaderDialog)
        assert len(readers) == 1 and readers[0].windowTitle() == "Preview - A.cbz"
        assert not readers[0].isModal()
        _close(readers[0])
        assert win.actions_["preview"].shortcut().toString() == "Ctrl+Shift+P"
    finally:
        win._cover_preview.shutdown()
        win._page_preview.shutdown()
        win.close()
