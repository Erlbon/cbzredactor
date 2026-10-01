"""Regression tests for the 2026-09-30 code-review findings in the archive
rewriters (core/cbz_file.py): atomic replace, temp-file cleanup, errors
wrapped as CbzError, decompression bombs reported as failed pages."""

import io
import os
import zipfile

import pytest
from PIL import Image

import core.cbz_file as cbz_file
import core.zip_rewrite as zip_rewrite
from core.cbz_file import CbzBook, CbzError
from core.image_resize import resize_page
from core.page_dimensions import read_image_size

COMICINFO_XML = b"<?xml version='1.0'?><ComicInfo><Title>T</Title></ComicInfo>"


def _jpeg(width=200, height=300) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color=(1, 2, 3)).save(out, format="JPEG")
    return out.getvalue()


def _make(path, comment=b"", pages=("001.jpg", "002.jpg")) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.comment = comment
        zf.writestr("ComicInfo.xml", COMICINFO_XML)
        for name in pages:
            zf.writestr(name, _jpeg())
    return str(path)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(zip_rewrite, "_REPLACE_DELAY", 0)  # the retry loop lives in core/zip_rewrite.py


def _tmp_files(tmp_path):
    return [p for p in os.listdir(tmp_path) if ".tmp_" in p]


def _no_shutil_move(monkeypatch):
    # H1: neither save() nor resize_images() may go through shutil.move.
    monkeypatch.setattr("shutil.move", lambda *a, **k: pytest.fail("shutil.move used"))


def test_save_and_resize_replace_atomically(tmp_path, monkeypatch):
    _no_shutil_move(monkeypatch)
    replaced = []
    real = os.replace
    monkeypatch.setattr(cbz_file.os, "replace", lambda a, b: (replaced.append(b), real(a, b))[1])
    path = _make(tmp_path / "a.cbz")
    book = CbzBook(path)
    book.save()
    book.resize_images(100)
    assert replaced == [path, path]


def test_save_removes_temp_file_on_failure_and_wraps_runtime_error(tmp_path, monkeypatch):
    path = _make(tmp_path / "a.cbz")
    book = CbzBook(path)

    def encrypted(self, name, *a, **k):
        raise RuntimeError("File is encrypted, password required")

    monkeypatch.setattr(zipfile.ZipFile, "read", encrypted)
    with pytest.raises(CbzError):
        book.save()
    monkeypatch.undo()
    assert _tmp_files(tmp_path) == []
    assert CbzBook(path).actual_page_count == 2  # original intact


@pytest.mark.parametrize("exc", [NotImplementedError("compression type 99"), ValueError("bad")])
def test_save_wraps_other_zip_errors(tmp_path, monkeypatch, exc):
    book = CbzBook(_make(tmp_path / "a.cbz"))

    def boom(self, name, *a, **k):
        raise exc

    monkeypatch.setattr(zipfile.ZipFile, "read", boom)
    with pytest.raises(CbzError):
        book.save()


def test_read_first_page_bytes_survives_encrypted_entry(tmp_path, monkeypatch):
    book = CbzBook(_make(tmp_path / "a.cbz"))

    def boom(self, name, *a, **k):
        raise RuntimeError("encrypted")

    monkeypatch.setattr(zipfile.ZipFile, "read", boom)
    assert book.read_first_page_bytes() is None


def test_resize_keeps_zip_comment(tmp_path):
    path = _make(tmp_path / "a.cbz", comment=b"fingerprint")
    CbzBook(path).resize_images(100)
    with zipfile.ZipFile(path) as zf:
        assert zf.comment == b"fingerprint"


def test_resize_removes_temp_on_unexpected_exception(tmp_path, monkeypatch):
    path = _make(tmp_path / "a.cbz")
    book = CbzBook(path)

    def boom(*a, **k):
        raise MemoryError("decoder blew up")

    monkeypatch.setattr(cbz_file, "resize_page", boom)
    with pytest.raises(MemoryError):
        book.resize_images(50)
    assert _tmp_files(tmp_path) == []


def test_decompression_bomb_page_counts_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)  # > 2x -> DecompressionBombError
    data = _jpeg(100, 100)
    result = resize_page(data, 10)
    assert result.error and result.data == data
    assert read_image_size(io.BytesIO(data)) is None
    path = _make(tmp_path / "a.cbz")
    summary = CbzBook(path).resize_images(10)
    assert summary.pages_failed == 2
    assert _tmp_files(tmp_path) == []


def test_replace_failure_after_dispose_keeps_temp_and_names_it(tmp_path, monkeypatch):
    path = _make(tmp_path / "a.cbz", pages=("001.jpg", "002.jpg", "003.jpg"))
    book = CbzBook(path)

    def trash(p):
        os.remove(p)  # what the Recycle Bin does to the original

    def fail_replace(a, b):
        raise PermissionError("locked")

    monkeypatch.setattr(cbz_file.os, "replace", fail_replace)
    with pytest.raises(CbzError) as info:
        book.remove_pages(["003.jpg"], dispose_original=trash)
    monkeypatch.undo()
    kept = path + ".tmp_pages"
    assert kept in str(info.value)
    assert os.path.exists(kept)
    assert CbzBook.__name__  # temp is a complete CBZ
    with zipfile.ZipFile(kept) as zf:
        assert "003.jpg" not in zf.namelist()


def test_replace_failure_without_dispose_still_removes_temp(tmp_path, monkeypatch):
    path = _make(tmp_path / "a.cbz")
    book = CbzBook(path)
    monkeypatch.setattr(cbz_file.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(CbzError):
        book.remove_pages(["002.jpg"])
    monkeypatch.undo()
    assert _tmp_files(tmp_path) == []
    assert os.path.exists(path)


def test_transient_permission_error_on_replace_is_retried(tmp_path, monkeypatch):
    path = _make(tmp_path / "a.cbz")
    book = CbzBook(path)
    real = os.replace
    calls = []

    def flaky(a, b):
        calls.append(b)
        if len(calls) < 3:
            raise PermissionError("held open by another reader")
        real(a, b)

    monkeypatch.setattr(cbz_file.os, "replace", flaky)
    book.metadata.title = "New"
    book.save()
    assert len(calls) == 3 and CbzBook(path).metadata.title == "New"
