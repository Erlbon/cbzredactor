"""Resize-related behaviour added with "ask if you want to resize in the same step":

- core: convert_to_cbz(resize=...) resizes the pages in the SAME pass as the
  conversion (one verify, no second rewrite), keeps Cancel's "nothing half
  written", and a mislabeled ZIP is resized in one rewrite too;
- resize speed guards: pages already within limits are not decoded, a resized
  page is stored (not deflated again), pages are written in source order from a
  sliding window of workers;
- the Resize Images dialog and its choices are remembered (in place, Recycle
  Bin, folder, "only oversized"), the original really goes to the Recycle Bin;
- the one question per batch, its "remember" setting, and Redact following the
  saved setting without asking.
"""

import io
import os
import sys
import tarfile
import zipfile

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMessageBox

import core.foreign_archive_convert as fac
import gui.main_window as mw
from core.cbz_file import CbzBook, ResizeCancelled, resize_zip
from core.foreign_archive_convert import ConversionCancelled, convert_to_cbz
from core.image_resize import ResizeOptions, resize_page
from core.zip_rewrite import CbzError
from gui import app_settings
from gui.convert_resize_dialog import ConvertResizeDialog
from gui.resize_dialog import ResizeImagesDialog
from redactor_common.core.trash import TrashError

_app = QApplication.instance() or QApplication(sys.argv)


def _jpeg(width, height, color=(120, 130, 140)):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


def _png(width, height):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (10, 200, 30)).save(out, "PNG")
    return out.getvalue()


def _tar(path, files: dict[str, bytes]) -> str:
    with tarfile.open(path, "w") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(path)


def _zip(path, files: dict[str, bytes], compression=zipfile.ZIP_STORED) -> str:
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return str(path)


def _book_files(count=3, width=2000, height=3000):
    files = {f"{i:03}.jpg": _jpeg(width, height) for i in range(count)}
    files["ComicInfo.xml"] = b"<ComicInfo><Title>T</Title></ComicInfo>"
    return files


def _size_in(path, name):
    with zipfile.ZipFile(path) as zf:
        return Image.open(io.BytesIO(zf.read(name))).size


def _leftovers(folder):
    return sorted(p.name for p in folder.iterdir())


# -- resize speed guards ---------------------------------------------------------------


def test_a_page_within_its_limits_is_never_decoded(monkeypatch):
    data = _jpeg(800, 1200)
    loads = []
    real_load = Image.Image.load
    monkeypatch.setattr(Image.Image, "load", lambda self, *a, **k: (loads.append(1), real_load(self, *a, **k))[1])
    result = resize_page(data, 1440)
    assert result.data is data and not result.resized and result.new_size == (800, 1200)
    assert loads == []  # only the header was read


def test_a_big_jpeg_is_decoded_at_reduced_size_and_still_hits_the_target(monkeypatch):
    data = _jpeg(4000, 6000)
    sizes = []
    from PIL import JpegImagePlugin

    real_draft = JpegImagePlugin.JpegImageFile.draft
    monkeypatch.setattr(
        JpegImagePlugin.JpegImageFile, "draft", lambda self, mode, size: (sizes.append(size), real_draft(self, mode, size))[1]
    )
    result = resize_page(data, 1440)
    assert sizes == [(1440, 2160)]
    assert result.resized and result.new_size == (1440, 2160)
    assert Image.open(io.BytesIO(result.data)).size == (1440, 2160)


def test_a_resized_page_is_stored_not_deflated_again(tmp_path):
    path = _zip(tmp_path / "b.cbz", _book_files(2), zipfile.ZIP_DEFLATED)
    CbzBook(path).resize_images(1000)
    with zipfile.ZipFile(path) as zf:
        assert zf.getinfo("000.jpg").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("ComicInfo.xml").compress_type == zipfile.ZIP_DEFLATED  # not a page: untouched


def test_pages_come_out_in_source_order_with_many_workers(tmp_path):
    files = {f"{i:03}.jpg": _jpeg(1200 + i, 1800) for i in range(25)}
    path = _zip(tmp_path / "b.cbz", files)
    summary = CbzBook(path).resize_images(600, workers=6)
    assert summary.pages_resized == 25
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist() == list(files)
        assert [Image.open(io.BytesIO(zf.read(n))).width for n in files] == [600] * 25


# -- Recycle Bin on an in-place resize ----------------------------------------------------


def test_in_place_resize_hands_the_original_to_dispose_before_replacing_it(tmp_path):
    path = _zip(tmp_path / "b.cbz", _book_files(2))
    seen = []

    def dispose(p):
        # The finished, checked file is waiting beside it, the original is still whole.
        seen.append((p, _size_in(p, "000.jpg"), sorted(os.listdir(tmp_path))))
        os.remove(p)

    CbzBook(path).resize_images(1000, dispose_original=dispose)
    assert seen[0][0] == path and seen[0][1] == (2000, 3000)
    assert "b.cbz.tmp_resize" in seen[0][2]
    assert _size_in(path, "000.jpg") == (1000, 1500) and _leftovers(tmp_path) == ["b.cbz"]


def test_a_failing_recycle_bin_keeps_the_original_and_leaves_no_temp(tmp_path):
    path = _zip(tmp_path / "b.cbz", _book_files(2))

    def broken(_p):
        raise OSError("bin unavailable")

    with pytest.raises(CbzError):
        CbzBook(path).resize_images(1000, dispose_original=broken)
    assert _size_in(path, "000.jpg") == (2000, 3000) and _leftovers(tmp_path) == ["b.cbz"]


def test_cancel_during_resize_leaves_the_original_and_no_temp(tmp_path):
    path = _zip(tmp_path / "b.cbz", _book_files(6))
    polls = []

    def cancel():
        polls.append(1)
        return len(polls) > 2

    with pytest.raises(ResizeCancelled):
        CbzBook(path).resize_images(1000, should_cancel=cancel)
    assert _size_in(path, "000.jpg") == (2000, 3000) and _leftovers(tmp_path) == ["b.cbz"]


# -- one pass: convert and resize together ---------------------------------------------------


def test_convert_with_resize_is_one_pass(tmp_path, monkeypatch):
    src = _tar(tmp_path / "book.cbt", _book_files(4))
    # A second rewrite of the finished .cbz would go through resize_zip: it must not.
    monkeypatch.setattr(fac, "resize_zip", lambda *a, **k: pytest.fail("a second rewrite ran"))
    verifies = []
    real_verify = fac._verify_cbz
    monkeypatch.setattr(fac, "_verify_cbz", lambda *a, **k: (verifies.append(a[0]), real_verify(*a, **k))[1])
    summary = fac.ResizeSummary()
    out = convert_to_cbz(src, resize=ResizeOptions(1000), resize_summary=summary)
    assert out == str(tmp_path / "book.cbz")
    assert [_size_in(out, f"{i:03}.jpg") for i in range(4)] == [(1000, 1500)] * 4
    assert summary.pages_resized == 4 and summary.new_bytes < summary.original_bytes
    assert len(verifies) == 1  # one check of the finished file
    assert _leftovers(tmp_path) == ["book.cbt", "book.cbz"]
    book = CbzBook(out)
    assert not book.load_error and book.actual_page_count == 4
    with zipfile.ZipFile(out) as zf:
        assert zf.getinfo("000.jpg").compress_type == zipfile.ZIP_STORED
        assert zf.read("ComicInfo.xml") == _book_files(1)["ComicInfo.xml"]


def test_convert_without_resize_leaves_pages_byte_identical(tmp_path):
    files = _book_files(2)
    out = convert_to_cbz(_tar(tmp_path / "book.cbt", files))
    with zipfile.ZipFile(out) as zf:
        assert all(zf.read(name) == data for name, data in files.items())


def test_convert_with_resize_can_change_the_format_and_keeps_order(tmp_path):
    files = {"001.png": _png(1800, 2400), "002.jpg": _jpeg(1800, 2400), "ComicInfo.xml": b"<ComicInfo/>"}
    out = convert_to_cbz(_tar(tmp_path / "book.cbt", files), resize=ResizeOptions(900, output_format="WEBP"))
    with zipfile.ZipFile(out) as zf:
        names = sorted(n for n in zf.namelist() if n != "ComicInfo.xml")
        assert names == ["001.webp", "002.webp"]
        assert all(Image.open(io.BytesIO(zf.read(n))).size == (900, 1200) for n in names)
    assert CbzBook(out).actual_page_count == 2


def test_convert_with_resize_leaves_small_pages_untouched(tmp_path):
    small = _jpeg(500, 700)
    out = convert_to_cbz(_tar(tmp_path / "book.cbt", {"001.jpg": small, "002.jpg": _jpeg(2000, 3000)}),
                         resize=ResizeOptions(1000))
    with zipfile.ZipFile(out) as zf:
        assert zf.read("001.jpg") == small
        assert Image.open(io.BytesIO(zf.read("002.jpg"))).size == (1000, 1500)


def test_cancel_during_convert_with_resize_leaves_nothing_behind(tmp_path):
    src = _tar(tmp_path / "book.cbt", _book_files(8))
    polls = []

    def cancel():
        polls.append(1)
        return len(polls) > 6

    with pytest.raises(ConversionCancelled):
        convert_to_cbz(src, resize=ResizeOptions(1000), should_cancel=cancel)
    assert _leftovers(tmp_path) == ["book.cbt"]


def test_a_mislabeled_zip_is_resized_in_one_rewrite_into_the_new_name(tmp_path):
    src = _zip(tmp_path / "book.cbr", _book_files(3))  # a ZIP named .cbr
    summary = fac.ResizeSummary()
    out = convert_to_cbz(src, resize=ResizeOptions(1000), resize_summary=summary)
    assert out == str(tmp_path / "book.cbz")
    assert [_size_in(out, f"{i:03}.jpg") for i in range(3)] == [(1000, 1500)] * 3
    assert summary.pages_resized == 3
    assert _leftovers(tmp_path) == ["book.cbr", "book.cbz"]


def test_cancel_on_a_mislabeled_zip_with_resize_leaves_nothing(tmp_path):
    src = _zip(tmp_path / "book.cbr", _book_files(6))
    polls = []

    def cancel():
        polls.append(1)
        return len(polls) > 3

    with pytest.raises(ConversionCancelled):
        convert_to_cbz(src, resize=ResizeOptions(1000), should_cancel=cancel)
    assert _leftovers(tmp_path) == ["book.cbr"]


def test_resize_zip_error_is_a_cbz_error(tmp_path):
    bad = tmp_path / "bad.cbz"
    bad.write_bytes(b"not a zip")
    with pytest.raises(CbzError):
        resize_zip(str(bad), str(tmp_path / "out.cbz"), ResizeOptions(1000))
    assert _leftovers(tmp_path) == ["bad.cbz"]


# -- settings: the Resize dialog is remembered ---------------------------------------------------


@pytest.fixture
def ini(tmp_path_factory, monkeypatch):
    path = str(tmp_path_factory.mktemp("settings") / "settings.ini")
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: path)


def test_resize_choices_have_sensible_defaults_and_round_trip(ini):
    assert app_settings.load_resize_in_place() is False  # nothing is overwritten unless chosen
    assert app_settings.load_resize_recycle_original() is True
    assert app_settings.load_resize_oversized_only() is False
    assert app_settings.load_resize_export_folder() == ""
    app_settings.save_resize_in_place(True)
    app_settings.save_resize_recycle_original(False)
    app_settings.save_resize_oversized_only(True)
    app_settings.save_resize_export_folder("D:/out")
    app_settings.save_resize_options(ResizeOptions(1920, 80, 2500, "WEBP"))
    assert app_settings.load_resize_in_place() and not app_settings.load_resize_recycle_original()
    assert app_settings.load_resize_oversized_only() and app_settings.load_resize_export_folder() == "D:/out"
    assert app_settings.load_resize_options() == ResizeOptions(1920, 80, 2500, "WEBP")
    app_settings.save_resize_options(ResizeOptions(1440, 90, None, None))
    assert app_settings.load_resize_options() == ResizeOptions(1440, 90, None, None)


def test_dialog_opens_the_way_it_was_left(tmp_path):
    folder = tmp_path / "exp"
    folder.mkdir()
    dialog = ResizeImagesDialog(
        3, 1920, 80, oversized_count=2, default_max_height=2500, default_output_format="WEBP",
        default_in_place=True, default_recycle_original=False, default_oversized_only=True,
        default_export_folder=str(folder),
    )
    assert dialog.max_width() == 1920 and dialog.jpeg_quality() == 80 and dialog.max_height() == 2500
    assert dialog.output_format() == "WEBP"
    assert not dialog.is_export_mode() and not dialog.recycle_original() and dialog.oversized_only()
    assert dialog.output_folder == str(folder)
    assert dialog.options() == ResizeOptions(1920, 80, 2500, "WEBP")
    fresh = ResizeImagesDialog(3, 1440, 90)
    assert fresh.is_export_mode() and fresh.recycle_original() and not fresh.oversized_only()


def test_the_settings_bundle_and_preferences_carry_the_new_keys(ini):
    from gui import preferences
    from gui.settings_adapter import CbzSettingsAdapter

    adapter = CbzSettingsAdapter()
    app_settings.save_resize_in_place(True)
    app_settings.save_resize_on_convert(app_settings.RESIZE_ON_CONVERT_YES)
    assert adapter.read_section("resize")["in_place"] is True
    assert adapter.read_section("conversion")["resize_on_convert"] == "yes"
    adapter.write_section("resize", {"in_place": False, "recycle_original": False, "oversized_only": True})
    adapter.write_section("conversion", {"resize_on_convert": "no"})
    assert app_settings.load_resize_in_place() is False and app_settings.load_resize_recycle_original() is False
    assert app_settings.load_resize_oversized_only() is True and app_settings.load_resize_on_convert() == "no"
    adapter.write_section("conversion", {"resize_on_convert": "bogus"})
    assert app_settings.load_resize_on_convert() == "no"  # invalid values are ignored
    keys = {spec.key for s in preferences.preference_sections() for spec in s.specs}
    assert {preferences.KEY_RESIZE_IN_PLACE, preferences.KEY_RESIZE_RECYCLE_ORIGINAL,
            preferences.KEY_RESIZE_OVERSIZED_ONLY, preferences.KEY_RESIZE_EXPORT_FOLDER,
            preferences.KEY_RESIZE_ON_CONVERT} <= keys
    dialog = preferences.make_dialog()
    assert dialog.value(preferences.KEY_RESIZE_ON_CONVERT) == "no"


# -- GUI: the Resize Images action -----------------------------------------------------------------


@pytest.fixture
def window(monkeypatch, ini):
    monkeypatch.setattr(app_settings, "load_foreign_load_behavior", lambda: app_settings.FOREIGN_LOAD_CONVERT)
    monkeypatch.setattr(app_settings, "load_recycle_originals", lambda: False)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"warning shown: {a[2]}")))
    return mw.MainWindow()


class _FakeResizeDialog:
    DialogCode = mw.ResizeImagesDialog.DialogCode
    in_place = True
    recycle = True
    folder = None
    oversized = False

    def __init__(self, *a, **k):
        type(self).args = (a, k)

    def exec(self):
        return self.DialogCode.Accepted

    def max_width(self): return 1000
    def max_height(self): return None
    def output_format(self): return None
    def jpeg_quality(self): return 85
    def is_export_mode(self): return not self.in_place
    def recycle_original(self): return self.recycle
    @property
    def output_folder(self): return self.folder
    def oversized_only(self): return self.oversized
    def output_path_for(self, p): return None if self.in_place else os.path.join(self.folder, os.path.basename(p))


def _load_two(window, tmp_path):
    paths = [_zip(tmp_path / f"{n}.cbz", _book_files(2, 1500, 2200)) for n in "ab"]
    window._load_paths(paths)
    window.table.selectAll()
    return paths


def test_resize_in_place_sends_the_original_to_the_recycle_bin_and_remembers_the_choice(window, tmp_path, monkeypatch):
    paths = _load_two(window, tmp_path)
    trashed = []
    def fake_trash(p):
        # Like the real one: a failure (the app's own background scan may hold the file open
        # for an instant on Windows) is a TrashError, which the window retries briefly.
        try:
            os.remove(p)
        except OSError as exc:
            raise TrashError(str(exc)) from exc
        trashed.append(p)

    monkeypatch.setattr(mw, "move_to_trash", fake_trash)
    monkeypatch.setattr(mw, "ResizeImagesDialog", _FakeResizeDialog)
    window.open_resize_images_dialog()
    assert sorted(trashed) == sorted(paths)
    assert all(_size_in(p, "000.jpg")[0] == 1000 for p in paths)
    assert app_settings.load_resize_in_place() is True and app_settings.load_resize_recycle_original() is True
    assert app_settings.load_resize_max_width() == 1000 and app_settings.load_resize_jpeg_quality() == 85
    # ...and the next run opens on those choices
    window.open_resize_images_dialog()
    _args, kwargs = _FakeResizeDialog.args
    assert kwargs["default_in_place"] is True and kwargs["default_recycle_original"] is True


def test_resize_in_place_without_the_recycle_bin_overwrites_directly(window, tmp_path, monkeypatch):
    paths = _load_two(window, tmp_path)
    monkeypatch.setattr(mw, "move_to_trash", lambda p: pytest.fail("the Recycle Bin was used"))

    class Dialog(_FakeResizeDialog):
        recycle = False

    monkeypatch.setattr(mw, "ResizeImagesDialog", Dialog)
    window.open_resize_images_dialog()
    assert all(_size_in(p, "000.jpg")[0] == 1000 for p in paths)
    assert app_settings.load_resize_recycle_original() is False


def test_a_recycle_bin_failure_keeps_the_original_and_is_reported(window, tmp_path, monkeypatch):
    paths = _load_two(window, tmp_path)

    def no_bin(_p):
        raise TrashError("couldn't move it to the Recycle Bin")

    monkeypatch.setattr(mw, "move_to_trash", no_bin)
    monkeypatch.setattr(mw, "ResizeImagesDialog", _FakeResizeDialog)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    window.open_resize_images_dialog()
    assert warnings and "Recycle Bin" in warnings[0]
    assert all(_size_in(p, "000.jpg") == (1500, 2200) for p in paths)
    assert not [n for n in os.listdir(tmp_path) if n.startswith(("a.cbz.", "b.cbz."))]


def test_exporting_does_not_touch_the_originals_and_remembers_the_folder(window, tmp_path, monkeypatch):
    paths = _load_two(window, tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    class Dialog(_FakeResizeDialog):
        in_place = False
        folder = str(out)

    monkeypatch.setattr(mw, "move_to_trash", lambda p: pytest.fail("the Recycle Bin was used"))
    monkeypatch.setattr(mw, "ResizeImagesDialog", Dialog)
    window.open_resize_images_dialog()
    assert all(_size_in(p, "000.jpg") == (1500, 2200) for p in paths)
    assert sorted(os.listdir(out)) == ["a.cbz", "b.cbz"]
    assert app_settings.load_resize_in_place() is False
    assert app_settings.load_resize_export_folder() == str(out)


# -- GUI: the one question per batch ------------------------------------------------------------------


class _Question:
    """Stand-in for ConvertResizeDialog: records how often it was shown."""

    shown = 0
    answer = "yes"  # "yes" | "no" | "closed"
    remember_ticked = False
    edited = ResizeOptions(900, 70, None, None)

    def __init__(self, count, options, parent=None):
        type(self).shown += 1
        type(self).count = count
        type(self).initial = options
        self.resize_pages = type(self).answer == "yes"
        self.explicit = type(self).answer != "closed"

    def exec(self): return 0
    def options(self): return type(self).edited
    def remember(self): return self.explicit and type(self).remember_ticked


@pytest.fixture
def question(monkeypatch, ini):
    _Question.shown, _Question.answer, _Question.remember_ticked = 0, "yes", False
    app_settings.save_resize_on_convert(app_settings.RESIZE_ON_CONVERT_ASK)  # conftest starts every test at Never
    monkeypatch.setattr(mw, "ConvertResizeDialog", _Question)
    return _Question


def _cbt_paths(tmp_path, n=3, **kw):
    return [_tar(tmp_path / f"{i}.cbt", _book_files(2, 1500, 2200)) for i in range(n)]


def test_loading_a_batch_asks_once_and_resizes_every_converted_file(window, question, tmp_path):
    window._load_paths(_cbt_paths(tmp_path, 3))
    assert question.shown == 1 and question.count == 3  # once per batch, not per file
    assert len(window.books) == 3
    assert all(_size_in(b.path, "000.jpg")[0] == 900 for b in window.books)
    assert app_settings.load_resize_options().max_width == 900  # what was edited in the question is kept
    assert app_settings.load_resize_on_convert() == "ask"  # not remembered unless ticked


def test_answering_no_converts_without_resizing(window, question, tmp_path):
    question.answer = "no"
    window._load_paths(_cbt_paths(tmp_path, 2))
    assert question.shown == 1
    assert all(_size_in(b.path, "000.jpg") == (1500, 2200) for b in window.books)


def test_remember_turns_the_question_into_a_setting(window, question, tmp_path):
    question.remember_ticked = True
    window._load_paths(_cbt_paths(tmp_path, 1))
    assert app_settings.load_resize_on_convert() == "yes"
    other = tmp_path / "x"
    other.mkdir()
    window._load_paths(_cbt_paths(other, 1))
    assert question.shown == 1  # the second batch used the saved choice without asking
    assert all(_size_in(b.path, "000.jpg")[0] == 900 for b in window.books)


def test_remembered_no_never_asks_and_never_resizes(window, question, tmp_path):
    question.answer, question.remember_ticked = "no", True
    window._load_paths(_cbt_paths(tmp_path, 1))
    assert app_settings.load_resize_on_convert() == "no"
    other = tmp_path / "o"
    other.mkdir()
    window._load_paths(_cbt_paths(other, 1))
    assert question.shown == 1
    assert all(_size_in(b.path, "000.jpg") == (1500, 2200) for b in window.books)


def test_closing_the_question_is_not_remembered(window, question, tmp_path):
    question.answer, question.remember_ticked = "closed", True
    window._load_paths(_cbt_paths(tmp_path, 1))
    assert app_settings.load_resize_on_convert() == "ask"
    assert _size_in(window.books[0].path, "000.jpg") == (1500, 2200)


def test_a_batch_with_nothing_to_convert_does_not_ask(window, question, tmp_path):
    window._load_paths([_zip(tmp_path / "a.cbz", _book_files(1))])
    assert question.shown == 0


def test_table_convert_asks_once_for_the_selection(window, question, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "load_foreign_load_behavior", lambda: app_settings.FOREIGN_LOAD_UNCONVERTED)
    window._load_paths(_cbt_paths(tmp_path, 2))
    assert all(b.needs_conversion for b in window.books) and question.shown == 0
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.convert_books_to_cbz(list(window.books))
    assert question.shown == 1 and question.count == 2
    assert all(_size_in(b.path, "000.jpg")[0] == 900 for b in window.books)


def test_convert_from_disk_asks_once(window, question, tmp_path, monkeypatch):
    paths = _cbt_paths(tmp_path, 2)
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileNames", lambda *a, **k: (paths, ""))
    window.convert_foreign_archives_dialog()
    assert question.shown == 1
    assert all(_size_in(str(tmp_path / f"{i}.cbz"), "000.jpg")[0] == 900 for i in range(2))


def test_saved_always_resizes_without_asking(window, question, tmp_path, monkeypatch):
    app_settings.save_resize_on_convert("yes")
    app_settings.save_resize_options(ResizeOptions(800, 90, None, None))
    window._load_paths(_cbt_paths(tmp_path, 1))
    assert question.shown == 0
    assert _size_in(window.books[0].path, "000.jpg")[0] == 800


def test_cancel_during_a_resizing_batch_leaves_no_half_written_file(window, question, tmp_path, monkeypatch):
    import time

    from PyQt6.QtCore import QTimer

    paths = _cbt_paths(tmp_path, 3)
    real = mw.convert_to_cbz

    def slow(source, output_path=None, progress=None, should_cancel=None, resize=None, resize_summary=None):
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            if should_cancel and should_cancel():
                raise ConversionCancelled("Conversion cancelled.")
            time.sleep(0.01)
        return real(source, output_path, resize=resize)

    monkeypatch.setattr(mw, "convert_to_cbz", slow)
    QTimer.singleShot(300, lambda: window._conversion_run._on_cancel())
    window._load_paths(paths)
    assert _leftovers(tmp_path) == ["0.cbt", "1.cbt", "2.cbt"]


# -- Redact: follows the saved setting, never asks ------------------------------------------------------


def test_redact_uses_the_saved_choice_without_asking(window, question, tmp_path):
    assert window._redact_env().convert_resize is None  # ask / no: Redact does not resize
    app_settings.save_resize_on_convert("ask")
    assert window._redact_env().convert_resize is None
    app_settings.save_resize_on_convert("yes")
    app_settings.save_resize_options(ResizeOptions(700, 88, 3000, None))
    env = window._redact_env()
    assert env.convert_resize == ResizeOptions(700, 88, 3000, None)
    assert question.shown == 0
    # the app's threaded converter takes it through to the one-pass conversion
    source = _tar(tmp_path / "r.cbt", _book_files(2, 1500, 2200))
    out = tmp_path / "r-work.cbz"
    assert env.convert(source, output_path=str(out), resize=env.convert_resize) == str(out)
    assert _size_in(str(out), "000.jpg")[0] == 700


def test_the_convert_step_resizes_in_the_same_pass_when_saved_as_always(tmp_path):
    import shutil

    from redactor_common.core.pipeline import Recipe, run_recipe

    import core.redact_steps as rs

    bin_dir = tmp_path / "_bin"
    bin_dir.mkdir()
    env = rs.RedactEnv(convert_resize=ResizeOptions(1000))

    def fake_trash(path):
        shutil.move(path, str(bin_dir / os.path.basename(path)))

    env.trash = fake_trash
    path = _tar(tmp_path / "Saga 001.cbt", _book_files(3, 2000, 3000))
    recipe = Recipe.default_for(rs.build_catalogue(env))
    recipe.enabled["convert_to_cbz"] = True
    report = run_recipe(
        [CbzBook(path)], rs.recipe_for_run(recipe), rs.run_catalogue(env), lambda book: rs.CbzCtx(book, env),
        describe=lambda book: os.path.basename(book.path), finalize=rs.save_stage, finalize_label=rs.FINALIZE_LABEL,
    )
    entry = report.entries[0]
    assert any("pages resized in the same pass" in str(a) for a in entry.applied)
    out = str(tmp_path / "Saga 001.cbz")
    assert [_size_in(out, f"{i:03}.jpg") for i in range(3)] == [(1000, 1500)] * 3
    assert os.listdir(bin_dir) == ["Saga 001.cbt"]  # the original went to the (fake) Recycle Bin


def test_the_dialog_class_is_the_one_the_window_asks_with():
    assert mw.ConvertResizeDialog is ConvertResizeDialog
    dialog = ConvertResizeDialog(5, ResizeOptions(1440, 90, None, None))
    assert not dialog.remember() and dialog.options() == ResizeOptions(1440, 90, None, None)
    dialog.remember_check.setChecked(True)
    assert not dialog.remember()  # closing with X (no button) is never remembered
    dialog.yes_button.click()
    assert dialog.resize_pages and dialog.remember()
    other = ConvertResizeDialog(1, ResizeOptions(1440, 90, None, None))
    other.remember_check.setChecked(True)
    other.no_button.click()
    assert not other.resize_pages and other.remember()
