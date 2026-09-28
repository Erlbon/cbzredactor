"""Tests for core/gcd_local.py (a local GCD SQLite dump), against a tiny
database with the same table layout as GCD's dump -- CI has no 7 GB
file. Covers what the real 2026-09-15 dump taught: ":"/" - " naming,
fuller GCD titles, "[nn]" one-shots, start-year and English
preference, structured vs. free-text credits, and characters linking to
gcd_character_name_detail. The last test runs against a real dump when
one is present on this machine."""

import os
import sqlite3

import pytest

from core.gcd_local import (
    NO_NUMBER,
    GcdLocalDatabase,
    GcdLocalError,
    normalize_name,
)

SCHEMA = """
create table gcd_publisher (id integer primary key, name text);
create table stddata_language (id integer primary key, code text);
create table gcd_series (id integer primary key, name text, year_began int, issue_count int,
    publisher_id int, language_id int, deleted int default 0);
create table gcd_issue (id integer primary key, number text, series_id int, key_date text,
    title text default '', editing text default '', deleted int default 0,
    variant_of_id int, modified text default '2026-09-15');
create table gcd_story (id integer primary key, issue_id int, type_id int, deleted int default 0,
    sequence_number int default 0, title text default '', genre text default '',
    characters text default '', synopsis text default '', script text default '',
    pencils text default '', inks text default '', colors text default '',
    letters text default '', editing text default '');
create table gcd_credit_type (id integer primary key, name text);
create table gcd_creator_name_detail (id integer primary key, name text);
create table gcd_story_credit (id integer primary key, story_id int, credit_type_id int,
    creator_id int, deleted int default 0);
create table gcd_issue_credit (id integer primary key, issue_id int, credit_type_id int,
    creator_id int, deleted int default 0);
create table gcd_character_name_detail (id integer primary key, name text);
create table gcd_story_character (id integer primary key, story_id int, character_id int,
    deleted int default 0);
"""


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "gcd.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("insert into gcd_publisher values (?,?)", [(1, "DC"), (2, "Image"), (3, "Panini"), (4, "Marvel")])
    con.executemany("insert into stddata_language values (?,?)", [(25, "en"), (30, "de")])
    con.executemany(
        "insert into gcd_series (id,name,year_began,issue_count,publisher_id,language_id) values (?,?,?,?,?,?)",
        [
            (10, "Batman", 1940, 700, 1, 25),
            (11, "Batman", 2016, 163, 1, 25),
            (12, "Batman", 2017, 90, 3, 30),  # German reprint
            (20, "G.I. Joe: A Real American Hero", 2023, 32, 2, 25),
            (30, "Marvel Mangaverse: Ghostlocke", 2026, 1, 4, 25),
        ],
    )
    con.executemany(
        "insert into gcd_issue (id,number,series_id,key_date,variant_of_id) values (?,?,?,?,?)",
        [
            (100, "45", 10, "1948-02-00", None),
            (101, "45", 11, "2018-06-00", None),
            (102, "45", 12, "2021-01-00", None),
            (103, "45", 11, "2018-06-00", 101),  # variant cover record -- skipped
            (200, "332", 20, "2026-09-09", None),
            (300, NO_NUMBER, 30, "2026-00-00", None),
        ],
    )
    con.executemany("insert into gcd_credit_type values (?,?)", [(1, "script"), (7, "pencils and inks"), (6, "editing")])
    con.executemany("insert into gcd_creator_name_detail values (?,?)", [(1, "Tom King"), (2, "Mikel Janin"), (3, "Jamie S. Rich")])
    con.executemany(
        "insert into gcd_story (id,issue_id,type_id,title,genre,characters,script,pencils) values (?,?,?,?,?,?,?,?)",
        [
            (1000, 101, 19, "Everyone Loves Ivy", "superhero", "", "", ""),
            (1001, 101, 6, "cover", "", "", "", ""),  # a cover "story" -- not content
            (2000, 200, 19, "", "military", "Deep Six [Malcolm R. Willoughby]", "Larry Hama", "Pat Olliffe"),
        ],
    )
    con.executemany(
        "insert into gcd_story_credit (id,story_id,credit_type_id,creator_id) values (?,?,?,?)",
        [(1, 1000, 1, 1), (2, 1000, 7, 2)],
    )
    con.execute("insert into gcd_issue_credit (id,issue_id,credit_type_id,creator_id) values (1,101,6,3)")
    con.execute("insert into gcd_character_name_detail values (1, 'Batman')")
    con.execute("insert into gcd_story_character (id,story_id,character_id) values (1,1000,1)")
    con.commit()
    con.close()
    return GcdLocalDatabase(str(path))


def test_normalize_name_ignores_punctuation():
    assert normalize_name("G.I. Joe - A Real American Hero") == normalize_name("G.I. Joe: A Real American Hero")
    assert normalize_name("Batman & Robin") == normalize_name("Batman and Robin")


def test_scene_dash_name_finds_the_colon_title(db):
    found = db.search("G.I. Joe - A Real American Hero", "332", "2026")
    assert [c.issue_id for c in found] == [200]
    assert found[0].exact_name


def test_all_words_match_finds_a_fuller_gcd_title_and_nn_one_shots(db):
    found = db.search("Mangaverse - Ghostlocke", "1")
    assert found[0].issue_id == 300
    assert "(one-shot)" in found[0].display_label()
    assert db.search("Mangaverse - Ghostlocke", "")[0].issue_id == 300
    assert "number" not in db.details(300).as_dict()  # "[nn]" is never written to ComicInfo


def test_series_start_year_picks_the_right_same_named_series(db):
    assert db.search("Batman", "045", series_year="2016")[0].issue_id == 101
    assert db.search("Batman", "45", year="1948")[0].issue_id == 100


def test_english_edition_beats_a_translated_reprint(db):
    # No year to go by: language decides between same-named series. (A
    # year, when given, rightly counts more -- see the test above.)
    found = db.search("Batman", "45")
    ids = [c.issue_id for c in found]
    assert ids[-1] == 102  # the German reprint comes last
    assert 103 not in ids  # variant-cover record skipped


def test_details_from_structured_credits(db):
    fields = db.details(101).as_dict()
    assert fields["writer"] == "Tom King"
    assert fields["penciller"] == "Mikel Janin" and fields["inker"] == "Mikel Janin"  # "pencils and inks"
    assert fields["editor"] == "Jamie S. Rich"  # issue-level credit
    assert fields["characters"] == "Batman"  # via gcd_character_name_detail
    assert fields["title"] == "Everyone Loves Ivy"  # the cover "story" isn't content
    assert (fields["year"], fields["month"]) == ("2018", "6")
    assert fields["publisher"] == "DC" and fields["language_iso"] == "en"
    assert fields["web"] == "https://www.comics.org/issue/101/"


def test_details_fall_back_to_free_text_credits(db):
    fields = db.details(200).as_dict()
    assert fields["writer"] == "Larry Hama"
    assert fields["penciller"] == "Pat Olliffe"
    assert fields["characters"] == "Deep Six"  # "[real name]" stripped
    assert fields["genre"] == "military"


def test_not_a_gcd_file_is_a_clear_error(tmp_path):
    with pytest.raises(GcdLocalError, match="not found"):
        GcdLocalDatabase(str(tmp_path / "missing.db"))
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("create table t (x)").connection.close()
    with pytest.raises(GcdLocalError, match="doesn't look like a GCD"):
        GcdLocalDatabase(str(other))


REAL_DUMP = r"C:\Dev\gcd\2026-09-15.db"


@pytest.mark.skipif(not os.path.isfile(REAL_DUMP), reason="real GCD dump not on this machine")
def test_real_dump_smoke():
    real = GcdLocalDatabase(REAL_DUMP)
    found = real.search("Batman", "45", series_year="2016")
    assert found[0].series_name == "Batman" and found[0].series_year == 2016
    fields = real.details(found[0].issue_id).as_dict()
    assert "Tom King" in fields["writer"]
