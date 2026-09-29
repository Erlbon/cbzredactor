"""Tests for Collection > Scan Collection Folder... and Collection
Report... (core/collection_names.py, core/collection_scan.py,
core/collection_report.py, gui/collection_report_dialog.py) -- on a
small made-up library laid out like the user's real one."""

import os
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core import collection_scan as scan
from core.collection_names import library_name, number3, parse_file_name, parse_folder_name, series_key
from core.collection_report import build_report, report_list

_app = QApplication.instance() or QApplication(sys.argv)

MARVEL = "5973 - US Comics/M/Marvel [1939 - dd]"
BOOM = "5973 - US Comics/B/Boom [2005 - dd]"
DARK_HORSE = "5973 - US Comics/D/Dark Horse [1986 - dd]/Patton Oswald & Jordan Blum/Minor Threats"


def _comicinfo(**fields):
    tags = {"series": "Series", "number": "Number", "volume": "Volume", "year": "Year", "month": "Month",
            "publisher": "Publisher", "page_count": "PageCount"}
    body = "".join(f"<{tags[k]}>{v}</{tags[k]}>" for k, v in fields.items())
    return f'<?xml version="1.0"?><ComicInfo>{body}</ComicInfo>'


def _cbz(root, rel, pages=3, info=True, **fields):
    path = scan.long_path(os.path.join(root, *rel.split("/")))  # the temp folder makes these long
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for page in range(1, pages + 1):
            archive.writestr(f"{page:03d}.jpg", b"jpeg")
        if info:
            name = parse_file_name(os.path.basename(rel))
            defaults = {"series": name.series, "number": name.number.lstrip("0") or name.number,
                        "year": name.year, "month": name.month.lstrip("0"), "publisher": name.publisher,
                        "page_count": str(pages)}
            defaults.update(fields)
            archive.writestr("ComicInfo.xml", _comicinfo(**{k: v for k, v in defaults.items() if v}))
    return path


@pytest.fixture
def library(tmp_path):
    root = str(tmp_path / "741.59 - Comic Books")
    for n, month in ((1, "08"), (2, "09")):
        _cbz(root, f"{BOOM}/3 Guns/3 Guns {n:03d} (Boom, 2013-{month}).webp.cbz")
    _cbz(root, f"{BOOM}/3 Guns/3 Guns 003 (Boom, 2013-10).webp.cbz", number="4")  # ComicInfo disagrees
    _cbz(root, f"{BOOM}/3 Guns/3 Guns 4 (Boom, 2013-11).webp.cbz")  # breaks the 3-digit pattern
    _cbz(root, f"{BOOM}/3 Guns/3 Guns TPB (Boom, 2014-10).webp.cbz")
    _cbz(root, f"{MARVEL}/The Incredible Hulk v1 1962-1963)(6 issues)/The Incredible Hulk 001 (Marvel, 1962-05).cbz")
    _cbz(root, f"{MARVEL}/The Incredible Hulk v1 1962-1963)(6 issues)/The Incredible Hulk 002 (Marvel, 1962-07).cbz")
    _cbz(root, f"{MARVEL}/The Incredible Hulk v2 (1968-1999) (issues 102-474)/The Incredible Hulk 102 (Marvel, 1968-04).cbz")
    _cbz(root, f"{MARVEL}/The Incredible Hulk v2 (1968-1999) (issues 102-474)/The Incredible Hulk 103 (Marvel, 1968-05).cbz",
         page_count="30")
    _cbz(root, f"{DARK_HORSE}/Minor Threats 001 (Dark Horse Comics, 2022-08).cbz")
    _cbz(root, f"{DARK_HORSE}/From the World of Minor Threats - The Brood TPB (Dark Horse Comics, 2025-07).cbz")
    _cbz(root, "Incoming/3 Guns 005 (Boom, 2013-12).webp.cbz")
    _cbz(root, "Incoming/The Incredible Hulk 104 (Marvel, 1968-06).cbz")  # no volume: its year picks v2
    _cbz(root, "Incoming/The Incredible Hulk v1 003 (Marvel, 1962-09).cbz", info=False)
    _cbz(root, "Incoming/3 Guns 002 (Boom, 2013-09).webp.cbz")  # a second copy
    zipped = scan.long_path(os.path.join(root, "Incoming", "Renamed Zip 001 (Boom, 2020-01).cbr"))
    os.rename(_cbz(root, "Incoming/Renamed Zip 001 (Boom, 2020-01).cbz"), zipped)
    with open(scan.long_path(os.path.join(root, "Incoming", "Broken 001 (Boom, 2020-02).cbz")), "wb") as handle:
        handle.write(b"PK\x03\x04 not really a zip")
    return root


def _exists(*parts):
    return os.path.exists(scan.long_path(os.path.join(*parts)))


def _scan(root):
    listed = list(scan.list_comics(root))
    return scan.new_info(root, True), [scan.read_comic(root, item) for item in listed]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def test_file_and_folder_names():
    hulk = parse_file_name("The Incredible Hulk v2 102 (Marvel, 1968-04).cbz")
    assert (hulk.series, hulk.volume, hulk.number, hulk.publisher, hulk.year, hulk.month) == \
        ("The Incredible Hulk", "2", "102", "Marvel", "1968", "04")
    tpb = parse_file_name("3 Guns TPB v02 (Boom, 2015-10).webp.cbz")
    assert (tpb.series, tpb.number_key, tpb.extensions) == ("3 Guns", "TPB 2", ".webp.cbz")
    assert parse_file_name("X-Men 2099 001 (Marvel, 1993-10).cbz").series == "X-Men 2099"
    brood = parse_file_name("From the World of Minor Threats - The Brood TPB (Dark Horse Comics, 2025-07).cbz")
    assert brood.series == "From the World of Minor Threats - The Brood" and brood.is_tpb
    folder = parse_folder_name("The Incredible Hulk v1 1962-1963)(6 issues)")  # the typo is real
    assert (folder.series, folder.volume, folder.first_year, folder.last_year) == ("The Incredible Hulk", "1", 1962, 1963)
    assert series_key("The Incredible Hulk") == series_key("incredible hulk")
    assert number3("1") == "001" and number3("0.5") == "000.5" and number3("TPB v02") == "TPB v02"
    assert library_name("3 Guns", "5", "Boom", "2013", "12", extensions=".webp.cbz") == "3 Guns 005 (Boom, 2013-12).webp.cbz"
    assert library_name("3 Guns", "5", "", "2013", "12") == ""  # a part missing: no suggestion


# ---------------------------------------------------------------------------
# Scan and the zipped CSV
# ---------------------------------------------------------------------------

def test_scan_reads_each_comic_and_round_trips(library, tmp_path):
    info, rows = _scan(library)
    by_file = {row.file: row for row in rows}
    assert len(rows) == 17
    three = by_file["3 Guns 003 (Boom, 2013-10).webp.cbz"]
    assert (three.format, three.pages, three.comicinfo, three.series, three.number, three.month) == \
        ("CBZ", "3", "yes", "3 Guns", "4", "10")
    assert three.path == f"{BOOM}/3 Guns/3 Guns 003 (Boom, 2013-10).webp.cbz"
    assert by_file["The Incredible Hulk v1 003 (Marvel, 1962-09).cbz"].comicinfo == "no"
    assert by_file["Renamed Zip 001 (Boom, 2020-01).cbr"].format == "CBR → ZIP"
    assert "can't be opened" in by_file["Broken 001 (Boom, 2020-02).cbz"].error

    zip_path = str(tmp_path / "collection_scan.zip")
    scan.write_scan(zip_path, info, rows)
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == [scan.CSV_NAME]
        assert archive.read(scan.CSV_NAME).startswith("\ufeffpath,size".encode("utf-8"))  # Excel-friendly
    info2, rows2 = scan.read_scan(zip_path)
    assert info2.root == os.path.abspath(library) and info2.complete
    assert sorted(rows2, key=lambda r: r.path) == sorted(rows, key=lambda r: r.path)


def test_rescan_reuses_unchanged_files(library):
    info, rows = _scan(library)
    previous = {row.path: row for row in rows}
    listed = list(scan.list_comics(library))
    assert all(scan.reusable(previous, item) is not None for item in listed)
    changed = scan.long_path(os.path.join(library, "Incoming", "3 Guns 005 (Boom, 2013-12).webp.cbz"))
    os.utime(changed, (1_000_000_000, 1_000_000_000))
    stale = [item.path for item in scan.list_comics(library) if scan.reusable(previous, item) is None]
    assert stale == ["Incoming/3 Guns 005 (Boom, 2013-12).webp.cbz"]


def test_a_broken_scan_file_is_reported(tmp_path):
    bad = tmp_path / "collection_scan.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(scan.ScanFileError):
        scan.read_scan(str(bad))


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def test_report_finds_what_is_out_of_place(library):
    info, rows = _scan(library)
    report = build_report(info, rows)

    moves = {m.path.rsplit("/", 1)[-1]: m.target_folder for m in report.moves}
    hulk_v1 = f"{MARVEL}/The Incredible Hulk v1 1962-1963)(6 issues)"
    hulk_v2 = f"{MARVEL}/The Incredible Hulk v2 (1968-1999) (issues 102-474)"
    assert moves == {
        "3 Guns 005 (Boom, 2013-12).webp.cbz": f"{BOOM}/3 Guns",
        "3 Guns 002 (Boom, 2013-09).webp.cbz": f"{BOOM}/3 Guns",
        "The Incredible Hulk 104 (Marvel, 1968-06).cbz": hulk_v2,  # picked by its year
        "The Incredible Hulk v1 003 (Marvel, 1962-09).cbz": hulk_v1,  # picked by its "v1"
    }  # the Minor Threats spin-off in its parent's folder is NOT out of place

    names = {n.path.rsplit("/", 1)[-1]: n for n in report.names}
    odd = names["3 Guns 4 (Boom, 2013-11).webp.cbz"]
    assert "3 digits" in odd.problems[0] and odd.suggested == "3 Guns 004 (Boom, 2013-11).webp.cbz"
    assert len(names) == 1

    duplicates = [(d.series, d.number, len(d.rows)) for d in report.duplicates]
    assert duplicates == [("3 Guns", "002", 2)]

    mismatches = [(m.path.rsplit("/", 1)[-1], m.field, m.in_name, m.in_comicinfo) for m in report.mismatches]
    assert mismatches == [("3 Guns 003 (Boom, 2013-10).webp.cbz", "Number", "003", "4")]

    formats = {(f.path.rsplit("/", 1)[-1], f.problem.split(":")[0].split(" —")[0]) for f in report.formats}
    assert ("Renamed Zip 001 (Boom, 2020-01).cbr", "named CBR but really ZIP") in formats
    assert ("The Incredible Hulk v1 003 (Marvel, 1962-09).cbz", "no ComicInfo.xml") in formats
    assert ("The Incredible Hulk 103 (Marvel, 1968-05).cbz", "PageCount says 30, the archive has 3 pages") in formats
    assert any(name.startswith("Broken") and problem.startswith("can't be opened") for name, problem in formats)

    listed = report_list(report)
    assert {row[0] for row in listed} >= {"Move", "File name", "Duplicate", "Name vs ComicInfo", "Format / missing"}


def test_several_fitting_folders_are_a_question_not_a_guess(library):
    _cbz(library, "Incoming/The Incredible Hulk 200 (Marvel, 2030-01).cbz")  # no volume, no fitting year
    _cbz(library, f"{MARVEL}/The Incredible Hulk v3 (1999-2008)/The Incredible Hulk 001 (Marvel, 1999-12).cbz")
    report = build_report(*_scan(library))
    question = next(m for m in report.moves if "Hulk 200" in m.path)
    assert question.target_folder == "" and "which one" in question.reason


def test_long_paths_are_flagged(library):
    info, rows = _scan(library)
    rows[0].path = "x" * 300 + ".cbz"
    report = build_report(info, rows)
    assert any("260" in f.problem for f in report.formats)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_scan_report_apply_and_undo(library, monkeypatch):
    from gui import collection_report_dialog
    from gui.main_window import MainWindow, _collection_scan_path

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: library)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window = MainWindow()
    window.scan_collection_folder()
    assert os.path.exists(_collection_scan_path())

    seen = {}

    def tick_moves(dialog):
        seen["apply_visible"] = not dialog.apply_button.isHidden()
        dialog._tick_all(dialog.moves_table, collection_report_dialog.Qt.CheckState.Checked)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(collection_report_dialog.CollectionReportDialog, "exec", tick_moves)
    window.open_collection_report()
    assert seen["apply_visible"]
    boom = os.path.join(library, *BOOM.split("/"), "3 Guns")
    assert _exists(boom, "3 Guns 005 (Boom, 2013-12).webp.cbz")
    assert _exists(library, "Incoming", "3 Guns 002 (Boom, 2013-09).webp.cbz")  # never overwrites
    _info, rows = scan.read_scan(_collection_scan_path())
    assert f"{BOOM}/3 Guns/3 Guns 005 (Boom, 2013-12).webp.cbz" in {r.path for r in rows}  # scan kept in step

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.undo_last_rename()
    assert _exists(library, "Incoming", "3 Guns 005 (Boom, 2013-12).webp.cbz")
    assert not _exists(boom, "3 Guns 005 (Boom, 2013-12).webp.cbz")


def test_report_elsewhere_is_read_only(library, monkeypatch):
    from gui import collection_report_dialog
    from gui.main_window import MainWindow, _collection_scan_path

    info, rows = _scan(library)
    info.root = r"E:\eLib\741.59 - Comic Books" if os.name != "nt" else "/nowhere/741.59 - Comic Books"
    scan.write_scan(_collection_scan_path(), info, rows)
    seen = {}

    def look(dialog):
        seen["apply_visible"] = not dialog.apply_button.isHidden()
        seen["checkable"] = bool(dialog._checkable_tables)
        seen["moves"] = dialog.moves_table.rowCount()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(collection_report_dialog.CollectionReportDialog, "exec", look)
    MainWindow().open_collection_report()
    assert seen == {"apply_visible": False, "checkable": False, "moves": 4}
