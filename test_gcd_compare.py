"""Tests for core/gcd_compare.py: a synthetic ComicRack library (built with
core/comicrack_import.py) compared with a synthetic GCD-layout dump."""

import csv
import sqlite3
import sys

import pytest
from PyQt6.QtWidgets import QApplication

from core import comicrack_import as cr
from core import gcd_compare as cmp

# Module level: an unreferenced QApplication is garbage-collected at once.
_app = QApplication.instance() or QApplication(sys.argv)


def _book(book_id, **fields):
    body = "".join(f"<{tag}>{value}</{tag}>" for tag, value in fields.items())
    return f'<Book Id="{book_id}" File="x.cbz">{body}</Book>'


LIBRARY = [
    _book("b45", Series="Batman", Number="45", Volume="2016", Publisher="DC Comics", Year="2018",
          Writer="Tom King", Characters="Batman, Booster Gold", Summary="The Gift.",
          Web="https://comicvine.gamespot.com/batman-45/4000-654321/"),
    _book("b46", Series="Batman", Number="46", Volume="2016", Publisher="DC Comics", Year="2018", Writer="Tom King"),
    _book("nf1", Series="Nick Fury, Agent of SHIELD", Number="1", Volume="1989", Publisher="Marvel", Year="1989"),
    _book("dd1", Series="Daredevil TPB", Number="1", Volume="2014", Publisher="Marvel", Year="2014"),
    _book("dc934", Series="Detective Comics", Number="934", Volume="2016", Publisher="DC Comics", Year="2016"),
    _book("dc935", Series="Detective Comics", Number="935", Volume="2016", Publisher="DC Comics", Year="2016"),
    # A 2025 relaunch GCD doesn't list yet: #45 exists in GCD's 2016
    # series, but published years apart -- not a match.
    _book("b2025", Series="Batman", Number="45", Volume="2025", Publisher="DC Comics", Year="2025"),
    _book("dw100", Series="Doctor Who Magazine", Number="100", Volume="1979", Publisher="Marvel UK", Year="1985"),
    _book("dw240", Series="Doctor Who Magazine", Number="240", Volume="1979", Publisher="Marvel UK", Year="1996"),
    _book("dw241", Series="Doctor Who Magazine", Number="241", Volume="1979", Publisher="Marvel UK", Year="1996"),
    _book("ur1", Series="L'Uomo Ragno v1", Number="1", Volume="1970", Publisher="Editoriale Corno", Year="1970"),
    _book("fx", Series="Fix und Foxi", Number="29", Volume="1955", Publisher="Pabel Verlag", Year="1955"),
    _book("kj", Series="Kuifje", Number="197606", Volume="1946", Publisher="Le Lombard", Year="1976"),
    _book("zz1", Series="Obscure Indie Book", Number="1", Volume="2013", Publisher="Tiny Press", Year="2013",
          Writer="Someone", CoverArtist="Cover Person", Editor="Ed Itor"),
    _book("zz2", Series="Obscure Indie Book", Number="2", Volume="2013", Publisher="Tiny Press", Year="2013"),
]


def _library(tmp_path):
    source = tmp_path / "ComicDb.xml"
    source.write_text(
        '<?xml version="1.0"?><ComicDatabase><Books>' + "".join(LIBRARY) + "</Books></ComicDatabase>",
        encoding="utf-8",
    )
    dest = tmp_path / "library.db"
    cr.build_comicrack_database(str(source), str(dest))
    return dest


def _gcd(tmp_path):
    """A tiny GCD dump: the tables and columns the comparison reads (the
    converter's schema is exactly that subset), without an import-info
    table -- so it counts as GCD, not a converted library."""
    path = tmp_path / "gcd.db"
    con = sqlite3.connect(path)
    for table, columns in cr.TABLES.items():
        if not table.startswith("cr_"):
            con.execute(f"create table {table} ({', '.join(columns)})")
    con.executemany("insert into gcd_publisher values (?, ?, 0)", [(1, "DC"), (2, "Marvel"), (3, "ECC Ediciones"), (4, "Le Lombard"), (5, "Marvel UK"),
                                                                 (6, "Editoriale Corno"), (7, "Pabel Verlag"),
                                                                 (8, "Gevacur")])
    con.executemany("insert into stddata_language values (?, ?, ?)", [(1, "en", "English"), (2, "es", "Spanish"), (3, "nl", "Dutch"),
                                                                    (4, "it", "Italian"), (5, "de", "German")])
    con.executemany("insert into gcd_series values (?, ?, ?, ?, ?, ?, ?, 0)", [
        (10, "Batman", 2016, None, 3, 2, 100),  # the Spanish edition -- same name and year
        (11, "Batman", 2016, None, 1, 1, 150),  # DC's own
        (20, "Nick Fury, Agent of S.H.I.E.L.D.", 1989, None, 2, 1, 47),
        (30, "Daredevil", 2014, None, 2, 1, 18),
        (40, "Kuifje", 1946, None, 4, 3, 2000),
        (50, "Detective Comics", 1937, None, 1, 1, 1100),
        # One library run, split by GCD over two publisher eras.
        (60, "Doctor Who Magazine", 1985, None, 5, 1, 135),
        (61, "Doctor Who Magazine", 1996, None, 5, 1, 399),
        (70, "L'Uomo Ragno [Collana Super-Eroi]", 1970, None, 6, 4, 283),
        # Another publisher's same-name series within a year must not block
        # the right one two years off.
        (80, "Fix und Foxi", 1956, None, 8, 5, 50),
        (81, "Fix und Foxi", 1953, None, 7, 5, 1288),
    ])
    con.executemany("insert into gcd_issue values (?, ?, ?, '', '', '', null, '', '', ?, 0, '')", [
        (100, 10, "45", None), (101, 10, "46", None),
        (110, 11, "45", None), (111, 11, "45", 110),  # a variant cover record: never the match
        (200, 20, "1", None), (300, 30, "1", None), (400, 40, "6/1976", None),
        (500, 50, "934", None), (501, 50, "935", None),
        (600, 60, "100", None), (610, 61, "240", None), (611, 61, "241", None),
        (700, 70, "1", None), (800, 80, "1", None), (810, 81, "29", None),
    ])
    con.execute("update gcd_issue set key_date = '2018-06-00' where id in (100, 110)")
    # DC's Batman #45 is indexed with credits but no characters or synopsis.
    con.execute("insert into gcd_story values (1000, 110, 1, 19, 'The Gift', '', '', '', '', '', '', '', '', '', 0)")
    con.execute("insert into gcd_creator_name_detail values (1, 'Tom King', 0)")
    con.execute("insert into gcd_story_credit values (1, 1000, 1, 1, 0)")
    con.commit()
    con.close()
    return path


@pytest.fixture
def compared(tmp_path):
    dest = tmp_path / "diff.db"
    summary = cmp.compare_with_gcd(str(_library(tmp_path)), str(_gcd(tmp_path)), str(dest))
    return summary, sqlite3.connect(dest)


def test_series_matching(compared):
    summary, con = compared
    rows = dict(
        (name, (status, gcd_id, method))
        for name, status, gcd_id, method in con.execute(
            "select name, status, gcd_series_id, method from diff_series where year_began <> 2025"
        )
    )
    assert con.execute("select status from diff_series where year_began = 2025").fetchone() == ("not found",)
    assert rows["Batman"] == ("matched", 11, cmp.METHOD_PUBLISHER)  # DC's, not the Spanish edition
    assert rows["Nick Fury, Agent of SHIELD"][:2] == ("matched", 20)  # S.H.I.E.L.D. = SHIELD
    assert rows["Daredevil TPB"][:2] == ("matched", 30)  # trailing TPB ignored
    assert rows["Kuifje"][:2] == ("matched", 40)
    assert rows["Detective Comics"] == ("matched", 50, cmp.METHOD_OTHER_YEAR)  # 2016 relaunch, GCD's 1937 series
    assert rows["Doctor Who Magazine"] == ("matched", 61, cmp.METHOD_SPLIT)  # main part first
    assert rows["L'Uomo Ragno v1"][:2] == ("matched", 70)  # GCD's [bracketed] qualifier optional
    assert rows["Fix und Foxi"] == ("matched", 81, cmp.METHOD_OTHER_YEAR)
    assert rows["Obscure Indie Book"] == ("not found", None, "")
    assert summary.series == 10 and summary.series_not_found == 2
    assert con.execute("select gcd_more_series_ids from diff_series where name = 'Doctor Who Magazine'").fetchone() == ("60",)


def test_issue_status_and_gaps(compared):
    summary, con = compared
    issues = {
        (series, number): (status, gcd_id, lacks)
        for series, number, status, gcd_id, lacks in con.execute(
            "select series, number, status, gcd_issue_id, gcd_lacks from diff_issue where key_date not like '2025%'"
        )
    }
    assert issues[("Batman", "45")] == ("matched", 110, "characters, summary")  # GCD has the credits
    assert issues[("Batman", "46")] == ("not in GCD series", None, "")
    assert issues[("Kuifje", "197606")][:2] == ("matched", 400)  # = GCD's "6/1976"
    assert issues[("Obscure Indie Book", "2")][0] == "series not in GCD"
    assert issues[("Doctor Who Magazine", "100")][:2] == ("matched", 600)  # from the other GCD part
    assert (summary.issues, summary.issues_matched, summary.issues_not_in_gcd_series,
            summary.issues_in_unfound_series) == (15, 11, 1, 3)
    assert summary.gaps == {"characters": 1, "summary": 1}
    assert "10 library series: 8 found in GCD" in summary.describe()


def test_handed_over_issues_carry_the_library_data(compared):
    _summary, con = compared
    row = con.execute(
        "select writer, cover_artist, editor, characters, synopsis, comicvine_issue_id from diff_issue "
        "where series = 'Batman' and number = '45'"
    ).fetchone()
    assert row == ("Tom King", "", "", "Batman, Booster Gold", "The Gift.", 654321)
    indie = con.execute("select writer, cover_artist, editor from diff_issue where series = 'Obscure Indie Book' "
                        "and number = '1'").fetchone()
    assert indie == ("Someone", "Cover Person", "Ed Itor")
    # A plain match has nothing to hand over: no copied details.
    assert con.execute("select writer from diff_issue where series = 'Daredevil TPB'").fetchone() == (None,)


def test_issue_forms():
    assert cmp.issue_forms("6/1976") & cmp.issue_forms("1976-06") & cmp.issue_forms("197606") == {"1976:6"}
    assert "1" in cmp.issue_forms("[1]") and "1" in cmp.issue_forms("v01") and "1" in cmp.issue_forms("")
    assert {"2", "14"} <= cmp.issue_forms("2 (14)")
    assert cmp.issue_forms("2486") == {"2486"}


def test_name_keys_and_publishers():
    assert cmp.name_keys("The Amazing X-Men v2 TPB") == {
        "the amazing x men v2 tpb", "the amazing x men v2", "amazing x men v2 tpb", "amazing x men v2",
    }
    assert "nick fury agent of shield" in cmp.name_keys("Nick Fury, Agent of S.H.I.E.L.D.")
    assert cmp.publisher_words("DC Comics") == cmp.publisher_words("DC") == {"dc"}


def test_csv_export(compared, tmp_path):
    _summary, _con = compared
    written = cmp.export_csv(str(tmp_path / "diff.db"), str(tmp_path / "csv"))
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in written] == list(cmp.CSV_FILES)
    with open(written[1], encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {(r["series"], r["number"], r["status"]) for r in rows} == {
        ("Batman", "46", "not in GCD series"), ("Batman", "45", "series not in GCD"), ("Obscure Indie Book", "1", "series not in GCD"),
        ("Obscure Indie Book", "2", "series not in GCD"),
    }
    with open(written[2], encoding="utf-8-sig", newline="") as handle:
        gaps = list(csv.DictReader(handle))
    assert gaps[0]["gcd_lacks"] == "characters, summary" and gaps[0]["gcd_issue_id"] == "110"


def test_the_files_must_be_the_right_way_round(tmp_path):
    library, gcd = _library(tmp_path), _gcd(tmp_path)
    with pytest.raises(cmp.GcdLocalError, match="converted ComicRack library"):
        cmp.compare_with_gcd(str(gcd), str(gcd), str(tmp_path / "x.db"))
    with pytest.raises(cmp.GcdLocalError, match="must be the GCD dump"):
        cmp.compare_with_gcd(str(library), str(library), str(tmp_path / "x.db"))


def test_cancel_leaves_no_file(tmp_path):
    with pytest.raises(cmp.ImportCancelled):
        cmp.compare_with_gcd(str(_library(tmp_path)), str(_gcd(tmp_path)), str(tmp_path / "d.db"),
                             cancelled=lambda: True)
    assert not (tmp_path / "d.db").exists()


def test_flow_explains_what_is_missing(monkeypatch):
    from gui import gcd_compare_flow as flow

    monkeypatch.setattr(flow.app_settings, "load_comicrack_database", lambda: "")
    monkeypatch.setattr(flow.app_settings, "load_gcd_local_database", lambda: "")
    shown = []
    monkeypatch.setattr(flow.QMessageBox, "information", lambda *a: shown.append(a[2]))
    assert flow.compare_library_with_gcd(None) is None
    assert "ComicRack Library Database and Tools > GCD Local Database" in shown[0]


def test_flow_compares_and_writes_csvs(tmp_path, monkeypatch):
    from gui import gcd_compare_flow as flow

    library, gcd = _library(tmp_path), _gcd(tmp_path)
    monkeypatch.setattr(flow.app_settings, "load_comicrack_database", lambda: str(library))
    monkeypatch.setattr(flow.app_settings, "load_gcd_local_database", lambda: str(gcd))
    monkeypatch.setattr(flow.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "vs.db"), ""))
    monkeypatch.setattr(flow.QMessageBox, "exec", lambda self: 0)
    folder = flow.compare_library_with_gcd(None)
    assert folder == str(tmp_path / "vs csv")
    assert sorted(p.name for p in (tmp_path / "vs csv").iterdir()) == sorted(cmp.CSV_FILES)
