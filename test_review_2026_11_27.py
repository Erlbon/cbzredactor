"""Tests for the 2026-11-27 review cards (one test per fixed card)."""

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
