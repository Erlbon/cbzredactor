"""Tests for core/foreign_archive_convert.py.

CBT (tar) and CB7 (7-Zip) can both be created AND read purely in
Python (stdlib tarfile / the py7zr package), so those two get real
round-trip tests -- unlike CBR, which needs a real RAR-writing tool
(never installed in this environment, or generally, since RAR is a
proprietary format nothing here can write) to produce a genuine test
fixture. CBR tests here are limited to the parts that don't need one:
dependency-missing and bad-input error handling, same limitation the
original core/cbr_convert.py's own tests had."""

import io
import sys
import tarfile
import zipfile

import pytest

from core.foreign_archive_convert import ForeignArchiveConversionError, convert_to_cbz

# Only the CB7 tests need this -- imported at module level (rather than
# per-test importorskip) since it's a normal requirements.txt
# dependency, expected present in any environment that runs this suite
# at all; a real environment missing it would fail loudly here rather
# than silently skipping real coverage.
import py7zr


# ---------------------------------------------------------------------------
# CBR -- dependency/bad-input only, no real archive available to create here
# ---------------------------------------------------------------------------


def test_cbr_missing_rarfile_dependency_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "rarfile", None)
    fake_cbr = tmp_path / "book.cbr"
    fake_cbr.write_bytes(b"not a real rar file")

    with pytest.raises(ForeignArchiveConversionError, match="rarfile"):
        convert_to_cbz(str(fake_cbr))


def test_cbr_bad_input_raises_clear_error_not_a_raw_exception(tmp_path):
    fake_cbr = tmp_path / "book.cbr"
    fake_cbr.write_bytes(b"not a real rar file")

    with pytest.raises(ForeignArchiveConversionError):
        # Either "rarfile not installed" or "could not read" -- both are
        # fine here; this just confirms no unhandled exception type
        # leaks out of convert_to_cbz for bad input.
        convert_to_cbz(str(fake_cbr))


# ---------------------------------------------------------------------------
# CBT -- real round-trip, tarfile is stdlib and can both write and read
# ---------------------------------------------------------------------------


def _make_cbt(path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def test_cbt_real_conversion_round_trips_file_contents(tmp_path):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {
        "page001.jpg": b"\xff\xd8\xff fake page one",
        "page002.jpg": b"\xff\xd8\xff fake page two",
        "ComicInfo.xml": b"<ComicInfo><Title>Test</Title></ComicInfo>",
    })

    output = convert_to_cbz(str(cbt_path))

    assert output == str(tmp_path / "book.cbz")
    with zipfile.ZipFile(output) as zf:
        names = set(zf.namelist())
        assert names == {"page001.jpg", "page002.jpg", "ComicInfo.xml"}
        assert zf.read("page001.jpg") == b"\xff\xd8\xff fake page one"
        assert zf.read("ComicInfo.xml") == b"<ComicInfo><Title>Test</Title></ComicInfo>"
    # Original left untouched -- deletion, if wanted, is the caller's
    # own separate step, never automatic.
    assert cbt_path.exists()


def test_cbt_preserves_subfolder_structure(tmp_path):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"chapter1/page001.jpg": b"data"})

    output = convert_to_cbz(str(cbt_path))

    with zipfile.ZipFile(output) as zf:
        assert "chapter1/page001.jpg" in zf.namelist()


def test_cbt_explicit_output_path_is_honored(tmp_path):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    custom_output = tmp_path / "renamed.cbz"

    output = convert_to_cbz(str(cbt_path), output_path=str(custom_output))

    assert output == str(custom_output)
    assert custom_output.exists()


def test_cbt_bad_input_raises_clear_error(tmp_path):
    fake_cbt = tmp_path / "book.cbt"
    fake_cbt.write_bytes(b"not a real tar file at all, just garbage bytes")

    with pytest.raises(ForeignArchiveConversionError, match="Could not read CBT"):
        convert_to_cbz(str(fake_cbt))


# ---------------------------------------------------------------------------
# CB7 -- real round-trip, py7zr can both write and read
# ---------------------------------------------------------------------------


def _make_cb7(path, files: dict[str, bytes]) -> None:
    with py7zr.SevenZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(data, name)


def test_cb7_real_conversion_round_trips_file_contents(tmp_path):
    cb7_path = tmp_path / "book.cb7"
    _make_cb7(cb7_path, {
        "page001.jpg": b"fake page one bytes",
        "page002.jpg": b"fake page two bytes",
    })

    output = convert_to_cbz(str(cb7_path))

    assert output == str(tmp_path / "book.cbz")
    with zipfile.ZipFile(output) as zf:
        names = set(zf.namelist())
        assert names == {"page001.jpg", "page002.jpg"}
        assert zf.read("page001.jpg") == b"fake page one bytes"
    assert cb7_path.exists()


def test_cb7_missing_py7zr_dependency_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "py7zr", None)
    fake_cb7 = tmp_path / "book.cb7"
    fake_cb7.write_bytes(b"not a real 7z file")

    with pytest.raises(ForeignArchiveConversionError, match="py7zr"):
        convert_to_cbz(str(fake_cb7))


def test_cb7_bad_input_raises_clear_error(tmp_path):
    fake_cb7 = tmp_path / "book.cb7"
    fake_cb7.write_bytes(b"not a real 7z file at all, just garbage bytes")

    with pytest.raises(ForeignArchiveConversionError, match="Could not read CB7"):
        convert_to_cbz(str(fake_cb7))


# ---------------------------------------------------------------------------
# Format dispatch
# ---------------------------------------------------------------------------


def test_unsupported_extension_raises_clear_error(tmp_path):
    fake = tmp_path / "book.pdf"
    fake.write_bytes(b"whatever")

    with pytest.raises(ForeignArchiveConversionError, match="Unsupported archive format"):
        convert_to_cbz(str(fake))
