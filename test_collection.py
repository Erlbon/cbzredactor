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


def test_reading_in_parallel_gives_the_same_rows(library):
    # Several at once: one at a time spent ~1 s per file waiting on the
    # virus scanner during a user's first scan of a big collection.
    listed = list(scan.list_comics(library))
    one_by_one = {item.path: scan.read_comic(library, item) for item in listed}
    seen = []
    rows, complete = scan.read_comics(library, listed, progress=lambda done, total: seen.append((done, total)))
    assert complete
    assert rows == one_by_one
    assert seen[-1] == (len(listed), len(listed))


def test_a_cancelled_parallel_read_keeps_what_it_finished(library):
    listed = list(scan.list_comics(library)) * 20  # enough that a cancel lands mid-way
    calls = []

    def cancel_after_first_batch():
        calls.append(1)
        return len(calls) > 1

    rows, complete = scan.read_comics(library, listed, should_cancel=cancel_after_first_batch, workers=1)
    assert not complete
    assert 0 < len(rows) <= len({item.path for item in listed})
    assert all(row.path in {item.path for item in listed} for row in rows.values())

def test_a_damaged_comicinfo_entry_does_not_stop_the_scan(tmp_path):
    # A corrupt deflate stream raised zlib.error out of read_comic(), and
    # read_comics() re-raised it: one bad comic ended the whole scan.
    good, bad = tmp_path / "Good 001.cbz", tmp_path / "Bad 001.cbz"
    for path in (good, bad):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("001.jpg", b"x" * 1000)
            archive.writestr("ComicInfo.xml", "<ComicInfo><Series>S</Series><Summary>"
                             + "A long summary. " * 100 + "</Summary></ComicInfo>")
    data = bytearray(bad.read_bytes())
    start = data.find(b"ComicInfo.xml") + len(b"ComicInfo.xml") + 5
    for i in range(start, start + 35):
        data[i] ^= 0xFF
    bad.write_bytes(bytes(data))

    listed = list(scan.list_comics(str(tmp_path)))
    rows, complete = scan.read_comics(str(tmp_path), listed)
    assert complete
    assert "can't be opened" in rows["Bad 001.cbz"].error
    assert rows["Good 001.cbz"].series == "S"


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


def test_numbered_tpbs_with_titles_are_not_duplicates(tmp_path):
    # "TPB - v03 - Title": v03 is the third TPB of Zombie Tramp v3, not a
    # volume. It used to be read as a bare "TPB" with title "v03 - ...",
    # so every TPB of the same year was reported as a duplicate.
    folder = "Action Lab/Zombie Tramp v3 (2014-2019)"
    files = [
        "Zombie Tramp v3 TPB - v03 - Sleazy Rider (ALE, 2015-02).cbz",
        "Zombie Tramp v3 TPB - v04 - Sleazy Rider (ALE, 2015-05).cbz",
        "Zombie Tramp v3 TPB - v05 - Breaking Bath (ALE, 2015-07).cbz",
        "Zombie Tramp v3 TPB - v06 - Unholy Tales of the Dirty South (ALE, 2015-12).cbz",
        "Zombie Tramp v3 TPB - v12 - Voodoo Vixen Death Match 001 (ALE, 2017-12).cbz",
    ]
    name = parse_file_name(files[4])
    assert (name.series, name.volume, name.number_key, name.title) == \
        ("Zombie Tramp", "3", "TPB 12", "Voodoo Vixen Death Match 001")
    rows = [scan.ScanRow(f"{folder}/{f}", comicinfo="yes", series="Zombie Tramp", volume="3") for f in files]
    report = build_report(scan.new_info(str(tmp_path), True), rows)
    assert (report.duplicates, report.moves, report.splits, report.names, report.mismatches) == ([], [], [], [], [])


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


# ---------------------------------------------------------------------------
# False positives found on a real 234,000-comic collection
# ---------------------------------------------------------------------------

def _rows(paths, tmp_path, **fields):
    return [scan.ScanRow(p, comicinfo="yes", **fields) for p in paths]


def test_grouping_and_era_folders_are_not_moves_or_splits(tmp_path):
    kuifje = "Belgian/Kuifje"
    rows = [
        scan.ScanRow(f"{kuifje}/1972 (52 issues)/Kuifje 1972{n:02d} (Le Lombard, 1972-11).cbz")
        for n in range(1, 4)
    ] + [scan.ScanRow(f"{kuifje}/Kuifje 001 (Le Lombard, 1970-01).cbz")]
    report = build_report(scan.new_info(str(tmp_path), True), rows)
    assert report.moves == [] and report.splits == []


def test_same_name_in_another_branch_or_publisher_is_not_a_stray(tmp_path):
    rows = [
        scan.ScanRow("Dutch/DPG/Donald Duck/Donald Duck 001 (DPG Media, 1986-11).cbz"),
        scan.ScanRow("German/Egmont/Donald Duck/Donald Duck 001 (Egmont, 1980-01).cbz"),
        scan.ScanRow("Incoming/Donald Duck 002 (DPG Media, 1986-12).cbz"),
    ]
    report = build_report(scan.new_info(str(tmp_path), True), rows)
    assert [(m.path, m.target_folder) for m in report.moves] == \
        [("Incoming/Donald Duck 002 (DPG Media, 1986-12).cbz", "Dutch/DPG/Donald Duck")]
    assert report.splits == []


def test_an_eras_series_folders_are_the_collections_layout_not_a_split(tmp_path):
    rows = [
        scan.ScanRow("DC/New Justice (2018-2021)/Green Lantern/Green Lantern 001 (DC, 2018-01).cbz"),
        scan.ScanRow("DC/All In (2024-)/Green Lantern/Green Lantern 001 (DC, 2024-01).cbz"),
    ]
    assert build_report(scan.new_info(str(tmp_path), True), rows).splits == []


def test_series_names_that_differ_only_in_punctuation_or_wording_agree(tmp_path):
    cases = [
        ("Batman - Superman 016 (DC, 2015-01).cbz", "Batman/Superman"),
        ("Gen13 001 (Wildstorm, 1994-03).cbz", "Gen 13"),
        ("Thor v5 026 (Marvel, 2022-08).cbz", "Thor"),
        ("Venom 055 - License to Kill 02 (Marvel, 1997-07).cbz", "Venom: License to Kill"),
        ("Sabrina the Teenage Witch v3 005 (Archie, 2000-05).cbz", "Sabrina"),
        ("Slaine v08 - The Grail War (2013).cbz", "Sláine: The Grail War"),
    ]
    rows = [scan.ScanRow(f"A/{file}", comicinfo="yes", series=series) for file, series in cases]
    assert build_report(scan.new_info(str(tmp_path), True), rows).mismatches == []


def test_a_series_convention_is_not_reported_file_by_file(tmp_path):
    files = [f"Pep Comics {n:03d} (Archie, 1961-0{n}).cbz" for n in range(1, 6)]
    rows = [scan.ScanRow(f"A/Pep Comics/{f}", comicinfo="yes", series="Pep", number=str(n), year="1961", month=str(n))
            for n, f in enumerate(files, 1)]
    assert build_report(scan.new_info(str(tmp_path), True), rows).mismatches == []
    rows.append(scan.ScanRow("A/Pep Comics/Pep Comics 006 (Archie, 1961-06).cbz", comicinfo="yes", series="Pep",
                             number="7", year="1961", month="6"))
    assert [(m.field, m.in_name, m.in_comicinfo) for m in build_report(scan.new_info(str(tmp_path), True), rows).mismatches] \
        == [("Number", "006", "7")]


def test_cover_date_vs_release_date_is_not_a_mismatch(tmp_path):
    rows = [
        scan.ScanRow("A/X/X 001 (Marvel, 1970-12).cbz", comicinfo="yes", series="X", number="1", year="1971", month="2"),
        scan.ScanRow("A/X/X 002 (Marvel, 1970-06).cbz", comicinfo="yes", series="X", number="2", year="1970", month="9"),
        scan.ScanRow("A/X/X 003 (Marvel, 2005).cbz", comicinfo="yes", series="X", number="3", year="2017", month="1"),
    ]
    found = {(m.path.rsplit("/", 1)[-1], m.field) for m in build_report(scan.new_info(str(tmp_path), True), rows).mismatches}
    assert found == {("X 002 (Marvel, 1970-06).cbz", "Month"), ("X 003 (Marvel, 2005).cbz", "Year")}


def test_year_issue_and_range_numbers_agree_with_comicinfo():
    from core.collection_names import numbers_agree
    assert numbers_agree("003", "3") and numbers_agree("1982-24", "24") and numbers_agree("2009-21", "200921")
    assert numbers_agree("233-234", "233") and numbers_agree("Vol. 10", "10")
    assert not numbers_agree("006", "7")


def test_copies_told_apart_by_title_or_brackets_are_not_duplicates(tmp_path):
    rows = [
        scan.ScanRow("A/Hyper Scape/Hyper Scape 003 - Shadow Rising Part 1 (DH, 2020-12).cbz"),
        scan.ScanRow("A/Hyper Scape/Hyper Scape 003 - Shadow Rising Part 2 (DH, 2020-12).cbz"),
        scan.ScanRow("A/Sexy Phone/Sexy Phone 01 (English).cbz"),
        scan.ScanRow("A/Sexy Phone/Sexy Phone 01 (German).cbz"),
        scan.ScanRow("A/Thanos/Thanos 008 (Marvel, 2004-05).cbz"),
        scan.ScanRow("B/Thanos/Thanos 008 (Marvel, 2004-05).cbz"),
    ]
    report = build_report(scan.new_info(str(tmp_path), True), rows)
    assert [(d.series, len(d.rows)) for d in report.duplicates] == [("Thanos", 2)]


def test_suggested_names_keep_the_title_and_never_rewrite_other_parts(tmp_path):
    rows = [scan.ScanRow(f"A/Cinebook/Alpha {n:03d} - Title {n} (Cinebook, 2008-0{n}).cbz", comicinfo="yes",
                         publisher="Cinebook Ltd", year="2009", month="7") for n in (1, 2, 3)]
    rows.append(scan.ScanRow("A/Cinebook/Alpha 4 - Wolves' Wages (Cinebook, 2009-04).cbz", comicinfo="yes",
                             publisher="Cinebook Ltd", year="2011", month="7"))
    report = build_report(scan.new_info(str(tmp_path), True), rows)
    (issue,) = report.names
    assert issue.suggested == "Alpha 004 - Wolves' Wages (Cinebook, 2009-04).cbz"  # its own publisher and date


# ---------------------------------------------------------------------------
# Apply: fixes
# ---------------------------------------------------------------------------

def test_report_offers_fixes_and_applies_them(library, monkeypatch):
    from core.collection_fix import KIND_COMICINFO_FROM_NAME, KIND_PAGECOUNT, KIND_RENAME_FROM_COMICINFO, KIND_NEW_COMICINFO
    from gui import collection_report_dialog
    from gui.main_window import MainWindow, _collection_scan_path

    info, rows = _scan(library)
    report = build_report(info, rows)
    kinds = {(f.path.rsplit("/", 1)[-1], f.kind) for f in report.fixes}
    assert ("The Incredible Hulk 103 (Marvel, 1968-05).cbz", KIND_PAGECOUNT) in kinds
    assert ("The Incredible Hulk v1 003 (Marvel, 1962-09).cbz", KIND_NEW_COMICINFO) in kinds
    odd = "3 Guns 003 (Boom, 2013-10).webp.cbz"  # the name says 003, ComicInfo says 4: either way round
    assert {(odd, KIND_COMICINFO_FROM_NAME), (odd, KIND_RENAME_FROM_COMICINFO)} <= kinds

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: library)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window = MainWindow()
    window.scan_collection_folder()
    seen = {}

    def tick(dialog):
        table = dialog.fixes_table
        Qt = collection_report_dialog.Qt
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            fix = item.data(collection_report_dialog._FIX_ROLE)
            if fix.kind in (KIND_PAGECOUNT, KIND_NEW_COMICINFO) or (fix.path.endswith(odd) and fix.kind == KIND_COMICINFO_FROM_NAME):
                item.setCheckState(Qt.CheckState.Checked)
        for row in range(table.rowCount()):  # ticking the rename alternative unticks the ComicInfo one
            item = table.item(row, 0)
            fix = item.data(collection_report_dialog._FIX_ROLE)
            if fix.path.endswith(odd) and fix.kind == KIND_RENAME_FROM_COMICINFO:
                item.setCheckState(Qt.CheckState.Checked)
                break
        seen["ticked"] = sorted((f.path.rsplit("/", 1)[-1], f.kind) for f in dialog.ticked_fixes())
        seen["text"] = dialog.apply_button.text()
        for row in range(table.rowCount()):  # ...and back again: settle it by rewriting ComicInfo
            item = table.item(row, 0)
            fix = item.data(collection_report_dialog._FIX_ROLE)
            if fix.path.endswith(odd) and fix.kind == KIND_COMICINFO_FROM_NAME:
                item.setCheckState(Qt.CheckState.Checked)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(collection_report_dialog.CollectionReportDialog, "exec", tick)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.open_collection_report()
    assert (odd, KIND_RENAME_FROM_COMICINFO) in seen["ticked"] and (odd, KIND_COMICINFO_FROM_NAME) not in seen["ticked"]

    info2, rows2 = _scan(library)
    report2 = build_report(info2, rows2)
    left = {(f.path.rsplit("/", 1)[-1], f.kind) for f in report2.fixes}
    assert ("The Incredible Hulk 103 (Marvel, 1968-05).cbz", KIND_PAGECOUNT) not in left
    assert ("The Incredible Hulk v1 003 (Marvel, 1962-09).cbz", KIND_NEW_COMICINFO) not in left
    assert not [f for f in report2.fixes if f.path.endswith(odd) and f.kind == KIND_COMICINFO_FROM_NAME]
    hulk = next(r for r in rows2 if r.file == "The Incredible Hulk v1 003 (Marvel, 1962-09).cbz")
    assert (hulk.comicinfo, hulk.series, hulk.number) == ("yes", "The Incredible Hulk", "3")
    _saved, saved_rows = scan.read_scan(_collection_scan_path())
    assert next(r for r in saved_rows if r.file == hulk.file).comicinfo == "yes"  # the saved scan followed the edit


def test_fixes_are_read_only_where_the_folder_is_not_present(library, monkeypatch):
    from gui import collection_report_dialog
    from gui.main_window import MainWindow, _collection_scan_path

    info, rows = _scan(library)
    info.root = r"E:\eLib\741.59 - Comic Books" if os.name != "nt" else "/nowhere/741.59 - Comic Books"
    scan.write_scan(_collection_scan_path(), info, rows)
    seen = {}

    def look(dialog):
        item = dialog.fixes_table.item(0, 0)
        seen["checkable"] = item.data(collection_report_dialog.Qt.ItemDataRole.CheckStateRole) is not None
        seen["rows"] = dialog.fixes_table.rowCount()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(collection_report_dialog.CollectionReportDialog, "exec", look)
    MainWindow().open_collection_report()
    assert seen["rows"] > 0 and not seen["checkable"]


def test_a_cbt_in_the_collection_is_converted_from_the_fixes_tab(library, monkeypatch):
    import io
    import tarfile

    from core.collection_fix import KIND_CONVERT
    from gui import collection_report_dialog
    from gui.main_window import MainWindow

    cbt = scan.long_path(os.path.join(library, "Incoming", "Tar Comic 001 (Boom, 2020-03).cbt"))
    with tarfile.open(cbt, "w") as archive:
        for page in ("001.jpg", "002.jpg"):
            entry = tarfile.TarInfo(page)
            entry.size = 4
            archive.addfile(entry, io.BytesIO(b"jpeg"))
    trashed = []
    monkeypatch.setattr("gui.main_window.move_to_trash", lambda path: (trashed.append(path), os.remove(path)))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: library)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window = MainWindow()
    window.scan_collection_folder()

    def tick_convert(dialog):
        table = dialog.fixes_table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item.data(collection_report_dialog._FIX_ROLE).kind == KIND_CONVERT:
                item.setCheckState(collection_report_dialog.Qt.CheckState.Checked)
        assert [f.path.rsplit("/", 1)[-1] for f in dialog.ticked_fixes()] == ["Tar Comic 001 (Boom, 2020-03).cbt"]
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(collection_report_dialog.CollectionReportDialog, "exec", tick_convert)
    window.open_collection_report()
    assert _exists(library, "Incoming", "Tar Comic 001 (Boom, 2020-03).cbz") and not _exists(cbt)
    assert len(trashed) == 1
    _info, rows = _scan(library)
    assert next(r for r in rows if r.file == "Tar Comic 001 (Boom, 2020-03).cbz").pages == "2"
