"""The operations journal (core/history.py, redactor_common.core.journal) with REAL small CBZ files:
every tag change a Save, Save As over a file, Redact or the command line makes is recorded with old and
new values, and Restore writes the old values back with the pages untouched. The `journal` fixture
(conftest.py) points the process-wide journal at tmp_path."""

import os
import sys
import zipfile

import pytest

from core import history
from core.cbz_file import CbzBook, CbzError
from core.zip_rewrite import RewriteError
from redactor_common.core.journal import KIND_REDACT, KIND_SAVE, STATUS_FAILED, STATUS_OK
from test_redact_steps import _cbz, _only, _recipe, _run, env  # noqa: F401 -- env is a fixture


@pytest.fixture(autouse=True)
def history_on():
    """History is opt-in (off by default, see test_history_opt_in.py): these tests switch it on first."""
    history.set_history_enabled(True)
    assert history.history_enabled()


def _pages(path):
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist() if n.endswith(".jpg")}


# --- J1: fields_text, get_journal, apply_restore ----------------------------------------------


def test_fields_text_is_plain_text_without_technical_fields(tmp_path):
    book = CbzBook(_cbz(tmp_path / "a.cbz"))
    book.record_scan("OK")
    fields = history.fields_text(book.metadata)
    assert fields["Series"] == "Saga" and fields["Writer"] == "BKV" and fields["Title"] == ""
    assert "PageCount" not in fields and "RedactorScan" not in fields and "scan_stamp" not in fields
    assert all(isinstance(v, str) for v in fields.values())


def test_the_journal_sits_next_to_the_settings(journal):
    from core.app_paths import base_dir

    assert history.get_journal() is journal  # the test's own one (conftest)
    assert history.journal_path() == os.path.join(base_dir(), "cbzredactor_journal.jsonl")
    history.use_journal(None)
    assert history.get_journal().path == history.journal_path()


def test_apply_restore_refuses_unknown_fields_and_missing_files(tmp_path):
    path = _cbz(tmp_path / "a.cbz")
    assert "PageCount" in history.apply_restore(path, {"PageCount": "7"})
    assert "not there" in history.apply_restore(str(tmp_path / "gone.cbz"), {"Series": "x"})


# --- J2 + J3 + J6: Save, restore round trip ---------------------------------------------------


def test_save_records_old_and_new_values_and_restore_brings_them_back(tmp_path, journal):
    path = _cbz(tmp_path / "Saga 001.cbz")
    pages_before = _pages(path)
    book = CbzBook(path)
    assert book.baseline["Series"] == "Saga"
    book.metadata.series = "Saga Deluxe"
    book.metadata.title = "Chapter One"
    book.save()
    assert book.baseline["Series"] == "Saga Deluxe"  # the baseline follows the file

    entry = _only_entry(journal)
    assert entry.kind == KIND_SAVE and entry.status == STATUS_OK and entry.path == os.path.abspath(path)
    assert entry.changes == {"Series": ("Saga", "Saga Deluxe"), "Title": ("", "Chapter One")}

    values, skipped = journal.restore_values(entry)
    assert history.apply_restore(path, values) == "" and skipped == []
    back = CbzBook(path)
    assert back.metadata.series == "Saga" and back.metadata.title == "" and back.metadata.writer == "BKV"
    assert _pages(path) == pages_before  # pages byte for byte
    assert len(journal.entries()) == 1  # apply_restore writes no entry itself (the dialog does)


def test_a_save_with_no_real_change_or_only_a_scan_stamp_records_nothing(tmp_path, journal):
    path = _cbz(tmp_path / "a.cbz")
    book = CbzBook(path)
    book.save()
    book.metadata.series = "  Saga  "  # only blanks around it: the same text
    book.save()
    assert book.record_scan("OK")
    book.save()
    assert journal.entries() == []


def test_a_failed_save_records_failed(tmp_path, journal, monkeypatch):
    import core.cbz_file as cbz_file

    path = _cbz(tmp_path / "a.cbz")
    book = CbzBook(path)
    book.metadata.series = "Other"

    def broken(*_a, **_k):
        raise RewriteError("Could not save CBZ file: disk full", "disk full")

    monkeypatch.setattr(cbz_file, "rewrite_archive", broken)
    with pytest.raises(CbzError):
        book.save()
    entry = _only_entry(journal)
    assert entry.status == STATUS_FAILED and entry.error == "disk full"
    assert book.baseline["Series"] == "Saga"  # not saved: the baseline is still what is on disk


def test_save_as_over_an_existing_file_records_that_files_old_values(tmp_path, journal):
    source = _cbz(tmp_path / "a.cbz")
    other = _cbz(tmp_path / "b.cbz", comicinfo=b"<ComicInfo><Series>Old Series</Series></ComicInfo>")
    book = CbzBook(source)
    book.save(output_path=str(tmp_path / "new.cbz"))  # a new file: nothing to undo, nothing recorded
    assert journal.entries() == []
    book.save(output_path=other)
    entry = _only_entry(journal)
    assert entry.path == os.path.abspath(other) and entry.changes["Series"] == ("Old Series", "Saga")
    assert book.baseline["Series"] == "Saga"


def test_a_journal_failure_never_blocks_a_save(tmp_path, monkeypatch, capsys):
    from redactor_common.core.journal import Journal

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    history.use_journal(Journal(str(blocker / "journal.jsonl")))  # its folder is a file: every write fails
    path = _cbz(tmp_path / "a.cbz")
    book = CbzBook(path)
    book.metadata.series = "Other"
    book.save()
    book.metadata.series = "Third"
    book.save()
    assert CbzBook(path).metadata.series == "Third"
    assert capsys.readouterr().err.count("could not record") == 1  # said once
    assert "could not record" in history.take_warning() and history.take_warning() == ""


# --- J4: Redact ------------------------------------------------------------------------------------


def test_redact_records_one_redact_entry_per_file_whose_tags_changed(env, tmp_path, journal, monkeypatch):  # noqa: F811
    import core.redact_steps as rs

    tagged = _cbz(tmp_path / "Saga 001.cbz", extra=[("Thumbs.db", b"junk")])
    cleaned_only = _cbz(tmp_path / "Saga 002.cbz", extra=[("Thumbs.db", b"junk")])  # no tag change
    untouched = _cbz(tmp_path / "Saga 003.cbz")
    original_begin = rs._journal_begin

    def edit_the_first(ctx):
        if ctx.original == os.path.realpath(tagged):
            ctx.work.metadata.notes = "tagged"
        return original_begin(ctx)

    monkeypatch.setattr(rs, "_journal_begin", edit_the_first)
    _run(env, [CbzBook(tagged), CbzBook(cleaned_only), CbzBook(untouched)])
    entries = journal.entries()
    assert [e.kind for e in entries] == [KIND_REDACT]  # a junk-file clean-up is not a tag change
    assert entries[0].path == os.path.realpath(tagged) and entries[0].status == STATUS_OK
    assert entries[0].changes == {"Notes": ("", "tagged")}


def test_redact_records_field_changes_against_the_file_as_read(env, tmp_path, journal, monkeypatch):  # noqa: F811
    import core.redact_steps as rs

    path = _cbz(tmp_path / "Saga 001.cbz", extra=[("Thumbs.db", b"junk")])

    original_begin = rs._journal_begin

    def with_an_edit(ctx):
        ctx.work.metadata.title = "Added by a step"
        return original_begin(ctx)

    monkeypatch.setattr(rs, "_journal_begin", with_an_edit)
    book = CbzBook(path)
    _only(_run(env, [book]))
    entry = journal.entries()[0]
    assert entry.kind == KIND_REDACT and entry.changes == {"Title": ("", "Added by a step")}
    assert book.baseline["Title"] == "Added by a step"  # the row was reloaded with the new baseline


def test_a_failed_redact_save_records_failed(env, tmp_path, journal, monkeypatch):  # noqa: F811
    import core.redact_steps as rs

    path = _cbz(tmp_path / "Saga 001.cbz", extra=[("Thumbs.db", b"junk")])
    monkeypatch.setattr(rs, "verify_written_file", lambda p, n: False)
    original_begin = rs._journal_begin

    def with_an_edit(ctx):
        ctx.work.metadata.title = "x"
        return original_begin(ctx)

    monkeypatch.setattr(rs, "_journal_begin", with_an_edit)
    _run(env, [CbzBook(path)])
    entry = journal.entries()[0]
    assert entry.kind == KIND_REDACT and entry.status == STATUS_FAILED and "NOT SAVED" in entry.error


# --- the command line ------------------------------------------------------------------------------


def test_cli_set_is_journaled(tmp_path, journal, capsys):
    from cbzcli.main import main

    path = _cbz(tmp_path / "a.cbz")
    assert main(["set", path, "-s", "Series=Else"]) == 0
    entry = _only_entry(journal)
    assert entry.kind == KIND_SAVE and entry.changes == {"Series": ("Saga", "Else")}


# --- J5: Tools > History ---------------------------------------------------------------------------


@pytest.fixture
def window(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    QApplication.instance() or QApplication(sys.argv)
    from gui.main_window import MainWindow

    return MainWindow()


def test_tools_history_opens_the_dialog_and_reloads_restored_books(window, tmp_path, journal, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from redactor_common.gui.history_dialog import HistoryDialog

    path = _cbz(tmp_path / "Saga 001.cbz")
    window._load_paths([path])
    book = window.books[0]
    book.metadata.series = "Wrong"
    book.dirty = True
    window.save_all_changed()
    assert CbzBook(path).metadata.series == "Wrong"

    seen = []

    def exec_and_restore(dialog):
        seen.append(dialog.windowTitle())
        dialog.table.selectRow(0)
        assert dialog.restore_selected() is True
        return 0

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(HistoryDialog, "exec", exec_and_restore)
    window.actions_["history"].trigger()

    assert seen == ["History"]
    assert window.books[0].metadata.series == "Saga" and window.books[0].baseline["Series"] == "Saga"
    assert window.table.item(0, window._col_index["series"]).text() == "Saga"
    kinds = sorted(e.kind for e in journal.entries())
    assert kinds == ["restore", "save"]


def _only_entry(journal):
    entries = journal.entries()
    assert len(entries) == 1, entries
    return entries[0]
