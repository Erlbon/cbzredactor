"""Window-level tests for scanner credit pages: learn one from a book
(right-click > Credit Pages...), then it's found in another,
differently-converted book (Credit Pages column, Operations > Remove
Credit Pages...) and removed, with the original sent to the (faked)
Recycle Bin."""

import io
import sys
import zipfile

import pytest
from PIL import Image, ImageDraw
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui import app_settings

_app = QApplication.instance() or QApplication(sys.argv)


def _tag_page(w=1200, h=1800):
    image = Image.new("RGB", (w, h), (20, 20, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((w * 0.1, h * 0.35, w * 0.9, h * 0.6), fill=(200, 30, 30))
    draw.ellipse((w * 0.3, h * 0.1, w * 0.7, h * 0.3), fill=(240, 240, 240))
    return image


def _story_page(seed, w=1200, h=1800):
    image = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(image)
    for i in range(6):
        x = (seed * 97 + i * 131) % (w - 300)
        y = (seed * 53 + i * 211) % (h - 300)
        draw.rectangle((x, y, x + 250, y + 200), fill=((seed * 40 + i * 30) % 255, 90, 160))
    return image


def _bytes(image, fmt="JPEG", **kw):
    out = io.BytesIO()
    image.save(out, fmt, **kw)
    return out.getvalue()


def _cbz(path, pages):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        for name, data in pages:
            zf.writestr(name, data)
    return str(path)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "credit_pages_path", lambda: str(tmp_path / "known.json"))
    trashed = []
    monkeypatch.setattr("gui.main_window.move_to_trash", trashed.append)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from gui.main_window import MainWindow

    w = MainWindow()
    w.trashed = trashed
    return w


def test_learn_in_one_book_then_find_and_remove_in_another(window, tmp_path, monkeypatch):
    raw = _cbz(tmp_path / "Raw 001.cbz",
               [(f"{i:03}.jpg", _bytes(_story_page(i))) for i in range(8)] + [("zzz_Zone-Empire.jpg", _bytes(_tag_page()))])
    converted = _cbz(tmp_path / "Other 002.webp.cbz",
                     [(f"Other-{i:04}.webp", _bytes(_story_page(i + 30).resize((1440, 2160)), "WEBP")) for i in range(8)]
                     + [("Other-0008.webp", _bytes(_tag_page().resize((1440, 2160)), "WEBP", quality=70))])
    window._load_paths([raw, converted])
    first, second = window.books

    # 1. Right-click > Credit Pages... on the first book: tick its last page.
    from gui import credit_pages_dialogs

    def tick_last(dialog):
        dialog._checks[dialog.candidates[-1].index].setChecked(True)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(credit_pages_dialogs.CreditPagesDialog, "exec", tick_last)
    window.open_credit_pages_dialog(first)

    assert first.actual_page_count == 8
    assert window.trashed == [raw]
    assert len(window._known_credits.pages) == 1

    # 2. The Credit Pages column flags the other, converted book.
    window._ensure_credit_scans(window.books)
    window._refresh_all_credit_cells()
    col = window._col_index["credit"]
    assert window.table.item(1, col).text() == "last page"
    assert window.table.item(0, col).text() == ""  # already removed

    # 3. Operations > Remove Credit Pages... removes it there too.
    seen = {}

    def accept_all(dialog):
        seen["rows"] = len(dialog.found)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(credit_pages_dialogs.RemoveCreditPagesDialog, "exec", accept_all)
    window.open_remove_credit_pages_dialog()

    assert seen["rows"] == 1
    assert second.actual_page_count == 8
    assert window.trashed == [raw, converted]
    with zipfile.ZipFile(converted) as zf:
        assert "Other-0008.webp" not in zf.namelist()
    assert window.table.item(1, col).text() == ""


def test_unsaved_book_is_not_rewritten(window, tmp_path, monkeypatch):
    path = _cbz(tmp_path / "A.cbz", [(f"{i:03}.jpg", _bytes(_story_page(i))) for i in range(4)])
    window._load_paths([path])
    window.books[0].dirty = True
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    window.open_credit_pages_dialog(window.books[0])
    assert any("unsaved" in m for m in shown)
    assert window.trashed == []


def test_nothing_learned_yet_explains_how(window, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    window.open_remove_credit_pages_dialog()
    assert any("Credit Pages..." in m for m in shown)
