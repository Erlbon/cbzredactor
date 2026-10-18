"""The shared conversion policy (redactor_common core/conversion_policy.py) in cbzredactor: the old settings map onto
it without changing behaviour, each Open choice, what happens to the original (keep / quarantine with an undo log /
Recycle Bin), the dry run, and the Preferences > Conversion page."""

import io
import os
import sys
import tarfile
import time
import zipfile
from datetime import date

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core import conversion_originals as co
from gui import app_settings, preferences
from gui.main_window import FOREIGN_CONVERT, FOREIGN_UNCONVERTED, MainWindow
from redactor_common.core import conversion_policy as cp

_app = QApplication.instance() or QApplication(sys.argv)

TODAY = date(2026, 10, 18)


def _make_cbt(path, pages=2) -> None:
    with tarfile.open(path, "w") as tf:
        for n in range(pages):
            data = b"page %d" % n
            info = tarfile.TarInfo(f"page{n:03d}.jpg")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


@pytest.fixture
def window(monkeypatch):
    trashed = []
    monkeypatch.setattr("gui.main_window.move_to_trash", lambda path: (trashed.append(path), os.remove(path)))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"warning shown: {a[2]}"))
    win = MainWindow()
    win.trashed = trashed
    return win


def _save(**fields) -> cp.ConversionPolicy:
    base = dict(on_open=cp.ON_OPEN_AUTO, originals=cp.ORIGINALS_KEEP, recent_minutes=0, dry_run_first=False)
    base.update(fields)
    policy = cp.ConversionPolicy(**base)
    app_settings.save_conversion_policy(policy)
    return policy


# -- the old settings are the default policy ----------------------------------------------------------------------


@pytest.mark.parametrize("behavior, on_open", [
    (app_settings.FOREIGN_LOAD_ASK, "ask"),
    (app_settings.FOREIGN_LOAD_CONVERT, "auto"),
    (app_settings.FOREIGN_LOAD_UNCONVERTED, "never"),
])
@pytest.mark.parametrize("recycle, originals", [(True, "recycle"), (False, "keep")])
def test_a_never_saved_policy_equals_the_old_settings(behavior, on_open, recycle, originals):
    app_settings.save_foreign_load_behavior(behavior)
    app_settings.save_recycle_originals(recycle)
    policy = app_settings.load_conversion_policy()
    assert (policy.on_open, policy.originals) == (on_open, originals)
    assert policy.quarantine_folder == "" and policy.recent_minutes == 0 and policy.dry_run_first is False


def test_a_fresh_install_lists_unconverted_and_keeps_originals():
    policy = app_settings.load_conversion_policy()
    assert (policy.on_open, policy.originals) == ("never", "keep")


def test_the_saved_policy_wins_and_mirrors_into_the_old_settings():
    app_settings.save_foreign_load_behavior(app_settings.FOREIGN_LOAD_ASK)
    _save(on_open=cp.ON_OPEN_AUTO, originals=cp.ORIGINALS_RECYCLE)
    assert app_settings.load_conversion_policy().on_open == "auto"
    assert app_settings.load_foreign_load_behavior() == app_settings.FOREIGN_LOAD_CONVERT
    assert app_settings.load_recycle_originals() is True
    app_settings.save_foreign_load_behavior(app_settings.FOREIGN_LOAD_ASK)  # the old key no longer decides
    assert app_settings.load_conversion_policy().on_open == "auto"
    _save(on_open=cp.ON_OPEN_NEVER, originals=cp.ORIGINALS_QUARANTINE)
    assert app_settings.load_foreign_load_behavior() == app_settings.FOREIGN_LOAD_UNCONVERTED
    assert app_settings.load_recycle_originals() is False


# -- each Open choice ---------------------------------------------------------------------------------------------


def test_on_open_never_lists_the_file_unconverted(window, tmp_path, monkeypatch):
    _save(on_open=cp.ON_OPEN_NEVER)
    cbt = tmp_path / "a.cbt"
    _make_cbt(cbt)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("should not prompt"))
    window._load_paths([str(cbt)])
    assert window.books[0].needs_conversion and cbt.exists() and not (tmp_path / "a.cbz").exists()


def test_on_open_auto_converts_without_asking_and_applies_the_originals_choice(window, tmp_path, monkeypatch):
    _save(on_open=cp.ON_OPEN_AUTO, originals=cp.ORIGINALS_RECYCLE)
    cbt = tmp_path / "a.cbt"
    _make_cbt(cbt, pages=3)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("should not prompt"))
    window._load_paths([str(cbt)])
    assert not window.books[0].needs_conversion and window.books[0].actual_page_count == 3
    assert window.trashed == [str(cbt)] and not cbt.exists()


def test_on_open_ask_prompts_and_the_originals_box_starts_on_the_policy(window, tmp_path, monkeypatch):
    _save(on_open=cp.ON_OPEN_ASK, originals=cp.ORIGINALS_QUARANTINE)
    cbt = tmp_path / "a.cbt"
    _make_cbt(cbt)
    seen = {}

    def _click(box):
        from PyQt6.QtWidgets import QComboBox
        seen["originals"] = box.findChild(QComboBox).currentData()
        next(b for b in box.buttons() if b.text() == "Convert Now").click()

    monkeypatch.setattr(QMessageBox, "exec", _click)
    assert window._prompt_convert_foreign_archives([str(cbt)]) == (FOREIGN_CONVERT, cp.ORIGINALS_QUARANTINE)
    assert seen["originals"] == cp.ORIGINALS_QUARANTINE
    assert window._resolve_foreign_choice([str(cbt)]) == (FOREIGN_CONVERT, cp.ORIGINALS_QUARANTINE)


def test_the_remember_box_saves_into_the_policy_and_keeps_its_other_fields(window, tmp_path, monkeypatch):
    _save(on_open=cp.ON_OPEN_ASK, originals=cp.ORIGINALS_QUARANTINE, quarantine_folder=str(tmp_path / "q"))
    cbt = tmp_path / "a.cbt"
    _make_cbt(cbt)

    def _click(box):
        from PyQt6.QtWidgets import QCheckBox
        for check in box.findChildren(QCheckBox):
            check.setChecked(True)
        next(b for b in box.buttons() if b.text() == "Add Unconverted").click()

    monkeypatch.setattr(QMessageBox, "exec", _click)
    assert window._prompt_convert_foreign_archives([str(cbt)])[0] == FOREIGN_UNCONVERTED
    policy = app_settings.load_conversion_policy()
    assert policy.on_open == "never" and policy.originals == "quarantine" and policy.quarantine_folder == str(tmp_path / "q")


def test_recent_files_are_listed_unconverted_instead_of_converted(window, tmp_path):
    _save(on_open=cp.ON_OPEN_AUTO, recent_minutes=10)
    cbt = tmp_path / "a.cbt"
    _make_cbt(cbt)
    window._load_paths([str(cbt)])  # just written: modified within the last 10 minutes
    assert window.books[0].needs_conversion and not (tmp_path / "a.cbz").exists()
    old = time.time() - 3600
    os.utime(cbt, (old, old))
    window.clear_list()
    window._load_paths([str(cbt)])
    assert not window.books[0].needs_conversion


# -- what happens to the original ---------------------------------------------------------------------------------


def test_keep_leaves_the_original(tmp_path):
    original = tmp_path / "a.cbt"
    original.write_bytes(b"x")
    assert co.dispose_original(str(original), str(tmp_path / "a.cbz"), "keep", cp.ConversionPolicy(), None) == "kept"
    assert original.exists()


def test_old_bool_values_still_mean_recycle_and_keep():
    assert co.normalise_originals(True) == "recycle" and co.normalise_originals(False) == "keep"


def test_recycle_uses_the_given_trash(tmp_path):
    original = tmp_path / "a.cbt"
    original.write_bytes(b"x")
    trashed = []
    co.dispose_original(str(original), str(tmp_path / "a.cbz"), "recycle", cp.ConversionPolicy(), trashed.append)
    assert trashed == [str(original)]


def test_quarantine_beside_the_file_moves_it_and_writes_the_undo_log(tmp_path):
    original = tmp_path / "a.cbt"
    original.write_bytes(b"x")
    new = tmp_path / "a.cbz"
    new.write_bytes(b"y")
    policy = cp.ConversionPolicy(originals="quarantine")
    co.dispose_original(str(original), str(new), "quarantine", policy, None, today=TODAY)
    moved = tmp_path / "converted originals 2026-10-18" / "a.cbt"
    assert moved.read_bytes() == b"x" and not original.exists()
    log = tmp_path / "converted originals 2026-10-18 undo.tsv"
    assert log.read_text(encoding="utf-8").splitlines() == [f"{new}\tCREATED", f"{moved}\t{original}"]


def test_quarantine_uses_the_set_folders_and_never_overwrites(tmp_path):
    quarantine, logs = tmp_path / "q", tmp_path / "logs"
    policy = cp.ConversionPolicy(originals="quarantine", quarantine_folder=str(quarantine), undo_log_folder=str(logs))
    for n in range(2):
        folder = tmp_path / f"src{n}"
        folder.mkdir()
        original = folder / "a.cbt"
        original.write_bytes(b"%d" % n)
        co.dispose_original(str(original), str(folder / "a.cbz"), "quarantine", policy, None, today=TODAY)
    dated = quarantine / "converted originals 2026-10-18"
    assert sorted(p.name for p in dated.iterdir()) == ["a (2).cbt", "a.cbt"]
    assert len((logs / "converted originals 2026-10-18 undo.tsv").read_text(encoding="utf-8").splitlines()) == 4


def test_a_converted_file_is_quarantined_only_after_it_was_verified(window, tmp_path):
    _save(on_open=cp.ON_OPEN_AUTO, originals=cp.ORIGINALS_QUARANTINE, quarantine_folder=str(tmp_path / "q"))
    cbt = tmp_path / "a.cbt"
    _make_cbt(cbt, pages=2)
    window._load_paths([str(cbt)])
    assert zipfile.is_zipfile(tmp_path / "a.cbz") and not cbt.exists()
    quarantined = list((tmp_path / "q").glob("converted originals ????-??-??"))
    assert len(quarantined) == 1 and (quarantined[0] / "a.cbt").exists()
    # A failed conversion (not an archive at all) never touches the original.
    bad = tmp_path / "bad.cbt"
    bad.write_bytes(b"not a tar")
    errors = []
    assert window._convert_path(str(bad), cp.ORIGINALS_QUARANTINE, errors) is None
    assert bad.exists() and errors


# -- the dry run --------------------------------------------------------------------------------------------------


def test_the_dry_run_changes_nothing_and_a_real_run_converts(window, tmp_path, monkeypatch):
    _save(on_open=cp.ON_OPEN_NEVER, originals=cp.ORIGINALS_RECYCLE, dry_run_first=True)
    cbt = tmp_path / "a.cbt"
    _make_cbt(cbt)
    window._load_paths([str(cbt)])
    asked = []
    shown = []
    monkeypatch.setattr(window, "_confirm_convert", lambda text, dry: asked.append(dry) or "dry-run")
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.detailedText()))
    window.convert_books_to_cbz(list(window.books))
    assert asked == [True]  # the box starts as the policy says
    assert cbt.exists() and not (tmp_path / "a.cbz").exists() and window.trashed == []
    assert shown == ["a.cbt -> a.cbz; the original goes to the Recycle Bin"]
    monkeypatch.setattr(window, "_confirm_convert", lambda text, dry: "convert")
    window.convert_books_to_cbz(list(window.books))
    assert (tmp_path / "a.cbz").exists() and window.trashed == [str(cbt)]


# -- the Preferences page -----------------------------------------------------------------------------------------


def test_the_page_has_the_shared_widget_on_top_of_the_resize_option():
    dlg = preferences.make_dialog()
    page = dlg._tabs.widget(dlg.page_titles().index("Conversion"))
    layout = page.layout()
    assert layout.itemAt(1).widget() is dlg.policy_widget
    assert "read-only rows" in dlg.policy_widget._on_open["never"].text()
    assert dlg.is_row_enabled(preferences.KEY_RESIZE_ON_CONVERT)


def test_ok_saves_the_policy_and_cancel_drops_it():
    dlg = preferences.make_dialog()
    dlg.policy_widget._on_open["ask"].setChecked(True)
    dlg.policy_widget._quarantine.setChecked(True)
    dlg.policy_widget._quarantine_edit.setText("D:/q")
    dlg.policy_widget._recent.setValue(5)
    dlg.policy_widget._dry_run.setChecked(True)
    assert dlg.apply_button.isEnabled()
    dlg.reject()
    assert not app_settings._settings().contains("conversion_policy/saved")

    dlg = preferences.make_dialog()
    dlg.policy_widget._on_open["ask"].setChecked(True)
    dlg.policy_widget._quarantine.setChecked(True)
    dlg.policy_widget._quarantine_edit.setText("D:/q")
    dlg.policy_widget._recent.setValue(5)
    dlg.policy_widget._dry_run.setChecked(True)
    dlg.accept()
    assert app_settings.load_conversion_policy() == cp.ConversionPolicy(
        on_open="ask", originals="quarantine", quarantine_folder="D:/q", recent_minutes=5, dry_run_first=True)


def test_apply_saves_without_closing_and_an_untouched_ok_pins_nothing():
    dlg = preferences.make_dialog()
    assert not dlg.apply_button.isEnabled()
    dlg.accept()
    assert not app_settings._settings().contains("conversion_policy/saved")

    dlg = preferences.make_dialog()
    dlg.policy_widget._on_open["auto"].setChecked(True)
    assert dlg.apply_button.isEnabled()
    dlg.apply_button.click()
    assert app_settings.load_conversion_policy().on_open == "auto"
    assert not dlg.apply_button.isEnabled()
