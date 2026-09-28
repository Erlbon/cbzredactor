"""Tests for core/archive_sniff.py and the needs-conversion rules built
on it (CbzBook.needs_conversion, convert_to_cbz's content-based
dispatch, relabel_mislabeled_cbz)."""

import io
import tarfile
import zipfile

import pytest

from core.archive_sniff import (
    CONTAINER_7Z,
    CONTAINER_RAR,
    CONTAINER_TAR,
    CONTAINER_UNKNOWN,
    CONTAINER_ZIP,
    detect_container,
    extension_label,
)
from core.cbz_file import CbzBook, CbzError, path_needs_conversion
from core.foreign_archive_convert import (
    ForeignArchiveConversionError,
    convert_to_cbz,
    relabel_mislabeled_cbz,
)

COMICINFO = b"<ComicInfo><Title>Inside</Title></ComicInfo>"


def _zip(path, pages=2):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", COMICINFO)
        for i in range(pages):
            zf.writestr(f"{i:03}.jpg", b"page")
    return str(path)


def _tar(path, pages=2):
    with tarfile.open(path, "w") as tf:
        for i in range(pages):
            info = tarfile.TarInfo(f"{i:03}.jpg")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"page"))
    return str(path)


def test_detects_each_container(tmp_path):
    assert detect_container(_zip(tmp_path / "a.cbz")) == CONTAINER_ZIP
    assert detect_container(_tar(tmp_path / "a.cbt")) == CONTAINER_TAR
    (tmp_path / "a.cbr").write_bytes(b"Rar!\x1a\x07\x01\x00rest")
    assert detect_container(str(tmp_path / "a.cbr")) == CONTAINER_RAR
    (tmp_path / "a.cb7").write_bytes(b"7z\xbc\xaf\x27\x1crest")
    assert detect_container(str(tmp_path / "a.cb7")) == CONTAINER_7Z
    (tmp_path / "junk.cbr").write_bytes(b"nothing")
    assert detect_container(str(tmp_path / "junk.cbr")) == CONTAINER_UNKNOWN
    assert detect_container(str(tmp_path / "missing.cbz")) == CONTAINER_UNKNOWN


def test_extension_label_flags_a_mismatch_only():
    assert extension_label("x.cbz", CONTAINER_ZIP) == "CBZ"
    assert extension_label("x.cbr", CONTAINER_RAR) == "CBR"
    assert extension_label("x.cbr", CONTAINER_ZIP) == "CBR → ZIP"
    assert extension_label("x.cbz", CONTAINER_RAR) == "CBZ → RAR"
    assert extension_label("x.cbr", CONTAINER_UNKNOWN) == "CBR"


def test_real_cbz_needs_no_conversion(tmp_path):
    book = CbzBook(_zip(tmp_path / "a.cbz"))
    assert not book.needs_conversion
    assert not path_needs_conversion(book.path)


def test_zip_named_cbr_loads_its_metadata_but_stays_read_only(tmp_path):
    book = CbzBook(_zip(tmp_path / "a.cbr"))
    assert book.needs_conversion
    assert book.metadata.title == "Inside"
    assert book.actual_page_count == 2
    with pytest.raises(CbzError, match="converting"):
        book.save()
    with pytest.raises(CbzError, match="converting"):
        book.resize_images(1440)


def test_tar_is_listed_without_reading_it(tmp_path):
    book = CbzBook(_tar(tmp_path / "a.cbt"))
    assert book.needs_conversion
    assert book.page_names == []
    assert not book.load_error


def test_broken_cbz_is_a_load_error_not_a_conversion(tmp_path):
    path = tmp_path / "broken.cbz"
    path.write_bytes(b"garbage")
    book = CbzBook(str(path))
    assert book.load_error
    assert not book.needs_conversion


def test_zip_named_cbr_converts_by_copying(tmp_path):
    source = _zip(tmp_path / "a.cbr")
    output = convert_to_cbz(source)
    assert output.endswith("a.cbz")
    with open(source, "rb") as a, open(output, "rb") as b:
        assert a.read() == b.read()  # byte-for-byte, no repacking


def test_tar_named_cbr_converts_by_content_not_extension(tmp_path):
    output = convert_to_cbz(_tar(tmp_path / "a.cbr"))
    assert CbzBook(output).actual_page_count == 2


def test_conversion_never_overwrites_an_existing_cbz(tmp_path):
    _zip(tmp_path / "a.cbz")
    with pytest.raises(ForeignArchiveConversionError, match="already exists"):
        convert_to_cbz(_tar(tmp_path / "a.cbt"))


def test_mislabeled_cbz_is_relabeled_then_converted(tmp_path):
    path = _tar(tmp_path / "a.cbz")
    assert path_needs_conversion(path)
    relabeled = relabel_mislabeled_cbz(path)
    assert relabeled.endswith("a.cbt")
    output = convert_to_cbz(relabeled)
    assert output.endswith("a.cbz")
    assert not CbzBook(output).needs_conversion


def test_failed_conversion_leaves_no_partial_output(tmp_path):
    bad = tmp_path / "bad.cbt"
    bad.write_bytes(b"not a tar")
    with pytest.raises(ForeignArchiveConversionError):
        convert_to_cbz(str(bad))
    assert not (tmp_path / "bad.cbz").exists()
    assert not (tmp_path / "bad.cbz.tmp_convert").exists()
