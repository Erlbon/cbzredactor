"""Tests for core/scan_quality_tag.py and Operations > Tag Low-Res Scans."""

import io
import sys
import zipfile

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.cbz_file import CbzBook
from core.scan_quality_tag import LOW_RES_TAG, add_tag, has_tag, remove_tag
from gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def test_add_keeps_existing_tags_and_is_idempotent():
    assert add_tag("") == LOW_RES_TAG
    assert add_tag("Favorite, To Read") == f"Favorite, To Read, {LOW_RES_TAG}"
    assert add_tag(f"favorite, {LOW_RES_TAG.lower()}") == f"favorite, {LOW_RES_TAG.lower()}"


def test_remove_keeps_other_tags():
    assert remove_tag(f"Favorite, {LOW_RES_TAG}, To Read") == "Favorite, To Read"
    assert remove_tag("Favorite") == "Favorite"


def test_has_tag_is_case_insensitive_and_exact():
    assert has_tag(f"x, {LOW_RES_TAG.upper()}")
    assert not has_tag("Low-res scans elsewhere")


def _cbz(path, width, tags=""):
    out = io.BytesIO()
    Image.new("RGB", (width, width * 3 // 2), color=(1, 2, 3)).save(out, format="JPEG")
    with zipfile.ZipFile(path, "w") as zf:
        tags_xml = f"<Tags>{tags}</Tags>" if tags else ""
        zf.writestr("ComicInfo.xml", f"<ComicInfo><Title>T</Title>{tags_xml}</ComicInfo>")
        zf.writestr("001.jpg", out.getvalue())
    return str(path)


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    return MainWindow()


def test_tags_low_res_and_untags_replaced_books(window, tmp_path):
    low = _cbz(tmp_path / "low.cbz", 800, tags="Favorite")
    fine = _cbz(tmp_path / "fine.cbz", 1440, tags=f"{LOW_RES_TAG}, Keep")  # replaced by a better scan
    untouched = _cbz(tmp_path / "big.cbz", 2000)
    window._load_paths([low, fine, untouched])

    window.tag_low_res_scans()

    low_book, fine_book, big_book = window.books
    assert low_book.metadata.tags == f"Favorite, {LOW_RES_TAG}"
    assert fine_book.metadata.tags == "Keep"
    assert big_book.metadata.tags == ""
    assert low_book.dirty and fine_book.dirty and not big_book.dirty

    window.undo_last_action()
    assert low_book.metadata.tags == "Favorite"
    assert fine_book.metadata.tags == f"{LOW_RES_TAG}, Keep"


def test_tag_is_written_on_save(window, tmp_path):
    path = _cbz(tmp_path / "low.cbz", 700)
    window._load_paths([path])
    window.tag_low_res_scans()
    window.books[0].save()
    assert CbzBook(path).metadata.tags == LOW_RES_TAG


def test_unconverted_rows_are_ignored(window, tmp_path, monkeypatch):
    from gui import app_settings
    monkeypatch.setattr(app_settings, "load_foreign_load_behavior", lambda: app_settings.FOREIGN_LOAD_UNCONVERTED)
    fake_cbr = _cbz(tmp_path / "zip.cbr", 700)
    window._load_paths([fake_cbr])
    window.tag_low_res_scans()
    assert window.books[0].metadata.tags == ""
