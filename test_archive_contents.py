"""Tests for Clean Up Archive Contents (core/archive_contents.py,
CbzBook.clean_contents, the dialog and the window flow) and the
numeric-aware page order it relies on (cbz_file.page_sort_key)."""

import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.archive_contents import is_junk, plan_cleanup
from core.cbz_file import CbzBook, CbzError, page_sort_key

_app = QApplication.instance() or QApplication(sys.argv)

COMICINFO = (b'<?xml version="1.0"?><ComicInfo><Title>The Gift</Title><Pages>'
             b'<Page Image="0" Type="FrontCover"/><Page Image="2" Type="Deleted"/></Pages></ComicInfo>')
SCENE = "Batman 045 (2018) (Digital) (Zone-Empire)"


def _scene_cbz(path):
    """A scene release as it arrives: pages in a group-named folder,
    unpadded page numbers, OS junk and a group .nfo."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{SCENE}/", b"")
        for number in (1, 2, 10):
            zf.writestr(f"{SCENE}/{SCENE} p{number}.jpg", f"page {number}".encode())
        zf.writestr("Thumbs.db", b"x")
        zf.writestr("__MACOSX/._p1.jpg", b"x")
        zf.writestr("Zone-Empire.nfo", b"greetz")
        zf.writestr("ComicInfo.xml", COMICINFO)
    return str(path)


def test_pages_are_ordered_like_a_reader_orders_them(tmp_path):
    assert sorted(["10.webp", "2.webp", "1.webp"], key=page_sort_key) == ["1.webp", "2.webp", "10.webp"]
    assert sorted(["b/2.JPG", "B/10.jpg", "a/3.jpg"], key=page_sort_key) == ["a/3.jpg", "b/2.JPG", "B/10.jpg"]
    book = CbzBook(_scene_cbz(tmp_path / "x.cbz"))
    assert [n.rsplit(" ", 1)[1] for n in book.page_names] == ["p1.jpg", "p2.jpg", "p10.jpg"]


def test_junk():
    for name in ("Thumbs.db", "sub/desktop.ini", ".DS_Store", "__MACOSX/._x.jpg", "._cover.jpg",
                 "Zone-Empire.nfo", "release.sfv", "Visit us.url", "info.TXT", "group.html"):
        assert is_junk(name), name
    for name in ("001.jpg", "ComicInfo.xml", "extras/map.png", "bonus.pdf"):
        assert not is_junk(name), name


def test_plan_for_a_scene_release(tmp_path):
    book = CbzBook(_scene_cbz(tmp_path / "x.cbz"))
    plan = book.cleanup_plan()
    assert list(plan.renames.values()) == ["001.jpg", "002.jpg", "003.jpg"]
    assert sorted(plan.removals) == ["Thumbs.db", "Zone-Empire.nfo", "__MACOSX/._p1.jpg"]
    assert plan.drop_folder_entries and plan.folders == 1
    assert "Zone-Empire" in plan.scene_tags and "Digital" in plan.scene_tags
    summary = plan.summary()
    assert summary.startswith("rename 3 page(s) (out of 1 folder(s); names had (")
    assert "remove 3 junk file(s)" in summary


def test_plan_details():
    # Already clean: nothing to do.
    clean = plan_cleanup(["001.jpg", "002.jpg", "ComicInfo.xml"], ["001.jpg", "002.jpg"], "ComicInfo.xml")
    assert not clean.needed and clean.summary() == "already clean"
    # Enough digits for a long book; extensions lower-cased.
    long = plan_cleanup([], [f"p{i}.JPG" for i in range(1, 1001)], None)
    assert long.renames["p1.JPG"] == "0001.jpg" and long.renames["p1000.JPG"] == "1000.jpg"
    # A ComicInfo.xml in a folder or odd case moves to the top level.
    moved = plan_cleanup(["x/comicinfo.xml", "001.jpg"], ["001.jpg"], "x/comicinfo.xml")
    assert moved.renames == {"x/comicinfo.xml": "ComicInfo.xml"}
    assert "move ComicInfo.xml to the top level" in moved.summary()


def test_clean_contents_keeps_pages_order_and_comicinfo(tmp_path):
    path = _scene_cbz(tmp_path / "x.cbz")
    book = CbzBook(path)
    trashed = []
    plan = book.clean_contents(dispose_original=trashed.append)
    assert plan.needed and trashed == [path]
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist() == ["001.jpg", "002.jpg", "003.jpg", "ComicInfo.xml"]  # stored in reading order
        assert [zf.read(n) for n in ("001.jpg", "002.jpg", "003.jpg")] == [b"page 1", b"page 2", b"page 10"]
        assert zf.read("ComicInfo.xml") == COMICINFO  # untouched, per-page entries still valid
    assert book.page_names == ["001.jpg", "002.jpg", "003.jpg"]
    reloaded = CbzBook(path)
    assert reloaded.page_names == book.page_names and not reloaded.cleanup_plan().needed


def test_clean_contents_refuses_unsaved_edits_and_does_nothing_when_clean(tmp_path):
    book = CbzBook(_scene_cbz(tmp_path / "x.cbz"))
    book.dirty = True
    with pytest.raises(CbzError, match="unsaved"):
        book.clean_contents()
    book.dirty = False
    book.clean_contents(dispose_original=lambda _p: None)
    trashed = []
    assert not book.clean_contents(dispose_original=trashed.append).needed and not trashed


def test_a_failed_trash_keeps_the_original(tmp_path):
    from redactor_common.core.trash import TrashError

    path = _scene_cbz(tmp_path / "x.cbz")
    original = open(path, "rb").read()

    def no_trash(_p):
        raise TrashError("couldn't move it to the Recycle Bin")

    with pytest.raises(TrashError):
        CbzBook(path).clean_contents(dispose_original=no_trash)
    assert open(path, "rb").read() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.cbz"]  # no temp file left behind


# ---------------------------------------------------------------------------
# The window flow
# ---------------------------------------------------------------------------

@pytest.fixture
def window(tmp_path, monkeypatch):
    from gui import app_settings

    monkeypatch.setattr(app_settings, "credit_pages_path", lambda: str(tmp_path / "known.json"))
    trashed = []
    monkeypatch.setattr("gui.main_window.move_to_trash", trashed.append)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from gui.main_window import MainWindow

    w = MainWindow()
    w.trashed = trashed
    return w


def test_window_reviews_then_cleans_the_ticked_files(window, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt

    from gui import archive_cleanup_dialog

    scene = _scene_cbz(tmp_path / "Batman 045.cbz")
    also = _scene_cbz(tmp_path / "Batman 046.cbz")
    with zipfile.ZipFile(tmp_path / "Clean 001.cbz", "w") as zf:
        zf.writestr("001.jpg", b"p")
        zf.writestr("ComicInfo.xml", COMICINFO)
    window._load_paths([scene, also, str(tmp_path / "Clean 001.cbz")])
    offered = {}

    def untick_second(dialog):
        offered["rows"] = dialog.table.rowCount()
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(archive_cleanup_dialog.ArchiveCleanupDialog, "exec", untick_second)
    window._selected_rows = []
    window.open_clean_contents_dialog()
    assert offered["rows"] == 2  # the clean file isn't offered
    assert window.trashed == [scene]
    assert zipfile.ZipFile(scene).namelist()[0] == "001.jpg"
    assert zipfile.ZipFile(also).namelist()[0].startswith(SCENE)  # unticked: untouched
