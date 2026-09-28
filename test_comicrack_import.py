"""Tests for core/comicrack_import.py (ComicRack ComicDb.xml -> GCD-layout
SQLite), the local lookup reading the result, and the settings dialog.

The XML is synthetic but shaped like a real ComicRack Community Edition
library (element names, Pages, CustomValuesStore, the stray lower-case
<writer> some older tool wrote)."""

import sqlite3
import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication

from core import comicrack_import as cr
from core.gcd_local import GcdLocalDatabase

# Module level: an unreferenced QApplication is garbage-collected at once.
_app = QApplication.instance() or QApplication(sys.argv)


def _book(book_id, path="E:\\\\eLib\\\\x.cbz", **fields):
    body = "".join(f"<{tag}>{value}</{tag}>" for tag, value in fields.items() if tag != "Pages")
    body += fields.get("Pages", "")
    return f'<Book Id="{book_id}" File="{path}">{body}<FileSize>123</FileSize></Book>'


BOOKS = [
    _book(
        "a1", Title="TPB", Series="Judge Dredd: The Three Amigos", Number="1", Volume="1996", Year="1996",
        Month="1", Day="26", Writer="John Wagner", Penciller="Trevor Hairsine", Inker="Trevor Hairsine",
        CoverArtist="Trevor Hairsine", Publisher="Hamlyn", Characters="Judge Dredd, Judge Death, Judge Dredd",
        Web="https://comicvine.gamespot.com/judge-dredd-the-three-amigos-1-tpb/4000-406658/",
        CustomValuesStore=",comicvine_issue=406658,comicvine_volume=62253",
        Pages='<Pages><Page Image="0" ImageWidth="982" ImageHeight="1363" Type="FrontCover" /></Pages>',
    ),
    # Batman (2016) #45 -- twice: a sparse copy first, then a fully tagged one.
    _book("b1", Series="Batman", Number="45", Volume="2016", Publisher="DC Comics", Year="2018"),
    _book(
        "b2", Series="Batman", Number="045", Volume="2016", Publisher="DC Comics", Year="2018", Month="6",
        Title="The Gift Part 1", Summary="\u201cTHE TRAVELERS\u201d part one!", writer="Tom King",
        Penciller="Tony Daniel", Colorist="Tomeu Morey", Editor="Jamie S. Rich, Brittany Holzherr",
        Genre="Superhero, Action", LanguageISO="EN", Teams="Justice League",
        Notes="Scraped metadata from ComicVine [CVDB654321].",
    ),
    # Batman (1940) is another series: same name, other Volume.
    _book("c1", Series="Batman", Number="45", Volume="1940", Publisher="DC Comics", Year="1948", Month="2"),
    # Volume as a volume NUMBER, not a year: start year from the issues.
    _book("d1", Series="Blake et Mortimer", Number="3", Volume="2", Publisher="Dargaud", Year="1987",
          LanguageISO="fr", Artist="Edgar P. Jacobs"),
    _book("d2", Series="Blake et Mortimer", Number="4", Volume="2", Publisher="Dargaud", Year="1989",
          LanguageISO="fr"),
    # One-shot without a number; a book without any series.
    _book("e1", Series="Some Graphic Novel", Publisher="Titan Books", Year="2001"),
    _book("f1", Title="Unknown scan"),
]

XML = (
    '<?xml version="1.0"?>\n<ComicDatabase xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Id="x">\n<Books>\n'
    + "\n".join(BOOKS)
    + '\n</Books>\n<ComicLists><Item xsi:type="ComicLibraryListItem" Name="Library" /></ComicLists>\n</ComicDatabase>\n'
)


@pytest.fixture
def library(tmp_path):
    source = tmp_path / "ComicDb.xml"
    source.write_text(XML, encoding="utf-8")
    dest = tmp_path / "library.db"
    summary = cr.build_comicrack_database(str(source), str(dest))
    return summary, dest


def test_summary_counts(library):
    summary, _dest = library
    assert (summary.books, summary.skipped_no_series, summary.merged_copies) == (8, 1, 1)
    assert (summary.series, summary.issues) == (5, 6)
    assert "8 books read" in summary.describe() and "without a Series" in summary.describe()


def test_series_split_by_volume_and_years(library):
    _summary, dest = library
    con = sqlite3.connect(dest)
    rows = con.execute("select name, year_began, year_ended, issue_count from gcd_series order by id").fetchall()
    assert rows == [
        ("Judge Dredd: The Three Amigos", 1996, 1996, 1),
        ("Batman", 2016, 2018, 1),
        ("Batman", 1940, 1948, 1),
        ("Blake et Mortimer", 1987, 1989, 2),  # Volume "2" isn't a year
        ("Some Graphic Novel", 2001, 2001, 1),
    ]
    assert con.execute("select number from gcd_issue where series_id = 5").fetchone() == ("[nn]",)


def test_no_private_data_is_copied(library):
    _summary, dest = library
    dump = "\n".join(sqlite3.connect(dest).iterdump())
    assert "eLib" not in dump and ".cbz" not in dump


def test_best_tagged_copy_wins_and_details_read_like_gcd(library):
    _summary, dest = library
    db = GcdLocalDatabase(str(dest))
    assert db.is_comicrack
    found = db.search("Batman", "45", year="2018", series_year="2016")
    assert [c.series_year for c in found] == [2016, 1940]
    details = db.details(found[0].issue_id).as_dict()
    assert details["title"] == "The Gift Part 1"
    assert details["summary"] == "\u201cTHE TRAVELERS\u201d part one!"
    assert details["writer"] == "Tom King"  # from the lower-case <writer>
    assert details["editor"] == "Jamie S. Rich, Brittany Holzherr"
    assert details["genre"] == "Superhero, Action"
    assert details["language_iso"] == "en"
    assert details.get("web", "") == ""  # ComicRack had no Web link for it: never a made-up comics.org one
    copies, cv_issue = sqlite3.connect(dest).execute(
        "select copies, comicvine_issue_id from cr_issue where issue_id = ?", (found[0].issue_id,)
    ).fetchone()
    assert (copies, cv_issue) == (2, 654321)


def test_credits_characters_cover_and_comicvine(library):
    _summary, dest = library
    db = GcdLocalDatabase(str(dest))
    candidate = db.search("Judge Dredd - The Three Amigos", "1")[0]
    details = db.details(candidate.issue_id).as_dict()
    assert details["web"] == "https://comicvine.gamespot.com/judge-dredd-the-three-amigos-1-tpb/4000-406658/"
    assert details["penciller"] == "Trevor Hairsine" and details["inker"] == "Trevor Hairsine"
    assert details["characters"] == "Judge Dredd, Judge Death"
    assert (details["year"], details["month"], details["day"]) == ("1996", "1", "26")
    con = sqlite3.connect(dest)
    assert con.execute(
        "select comicvine_issue_id, comicvine_volume_id, cover_width, cover_height from cr_issue where issue_id = ?",
        (candidate.issue_id,),
    ).fetchone() == (406658, 62253, 982, 1363)
    # Cover Artist sits on a separate GCD-style "cover" story.
    assert con.execute(
        """select cnd.name from gcd_story st join gcd_story_credit sc on sc.story_id = st.id
           join gcd_creator_name_detail cnd on cnd.id = sc.creator_id
           where st.issue_id = ? and st.type_id = 6""", (candidate.issue_id,)
    ).fetchall() == [("Trevor Hairsine",)]


def test_a_blank_number_finds_a_numbered_one_shot(library):
    _summary, dest = library
    db = GcdLocalDatabase(str(dest))
    # Judge Dredd: The Three Amigos is #1 in ComicRack; the filename had no number.
    assert [c.number for c in db.search("Judge Dredd The Three Amigos", "")] == ["1"]
    assert [c.number for c in db.search("Some Graphic Novel", "")] == ["[nn]"]


def test_artist_means_pencils_and_inks(library):
    _summary, dest = library
    db = GcdLocalDatabase(str(dest))
    details = db.details(db.search("Blake et Mortimer", "3")[0].issue_id).as_dict()
    assert details["penciller"] == details["inker"] == "Edgar P. Jacobs"


def test_reads_a_zipped_library_and_rejects_an_empty_one(tmp_path):
    with zipfile.ZipFile(tmp_path / "comicdb.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ComicDb.xml", XML)
    summary = cr.build_comicrack_database(str(tmp_path / "comicdb.zip"), str(tmp_path / "z.db"))
    assert summary.issues == 6
    (tmp_path / "empty.xml").write_text('<?xml version="1.0"?><ComicDatabase><Books /></ComicDatabase>')
    with pytest.raises(cr.DumpImportError, match="No books"):
        cr.build_comicrack_database(str(tmp_path / "empty.xml"), str(tmp_path / "e.db"))
    assert not (tmp_path / "e.db").exists()


def test_number_key():
    assert cr.number_key("#007") == cr.number_key("7") == "7"
    assert cr.number_key("0") == cr.number_key("000") == "0"
    assert cr.number_key("") == "[nn]"
    assert cr.number_key("1.MU") == "1.mu"


def test_settings_check_accepts_a_library_and_refuses_a_gcd_dump(library, tmp_path, monkeypatch):
    from gui import comicrack_settings_dialog as dialog_module

    _summary, dest = library
    assert dialog_module._check(str(dest)).startswith("Looks good: 5 series and 6 issues")
    gcd_like = tmp_path / "gcd.db"
    con = sqlite3.connect(gcd_like)
    for table, columns in cr.TABLES.items():
        if table.startswith("gcd_") or table.startswith("stddata"):
            con.execute(f"create table {table} ({', '.join(columns)})")
    con.commit()
    con.close()
    with pytest.raises(dialog_module.GcdLocalError, match="GCD dump"):
        dialog_module._check(str(gcd_like))


def test_build_from_comicrack_flow(tmp_path, monkeypatch):
    from gui import comicrack_settings_dialog as dialog_module

    source = tmp_path / "ComicDb.xml"
    source.write_text(XML, encoding="utf-8")
    dest = tmp_path / "out.db"
    monkeypatch.setattr(dialog_module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    monkeypatch.setattr(dialog_module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))
    shown = []
    monkeypatch.setattr(dialog_module.QMessageBox, "information", lambda *a: shown.append(a[2]))
    assert dialog_module.build_from_comicrack(None) == str(dest)
    assert dest.exists() and "8 books read" in shown[0]
