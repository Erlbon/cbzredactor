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


# ---------------------------------------------------------------------------
# 2026-09-30 review fixes: everything wraps, tar fallback is safe, copy is atomic
# ---------------------------------------------------------------------------


def test_cbt_oserror_during_extraction_becomes_conversion_error(tmp_path, monkeypatch):
    _make_cbt(tmp_path / "book.cbt", {"001.jpg": b"x"})

    def disk_full(self, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tarfile.TarFile, "extractall", disk_full)
    with pytest.raises(ForeignArchiveConversionError, match="space"):
        convert_to_cbz(str(tmp_path / "book.cbt"))
    assert not (tmp_path / "book.cbz").exists()


def test_cb7_lzma_error_becomes_conversion_error(tmp_path, monkeypatch):
    import lzma

    with py7zr.SevenZipFile(tmp_path / "book.cb7", "w") as zf:
        zf.writestr(b"x", "001.jpg")

    def corrupt(self, *a, **k):
        raise lzma.LZMAError("Corrupt input data")

    monkeypatch.setattr(py7zr.SevenZipFile, "extractall", corrupt)
    with pytest.raises(ForeignArchiveConversionError, match="Corrupt"):
        convert_to_cbz(str(tmp_path / "book.cb7"))


def test_cbr_oserror_becomes_conversion_error(tmp_path, monkeypatch):
    import types

    class _Error(Exception):
        pass

    class _RarFile:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extractall(self, dest):
            raise OSError(22, "Invalid argument: illegal file name")

    fake = types.SimpleNamespace(RarFile=_RarFile, RarCannotExec=type("RarCannotExec", (_Error,), {}), Error=_Error)
    monkeypatch.setitem(sys.modules, "rarfile", fake)
    (tmp_path / "book.cbr").write_bytes(b"Rar!\x1a\x07\x00 not really")
    with pytest.raises(ForeignArchiveConversionError, match="illegal file name"):
        convert_to_cbz(str(tmp_path / "book.cbr"))


def test_tar_fallback_skips_traversal_and_links(tmp_path):
    from core.foreign_archive_convert import _extract_tar_safely

    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tf:
        for name, data in (("../escaped.txt", b"bad"), ("sub/001.jpg", b"ok")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("link.jpg")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    dest = tmp_path / "out" / "dest"
    dest.mkdir(parents=True)
    with tarfile.open(archive) as tf:
        _extract_tar_safely(tf, str(dest))
    assert (dest / "sub" / "001.jpg").read_bytes() == b"ok"
    assert not (dest.parent / "escaped.txt").exists()
    assert not (dest / "link.jpg").exists() and not (dest / "link.jpg").is_symlink()


def test_cbt_on_old_python_path_uses_safe_extraction(tmp_path, monkeypatch):
    # Emulate Python < 3.12: no data_filter, extractall() trusts everything.
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    monkeypatch.setattr(tarfile.TarFile, "extraction_filter", staticmethod(lambda member, path: member), raising=False)
    _make_cbt(tmp_path / "book.cbt", {"../escaped.jpg": b"bad", "001.jpg": b"ok"})
    output = convert_to_cbz(str(tmp_path / "book.cbt"))
    with zipfile.ZipFile(output) as zf:
        assert zf.namelist() == ["001.jpg"]
    assert not (tmp_path / "escaped.jpg").exists()


def test_same_format_copy_goes_through_a_temp_file(tmp_path, monkeypatch):
    import shutil

    src = tmp_path / "book.cbr"  # really a ZIP
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("001.jpg", b"x")

    def partial_copy(s, d, *a, **k):
        with open(d, "wb") as f:
            f.write(b"trunc")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", partial_copy)
    with pytest.raises(ForeignArchiveConversionError):
        convert_to_cbz(str(src))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.cbr"]


def test_failed_conversion_restores_a_relabeled_file_name(tmp_path, monkeypatch):
    import gui.main_window as mw

    path = tmp_path / "book.cbz"
    _make_cbt(path, {"001.jpg": b"x"})  # a tar wearing a .cbz name

    def fail(_source):
        raise ForeignArchiveConversionError("boom")

    monkeypatch.setattr(mw, "convert_to_cbz", fail)
    errors: list[str] = []
    assert mw.MainWindow._convert_path(None, str(path), False, errors) is None
    assert path.exists() and not (tmp_path / "book.cbt").exists()
    assert errors == ["book.cbz: boom"]
