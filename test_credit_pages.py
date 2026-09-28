"""Tests for core/credit_pages.py and CbzBook.remove_pages()."""

import io
import zipfile

import pytest
from PIL import Image, ImageDraw

from core.cbz_file import CbzBook, CbzError
from core.comicinfo import parse_comicinfo_xml
from core.credit_pages import (
    KnownCreditPages,
    candidate_indices,
    credit_matches,
    dhash,
    hamming,
    name_hints,
    open_page_image,
    scan_book,
)


def _tag_page(w=1200, h=1800):
    image = Image.new("RGB", (w, h), (20, 20, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((w * 0.1, h * 0.35, w * 0.9, h * 0.6), fill=(200, 30, 30))
    draw.ellipse((w * 0.3, h * 0.1, w * 0.7, h * 0.3), fill=(240, 240, 240))
    return image


def _story_page(seed, w=1200, h=1800):
    image = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(image)
    for i in range(6):
        x = (seed * 97 + i * 131) % (w - 300)
        y = (seed * 53 + i * 211) % (h - 300)
        draw.rectangle((x, y, x + 250, y + 200), fill=((seed * 40 + i * 30) % 255, 90, 160))
    return image


def _bytes(image, fmt="JPEG", **kw):
    out = io.BytesIO()
    image.save(out, fmt, **kw)
    return out.getvalue()


def _cbz(path, pages, comicinfo=None):
    with zipfile.ZipFile(path, "w") as zf:
        if comicinfo is not None:
            zf.writestr("ComicInfo.xml", comicinfo)
        for name, data in pages:
            zf.writestr(name, data)
    return str(path)


def test_candidates_are_the_first_two_and_last_four():
    assert candidate_indices(20) == [0, 1, 16, 17, 18, 19]
    assert candidate_indices(3) == [0, 1, 2]
    assert candidate_indices(0) == []


def test_fingerprint_survives_resizing_and_reencoding():
    original = dhash(open_page_image(_bytes(_tag_page())))
    webp = dhash(open_page_image(_bytes(_tag_page().resize((1440, 2160)), "WEBP", quality=70)))
    small = dhash(open_page_image(_bytes(_tag_page().resize((700, 1050)), "JPEG", quality=55)))
    assert hamming(original, webp) <= 2 and hamming(original, small) <= 2
    assert hamming(original, dhash(open_page_image(_bytes(_story_page(3))))) > 12


def test_known_pages_persist_and_match_near_duplicates(tmp_path):
    store = KnownCreditPages(str(tmp_path / "credit_pages.json"))
    page_hash = dhash(_tag_page())
    assert store.add(page_hash, b"thumb", "zzz_Zone-Empire.jpg")
    assert not store.add(page_hash ^ 0b11, b"", "again.jpg")  # near-identical: already known

    reloaded = KnownCreditPages(str(tmp_path / "credit_pages.json"))
    assert reloaded.match(page_hash ^ 0b111) is not None
    assert reloaded.pages[0].source == "zzz_Zone-Empire.jpg"
    assert reloaded.match(dhash(_story_page(1))) is None

    reloaded.forget(page_hash)
    assert reloaded.match(page_hash) is None


def test_name_hints():
    assert any("zz" in h for h in name_hints("comic/zzzz_tag.jpg"))
    assert any("scan group" in h for h in name_hints("zz_Zone-Empire.jpg"))
    assert any("tag/credits/scan" in h for h in name_hints("scanned_by_someone.png"))
    assert name_hints("Batman 045-0012.webp") == []


def test_scan_finds_a_learned_page_even_after_conversion(tmp_path):
    pages = [(f"{i:03}.jpg", _bytes(_story_page(i))) for i in range(10)]
    pages.append(("010.jpg", _bytes(_tag_page())))
    path = _cbz(tmp_path / "raw.cbz", pages)
    store = KnownCreditPages(str(tmp_path / "known.json"))
    tag = next(c for c in scan_book(path, sorted(n for n, _ in pages)) if c.name == "010.jpg")
    store.add(tag.hash, b"", tag.name)

    # Another release of a different comic, CbxConverter-style: renamed,
    # resized, WebP -- the same tag page is still found.
    converted = [(f"Other-{i:04}.webp", _bytes(_story_page(i + 20).resize((1440, 2160)), "WEBP")) for i in range(8)]
    converted.append(("Other-0008.webp", _bytes(_tag_page().resize((1440, 2160)), "WEBP", quality=70)))
    path2 = _cbz(tmp_path / "converted.cbz", converted)
    found = credit_matches(scan_book(path2, sorted(n for n, _ in converted)), store)
    assert [c.name for c in found] == ["Other-0008.webp"]


def test_plain_pages_are_never_fingerprinted(tmp_path):
    pages = [(f"{i:03}.jpg", _bytes(_story_page(i))) for i in range(6)]
    pages.append(("006.jpg", _bytes(Image.new("RGB", (1200, 1800), "white"))))
    path = _cbz(tmp_path / "blank.cbz", pages)
    blank = next(c for c in scan_book(path, sorted(n for n, _ in pages)) if c.name == "006.jpg")
    assert blank.plain and blank.hash is None


COMICINFO = b"""<?xml version="1.0"?>
<ComicInfo><Title>T</Title><PageCount>4</PageCount>
<Pages>
  <Page Image="0" Type="FrontCover"/>
  <Page Image="1" Type="Story"/>
  <Page Image="2" Type="Story" Bookmark="Chapter 2"/>
  <Page Image="3" Type="Other"/>
</Pages></ComicInfo>"""


def test_remove_pages_rewrites_archive_and_renumbers_pages(tmp_path):
    pages = [(f"{i:03}.jpg", _bytes(_story_page(i))) for i in range(4)]
    path = _cbz(tmp_path / "b.cbz", pages, COMICINFO)
    book = CbzBook(path)
    disposed = []

    removed = book.remove_pages(["001.jpg", "003.jpg"], dispose_original=disposed.append)

    assert removed == 2 and disposed == [path]
    assert book.page_names == ["000.jpg", "002.jpg"]
    with zipfile.ZipFile(path) as zf:
        assert sorted(n for n in zf.namelist() if n.endswith(".jpg")) == ["000.jpg", "002.jpg"]
        info = zf.read("ComicInfo.xml")
    meta = parse_comicinfo_xml(info)
    assert meta.page_count == "2"
    pages_el = next(e for e in meta.extra_elements if e.tag.endswith("Pages"))
    assert [(p.get("Image"), p.get("Type"), p.get("Bookmark")) for p in pages_el] == [
        ("0", "FrontCover", None),
        ("1", "Story", "Chapter 2"),  # was page 2 -- tag kept with its page
    ]
    assert not (tmp_path / "b.cbz.tmp_pages").exists()


def test_remove_pages_refuses_unsaved_edits_and_keeps_file_if_disposal_fails(tmp_path):
    pages = [(f"{i:03}.jpg", _bytes(_story_page(i))) for i in range(3)]
    path = _cbz(tmp_path / "c.cbz", pages, COMICINFO)
    book = CbzBook(path)
    book.dirty = True
    with pytest.raises(CbzError, match="unsaved"):
        book.remove_pages(["002.jpg"])
    book.dirty = False

    def refuse(_path):
        raise OSError("recycle bin unavailable")

    with pytest.raises(CbzError):
        book.remove_pages(["002.jpg"], dispose_original=refuse)
    assert CbzBook(path).actual_page_count == 3  # untouched
    assert not (tmp_path / "c.cbz.tmp_pages").exists()
