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
