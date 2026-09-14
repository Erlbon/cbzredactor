"""Tests for MainWindow's CBR/CBT/CB7 -> CBZ conversion prompt in the
actual load flow (_load_paths / convert_foreign_archives_dialog) --
the real archive reading/writing itself is covered by
test_foreign_archive_convert.py; this file is about the GUI-level
prompt/delete-option wiring around it: does declining skip the file
instead of loading it, does the delete option actually delete only on
success, does a mixed batch of native + foreign files still load the
native ones regardless of the answer."""

import io
import sys
import tarfile

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.foreign_archive_convert import ForeignArchiveConversionError
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _make_cbt(path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _make_real_cbz(path, title="Native") -> None:
    import zipfile
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "ComicInfo.xml",
            f"<ComicInfo><Title>{title}</Title></ComicInfo>",
        )
        zf.writestr("page001.jpg", b"fake page")


@pytest.fixture
def window():
    return MainWindow()


def test_accepting_conversion_loads_the_book_and_keeps_the_original(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (True, False))

    window._load_paths([str(cbt_path)])

    assert len(window.books) == 1
    assert not window.books[0].load_error
    assert cbt_path.exists()  # never deleted unless explicitly asked


def test_accepting_conversion_with_delete_removes_the_original(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (True, True))

    window._load_paths([str(cbt_path)])

    assert len(window.books) == 1
    assert not window.books[0].load_error
    assert not cbt_path.exists()


def test_declining_conversion_skips_the_file_entirely(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (False, False))

    window._load_paths([str(cbt_path)])

    assert window.books == []
    assert cbt_path.exists()  # declined -- untouched, not even attempted


def test_declining_conversion_still_loads_native_cbz_files_in_the_same_batch(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    cbz_path = tmp_path / "native.cbz"
    _make_real_cbz(cbz_path)
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (False, False))

    window._load_paths([str(cbt_path), str(cbz_path)])

    assert len(window.books) == 1
    assert window.books[0].metadata.title == "Native"


def test_failed_conversion_never_deletes_the_original_even_if_delete_was_requested(window, tmp_path, monkeypatch):
    bad_cbt = tmp_path / "corrupt.cbt"
    bad_cbt.write_bytes(b"not a real tar file")
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (True, True))
    # A conversion failure reports through QMessageBox.warning() at the
    # end of _load_paths -- would otherwise block forever in a headless
    # test run, same as every other modal-dialog test in this project.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    window._load_paths([str(bad_cbt)])

    assert window.books == []
    assert bad_cbt.exists()  # conversion failed -- delete must never run


def test_prompt_not_shown_at_all_for_an_all_cbz_batch(window, tmp_path, monkeypatch):
    """No foreign files in the batch -- _prompt_convert_foreign_archives
    should short-circuit to (True, False) without a real QMessageBox
    ever appearing (which would otherwise hang a headless test run)."""
    cbz_path = tmp_path / "native.cbz"
    _make_real_cbz(cbz_path)
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: pytest.fail("should not prompt when nothing needs converting")
    )

    window._load_paths([str(cbz_path)])

    assert len(window.books) == 1


def test_convert_foreign_archives_dialog_respects_delete_option(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(
        "gui.main_window.QFileDialog.getOpenFileNames", lambda *a, **k: ([str(cbt_path)], "")
    )
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (True, True))
    # The "Conversion Complete" QMessageBox.information() at the end of
    # the real dialog would otherwise block forever in a headless test
    # run, waiting for a click that never comes -- same class of issue
    # as every other modal-dialog test in this project.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window.convert_foreign_archives_dialog()

    assert not cbt_path.exists()
    assert len(window.books) == 1  # the dialog loads the converted result back in
