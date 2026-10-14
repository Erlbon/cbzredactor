"""F1-F3 of the History follow-up (2026-10-14): journal entries follow a renamed, moved or converted file
(core/history.note_moves / note_conversion, wired into every RenameLog the app makes, the Redact env of
the command line, and the command line's rename / move / convert); Redact records where the save
happened, not the planned name; Delete History is a PrefAction and uses the Recycle Bin; the History
setting travels with Export / Import Settings. Real small CBZ files in tmp_path; the journal is the
test's own (conftest.py)."""

import os
import sys

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

import core.redact_steps as rs
import gui.main_window as mw
from core import history
from core.cbz_file import CbzBook
from core.redact_steps import RedactEnv
from gui import app_settings, preferences
from gui.settings_adapter import CbzSettingsAdapter
from redactor_common.core import settings_bundle as sb
from redactor_common.core.journal import KIND_REDACT, KIND_SAVE, STATUS_OK
from test_redact_steps import _cbt, _cbz, _only, _recipe, _run, env  # noqa: F401 -- env is a fixture

_app = QApplication.instance() or QApplication(sys.argv)
JUNK = [("Thumbs.db", b"junk")]  # the clean-up step removes it, so Redact saves the file


def _key(path):
    return os.path.normcase(os.path.realpath(path))


def _same(a, b):
    return _key(a) == _key(b)


def _save(path, series="Other"):
    book = CbzBook(path)
    book.metadata.series = series
    book.save()
    return book


@pytest.fixture
def on():
    history.set_history_enabled(True)


# --- F1: the app's RenameLog --------------------------------------------------------------------


def test_every_rename_log_of_the_app_has_the_history_hook(tmp_path):
    log = mw._make_rename_log(str(tmp_path / "log.json"))
    assert log.on_move is history.note_moves
    assert mw._rename_log().on_move is history.note_moves  # the conftest one is made the same way


def test_a_rename_moves_the_entry_and_undo_moves_it_back(tmp_path, journal, on):
    old = _cbz(tmp_path / "a.cbz")
    _save(old)
    new = str(tmp_path / "b.cbz")
    os.rename(old, new)
    log = mw._rename_log()
    log.record("Rename File", [(old, new)])
    entry = journal.entries()[0]
    assert entry.kind == KIND_SAVE and _same(entry.path, new) and _same(entry.original_path, old)
    assert journal.entries(path=os.path.abspath(new))  # found under its new name

    log.undo_last()
    entry = journal.entries()[0]
    assert _same(entry.path, old) and os.path.exists(old)


def test_history_off_a_rename_writes_nothing(tmp_path, journal):
    old = _cbz(tmp_path / "a.cbz")
    _save(old)  # off: nothing recorded, no file
    new = str(tmp_path / "b.cbz")
    os.rename(old, new)
    mw._rename_log().record("Rename File", [(old, new)])
    assert not os.path.exists(journal.path)


def test_on_but_no_journal_file_yet_a_rename_creates_none(tmp_path, journal, on):
    old = _cbz(tmp_path / "a.cbz")
    new = str(tmp_path / "b.cbz")
    os.rename(old, new)
    mw._rename_log().record("Rename File", [(old, new)])
    assert not os.path.exists(journal.path)


# --- F1: Redact ------------------------------------------------------------------------------------


def _with_an_edit(monkeypatch):
    original_begin = rs._journal_begin

    def edit(ctx):
        ctx.work.metadata.notes = "tagged"
        return original_begin(ctx)

    monkeypatch.setattr(rs, "_journal_begin", edit)


def _rename_recipe(env):
    env.pattern_history = ["%series% %number%"]
    return _recipe(env, enable=("rename",), disable=("filename_tags",))


def test_redact_records_at_the_saved_path_and_the_rename_moves_it(env, tmp_path, journal, monkeypatch, on):  # noqa: F811
    env.rename_log = mw._make_rename_log(str(tmp_path / "log.json"))
    _with_an_edit(monkeypatch)
    path = _cbz(tmp_path / "old name.cbz", extra=JUNK)
    book = CbzBook(path)
    _only(_run(env, [book], _rename_recipe(env)))
    entry = journal.entries()[0]
    assert entry.kind == KIND_REDACT and entry.status == STATUS_OK
    assert _same(entry.original_path, path)  # where the save happened
    assert _same(entry.path, tmp_path / "Saga 1.cbz") and _same(book.path, entry.path)


def test_redact_with_a_failing_rename_keeps_the_entry_at_the_saved_path(env, tmp_path, journal, monkeypatch, on):  # noqa: F811
    env.rename_log = mw._make_rename_log(str(tmp_path / "log.json"))
    _with_an_edit(monkeypatch)
    real = rs.rename_no_clobber

    def fail_the_rename(src, dst):
        if os.path.basename(dst) == "Saga 1.cbz":
            raise OSError("locked")
        return real(src, dst)

    monkeypatch.setattr(rs, "rename_no_clobber", fail_the_rename)
    path = _cbz(tmp_path / "old name.cbz", extra=JUNK)
    _run(env, [CbzBook(path)], _rename_recipe(env))
    entry = journal.entries()[0]
    assert _same(entry.path, path) and _same(entry.original_path, path) and os.path.exists(path)
    assert not os.path.exists(tmp_path / "Saga 1.cbz")
    assert entry.status == STATUS_OK and "couldn't rename" in entry.error  # saved, then the rename failed


def test_redact_from_the_command_line_env_follows_too(env, tmp_path, journal, monkeypatch, on):  # noqa: F811
    env.rename_log = None
    env.on_move = history.note_moves  # what cbzcli/cmd_redact.build_env gives it
    _with_an_edit(monkeypatch)
    path = _cbz(tmp_path / "old name.cbz", extra=JUNK)
    _run(env, [CbzBook(path)], _rename_recipe(env))
    entry = journal.entries()[0]
    assert _same(entry.path, tmp_path / "Saga 1.cbz") and _same(entry.original_path, path)


def test_cli_redact_env_has_the_hook():
    import argparse

    from cbzcli.cmd_redact import build_env

    built = build_env(argparse.Namespace(trash_dir=None))
    assert built.rename_log is None and built.on_move is history.note_moves


def test_redact_move_into_folders_moves_the_entry(env, tmp_path, journal, monkeypatch, on):  # noqa: F811
    root = tmp_path / "Library"
    root.mkdir()
    env.library_root = str(root)
    env.pattern_history = ["%publisher%/%series%/%series% %number%"]
    env.rename_log = mw._make_rename_log(str(tmp_path / "log.json"))
    _with_an_edit(monkeypatch)
    path = _cbz(tmp_path / "old.cbz", extra=JUNK)
    book = CbzBook(path)
    _only(_run(env, [book], _recipe(env, enable=("move_into_folders",), disable=("filename_tags",))))
    entry = journal.entries()[0]
    assert _same(entry.original_path, path) and _same(entry.path, book.path) and str(root) in book.path


# --- F1: the command line --------------------------------------------------------------------------


def test_cli_rename_and_move_move_the_entry(tmp_path, journal, on, capsys):
    from cbzcli.main import main

    path = _cbz(tmp_path / "a.cbz")
    _save(path, "Saga")
    _save(path, "Saga")  # unchanged: no second entry
    book = CbzBook(path)
    book.metadata.title = "T"
    book.save()
    assert main(["rename", path, "-p", "%series% %number%"]) == 0
    renamed = str(tmp_path / "Saga 1.cbz")
    assert os.path.exists(renamed) and all(_same(e.path, renamed) for e in journal.entries())

    root = tmp_path / "Library"
    root.mkdir()
    assert main(["move", renamed, "-p", "%publisher%/%series% %number%", "--root", str(root)]) == 0
    moved = str(root / "Image" / "Saga 1.cbz")
    assert os.path.exists(moved) and all(_same(e.path, moved) for e in journal.entries())
    assert all(_same(e.original_path, path) for e in journal.entries())


def test_cli_copy_and_dry_run_do_not_move_the_entry(tmp_path, journal, on, capsys):
    from cbzcli.main import main

    path = _cbz(tmp_path / "a.cbz")
    _save(path)
    root = tmp_path / "Library"
    root.mkdir()
    assert main(["move", path, "-p", "%series% %number%", "--root", str(root), "--copy"]) == 0
    assert main(["rename", path, "-p", "%series% %number%", "--dry-run"]) == 0
    assert _same(journal.entries()[0].path, path)


def test_cli_rename_with_history_off_writes_nothing(tmp_path, journal, capsys):
    from cbzcli.main import main

    path = _cbz(tmp_path / "a.cbz")
    assert main(["rename", path, "-p", "%series% %number%"]) == 0
    assert not os.path.exists(journal.path)


# --- F1: conversion --------------------------------------------------------------------------------


def test_conversion_moves_entries_of_the_old_name(tmp_path, journal, on, capsys):
    from cbzcli.main import main

    cbt = _cbt(tmp_path / "Saga 001.cbt")
    journal.record(os.path.abspath(cbt), KIND_SAVE, {"Series": ("", "Saga")})  # an entry under the old name
    assert main(["convert", cbt]) == 0
    entry = journal.entries()[0]
    assert _same(entry.path, tmp_path / "Saga 001.cbz") and _same(entry.original_path, cbt)


def test_conversion_without_entries_writes_no_move(tmp_path, journal, on, capsys):
    from cbzcli.main import main

    other = _cbz(tmp_path / "other.cbz")
    _save(other)
    before = open(journal.path, encoding="utf-8").read()
    assert main(["convert", _cbt(tmp_path / "Saga 001.cbt")]) == 0
    assert open(journal.path, encoding="utf-8").read() == before


def test_note_conversion_does_nothing_while_off(tmp_path, journal):
    journal.record(str(tmp_path / "a.cbr"), KIND_SAVE, {"Series": ("", "x")})
    history.note_conversion(str(tmp_path / "a.cbr"), str(tmp_path / "a.cbz"))
    assert _same(journal.entries()[0].path, tmp_path / "a.cbr")


def test_window_conversion_calls_the_hook(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(history, "note_conversion", lambda old, new: seen.append((old, new)))
    window = mw.MainWindow()
    monkeypatch.setattr(window, "_convert_file", lambda source, resize=None: str(tmp_path / "a.cbz"))
    cbt = _cbt(tmp_path / "a.cbt")
    assert window._convert_path(cbt, False, []) == str(tmp_path / "a.cbz")
    assert seen == [(cbt, str(tmp_path / "a.cbz"))]


# --- F2: Delete History is a PrefAction, to the Recycle Bin --------------------------------------


def test_delete_history_is_a_pref_action_on_the_history_page():
    section = next(s for s in preferences.preference_sections() if s.key == "history")
    assert [a.label for a in section.actions] == [preferences.DELETE_HISTORY_LABEL]
    dlg = preferences.make_dialog()
    assert dlg.delete_history_button in dlg.action_buttons


def test_delete_history_sends_the_file_to_the_recycle_bin(tmp_path, journal, monkeypatch, on):
    import redactor_common.core.trash as trash

    _save(_cbz(tmp_path / "a.cbz"))
    binned = []
    monkeypatch.setattr(trash, "move_to_trash", lambda p: binned.append(p) or os.replace(p, str(tmp_path / "bin")))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dlg = preferences.make_dialog()
    dlg.delete_history_button.click()
    assert binned == [journal.path] and not os.path.exists(journal.path)
    assert not dlg.delete_history_button.isEnabled()


# --- F3: Export / Import Settings ------------------------------------------------------------------


def _roundtrip(adapter, keys):
    return sb.parse_bundle(sb.dump_bundle(sb.build_bundle(adapter, keys)), "cbzredactor")


def test_history_setting_round_trips_through_export_import():
    adapter = CbzSettingsAdapter("x")
    spec = {s.key: s for s in adapter.sections()}["history"]
    assert spec.portable and "turns the recording of tag changes on" in spec.label
    app_settings.save_history_enabled(True)
    bundle = _roundtrip(adapter, {"history"})
    assert bundle.sections["history"].items == {"enabled": True}
    app_settings.save_history_enabled(False)
    assert sb.apply_bundle(adapter, bundle, {"history"}).failed == {}
    assert app_settings.load_history_enabled() is True


def test_a_bundle_without_the_history_key_leaves_it_alone():
    adapter = CbzSettingsAdapter("x")
    app_settings.save_history_enabled(True)
    bundle = _roundtrip(adapter, {"resize"})
    app_settings.save_history_enabled(False)
    sb.apply_bundle(adapter, bundle, {"resize", "history"})
    assert app_settings.load_history_enabled() is False
    app_settings.save_history_enabled(True)
    sb.apply_bundle(adapter, bundle, {"resize", "history"})
    assert app_settings.load_history_enabled() is True
