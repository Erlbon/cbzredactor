"""Window-level test for Operations > Find Duplicates...: two releases of
one comic plus an unrelated one are loaded; the worse release is
suggested for removal, goes to the (faked) Recycle Bin and leaves the
list."""

import io
import random
import sys
import zipfile

import pytest
from PIL import Image, ImageDraw
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui import app_settings

_app = QApplication.instance() or QApplication(sys.argv)


def _page(seed, w=1200, h=1800):
    rng = random.Random(seed)
    image = Image.new("RGB", (w, h), tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(image)
    for _ in range(40):
        x, y = rng.randrange(w), rng.randrange(h)
        draw.rectangle((x, y, x + rng.randrange(60, 500), y + rng.randrange(60, 500)),
                       fill=tuple(rng.randrange(256) for _ in range(3)))
    return image


def _cbz(path, images, size=None, fmt="JPEG"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", "<ComicInfo><Series>S</Series></ComicInfo>")
        for i, image in enumerate(images):
            if size:
                image = image.resize(size)
            out = io.BytesIO()
            image.save(out, fmt)
            zf.writestr(f"{i:03}.{'webp' if fmt == 'WEBP' else 'jpg'}", out.getvalue())
    return str(path)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "credit_pages_path", lambda: str(tmp_path / "known.json"))
    trashed = []
    monkeypatch.setattr("gui.main_window.move_to_trash", trashed.append)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    from gui.main_window import MainWindow

    w = MainWindow()
    w.trashed, w.shown = trashed, shown
    return w


def test_finds_the_duplicate_and_removes_the_worse_copy(window, tmp_path, monkeypatch):
    story = [_page(1000 + i) for i in range(20)]
    best = _cbz(tmp_path / "Comic 001 (HD).cbz", story)
    worse = _cbz(tmp_path / "Comic 001 (SD).webp.cbz", story, size=(800, 1200), fmt="WEBP")
    other = _cbz(tmp_path / "Other 001.cbz", [_page(2000 + i) for i in range(20)])
    window._load_paths([best, worse, other])

    from gui import duplicates_dialog

    seen = {}

    def accept(dialog):
        seen["ticked"] = dialog.ticked()
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(duplicates_dialog.DuplicatesDialog, "exec", accept)
    window.open_find_duplicates_dialog()

    assert len(seen["ticked"]) == 1
    assert window.trashed == [worse]
    assert [b.path for b in window.books] == [best, other]


def test_no_duplicates_says_so(window, tmp_path):
    a = _cbz(tmp_path / "A.cbz", [_page(3000 + i) for i in range(12)])
    b = _cbz(tmp_path / "B.cbz", [_page(4000 + i) for i in range(12)])
    window._load_paths([a, b])
    window.open_find_duplicates_dialog()
    assert any("No duplicates" in m for m in window.shown)
    assert window.trashed == []
