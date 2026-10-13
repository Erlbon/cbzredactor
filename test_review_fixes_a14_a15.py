"""Review fixes: filename Search/Replace renames under progress without clobbering (A15d), a check that finds the
same result again does not make the file 'unsaved' (A14), and the secret prompt survives an unwritable fallback (A15b)."""

import os
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

import gui.main_window as mw
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        zf.writestr("001.jpg", b"x")
    return str(path)


class _FakeSearchReplace:
    """Stands in for the shared dialog: accepts the filename field with the changes handed in."""

    changes: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def exec(self):
        return 1

    DialogCode = type("DialogCode", (), {"Accepted": 1})

    def result_field_key(self):
        return mw.FILENAME_FIELD_KEY

    def accepted_changes(self):
        return dict(self.changes)


@pytest.fixture
def window(tmp_path, monkeypatch):
    win = MainWindow()
    win._load_paths([_cbz(tmp_path / "A.cbz"), _cbz(tmp_path / "B.cbz"), _cbz(tmp_path / "C.cbz")])
    monkeypatch.setattr(mw, "SearchReplaceDialog", _FakeSearchReplace)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    yield win
    for book in win.books:
        book.dirty = False


def test_search_replace_filenames_renames_and_never_clobbers(window, tmp_path):
    names = [os.path.basename(b.path) for b in window.books]
    assert names == ["A.cbz", "B.cbz", "C.cbz"]
    # two files asked to become "Same", and one asked to take the name of a file that already exists elsewhere
    (tmp_path / "Taken.cbz").write_text("keep me")
    _FakeSearchReplace.changes = {0: "Same", 1: "Same", 2: "Taken"}
    window.open_search_replace_dialog()
    after = sorted(n for n in os.listdir(tmp_path) if n.endswith(".cbz"))
    assert after == ["Same (2).cbz", "Same.cbz", "Taken (2).cbz", "Taken.cbz"]
    assert (tmp_path / "Taken.cbz").read_text() == "keep me"  # the existing file was not overwritten
    assert [os.path.basename(b.path) for b in window.books] == ["Same.cbz", "Same (2).cbz", "Taken (2).cbz"]


def test_a_check_that_finds_the_same_result_again_does_not_dirty_the_file(tmp_path):
    from core.cbz_file import CbzBook

    book = CbzBook(_cbz(tmp_path / "A.cbz"))
    assert book.record_scan("OK") is True  # first time: stamps the file
    assert book.metadata.scan_stamp
    book.dirty = False
    stamp = book.metadata.scan_stamp
    assert book.record_scan("OK") is False  # same result, same pages: nothing to rewrite
    assert not book.dirty and book.metadata.scan_stamp == stamp


def test_secret_prompt_survives_an_unwritable_fallback(monkeypatch):
    import gui.secret_prompts as sp
    from redactor_common.core import secret_store

    warnings = []
    monkeypatch.setattr(sp.QMessageBox, "question", staticmethod(lambda *a, **k: sp.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(sp.QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    monkeypatch.setattr(sp.app_settings, "save_allow_unencrypted_fallback", lambda on: None)
    monkeypatch.setattr(secret_store, "set_allow_unencrypted_fallback", lambda on: None)

    def save(flag):
        raise secret_store.SecretStoreUnavailable("fallback file is read-only")

    assert sp.save_with_fallback_prompt(None, save) is False
    assert warnings and "read-only" in str(warnings[0])


def test_the_database_dialogs_hand_the_shared_dialog_a_plain_check_callable():
    """redactor_common's LocalDatabaseSettingsDialog runs Check File on a worker thread itself; a check that
    wraps its own run_busy would nest two busy dialogs (and call a GUI-thread helper from the worker)."""
    import inspect

    import gui.comicrack_settings_dialog as cr
    import gui.gcd_local_settings_dialog as gcd

    for module, cls in ((gcd, gcd.GcdLocalSettingsDialog), (cr, cr.ComicRackSettingsDialog)):
        assert "run_busy" not in inspect.getsource(cls.__init__).replace("no run_busy", "")
        assert not hasattr(module, "run_busy")
