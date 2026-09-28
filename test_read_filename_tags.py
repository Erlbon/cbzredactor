"""Tests for Import > Read Filename Tags (MainWindow.read_filename_tags)
and the Resize dialog's width presets."""

import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui.main_window import MainWindow
from gui.resize_dialog import WIDTH_PRESETS, ResizeImagesDialog

_app = QApplication.instance() or QApplication(sys.argv)


def _cbz(path, comicinfo="<ComicInfo></ComicInfo>"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", comicinfo)
        zf.writestr("001.jpg", b"page")
    return str(path)


@pytest.fixture
def window(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    w = MainWindow()
    w.shown_messages = shown
    return w


def test_fills_blank_fields_from_a_scene_name(window, tmp_path):
    path = _cbz(tmp_path / "Ghost Pepper 015 (2026) (Digital) (TPB) (missing ifc) (Zone-Empire).webp.cbz")
    window._load_paths([path])

    window.read_filename_tags()

    meta = window.books[0].metadata
    assert (meta.series, meta.number, meta.year) == ("Ghost Pepper", "15", "2026")
    assert meta.scan_information == "Digital, Zone-Empire"
    assert meta.format == "TPB"
    assert meta.notes == "missing ifc"
    assert window.books[0].dirty

    window.undo_last_action()
    assert window.books[0].metadata.series == ""


def test_existing_values_go_through_the_overwrite_review(window, tmp_path, monkeypatch):
    path = _cbz(
        tmp_path / "Ghost Pepper 015 (2026) (Digital) (Zone-Empire).cbz",
        "<ComicInfo><Series>Ghost Pepper (Hand Typed)</Series></ComicInfo>",
    )
    window._load_paths([path])
    captured = {}

    def review(parent, books, changes, label):
        captured.update(changes)
        return {i: {k: v for k, v in f.items() if k != "series"} for i, f in changes.items()}

    monkeypatch.setattr("gui.main_window.resolve_overwrite_conflicts", review)
    window.read_filename_tags()

    assert captured[0]["series"] == "Ghost Pepper"  # offered...
    meta = window.books[0].metadata
    assert meta.series == "Ghost Pepper (Hand Typed)"  # ...but declined in the review
    assert meta.number == "15"


def test_unrecognised_tags_are_reported_not_written(window, tmp_path):
    path = _cbz(tmp_path / "Saga 001 (2012) (Mystery Tag) (Zone-Empire).cbz")
    window._load_paths([path])

    window.read_filename_tags()

    assert window.books[0].metadata.scan_information == "Zone-Empire"
    assert any("(Mystery Tag)" in m for m in window.shown_messages)


def test_nothing_new_is_reported(window, tmp_path):
    path = _cbz(tmp_path / "Just A Title.cbz", "<ComicInfo><Series>Just A Title</Series></ComicInfo>")
    window._load_paths([path])
    window.read_filename_tags()
    assert not window.books[0].dirty
    assert any("Nothing new" in m for m in window.shown_messages)


# ---------------------------------------------------------------------------
# Resize presets
# ---------------------------------------------------------------------------

def test_presets_match_the_agreed_widths():
    assert WIDTH_PRESETS == [("Standard", 1280), ("Wide", 1440), ("HD", 1920), ("UHD", 2560)]


def test_choosing_a_preset_sets_the_width_and_typing_shows_custom():
    dialog = ResizeImagesDialog(1, 1440, 90)
    assert dialog.preset_combo.currentText() == "Wide (1440px)"

    index = dialog.preset_combo.findData(2560)
    dialog.preset_combo.setCurrentIndex(index)
    dialog._on_preset_chosen(index)
    assert dialog.max_width() == 2560

    dialog.max_width_spin.setValue(1500)
    assert dialog.preset_combo.currentText() == "Custom"
    dialog.max_width_spin.setValue(1920)
    assert dialog.preset_combo.currentText() == "HD (1920px)"
