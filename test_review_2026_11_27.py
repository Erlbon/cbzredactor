"""Tests for the 2026-11-27 review cards (one test per fixed card)."""

import datetime
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


def test_card01_shared_series_link_does_not_group_different_volumes():
    import random

    rng = random.Random(3)
    h1 = {n: rng.getrandbits(64) for n in range(1, 13)}
    h2 = {n: rng.getrandbits(64) for n in range(1, 13)}

    def volume(n):
        fp = duplicates.BookFingerprint(page_count=20, cover=n, samples={1 / 3: [h1[n]], 2 / 3: [h2[n]]})
        return duplicates.BookFacts(fingerprint=fp, web="https://anilist.co/manga/30013")

    assert duplicates.find_duplicates([volume(n) for n in range(1, 13)]) == []
    series = duplicates.BookFacts(
        fingerprint=duplicates.BookFingerprint(page_count=20), web="https://mangaupdates.com/series/abc")
    assert duplicates.find_duplicates([series, series]) == []


def test_card01_issue_links_still_group():
    for web in ("https://comicvine.gamespot.com/x/4000-12/", "https://www.comics.org/issue/55/",
                "https://www.bedetheque.com/BD-Titel-12.html"):
        fp = duplicates.BookFingerprint(page_count=20, cover=1, samples={1 / 3: [1], 2 / 3: [2]})
        fp2 = duplicates.BookFingerprint(page_count=20, cover=1 << 40, samples={1 / 3: [1 << 41], 2 / 3: [1 << 50]})
        groups = duplicates.find_duplicates([duplicates.BookFacts(fingerprint=fp, web=web),
                                             duplicates.BookFacts(fingerprint=fp2, web=web)])
        assert len(groups) == 1 and groups[0].by_link, web


def test_card40_bucketed_comparison_gives_the_same_groups_as_all_pairs():
    import random

    rng = random.Random(7)

    def flip(value, bits):
        for bit in rng.sample(range(64), bits):
            value ^= 1 << bit
        return value

    books = []
    for _ in range(60):
        base = [rng.getrandbits(64), rng.getrandbits(64)]
        for copy in range(rng.choice([1, 2, 3])):
            distance = rng.choice([0, 3, 8, 8, 9, 12])  # on both sides of MATCH_DISTANCE
            samples = {1 / 3: [flip(base[0], distance)], 2 / 3: [flip(base[1], rng.choice([0, 8]))]}
            books.append(duplicates.BookFacts(fingerprint=duplicates.BookFingerprint(page_count=20, samples=samples)))

    def brute_groups():
        parent = list(range(len(books)))

        def find(i):
            while parent[i] != i:
                i = parent[i]
            return i

        for i in range(len(books)):
            for j in range(i + 1, len(books)):
                if duplicates.same_story(books[i].fingerprint, books[j].fingerprint):
                    parent[find(i)] = find(j)
        out = {}
        for i in range(len(books)):
            out.setdefault(find(i), []).append(i)
        return sorted(m for m in out.values() if len(m) > 1)

    expected = brute_groups()
    assert expected  # the data does contain duplicates
    assert sorted(g.members for g in duplicates.find_duplicates(books)) == expected


def test_card40_unrelated_comics_are_not_compared_pairwise():
    import random

    rng = random.Random(11)
    books = [
        duplicates.BookFacts(fingerprint=duplicates.BookFingerprint(
            page_count=20, samples={1 / 3: [rng.getrandbits(64)], 2 / 3: [rng.getrandbits(64)]}))
        for _ in range(400)
    ]
    assert len(duplicates._candidate_pairs(books)) < 400 * 399 // 2 // 4
    assert duplicates.find_duplicates(books) == []


# ---------------------------------------------------------------------------
# Batch 4: conversion and resize
# ---------------------------------------------------------------------------


def _jpeg_with_profile_and_orientation(width: int, height: int, orientation: int, profile: bytes) -> bytes:
    image = Image.effect_noise((width, height), 60).convert("RGB")
    exif = Image.Exif()
    exif[0x0112] = orientation
    out = io.BytesIO()
    image.save(out, "JPEG", icc_profile=profile, exif=exif.tobytes())
    return out.getvalue()


def test_card9_resize_keeps_the_colour_profile_and_turns_an_exif_rotated_page():
    from core.image_resize import resize_page

    profile = b"fake-icc-profile-bytes" * 8
    source = _jpeg_with_profile_and_orientation(400, 600, 6, profile)
    result = resize_page(source, max_width=200)
    assert result.resized
    out = Image.open(io.BytesIO(result.data))
    assert out.info.get("icc_profile") == profile
    assert out.getexif().get(0x0112, 1) == 1  # the rotation is in the pixels, not a lost tag
    assert out.width > out.height  # turned to landscape, as a viewer showed the original
    assert result.original_size == (600, 400)
    assert result.new_size == out.size


def test_card9_png_profile_survives_a_resize():
    from core.image_resize import resize_page

    profile = b"another-fake-profile" * 8
    out_png = io.BytesIO()
    Image.effect_noise((300, 500), 60).convert("RGB").save(out_png, "PNG", icc_profile=profile)
    result = resize_page(out_png.getvalue(), max_width=100)
    assert result.resized
    assert Image.open(io.BytesIO(result.data)).info.get("icc_profile") == profile


@pytest.fixture
def temp_ini(tmp_path, monkeypatch):
    from gui import app_settings

    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "settings.ini"))


def test_card13_imported_resize_values_outside_the_dialog_limits_are_refused(temp_ini):
    from gui import app_settings
    from gui.settings_adapter import CbzSettingsAdapter

    adapter = CbzSettingsAdapter("x")
    adapter.write_section("resize", {"max_width": 1, "jpeg_quality": 1, "max_height": 5})
    assert app_settings.load_resize_max_width() == app_settings._DEFAULT_RESIZE_MAX_WIDTH
    assert app_settings.load_resize_jpeg_quality() == 90
    assert app_settings.load_resize_max_height() == 0
    adapter.write_section("resize", {"max_width": 1200, "jpeg_quality": 70, "max_height": 0})
    assert app_settings.load_resize_max_width() == 1200
    assert app_settings.load_resize_jpeg_quality() == 70


def test_card13_load_resize_options_clamps_a_hand_edited_ini(temp_ini):
    from gui import app_settings

    app_settings.save_resize_max_width(1)
    app_settings.save_resize_jpeg_quality(1)
    app_settings.save_resize_max_height(3)
    options = app_settings.load_resize_options()
    assert (options.max_width, options.jpeg_quality, options.max_height) == (200, 50, 200)
    app_settings.save_resize_max_width(99999)
    app_settings.save_resize_jpeg_quality(500)
    app_settings.save_resize_max_height(0)
    options = app_settings.load_resize_options()
    assert (options.max_width, options.jpeg_quality, options.max_height) == (10000, 100, None)


def _make_cbt_files(path, files):
    import tarfile

    with tarfile.open(path, "w") as tf:
        for name, data in files:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def test_card4_case_clashing_pages_are_not_silently_lost(tmp_path):
    from core.foreign_archive_convert import ForeignArchiveConversionError, convert_to_cbz

    source = tmp_path / "clash.cbt"
    _make_cbt_files(source, [("A.jpg", b"one"), ("a.jpg", b"two"), ("b.jpg", b"three")])
    out = tmp_path / "clash.cbz"
    if os.path.normcase("A") == "a":  # Windows: the two names are one file once unpacked
        with pytest.raises(ForeignArchiveConversionError):
            convert_to_cbz(str(source), str(out))
        assert not out.exists()
        assert source.exists()
    else:
        convert_to_cbz(str(source), str(out))
        assert sum(1 for n in zipfile.ZipFile(out).namelist() if n.endswith(".jpg")) == 3


def test_card4_a_page_missing_after_unpacking_refuses_the_conversion(tmp_path, monkeypatch):
    from core import foreign_archive_convert as fac

    source = tmp_path / "book.cbt"
    _make_cbt_files(source, [("1.jpg", b"one"), ("2.jpg", b"two"), ("3.jpg", b"three")])

    def lossy_extract(path, dest_dir, report=None):
        for name in ("1.jpg", "2.jpg"):  # drops 3.jpg, as a name clash would
            with open(os.path.join(dest_dir, name), "wb") as handle:
                handle.write(b"x")

    monkeypatch.setattr(fac, "_extract_cbt", lossy_extract)
    out = tmp_path / "book.cbz"
    with pytest.raises(fac.ForeignArchiveConversionError):
        fac.convert_to_cbz(str(source), str(out))
    assert not out.exists()
    assert not os.path.exists(str(out) + ".tmp_convert")


# ---------------------------------------------------------------------------
# Batch 5: main window and panel (cards 3, 5, 10, 14, 18, 26, 41, 61)
# ---------------------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    import sys

    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


_APP = _qapp()  # at import, as the other Qt test files do: conftest fixtures build Qt objects before any test runs


@pytest.fixture
def gui_env(tmp_path, monkeypatch):
    """Offscreen app with settings and crash log redirected into tmp_path."""
    from core import crash_log
    from gui import app_settings
    from redactor_common.gui import crash_dialog

    _qapp()
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "settings.ini"))
    monkeypatch.setattr(crash_log, "log_path", lambda: str(tmp_path / "logs" / "cbzredactor_crash.log"))
    monkeypatch.setattr(crash_dialog, "_reporter", None)
    return tmp_path


def _real_cbz(path, title="T") -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", f"<ComicInfo><Title>{title}</Title></ComicInfo>")
        zf.writestr("001.jpg", b"x")
    return str(path)


def _fake_book(name="a", **metadata_kwargs):
    from core import history
    from core.comicinfo import ComicInfoMetadata

    book = CbzBook.__new__(CbzBook)  # bypass __post_init__'s real zip load
    book.path = os.path.join(os.sep, "x", f"{name}.cbz")  # never touched on disk
    book.metadata = ComicInfoMetadata(**metadata_kwargs)
    book.page_names = []
    book.comicinfo_name = None
    book.load_error = ""
    book.save_error = ""
    book.dirty = False
    book.baseline = history.fields_text(book.metadata)
    return book


def _fake_window(count, **metadata_kwargs):
    from gui.main_window import MainWindow

    window = MainWindow()
    for i in range(count):
        book = _fake_book(f"f{i}", **metadata_kwargs)
        window.books.append(book)
        window._add_table_row(book)
    window._show_idle_panel()
    return window


def test_card3_undo_after_save_keeps_the_book_dirty(gui_env):
    window = _fake_window(2)
    books = list(window.books)
    window._push_undo("Bulk edit", books)
    for book in books:
        book.metadata.genre = "Horror"
        book.dirty = True
    for book in books:  # what a Save does: the file now holds Horror
        from core import history

        book.baseline = history.fields_text(book.metadata)
        book.dirty = False
    window.undo_last_action()
    for book in books:
        assert book.metadata.genre == ""
        assert book.dirty  # the file still says Horror, so this is an unsaved change
    window.redo_last_action()
    for book in books:
        assert book.metadata.genre == "Horror"
        assert not book.dirty  # back to what is on disk


def test_card5_selecting_a_comic_does_not_rewrite_untouched_fields(gui_env):
    window = _fake_window(1)
    summary = "Line one" + chr(13) + chr(10) + "line two   "
    book = window.books[0]
    book.metadata.summary = summary
    book.metadata.community_rating = "3.75"
    book.metadata.title = "  padded  "
    window.panel.load_metadata(book.metadata, None, "0 page(s)")
    window.panel.apply_to_metadata(book.metadata)
    assert book.metadata.summary == summary
    assert book.metadata.community_rating == "3.75"
    assert book.metadata.title == "  padded  "
    window.panel._line_edits["title"].setText("  new  ")  # a real edit is still written (trimmed)
    window.panel.apply_to_metadata(book.metadata)
    assert book.metadata.title == "new"
    assert book.metadata.summary == summary


def test_card10_restore_from_history_asks_before_dropping_unsaved_edits(gui_env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from gui.main_window import MainWindow

    path = _real_cbz(gui_env / "one.cbz", "Disk")
    window = MainWindow()
    window._load_paths([path])
    book = window.books[0]
    book.metadata.title = "Unsaved"
    book.dirty = True
    asked = []
    answer = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a) or answer[0]))
    window._reload_restored([os.path.normcase(os.path.abspath(path))])
    assert asked and book.metadata.title == "Unsaved" and book.dirty  # No: the edit survives
    answer[0] = QMessageBox.StandardButton.Yes
    window._reload_restored([os.path.normcase(os.path.abspath(path))])
    assert book.metadata.title == "Disk" and not book.dirty


def test_card14_undo_keeps_text_typed_in_the_panel(gui_env):
    window = _fake_window(2)
    books = list(window.books)
    window._push_undo("Bulk edit", books)
    for book in books:
        book.metadata.genre = "Horror"
        book.dirty = True
    window.table.selectRow(0)
    _qapp().processEvents()
    assert window._selected_rows == [0]
    window.panel._line_edits["title"].setText("Typed title")
    window.undo_last_action()
    assert books[0].metadata.genre == ""  # the step was undone
    assert books[0].metadata.title == "Typed title"  # the typed text survived
    assert books[0].dirty
    assert window.panel._line_edits["title"].text() == "Typed title"
    assert books[1].metadata.genre == ""


def test_card18_the_same_file_is_not_listed_twice(gui_env):
    from gui.main_window import MainWindow

    first = _real_cbz(gui_env / "a.cbz")
    second = _real_cbz(gui_env / "b.cbz")
    window = MainWindow()
    window._load_paths([first, second])
    window._load_paths([first, second, first])
    assert len(window.books) == 2 and window.table.rowCount() == 2
    assert "already loaded" in window.statusBar().currentMessage()
    # two loads of one file are two objects: never equal, found by identity
    twin_a, twin_b = CbzBook(first), CbzBook(first)
    assert twin_a != twin_b
    assert [twin_a, twin_b].index(twin_b) == 1


def test_card26_every_row_unticked_is_a_cancel_and_keeps_the_typed_fields(gui_env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    window = _fake_window(2)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    window.table.selectAll()
    _qapp().processEvents()
    window.panel._line_edits["genre"].setText("Horror")
    monkeypatch.setattr(window, "_resolve_overwrite_conflicts", lambda books, changes: {i: {} for i in changes})
    window._apply_bulk_edit()
    assert not window.undo_manager.can_undo()
    assert all(not book.dirty and book.metadata.genre == "" for book in window.books)
    assert window.panel._line_edits["genre"].text() == "Horror"
    assert not window.statusBar().currentMessage().startswith("Applied")


def test_card41_save_all_rewrites_run_off_the_gui_thread(gui_env, monkeypatch):
    import threading

    from PyQt6.QtWidgets import QMessageBox

    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    paths = [_real_cbz(gui_env / f"{n}.cbz") for n in ("A", "B", "C")]
    window = MainWindow()
    window._load_paths(paths)
    threads = []
    real_save = CbzBook.save

    def spying_save(self, *a, **k):
        threads.append(threading.current_thread())
        return real_save(self, *a, **k)

    monkeypatch.setattr(CbzBook, "save", spying_save)
    for i, book in enumerate(window.books):
        book.metadata.title = f"New {i}"
        book.dirty = True
    window.save_all_changed()
    assert len(threads) == 3
    assert all(t is not threading.main_thread() for t in threads)
    assert window.statusBar().currentMessage() == "Saved 3 file(s)"
    assert [CbzBook(p).metadata.title for p in paths] == ["New 0", "New 1", "New 2"]
    assert not any(book.dirty for book in window.books)


def test_card61_page_reader_follows_a_changed_page_list(gui_env):
    from gui.page_reader_dialog import PageReaderDialog

    path = _cbz_with_pages(gui_env / "r.cbz", {f"00{i}.png": _png(8, 8) for i in (1, 2, 3)})
    book = CbzBook(path)
    reader = PageReaderDialog(book)
    assert reader.nav.names == ["001.png", "002.png", "003.png"]
    book.page_names = ["002.png", "003.png"]  # what Remove Pages / Clean Up do to the open book
    reader.go_next()
    assert reader.nav.names == book.page_names
    assert reader.name_label.text() == book.page_names[reader.nav.index]
    assert reader.nav.count == 2
    reader._loader.shutdown()
    reader._prefetch.shutdown()
    reader.close()


# --- batch 6: Validate, Redact, scene names ---------------------------------------------------------------------

from test_redact_steps import COMICINFO as _REDACT_COMICINFO  # noqa: E402
from test_redact_steps import _cbz as _redact_cbz  # noqa: E402
from test_redact_steps import _only as _redact_only  # noqa: E402
from test_redact_steps import _recipe as _redact_recipe  # noqa: E402
from test_redact_steps import _run as _redact_run  # noqa: E402
from test_redact_steps import env  # noqa: E402,F401  (the fixture)


def test_card6_real_subtitles_in_brackets_are_kept_but_scene_tags_go():
    from core.comicinfo import ComicInfoMetadata
    from core.comicinfo_check import check_metadata

    today = datetime.date(2026, 11, 27)
    for text in ("Hulk (The Missing Years)", "Batman (Variant Edition)", "Wolverine (Preview Edition)", "Maus (Scan Wars)"):
        fields = {f.field for f in check_metadata(ComicInfoMetadata(series=text), None, today)}
        assert "series" not in fields, text
    fixes = {f.field: f.fix for f in check_metadata(ComicInfoMetadata(title="Saga (Digital) (Zone-Empire) (TPB)"), None, today)}
    assert fixes["title"] == "Saga"


def test_card7_year_month_day_with_spaces_are_trimmed_not_erased():
    from core.comicinfo import ComicInfoMetadata
    from core.comicinfo_check import check_metadata

    meta = ComicInfoMetadata(series="S", year="\n  1999\n", month=" 5", day="12 ")
    fixes = {f.field: f.fix for f in check_metadata(meta, None, datetime.date(2026, 11, 27))}
    assert fixes == {"year": "1999", "month": "5", "day": "12"}


def test_card27_number_zero_a_keeps_its_zero():
    from core.scene_name import parse_filename

    assert parse_filename("X-Men 0a (2020).cbz").number == "0a"
    assert parse_filename("X-Men 007 (2020).cbz").number == "7"
    assert parse_filename("X-Men 000 (2020).cbz").number == "0"


def test_card28_object_not_found_does_not_switch_comicvine_off(env, tmp_path, monkeypatch):
    import core.redact_steps as rs
    from core.comicvine_lookup import ComicVineLookupError, _check_status

    with pytest.raises(ComicVineLookupError) as not_found:
        _check_status({"status_code": 101})
    assert not_found.value.concerns_one_item
    with pytest.raises(ComicVineLookupError) as bad_key:
        _check_status({"status_code": 100})
    assert not bad_key.value.concerns_one_item
    assert not ComicVineLookupError("network unreachable").concerns_one_item

    calls = []

    def lookup(*args, **kwargs):
        calls.append(1)
        raise not_found.value

    monkeypatch.setattr(rs, "search_comicvine", lookup)
    env.comicvine_key = "key"
    books = [CbzBook(_redact_cbz(tmp_path / f"Saga 00{i}.cbz", comicinfo=b"<ComicInfo/>")) for i in (1, 2, 3)]
    report = _redact_run(env, books, _redact_recipe(env, disable=("filename_tags",)))
    assert len(calls) == 3
    assert not any("unreachable" in n for e in report.entries for n in e.notes)
    assert env.comicvine_down is False


def test_card36_trash_dir_is_named_in_the_report(env, tmp_path):
    messy = _REDACT_COMICINFO.replace(b"<Series>Saga</Series>", b"<Series>  Saga  </Series>")
    path = _redact_cbz(tmp_path / "Saga 001.cbz", comicinfo=messy, extra=[("Thumbs.db", b"junk")])
    env.trash_label = str(tmp_path / "old")
    entry = _redact_only(_redact_run(env, [CbzBook(path)]))
    assert any(str(tmp_path / "old") in a for a in entry.applied)
    assert not any("Recycle Bin" in a for a in entry.applied)


def test_card50_unreadable_credit_file_is_not_overwritten_by_add(tmp_path, monkeypatch):
    import builtins

    path = tmp_path / "known.json"
    first = credit_pages.KnownCreditPages(str(path))
    first.add(0x0F0F0F0F0F0F0F0F, b"t1", "a.png")
    first.add(0x00FF00FF00FF00FF, b"t2", "b.png")
    before = path.read_bytes()
    real_open = builtins.open
    state = {"fail": True}

    def flaky(file, *args, **kwargs):
        if state["fail"] and os.path.abspath(str(file)) == str(path):
            raise PermissionError(13, "held by a scanner")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", flaky)
    held = credit_pages.KnownCreditPages(str(path))
    assert held.unreadable
    assert held.add(0x123456789ABCDEF0, b"t3", "c.png")
    assert path.read_bytes() == before  # not overwritten while unreadable
    state["fail"] = False
    held.add(0x7777777777777777, b"t4", "d.png")
    assert len(credit_pages.KnownCreditPages(str(path)).pages) == 4


# --- Batch 7: lookups ---------------------------------------------------------------------------------------------


import json
import urllib.error


def _answer(data):
    return lambda url: json.dumps(data).encode("utf-8")


def test_card17_odd_shaped_answers_are_lookup_errors_or_empty_not_crashes():
    from core import bedetheque_lookup as bl
    from core import comicvine_browse as cvb
    from core import comicvine_lookup as cv
    from core import gcd_lookup as gl

    # Comic Vine: a list instead of an object is an error of its own; odd members are skipped.
    with pytest.raises(cv.ComicVineLookupError):
        cv.search_comicvine("KEY", "Saga", fetch=_answer([]))
    assert cv.search_comicvine("KEY", "Saga", fetch=_answer({"status_code": 1, "results": "x"})) == []
    odd = {"status_code": 1, "results": [5, None, {"volume": 7, "name": ["x"], "id": None, "image": "i"}]}
    candidates = cv.search_comicvine("KEY", "Saga", fetch=_answer(odd))
    assert len(candidates) == 1 and candidates[0].name == "" and candidates[0].volume_name == ""
    details = cv.fetch_issue_details(
        "KEY", "https://x/issue/1/", fetch=_answer({"status_code": 1, "results": {"person_credits": ["a", {"name": 5, "role": 6}]}})
    )
    assert details.writer == ""
    with pytest.raises(cv.ComicVineLookupError):
        cv.fetch_issue_details("KEY", "https://x/issue/1/", fetch=_answer([1]))
    assert cv.fetch_volume_info("KEY", "https://x/volume/1/", fetch=_answer({"status_code": 1, "results": "x"})) == ("", "")
    # the browser's volume and issue lists
    assert cvb.search_volumes("KEY", "Saga", fetch=_answer({"status_code": 1, "results": "x"})) == []
    volumes = cvb.search_volumes("KEY", "Saga", fetch=_answer({"status_code": 1, "results": [3, {"count_of_issues": "x", "name": 4}]}))
    assert volumes[0].issue_count == 0
    assert cvb.fetch_volume_issues("KEY", "1", fetch=_answer({"status_code": 1, "results": "x", "number_of_total_results": "n"})) == []
    with pytest.raises(cv.ComicVineLookupError):
        cvb.search_volumes("KEY", "Saga", fetch=_answer([]))

    # GCD
    with pytest.raises(gl.GcdLookupError):
        gl.search_gcd("Saga", "1", fetch=_answer([]))
    assert gl.search_gcd("Saga", "1", fetch=_answer({"results": "x", "next": 5})) == []
    story = {"type": "comic story", "script": 5, "pencils": ["a"], "title": 7, "synopsis": {"a": 1}}
    gcd = gl.fetch_issue_details(
        "https://www.comics.org/api/issue/1/", fetch=_answer({"story_set": [story, 3, None], "key_date": 5, "series": 9})
    )
    assert gcd.summary == "" and gcd.penciller == ""
    with pytest.raises(gl.GcdLookupError):
        gl.fetch_issue_details("https://www.comics.org/api/issue/1/", fetch=_answer([]))
    assert gl._distinct_issue_count({"active_issues": 5}) == 0

    # Bedetheque
    assert bl._parse_series_entries({"category": "Séries"}) == []
    assert bl._parse_series_entries([1, {"category": "Séries", "id": 5}, {"category": "Séries", "id": "S9", "label": 3}]) == [("9", "3")]


def test_card20_comicvine_summary_decodes_entities_and_keeps_paragraphs_apart():
    from core import comicvine_lookup as cv

    html_text = "<p>Batman &amp; Robin fight.</p><p>It&#39;s dark.&nbsp;Very.</p><ul><li>One</li><li>Two</li></ul>"
    summary = cv._candidate_from_result({"description": html_text}).summary
    assert "Batman & Robin fight." in summary and "It's dark." in summary
    assert "fight.It" not in summary and "&amp;" not in summary and "&#39;" not in summary and "&nbsp;" not in summary
    assert "One\nTwo" in summary
    data = {"status_code": 1, "results": {"description": html_text}}
    assert cv.fetch_issue_details("KEY", "https://x/issue/1/", fetch=_answer(data)).summary == summary


def test_card21_slash_in_series_or_number_is_escaped_in_the_gcd_search_url():
    from core.gcd_lookup import API_BASE, build_search_url

    url = build_search_url("Batman/Superman", "1/2", "1986")
    assert url == f"{API_BASE}/series/name/Batman%2FSuperman/issue/1%2F2/year/1986/?format=json"


def test_card22_manga_lookup_does_not_write_the_series_start_date():
    from core import manga_lookup as ml

    ani = ml.AniListSeries(series_id="1", english="Series", year="1998", month="3", day="7")
    fields = ml.build_details(ml.MangaMatch(anilist=ani)).as_dict()
    assert fields["series"] == "Series"
    assert "year" not in fields and "month" not in fields and "day" not in fields


def test_card29_comicrack_month_stops_at_12():
    import xml.etree.ElementTree as ET

    from core.comicrack_import import _key_date

    def book(month, day):
        return ET.fromstring(f"<Book><Year>2000</Year><Month>{month}</Month><Day>{day}</Day></Book>")

    assert _key_date(book(20, 5)) == "2000-00-05"
    assert _key_date(book(12, 31)) == "2000-12-31"
    assert _key_date(book(13, 32)) == "2000-00-00"


def test_card30_gcd_year_pass_only_for_a_four_digit_year():
    from core.gcd_lookup import search_gcd

    def count_requests(year):
        calls = []

        def fetch(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

        assert search_gcd("Saga", "1", fetch=fetch, year=year) == []
        return len(calls), len(set(calls))

    assert count_requests("86") == (1, 1)
    assert count_requests("1986-87") == (1, 1)
    assert count_requests("1986") == (2, 2)


def test_card31_manga_initials_are_not_swapped_to_the_end():
    from core.manga_lookup import person_name

    assert person_name("J.K. Rowling") == "J.K. Rowling"
    assert person_name("H.P. Lovecraft") == "H.P. Lovecraft"
    assert person_name("MIURA Kentaro") == "Kentaro Miura"


def test_card32_manga_description_keeps_underscores_and_asterisks_inside_words():
    from core.manga_lookup import plain_text

    assert plain_text("Uses file_name_here and 2*3*4") == "Uses file_name_here and 2*3*4"
    assert plain_text("A *bold* and **very** _quiet_ day") == "A bold and very quiet day"


def test_card33_remove_account_waits_for_save_and_cancel_keeps_it(fake_keyring):
    from gui import app_settings
    from gui.api_keys_dialog import ApiKeysDialog

    app_settings.save_gcd_account("me", "pw")
    cancelled = ApiKeysDialog()
    cancelled._remove_account()
    assert cancelled.username_edit.text() == ""
    cancelled.reject()
    assert app_settings.load_gcd_account() == ("me", "pw")
    saved = ApiKeysDialog()
    saved._remove_account()
    saved.accept()
    assert app_settings.load_gcd_account() == ("", "")


def test_card42_comicvine_browser_reads_the_first_page_off_the_gui_thread(tmp_path, monkeypatch):
    import test_comicvine_browse_dialog as td
    from gui import comicvine_browse_dialog as dlg

    later = td._Later()
    monkeypatch.setattr(dlg, "start_background", later)
    reads = []
    real = CbzBook.read_first_page_bytes

    def spy(self):
        reads.append(1)
        return real(self)

    monkeypatch.setattr(CbzBook, "read_first_page_bytes", spy)
    monkeypatch.setattr("gui.app_settings.load_comicvine_api_key", lambda: "KEY")
    monkeypatch.setattr(dlg, "SELECTION_DELAY_MS", 0)

    def search_volumes(*args, **kwargs):
        return list(td.VOLUMES)

    monkeypatch.setattr(dlg, "search_volumes", search_volumes)
    monkeypatch.setattr(dlg, "download_image", lambda url, fetch=None: td.IMAGES[url])
    dialog = dlg.ComicVineBrowseDialog([td._book(tmp_path)])
    assert reads == [] and dialog._local_hash is None  # nothing read on the GUI thread
    assert dialog.cover_yours.text() == dlg.LOADING
    later.run("search_volumes")  # the series list can arrive before the file's own cover
    later.run("_read_local_cover")
    later.run_all()
    assert reads == [1] and dialog._local_hash is not None
    assert dialog._selected_volume().volume_id == "2"  # the look-alike cover, once both had arrived


def test_card42_cover_preview_is_small_and_a_huge_page_is_not_decoded(monkeypatch):
    from core import cover_hash as ch

    out = io.BytesIO()
    Image.new("RGB", (2400, 3200), (90, 40, 200)).save(out, "JPEG")
    preview, digest = ch.preview_and_hash(out.getvalue())
    assert digest is not None and preview
    with Image.open(io.BytesIO(preview)) as shown:
        assert max(shown.size) <= ch.PREVIEW_MAX_SIDE
    monkeypatch.setattr(ch, "MAX_HASH_PIXELS", 1000)
    assert ch.preview_and_hash(out.getvalue()) == (None, None)
    assert ch.cover_hash(out.getvalue()) is None


class _RecordingBase:
    def __init__(self):
        self.seen = []

    def __call__(self, request):
        self.seen.append((request.full_url, request.get_header("Authorization")))
        return b"{}"


def test_card43_gcd_login_goes_only_to_https_www_comics_org(monkeypatch):
    from core import gcd_lookup as gl

    recorder = _RecordingBase()
    monkeypatch.setattr(gl, "make_default_fetch", lambda *a, **k: recorder)
    fetch = gl.make_gcd_fetch("someone", "hunter2")
    for url in (
        "https://www.comics.org/api/series/1/",
        "https://files1.comics.org//img/gcd/covers_by_id/17/w400/17183.jpg",
        "http://evil.example/c.jpg",
        "http://www.comics.org/api/series/1/",
        "https://www.comics.org.evil.example/api/",
    ):
        fetch(url)
    auth = {url: header for url, header in recorder.seen}
    assert auth["https://www.comics.org/api/series/1/"].startswith("Basic ")
    assert [h for u, h in recorder.seen if u != "https://www.comics.org/api/series/1/"] == [None] * 4


def test_card43_gcd_next_and_series_links_off_comics_org_are_not_followed():
    from core import gcd_lookup as gl

    calls = []

    def fetch(url):
        calls.append(url)
        page = {"next": "http://evil.example/page2", "results": [{"series_name": "Other", "descriptor": "1"}]}
        return json.dumps(page).encode("utf-8")

    gl.search_gcd("Saga", "1", fetch=fetch)
    assert len(calls) == 1
    calls.clear()
    assert gl._series_info("http://evil.example/series/1/", fetch) == {}
    assert gl._series_info("https://www.comics.org.evil.example/series/1/", fetch) == {}
    assert calls == []


def test_card55_comicvine_cover_date_zero_parts_stay_empty():
    from core.comicvine_lookup import ComicVineIssueDetails, _split_cover_date

    assert _split_cover_date("1985-00-00") == ("1985", "", "")
    assert _split_cover_date("1985-06-00") == ("1985", "6", "")
    assert _split_cover_date("1985-06-09") == ("1985", "6", "9")
    assert _split_cover_date("0000-00-00") == ("", "", "")
    fields = ComicVineIssueDetails(volume_name="X", cover_date="1985-00-00").as_dict()
    assert fields["year"] == "1985" and "month" not in fields and "day" not in fields


# ---------------------------------------------------------------------------
# Batch 8: CLI and dialogs (cards 19, 34, 35, 37, 38, 39, 45)
# ---------------------------------------------------------------------------


def test_card19_import_changes_the_saved_conversion_policy(tmp_path):
    from gui import app_settings
    from gui.settings_adapter import CbzSettingsAdapter
    from redactor_common.core import conversion_policy as cp

    # Preferences > Conversion has been saved once: only the saved policy counts from now on.
    app_settings.save_conversion_policy(cp.ConversionPolicy(
        on_open=cp.ON_OPEN_NEVER, originals=cp.ORIGINALS_KEEP, recent_minutes=0, dry_run_first=False))
    adapter = CbzSettingsAdapter("x")
    adapter.write_section("conversion", {"on_load": "convert", "recycle_originals": True})
    policy = app_settings.load_conversion_policy()
    assert policy.on_open == cp.ON_OPEN_AUTO and policy.originals == cp.ORIGINALS_RECYCLE
    assert adapter.read_section("conversion")["on_load"] == "convert"
    assert adapter.read_section("conversion")["recycle_originals"] is True


def test_card19_whole_policy_round_trips_and_folders_are_machine_paths(tmp_path):
    from gui import app_settings
    from gui.settings_adapter import CbzSettingsAdapter
    from redactor_common.core import conversion_policy as cp

    adapter = CbzSettingsAdapter("x")
    quarantine = str(tmp_path / "quarantine")
    app_settings.save_conversion_policy(cp.ConversionPolicy(
        on_open=cp.ON_OPEN_ASK, originals=cp.ORIGINALS_QUARANTINE, quarantine_folder=quarantine,
        undo_log_folder=str(tmp_path / "logs"), recent_minutes=25, dry_run_first=False))
    portable = adapter.read_section("conversion")
    machine = adapter.read_section("paths")
    assert portable["originals"] == "quarantine" and portable["recent_minutes"] == 25
    assert portable["dry_run_first"] is False and "quarantine_folder" not in portable
    assert machine["quarantine_folder"] == quarantine
    app_settings.save_conversion_policy(cp.ConversionPolicy())
    # an old key in a bundle does not undo the newer one, whatever order the file lists them in
    adapter.write_section("conversion", {"originals": "quarantine", "recycle_originals": False, "recent_minutes": 25,
                                         "dry_run_first": False})
    adapter.write_section("paths", {"quarantine_folder": quarantine})
    policy = app_settings.load_conversion_policy()
    assert (policy.originals, policy.recent_minutes, policy.dry_run_first) == ("quarantine", 25, False)
    assert policy.quarantine_folder == quarantine


def test_card34_rename_with_an_empty_pattern_is_refused(tmp_path, capsys):
    from cbzcli.main import main
    from redactor_common.cli import CliError
    from test_redact_steps import _cbz

    path = _cbz(tmp_path / "keep me.cbz")
    for empty in ("", "  "):
        with pytest.raises(CliError, match="empty"):
            main(["rename", path, "-p", empty])
    assert os.listdir(tmp_path) == ["keep me.cbz"]


def test_card35_rating_is_a_plain_decimal_only():
    from cbzcli.fields import check_value
    from redactor_common.cli import CliError

    for bad in ("0_1", "1e0", "nan", "+1", "-1", "1_0", "6", "1."):
        with pytest.raises(CliError):
            check_value("CommunityRating", bad)
    for good in ("0", "3.5", "5", ".5", "4.25"):
        assert check_value("CommunityRating", good) == good


def test_card37_redact_exits_1_when_a_file_could_not_be_read(tmp_path, capsys):
    from cbzcli.main import main

    broken = tmp_path / "broken.cbz"
    broken.write_bytes(b"not a zip")
    code = main(["redact", str(broken), "--disable", "lookup"])
    assert code == 1
    assert "could not be read" in capsys.readouterr().err
    assert broken.read_bytes() == b"not a zip"


def test_card38_page_names_are_shown_as_plain_text(tmp_path):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel

    from core.credit_pages import KnownCreditPages, PageCandidate
    from gui.credit_pages_dialogs import CreditPagesDialog, RemoveCreditPagesDialog
    from gui.page_reader_dialog import PageReaderDialog

    name = "<h1>BIG</h1>001.jpg"
    path = _cbz_with_pages(tmp_path / "b.cbz", {name: _png(8, 8), "002.png": _png(8, 8)})
    reader = PageReaderDialog(CbzBook(path))
    try:
        assert reader.name_label.textFormat() == Qt.TextFormat.PlainText
        assert reader.image_label.textFormat() == Qt.TextFormat.PlainText
    finally:
        reader._loader.shutdown()
        reader._prefetch.shutdown()
    # a hint quotes part of the page name; the note must not render it
    candidate = PageCandidate(0, name, hints=['name contains scan group "<b>x</b>"'])
    dialog = CreditPagesDialog("b", [candidate], KnownCreditPages(str(tmp_path / "known.json")))
    notes = [label.text() for label in dialog.findChildren(QLabel) if "scan group" in label.text()]
    assert notes and "&lt;b&gt;" in notes[0] and "<b>x</b>" not in notes[0]
    removal = RemoveCreditPagesDialog([(None, "<i>file</i>.cbz", candidate)], skipped_note="<u>x</u>.cbz skipped")
    intro = [label for label in removal.findChildren(QLabel) if "known credit page" in label.text()]
    assert intro and intro[0].textFormat() == Qt.TextFormat.PlainText


def test_card38_duplicates_details_escape_the_file_name():
    from PyQt6.QtWidgets import QLabel

    from core.duplicates import BookFacts, BookFingerprint, DuplicateGroup
    from gui.duplicates_dialog import DuplicatesDialog

    facts = [BookFacts(BookFingerprint(), width=100, pages=3), BookFacts(BookFingerprint(), width=90, pages=3)]
    dialog = DuplicatesDialog([DuplicateGroup([0, 1], 0)], facts, ["Spider <Man> 1.cbz", "Spider 1.cbz"], [None, None])
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("Spider &lt;Man&gt; 1.cbz" in text for text in texts)
    assert not any("<Man>" in text for text in texts)


def _report_dialog(tmp_path, rows):
    from core import collection_scan as scan
    from core.collection_report import DuplicateGroup, Report
    from gui.collection_report_dialog import CollectionReportDialog

    info = scan.new_info(str(tmp_path), True)
    report = Report(duplicates=[DuplicateGroup("S", "1", rows)])
    return CollectionReportDialog(info, len(rows), report), report


def test_card39_report_mb_and_pages_sort_as_numbers(tmp_path):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QTableWidget

    from core import collection_scan as scan

    megabytes = [1.2, 10.5, 100.0, 2.0]
    pages = ["30", "100", "", "9"]
    rows = [scan.ScanRow(f"S/{i}.cbz", size=int(mb * 1048576), pages=p) for i, (mb, p) in enumerate(zip(megabytes, pages))]
    dialog, _report = _report_dialog(tmp_path, rows)
    table = next(t for t in dialog.findChildren(QTableWidget) if t.columnCount() == 7)
    table.sortItems(3, Qt.SortOrder.AscendingOrder)
    assert [table.item(r, 3).text() for r in range(4)] == ["1.2", "2.0", "10.5", "100.0"]
    table.sortItems(4, Qt.SortOrder.AscendingOrder)
    assert [table.item(r, 4).text() for r in range(4)] == ["?", "9", "30", "100"]


def test_card45_save_list_neutralises_formula_cells():
    from core.collection_report import csv_cell

    assert csv_cell('=HYPERLINK("http://x","click").cbz').startswith("'=")
    assert csv_cell("-1 Annual.cbz") == "'-1 Annual.cbz"
    assert csv_cell("+1") == "'+1" and csv_cell("@x") == "'@x"
    assert csv_cell("Saga 1.cbz") == "Saga 1.cbz" and csv_cell("") == ""


def test_card45_save_list_file_has_no_formula_cells(tmp_path, monkeypatch):
    import csv

    from PyQt6.QtWidgets import QFileDialog

    from core import collection_scan as scan

    rows = [scan.ScanRow('=HYPERLINK("http://x","click").cbz', size=1048576, pages="3"),
            scan.ScanRow("-1 Annual.cbz", size=1048576, pages="3")]
    dialog, _report = _report_dialog(tmp_path, rows)
    target = str(tmp_path / "list.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (target, ""))
    dialog._save_list()
    with open(target, encoding="utf-8-sig", newline="") as handle:
        cells = [cell for row in csv.reader(handle) for cell in row]
    assert not [c for c in cells if c and c[0] in "=+-@"]
    assert any(c.startswith("'=HYPERLINK") for c in cells)
