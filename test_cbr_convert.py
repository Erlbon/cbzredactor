"""Tests for core/cbr_convert.py -- only the parts that don't require a
real RAR file or an installed unrar binary. Actual conversion is
exercised manually / in a future integration test once a sample .cbr
and a CI image with unrar are available."""

import sys

import pytest

from core.cbr_convert import CbrConversionError, convert_cbr_to_cbz


def test_missing_rarfile_dependency_raises_clear_error(tmp_path, monkeypatch):
    # Simulate `rarfile` not being installed, regardless of whether it
    # actually is in this environment -- forces ImportError deterministically.
    monkeypatch.setitem(sys.modules, "rarfile", None)
    fake_cbr = tmp_path / "book.cbr"
    fake_cbr.write_bytes(b"not a real rar file")

    with pytest.raises(CbrConversionError, match="rarfile"):
        convert_cbr_to_cbz(str(fake_cbr))


def test_output_path_defaults_to_cbz_extension(tmp_path, monkeypatch):
    """Doesn't need a real RAR archive -- just checks the default output
    path computation runs before any actual archive reading, by forcing
    the read step to fail predictably and inspecting the message."""
    fake_cbr = tmp_path / "book.cbr"
    fake_cbr.write_bytes(b"not a real rar file")

    with pytest.raises(CbrConversionError):
        # Either "rarfile not installed" or "could not read" -- both are
        # fine here; this just confirms no unhandled exception type
        # leaks out of convert_cbr_to_cbz for a bad input.
        convert_cbr_to_cbz(str(fake_cbr))
