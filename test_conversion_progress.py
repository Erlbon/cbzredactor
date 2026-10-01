"""CBR/CBT/CB7 -> CBZ conversion: speed guards, progress and Cancel.

Core (core/foreign_archive_convert.py): page images are stored, not deflated
a second time (the old behaviour spent ~90% of a conversion's time compressing
JPEGs that don't shrink); progress is reported in bytes over extract / pack /
check; Cancel leaves no .cbz, no temp file and no extracted folder behind.

GUI (gui/conversion_progress.py + MainWindow): every conversion path runs
convert_to_cbz on a worker thread under one progress dialog, the event loop
keeps running meanwhile, and Cancel stops the batch without an error dialog.
"""

import io
import os
import sys
import tarfile
import tempfile
import threading
import time
import types
import zipfile

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

import gui.main_window as mw
from core.foreign_archive_convert import (
    STORED_EXTENSIONS,
    ConversionCancelled,
    ForeignArchiveConversionError,
    convert_to_cbz,
)
from gui import app_settings

_app = QApplication.instance() or QApplication(sys.argv)


def _make_cbt(path, files: dict[str, bytes]) -> str:
    with tarfile.open(path, "w") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(path)


def _pages(count: int, size: int = 200_000) -> dict[str, bytes]:
    # Incompressible, like a real JPEG.
    files = {f"{i:03}.jpg": b"\xff\xd8" + os.urandom(size) for i in range(count)}
    files["ComicInfo.xml"] = b"<ComicInfo><Title>" + b"T" * 2000 + b"</Title></ComicInfo>"
    return files


def _leftovers(folder) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def _convert_temp_dirs() -> set[str]:
    return {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("cbzredactor_convert_")}


# -- core: speed guard ---------------------------------------------------------


def test_page_images_are_stored_not_deflated_again(tmp_path):
    """Regression guard for the slow conversions: deflating already-compressed
    pages was most of the time. Pages are stored; ComicInfo.xml is deflated."""
    output = convert_to_cbz(_make_cbt(tmp_path / "book.cbt", _pages(5)))
    with zipfile.ZipFile(output) as zf:
        for info in zf.infolist():
            if info.filename.endswith(".jpg"):
                assert info.compress_type == zipfile.ZIP_STORED, info.filename
                assert info.compress_size == info.file_size
            else:
                assert info.compress_type == zipfile.ZIP_DEFLATED
                assert info.compress_size < info.file_size
        assert zf.testzip() is None
        assert zf.read("000.jpg")[:2] == b"\xff\xd8"


def test_stored_extensions_cover_the_common_page_formats():
    assert {".jpg", ".jpeg", ".png", ".webp", ".gif"} <= STORED_EXTENSIONS


def test_conversion_does_not_hold_pages_in_memory_or_recompress(tmp_path, monkeypatch):
    """No page goes through zlib.compressobj: nothing is deflated but the XML."""
    import zlib

    compressed_bytes = []
    real = zlib.compressobj

    def counting(*a, **k):
        obj = real(*a, **k)
        original = obj.compress
        return types.SimpleNamespace(
            compress=lambda data: compressed_bytes.append(len(data)) or original(data),
            flush=obj.flush,
        )

    monkeypatch.setattr(zlib, "compressobj", counting)
    convert_to_cbz(_make_cbt(tmp_path / "book.cbt", _pages(4)))
    assert sum(compressed_bytes) < 10_000  # only ComicInfo.xml


# -- core: progress -------------------------------------------------------------


def test_progress_runs_through_extract_pack_check_and_never_goes_backwards(tmp_path):
    calls = []
    output = convert_to_cbz(
        _make_cbt(tmp_path / "book.cbt", _pages(6)), progress=lambda d, t, s: calls.append((d, t, s))
    )
    assert os.path.exists(output)
    stages = [s for _d, _t, s in calls]
    assert stages[0] == "Extracting" and "Packing" in stages and stages[-1] == "Checking"
    assert stages == sorted(stages, key=["Extracting", "Packing", "Checking"].index)
    totals = {t for _d, t, _s in calls}
    assert len(totals) == 1 and totals.pop() > 0
    done = [d for d, _t, _s in calls]
    assert done == sorted(done)
    assert done[-1] <= calls[-1][1]


def test_progress_is_indeterminate_when_the_size_is_unknown(tmp_path, monkeypatch):
    calls = []
    fake = _fake_rarfile(monkeypatch, expected_raises=True)
    (tmp_path / "book.cbr").write_bytes(b"Rar!\x1a\x07\x00 not really")
    convert_to_cbz(str(tmp_path / "book.cbr"), progress=lambda d, t, s: calls.append((d, t, s)))
    assert calls and all(total == 0 for _d, total, _s in calls)
    assert fake.extracted


def _fake_rarfile(monkeypatch, expected_raises=False, size=1000):
    """A rarfile stand-in whose extractall writes two 'pages' (no RAR tool needed)."""
    state = types.SimpleNamespace(extracted=False)

    class _Error(Exception):
        pass

    class _Info:
        file_size = size

        @staticmethod
        def is_dir():
            return False

    class _RarFile:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def infolist(self):
            if expected_raises:
                raise _Error("no listing")
            return [_Info(), _Info()]

        def extractall(self, dest):
            time.sleep(0.25)  # long enough for the monitor to poll
            for name in ("001.jpg", "002.jpg"):
                with open(os.path.join(dest, name), "wb") as handle:
                    handle.write(b"x" * size)
            state.extracted = True

    module = types.SimpleNamespace(RarFile=_RarFile, RarCannotExec=type("RarCannotExec", (_Error,), {}), Error=_Error)
    monkeypatch.setitem(sys.modules, "rarfile", module)
    return state


def test_cbr_progress_is_byte_level_while_the_extractor_runs(tmp_path, monkeypatch):
    _fake_rarfile(monkeypatch)
    (tmp_path / "book.cbr").write_bytes(b"Rar!\x1a\x07\x00 not really")
    calls = []
    output = convert_to_cbz(str(tmp_path / "book.cbr"), progress=lambda d, t, s: calls.append((d, t, s)))
    assert zipfile.ZipFile(output).namelist() == ["001.jpg", "002.jpg"]
    assert all(t == 3 * 2000 for _d, t, _s in calls)
    assert any(s == "Extracting" for _d, _t, s in calls)


# -- core: cancel -----------------------------------------------------------------


def test_cancel_before_start_does_nothing(tmp_path):
    path = _make_cbt(tmp_path / "book.cbt", _pages(2))
    with pytest.raises(ConversionCancelled):
        convert_to_cbz(path, should_cancel=lambda: True)
    assert _leftovers(tmp_path) == ["book.cbt"]


@pytest.mark.parametrize("stage", ["Extracting", "Packing", "Checking"])
def test_cancel_in_any_stage_leaves_no_file_and_no_temp_folder(tmp_path, stage):
    path = _make_cbt(tmp_path / "book.cbt", _pages(8))
    before = _convert_temp_dirs()
    seen = []

    def progress(done, total, current):
        seen.append(current)

    def should_cancel():
        return bool(seen) and seen[-1] == stage

    with pytest.raises(ConversionCancelled):
        convert_to_cbz(path, progress=progress, should_cancel=should_cancel)
    assert _leftovers(tmp_path) == ["book.cbt"]  # no .cbz, no .tmp_convert
    assert _convert_temp_dirs() == before


def test_cancelled_is_a_conversion_error_for_old_callers():
    assert issubclass(ConversionCancelled, ForeignArchiveConversionError)


def test_cancel_after_a_cbr_extraction_that_cannot_be_interrupted(tmp_path, monkeypatch):
    _fake_rarfile(monkeypatch)
    (tmp_path / "book.cbr").write_bytes(b"Rar!\x1a\x07\x00 not really")
    flag = threading.Event()
    threading.Timer(0.1, flag.set).start()
    with pytest.raises(ConversionCancelled):
        convert_to_cbz(str(tmp_path / "book.cbr"), should_cancel=flag.is_set)
    assert _leftovers(tmp_path) == ["book.cbr"]


def test_cb7_cancel_is_honoured_when_the_extraction_ends(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    path = tmp_path / "book.cb7"
    with py7zr.SevenZipFile(path, "w") as zf:
        for name, data in _pages(3).items():
            zf.writestr(data, name)
    with pytest.raises(ConversionCancelled):
        convert_to_cbz(str(path), should_cancel=lambda: True)
    assert _leftovers(tmp_path) == ["book.cb7"]


def test_a_real_cb7_reports_progress_and_converts(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    path = tmp_path / "book.cb7"
    with py7zr.SevenZipFile(path, "w") as zf:
        for name, data in _pages(3).items():
            zf.writestr(data, name)
    calls = []
    output = convert_to_cbz(str(path), progress=lambda d, t, s: calls.append((d, t, s)))
    assert sorted(zipfile.ZipFile(output).namelist()) == ["000.jpg", "001.jpg", "002.jpg", "ComicInfo.xml"]
    assert calls and calls[-1][2] == "Checking" and calls[-1][1] > 0


def test_mislabeled_zip_copy_reports_progress_and_can_be_cancelled(tmp_path):
    src = tmp_path / "book.cbr"  # really a ZIP
    with zipfile.ZipFile(src, "w") as zf:
        for name, data in _pages(3).items():
            zf.writestr(name, data)
    calls = []
    output = convert_to_cbz(str(src), progress=lambda d, t, s: calls.append(s))
    assert os.path.exists(output) and {"Checking", "Copying"} <= set(calls)
    os.remove(output)
    with pytest.raises(ConversionCancelled):
        convert_to_cbz(str(src), should_cancel=lambda: True)
    assert _leftovers(tmp_path) == ["book.cbr"]


# -- GUI: worker thread, repainting, Cancel -------------------------------------------


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(app_settings, "load_foreign_load_behavior", lambda: app_settings.FOREIGN_LOAD_CONVERT)
    monkeypatch.setattr(app_settings, "load_recycle_originals", lambda: False)
    monkeypatch.setattr(mw, "move_to_trash", lambda path: os.remove(path))
    # A modal "n files failed" box would hang a test: fail loudly instead.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: (_ for _ in ()).throw(AssertionError("warning shown")))
    return mw.MainWindow()


def _slow_converter(monkeypatch, record, seconds=0.4):
    """convert_to_cbz stand-in: slow, reports progress, honours Cancel."""
    real = convert_to_cbz

    def slow(source, output_path=None, progress=None, should_cancel=None):
        record.append({"thread": threading.current_thread(), "source": source,
                       "has_progress": progress is not None, "has_cancel": should_cancel is not None})
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if progress:
                progress(int((1 - (end - time.monotonic()) / seconds) * 300), 300, "Extracting")
            if should_cancel and should_cancel():
                raise ConversionCancelled("Conversion cancelled.")
            time.sleep(0.01)
        return real(source, output_path)

    monkeypatch.setattr(mw, "convert_to_cbz", slow)


def test_convert_selected_runs_off_the_gui_thread_and_the_ui_keeps_repainting(window, tmp_path, monkeypatch):
    paths = [_make_cbt(tmp_path / f"{n}.cbt", _pages(2)) for n in ("a", "b")]
    for p in paths:
        book = mw.CbzBook(p)
        window.books.append(book)
        window._add_table_row(book)
    assert all(b.needs_conversion for b in window.books)

    record = []
    _slow_converter(monkeypatch, record)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    window._convert_books(list(window.books), False)
    timer.stop()

    assert len(record) == 2
    assert all(r["thread"] is not threading.main_thread() for r in record)
    assert all(r["has_progress"] and r["has_cancel"] for r in record)
    assert len(ticks) > 10  # the event loop ran during the ~0.8 s of conversion
    assert not any(b.needs_conversion for b in window.books)
    assert (tmp_path / "a.cbz").exists() and (tmp_path / "b.cbz").exists()


def test_loading_with_convert_on_load_uses_the_worker_thread_even_for_one_file(window, tmp_path, monkeypatch):
    record = []
    _slow_converter(monkeypatch, record, seconds=0.1)
    window._load_paths([_make_cbt(tmp_path / "one.cbt", _pages(1))])
    assert len(record) == 1 and record[0]["thread"] is not threading.main_thread()
    assert len(window.books) == 1 and not window.books[0].needs_conversion


def test_cancel_stops_the_batch_without_error_or_half_written_files(window, tmp_path, monkeypatch):
    paths = [_make_cbt(tmp_path / f"{n}.cbt", _pages(2)) for n in ("a", "b", "c")]
    for p in paths:
        book = mw.CbzBook(p)
        window.books.append(book)
        window._add_table_row(book)

    record = []
    _slow_converter(monkeypatch, record, seconds=1.0)
    # Click Cancel half a second in, i.e. during the first file.
    QTimer.singleShot(300, lambda: window._conversion_run._on_cancel())
    window._convert_books(list(window.books), False)

    assert len(record) == 1  # b and c were never started
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.cbt", "b.cbt", "c.cbt"]
    assert all(b.needs_conversion for b in window.books)
    assert window._conversion_run is None


def test_convert_dialog_progress_label_names_the_file(window, tmp_path, monkeypatch):
    path = _make_cbt(tmp_path / "Some Comic 01.cbt", _pages(1))
    book = mw.CbzBook(path)
    window.books.append(book)
    window._add_table_row(book)
    labels = []
    original = mw.ConversionRun.begin

    def spy(self, index, name):
        labels.append((index, name))
        return original(self, index, name)

    monkeypatch.setattr(mw.ConversionRun, "begin", spy)
    window._convert_books([book], False)
    assert labels == [(0, "Converting: Some Comic 01.cbt")]


def test_redact_convert_step_uses_the_apps_threaded_converter(window, tmp_path, monkeypatch):
    record = []
    _slow_converter(monkeypatch, record, seconds=0.05)
    env = window._redact_env()
    assert env.convert == window._convert_for_redact
    source = _make_cbt(tmp_path / "r.cbt", _pages(1))
    out = tmp_path / "r-work.cbz"
    assert env.convert(source, output_path=str(out)) == str(out)
    assert record and record[0]["thread"] is not threading.main_thread()
