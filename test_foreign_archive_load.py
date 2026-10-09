"""Tests for MainWindow's CBR/CBT/CB7 -> CBZ conversion prompt in the
actual load flow (_load_paths / convert_foreign_archives_dialog) --
the real archive reading/writing itself is covered by
test_foreign_archive_convert.py; this file is about the GUI-level
prompt/delete-option wiring around it: does declining skip the file
instead of loading it, does the delete option actually delete only on
success, does a mixed batch of native + foreign files still load the
native ones regardless of the answer."""

import io
import os
import sys
import tarfile

import pytest
from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox

from core.foreign_archive_convert import ForeignArchiveConversionError
from gui import app_settings
from gui.main_window import FOREIGN_CONVERT, FOREIGN_SKIP, FOREIGN_UNCONVERTED, MainWindow

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
def settings(monkeypatch):
    """In-memory stand-in for the two conversion settings, so tests never
    read or write the real cbzredactor_settings.ini. Starts at "ask" so
    the tests that monkeypatch the prompt itself keep exercising it."""
    store = {"behavior": app_settings.FOREIGN_LOAD_ASK, "recycle": False}
    monkeypatch.setattr(app_settings, "load_foreign_load_behavior", lambda: store["behavior"])
    monkeypatch.setattr(app_settings, "save_foreign_load_behavior", lambda v: store.__setitem__("behavior", v))
    monkeypatch.setattr(app_settings, "load_recycle_originals", lambda: store["recycle"])
    monkeypatch.setattr(app_settings, "save_recycle_originals", lambda v: store.__setitem__("recycle", v))
    return store


@pytest.fixture
def window(monkeypatch, settings):
    # Never touch the real Recycle Bin from a test run.
    monkeypatch.setattr("gui.main_window.move_to_trash", lambda path: os.remove(path))
    return MainWindow()


def test_accepting_conversion_loads_the_book_and_keeps_the_original(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_CONVERT, False))

    window._load_paths([str(cbt_path)])

    assert len(window.books) == 1
    assert not window.books[0].load_error
    assert cbt_path.exists()  # never deleted unless explicitly asked


def test_accepting_conversion_with_delete_removes_the_original(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_CONVERT, True))

    window._load_paths([str(cbt_path)])

    assert len(window.books) == 1
    assert not window.books[0].load_error
    assert not cbt_path.exists()


def test_declining_conversion_skips_the_file_entirely(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_SKIP, False))

    window._load_paths([str(cbt_path)])

    assert window.books == []
    assert cbt_path.exists()  # declined -- untouched, not even attempted


def test_declining_conversion_still_loads_native_cbz_files_in_the_same_batch(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    cbz_path = tmp_path / "native.cbz"
    _make_real_cbz(cbz_path)
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_SKIP, False))

    window._load_paths([str(cbt_path), str(cbz_path)])

    assert len(window.books) == 1
    assert window.books[0].metadata.title == "Native"


def test_failed_conversion_never_deletes_the_original_even_if_delete_was_requested(window, tmp_path, monkeypatch):
    bad_cbt = tmp_path / "corrupt.cbt"
    bad_cbt.write_bytes(b"not a real tar file")
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_CONVERT, True))
    # A conversion failure reports through QMessageBox.warning() at the
    # end of _load_paths -- would otherwise block forever in a headless
    # test run, same as every other modal-dialog test in this project.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    window._load_paths([str(bad_cbt)])

    assert window.books == []
    assert bad_cbt.exists()  # conversion failed -- delete must never run


def test_prompt_not_shown_at_all_for_an_all_cbz_batch(window, tmp_path, monkeypatch):
    """No foreign files in the batch -- _prompt_convert_foreign_archives
    should short-circuit to (FOREIGN_CONVERT, False) without a real QMessageBox
    ever appearing (which would otherwise hang a headless test run)."""
    cbz_path = tmp_path / "native.cbz"
    _make_real_cbz(cbz_path)
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: pytest.fail("should not prompt when nothing needs converting")
    )

    window._load_paths([str(cbz_path)])

    assert len(window.books) == 1


def test_convert_foreign_archives_dialog_respects_delete_option(window, settings, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(
        "gui.main_window.QFileDialog.getOpenFileNames", lambda *a, **k: ([str(cbt_path)], "")
    )
    settings["recycle"] = True
    # The "Conversion Complete" QMessageBox.information() at the end of
    # the real dialog would otherwise block forever in a headless test
    # run, waiting for a click that never comes -- same class of issue
    # as every other modal-dialog test in this project.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window.convert_foreign_archives_dialog()

    assert not cbt_path.exists()
    assert len(window.books) == 1  # the dialog loads the converted result back in


# ---------------------------------------------------------------------------
# "Add Unconverted": read-only rows, converted later from the table
# ---------------------------------------------------------------------------


def test_add_unconverted_lists_the_file_read_only(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    cbz_path = tmp_path / "native.cbz"
    _make_real_cbz(cbz_path)
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_UNCONVERTED, False))

    window._load_paths([str(cbt_path), str(cbz_path)])

    assert len(window.books) == 2
    ext_col, status_col = window._col_index["ext"], window._col_index["status"]
    assert window.table.item(0, ext_col).text() == "CBT"
    assert window.table.item(0, status_col).text() == "Needs conversion"
    assert window.table.item(1, ext_col).text() == "CBZ"
    # Editing actions only ever see the real CBZ.
    assert window._target_books() == [window.books[1]]
    window.table.selectRow(0)
    assert window._editable_selected_rows() == []
    assert not (tmp_path / "book.cbz").exists()  # nothing written yet


def test_convert_from_the_table_replaces_the_row_in_place(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data", "page002.jpg": b"data"})
    _make_real_cbz(tmp_path / "first.cbz")
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_UNCONVERTED, False))
    window._load_paths([str(tmp_path / "first.cbz"), str(cbt_path)])

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.convert_books_to_cbz([window.books[1]])

    book = window.books[1]
    assert book.path.endswith("book.cbz")
    assert not book.needs_conversion
    assert book.actual_page_count == 2
    assert window.table.item(1, window._col_index["ext"]).text() == "CBZ"
    assert cbt_path.exists()  # kept: the Recycle Bin setting is off by default


def test_convert_from_the_table_can_recycle_the_original(window, settings, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_UNCONVERTED, False))
    window._load_paths([str(cbt_path)])
    settings["recycle"] = True

    trashed = []
    monkeypatch.setattr("gui.main_window.move_to_trash", trashed.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.convert_books_to_cbz(list(window.books))

    assert trashed == [str(cbt_path)]
    assert not window.books[0].needs_conversion


# ---------------------------------------------------------------------------
# Settings > Converting to CBZ...
# ---------------------------------------------------------------------------


def test_default_setting_adds_unconverted_without_prompting(window, settings, tmp_path, monkeypatch):
    settings["behavior"] = app_settings.FOREIGN_LOAD_UNCONVERTED
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("should not prompt"))

    window._load_paths([str(cbt_path)])

    assert window.books[0].needs_conversion
    assert not (tmp_path / "book.cbz").exists()


def test_convert_setting_converts_and_recycles_without_prompting(window, settings, tmp_path, monkeypatch):
    settings["behavior"] = app_settings.FOREIGN_LOAD_CONVERT
    settings["recycle"] = True
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("should not prompt"))

    window._load_paths([str(cbt_path)])

    assert not window.books[0].needs_conversion
    assert not cbt_path.exists()


def test_remember_my_choice_saves_the_setting(window, settings, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})

    def _click(box):
        for check in box.findChildren(QCheckBox):
            check.setChecked(True)  # recycle + remember
        next(b for b in box.buttons() if b.text() == "Convert Now").click()

    monkeypatch.setattr(QMessageBox, "exec", _click)
    choice, delete = window._prompt_convert_foreign_archives([str(cbt_path)])

    assert (choice, delete) == (FOREIGN_CONVERT, True)
    assert settings == {"behavior": app_settings.FOREIGN_LOAD_CONVERT, "recycle": True}


def test_skip_is_never_remembered(window, settings, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})

    def _click(box):
        for check in box.findChildren(QCheckBox):
            check.setChecked(True)
        next(b for b in box.buttons() if b.text() == "Skip").click()

    monkeypatch.setattr(QMessageBox, "exec", _click)
    window._prompt_convert_foreign_archives([str(cbt_path)])
    assert settings["behavior"] == app_settings.FOREIGN_LOAD_ASK


def test_settings_dialog_saves_both_choices(settings):
    from gui.conversion_settings_dialog import ConversionSettingsDialog

    dialog = ConversionSettingsDialog()
    dialog._radios[app_settings.FOREIGN_LOAD_CONVERT].setChecked(True)
    dialog.recycle_check.setChecked(True)
    dialog.accept()
    assert settings == {"behavior": app_settings.FOREIGN_LOAD_CONVERT, "recycle": True}


@pytest.mark.parametrize(
    "button, expected",
    [("Convert Now", FOREIGN_CONVERT), ("Add Unconverted", FOREIGN_UNCONVERTED), ("Skip", FOREIGN_SKIP)],
)
def test_real_prompt_buttons_map_to_choices(window, tmp_path, monkeypatch, button, expected):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})

    def _click(box):
        next(b for b in box.buttons() if b.text() == button).click()

    monkeypatch.setattr(QMessageBox, "exec", _click)
    choice, delete = window._prompt_convert_foreign_archives([str(cbt_path)])
    assert choice == expected
    assert delete is False


def test_zip_mislabeled_as_cbr_shows_both_names(window, tmp_path, monkeypatch):
    fake_cbr = tmp_path / "actually_zip.cbr"
    _make_real_cbz(fake_cbr, title="Hidden")
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_UNCONVERTED, False))
    window._load_paths([str(fake_cbr)])
    assert window.table.item(0, window._col_index["ext"]).text() == "CBR → ZIP"
    assert window.books[0].metadata.title == "Hidden"


def test_a_cbr_whose_cbz_already_exists_loads_that_cbz_instead_of_failing(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    cbz_path = tmp_path / "book.cbz"
    _make_real_cbz(cbz_path)  # converted earlier
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_CONVERT, True))

    window._load_paths([str(cbt_path)])

    assert warnings == []  # no "already exists -- not overwriting it" failure
    assert [os.path.basename(b.path) for b in window.books] == ["book.cbz"]
    assert window.books[0].metadata.title == "Native"  # the existing file, not a new conversion
    assert cbt_path.exists()  # the original is left alone even though "delete" was asked


def test_the_existing_cbz_is_not_listed_twice_when_it_is_in_the_same_batch(window, tmp_path, monkeypatch):
    cbt_path = tmp_path / "book.cbt"
    _make_cbt(cbt_path, {"page001.jpg": b"data"})
    cbz_path = tmp_path / "book.cbz"
    _make_real_cbz(cbz_path)
    monkeypatch.setattr(window, "_prompt_convert_foreign_archives", lambda paths: (FOREIGN_CONVERT, False))

    window._load_paths([str(cbt_path), str(cbz_path)])

    assert [os.path.basename(b.path) for b in window.books] == ["book.cbz"]
