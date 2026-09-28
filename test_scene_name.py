"""Tests for core/scene_name.py and core/scene_tags.py -- the scene-style
filename parser. The first block is real incoming filenames from the
maintainer's own ingestions (2026-09-28), all run through CbxConverter,
hence the ".webp.cbz" ending."""

import pytest

from core import scene_tags
from core.scene_name import clean_name, parse_filename, proposed_fields, strip_extensions


# (filename, series, number, volume, title, year, chapter, scan_information)
REAL_SAMPLES = [
    ("C K.O. - Titans (2026) (digital) (Son of Ultron-Empire).webp.cbz",
     "C K.O. - Titans", "", "", "", "2026", "", "Digital, Son of Ultron-Empire"),
    ("Empress Death (Chapter 01) - Epitaph for Humanity 001 (2026) (Digital) (DR & Quinch-Empire).webp.cbz",
     "Empress Death - Epitaph for Humanity", "1", "", "", "2026", "1", "Digital, DR & Quinch-Empire"),
    ("G.I. Joe - A Real American Hero 332 (2026) (Digital) (Mephisto-Empire).webp.cbz",
     "G.I. Joe - A Real American Hero", "332", "", "", "2026", "", "Digital, Mephisto-Empire"),
    ("Ghost Pepper 015 (2026) (Digital) (Zone-Empire).webp.cbz",
     "Ghost Pepper", "15", "", "", "2026", "", "Digital, Zone-Empire"),
    ("Havana Split v01 - Welcome to Cuba (2026) (digital) (Knight Ripper-Empire).webp.cbz",
     "Havana Split", "", "1", "Welcome to Cuba", "2026", "", "Digital, Knight Ripper-Empire"),
    ("It's Symbie 001 (2026) (digital) (Marika-Empire).webp.cbz",
     "It's Symbie", "1", "", "", "2026", "", "Digital, Marika-Empire"),
    ("Lady Death (Chapter 23) - Primordial Havoc 001 (2026) (Digital) (DR & Quinch-Empire).webp.cbz",
     "Lady Death - Primordial Havoc", "1", "", "", "2026", "23", "Digital, DR & Quinch-Empire"),
    ("Lady Death - Pirate Queen 001 (2026) (Digital) (DR & Quinch-Empire).webp.cbz",
     "Lady Death - Pirate Queen", "1", "", "", "2026", "", "Digital, DR & Quinch-Empire"),
    ("M1 - Monster Racing League 004 (2026) (Digital) (Zone-Empire).webp.cbz",
     "M1 - Monster Racing League", "4", "", "", "2026", "", "Digital, Zone-Empire"),
    ("Mangaverse- Ghostlocke 01 (2026).webp.cbz",
     "Mangaverse - Ghostlocke", "1", "", "", "2026", "", ""),
]


@pytest.mark.parametrize("name, series, number, volume, title, year, chapter, scan", REAL_SAMPLES)
def test_real_ingestion_names(name, series, number, volume, title, year, chapter, scan):
    parsed = parse_filename(name)
    assert (parsed.series, parsed.number, parsed.volume, parsed.title, parsed.year, parsed.chapter) == (
        series, number, volume, title, year, chapter
    )
    assert parsed.scan_information == scan
    assert parsed.unknown == []


def test_converter_image_marker_is_stripped():
    assert strip_extensions("Name (2026) (Zone-Empire).webp.cbz") == "Name (2026) (Zone-Empire)"
    assert strip_extensions("Name.jpg.webp.cbr") == "Name"
    assert strip_extensions("Name Vol. 2.cbz") == "Name Vol. 2"


def test_year_before_the_number_is_the_series_start_year():
    parsed = parse_filename("Batman (2016) 045 (2018) (Digital) (Zone-Empire).cbz")
    assert (parsed.series, parsed.number, parsed.volume, parsed.year) == ("Batman", "45", "2016", "2018")


def test_x_of_y_gives_number_and_count():
    for name in ("Watchmen 03 of 12 (1986).cbz", "Watchmen (3 of 12) (1986).cbz", "Watchmen 3of12.cbz"):
        parsed = parse_filename(name)
        assert (parsed.series, parsed.number, parsed.count) == ("Watchmen", "3", "12"), name


def test_issue_title_after_the_number():
    parsed = parse_filename("The Walking Dead 100 - Something to Fear (2012).cbz")
    assert (parsed.series, parsed.number, parsed.title) == ("The Walking Dead", "100", "Something to Fear")


def test_leading_number_in_series_name():
    parsed = parse_filename("2000 AD 1234 (2021) (Digital).cbz")
    assert (parsed.series, parsed.number) == ("2000 AD", "1234")


def test_tags_are_sorted_into_the_right_places():
    parsed = parse_filename("Saga v01 (2012) (TPB) (c2c) (2 covers) (Charlie Adlard) (Mystery Group).cbz")
    assert parsed.formats == ["TPB"]
    assert parsed.scan_info == ["c2c"]
    assert parsed.notes == ["2 covers"]
    assert parsed.hints == ["Charlie Adlard"]
    assert parsed.unknown == ["Mystery Group"]


def test_title_words_are_never_treated_as_tags():
    # "Empire" and "Madness" are scan tags in brackets, but real words here.
    parsed = parse_filename("Star Wars - Empire 012 (Madness).cbz")
    assert parsed.series == "Star Wars - Empire"
    assert parsed.scan_info == ["Madness"]


def test_clean_name_rules():
    assert clean_name("Amazing%20Spider-Man%20%23005") == "Amazing Spider-Man #005"
    assert clean_name("Some+Comic+012") == "Some Comic 012"
    assert clean_name("Some_Comic_012_[Digital]_{Zone-Empire}") == "Some Comic 012 (Digital) (Zone-Empire)"
    assert clean_name("Name  (  Digital )  ()") == "Name (Digital)"


def test_unbracketed_plus_is_kept_when_there_are_spaces():
    assert clean_name("Batman + Robin 001") == "Batman + Robin 001"


def test_proposed_fields_append_notes_and_skip_blanks():
    parsed = parse_filename("Saga 001 (2012) (TPB) (missing ifc) (Zone-Empire).cbz")
    fields = proposed_fields(parsed, current_notes="Bought at a con")
    assert fields["format"] == "TPB"
    assert fields["scan_information"] == "Zone-Empire"
    assert fields["notes"] == "Bought at a con\nmissing ifc"
    assert "title" not in fields and "count" not in fields
    # Already noted: not appended twice.
    assert "notes" not in proposed_fields(parsed, current_notes="missing ifc")


# ---------------------------------------------------------------------------
# core/scene_tags.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase, category", [
    ("Zone-Empire", scene_tags.SCAN),
    ("Son of Ultron-Empire", scene_tags.SCAN),  # pattern: a group never listed would still match
    ("Brand New Group-Empire", scene_tags.SCAN),
    ("darkmark-dcp", scene_tags.SCAN),
    ("Minutemen-Whoever", scene_tags.SCAN),
    ("Digital-HD", scene_tags.SCAN),
    ("digtial", scene_tags.SCAN),
    ("1920px", scene_tags.SCAN),
    ("c2c", scene_tags.SCAN),
    ("pmack", scene_tags.SCAN),  # irregular: from the built-in list
    ("by roy", scene_tags.SCAN),
    ("digital tpb", scene_tags.FORMAT),
    ("One Shot", scene_tags.FORMAT),
    ("3 covers", scene_tags.NOTE),
    ("variant cover only", scene_tags.NOTE),
    ("missing ifc, ibc", scene_tags.NOTE),
    ("cronology 00247", scene_tags.ORDER),
    ("Charlie Adlard", scene_tags.HINT),
    ("Magnetic Press", scene_tags.HINT),
    ("Complete", None),
])
def test_classify(phrase, category):
    assert scene_tags.classify(phrase) == category


def test_user_additions_win_over_the_built_in_list():
    assert scene_tags.classify("pmack", extra={"pmack": scene_tags.HINT}) == scene_tags.HINT
    assert scene_tags.classify("Complete", extra={"complete": scene_tags.NOTE}) == scene_tags.NOTE


def test_canonical_formats():
    assert scene_tags.canonical_format("digital tpb") == "TPB"
    assert scene_tags.canonical_format("hard cover") == "Hardcover"
    assert scene_tags.canonical_format("gn-1600") == "Graphic Novel"


def test_built_in_list_has_no_junk():
    from core.scene_tag_list import SCENE_TAGS

    for phrase in SCENE_TAGS:
        assert phrase == scene_tags.normalize(phrase)
        assert not phrase.endswith((".cbr", ".cbz"))
        assert len(phrase) > 2
