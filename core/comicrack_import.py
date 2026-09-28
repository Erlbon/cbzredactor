"""
core/comicrack_import.py

Converts a ComicRack (Community Edition) library -- its ComicDb.xml,
plain or zipped -- into a SQLite file with the SAME table layout as the
Grand Comics Database dump, so the local GCD lookup (core/gcd_local.py)
can search a user's own carefully tagged library exactly as it
searches GCD: Settings > ComicRack Library Database... > Build from
ComicRack Library..., then Import > Look Up via ComicRack Library...

Streaming, reading and writing are redactor_common's core/dump_import.py;
what's here is the mapping ("recipe"):

- ComicRack keeps one record per FILE; GCD has series -> issues ->
  stories -> credits. A series is Series + Volume + Publisher (names
  compared punctuation-insensitively); an issue is its series + Number.
  Several files of the same issue (duplicates, upgrades) become one
  issue, taken from the most completely tagged file; cr_issue.copies
  says how many there were.
- Credits and characters (comma-separated text in ComicRack) become
  GCD-style rows with made-up ids, one per distinct name. Cover Artist
  goes on a separate "cover" story (GCD's type 6), Editor on the issue,
  like GCD.
- No Number -> GCD's "[nn]"; Volume that's a year (Comic Vine's style,
  "Batman (2016)") -> the series' start year, otherwise the first issue's.
- ComicRack-only facts GCD's layout has no place for -- the Comic Vine
  link and ids, teams, locations, story arc, the cover scan's size --
  go in an extra table, cr_issue.
- Private things are never copied: file paths, file sizes, dates added
  or read, ratings, reading lists. The result describes comics, not a
  collection, so it can be shared (e.g. offered to GCD as a diff).

Two streaming passes over the XML (~8 s each for 234k books): the first
picks each issue's best file and gathers per-series facts, the second
writes the rows.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from redactor_common.core.dump_import import (
    DumpImportError,
    SqliteBuilder,
    iter_xml_records,
    open_dump,
    split_list,
)
from redactor_common.core.local_db import normalize_words

SOURCE_NAME = "ComicRack"
RECIPE = "comicrack-gcd/1"
NO_NUMBER = "[nn]"
COVER_STORY_TYPE_ID = 6  # GCD's gcd_story_type "cover"
COMIC_STORY_TYPE_ID = 19  # "comic story"
# GCD's own gcd_credit_type ids and names (verified on the 2026-09-15 dump).
CREDIT_TYPES = {1: "script", 2: "pencils", 3: "inks", 4: "colors", 5: "letters", 6: "editing"}
SCRIPT, PENCILS, INKS, COLORS, LETTERS, EDITING = 1, 2, 3, 4, 5, 6
# ComicRack element -> GCD credit types. "writer" (lower case) appears in
# ~1k real books, written by some older tool; "Artist" means pencils+inks.
_STORY_CREDITS = [
    ("Writer", (SCRIPT,)), ("writer", (SCRIPT,)),
    ("Penciller", (PENCILS,)), ("Inker", (INKS,)), ("Artist", (PENCILS, INKS)),
    ("Colorist", (COLORS,)), ("Letterer", (LETTERS,)),
]

TABLES = {
    "gcd_publisher": ["id integer primary key", "name text", "deleted integer"],
    "stddata_language": ["id integer primary key", "code text", "name text"],
    "gcd_series": [
        "id integer primary key", "name text", "year_began integer", "year_ended integer",
        "publisher_id integer", "language_id integer", "issue_count integer", "deleted integer",
    ],
    "gcd_issue": [
        "id integer primary key", "series_id integer", "number text", "key_date text", "title text",
        "editing text", "page_count integer", "isbn text", "barcode text", "variant_of_id integer",
        "deleted integer", "modified text",
    ],
    "gcd_story_type": ["id integer primary key", "name text"],
    "gcd_story": [
        "id integer primary key", "issue_id integer", "sequence_number integer", "type_id integer",
        "title text", "genre text", "characters text", "synopsis text", "script text", "pencils text",
        "inks text", "colors text", "letters text", "editing text", "deleted integer",
    ],
    "gcd_credit_type": ["id integer primary key", "name text"],
    "gcd_creator_name_detail": ["id integer primary key", "name text", "deleted integer"],
    "gcd_story_credit": [
        "id integer primary key", "story_id integer", "creator_id integer", "credit_type_id integer",
        "deleted integer",
    ],
    "gcd_issue_credit": [
        "id integer primary key", "issue_id integer", "creator_id integer", "credit_type_id integer",
        "deleted integer",
    ],
    "gcd_character_name_detail": ["id integer primary key", "name text", "deleted integer"],
    "gcd_story_character": ["id integer primary key", "story_id integer", "character_id integer", "deleted integer"],
    "cr_issue": [
        "issue_id integer primary key", "web text", "comicvine_issue_id integer", "comicvine_volume_id integer",
        "teams text", "locations text", "story_arc text", "alternate_series text", "alternate_number text",
        "series_count integer", "format text", "age_rating text", "manga text", "black_and_white text",
        "cover_width integer", "cover_height integer", "copies integer",
    ],
}

INDEXES = [
    "create index issue_series on gcd_issue(series_id)",
    "create index story_issue on gcd_story(issue_id)",
    "create index story_credit_story on gcd_story_credit(story_id)",
    "create index story_character_story on gcd_story_character(story_id)",
    "create index issue_credit_issue on gcd_issue_credit(issue_id)",
    "create index cr_issue_comicvine on cr_issue(comicvine_issue_id)",
]

_COMICVINE_ISSUE_URL = re.compile(r"comicvine\.(?:gamespot\.)?com/.*/4000-(\d+)")
_COMICVINE_NOTE = re.compile(r"\[CVDB(\d+)\]")


def _text(book, tag: str) -> str:
    return (book.findtext(tag) or "").strip()


def number_key(number: str) -> str:
    """How issue numbers compare: "#007" == "7", "" is GCD's "[nn]"."""
    text = (number or "").strip().lstrip("#").casefold()
    if not text:
        return NO_NUMBER
    stripped = text.lstrip("0")
    return stripped if stripped else "0"


def series_key(book) -> Optional[tuple[str, str, str]]:
    name = normalize_words(_text(book, "Series"))
    if not name:
        return None
    return (name, _text(book, "Volume"), normalize_words(_text(book, "Publisher")))


def _year(text: str) -> Optional[int]:
    return int(text) if text.isdigit() and 1800 <= int(text) <= 2100 else None


def _key_date(book) -> str:
    year = _year(_text(book, "Year"))
    if year is None:
        return ""
    parts = [year]
    for tag in ("Month", "Day"):
        value = _text(book, tag)
        parts.append(int(value) if value.isdigit() and 0 < int(value) <= 31 else 0)
    return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"


def _completeness(book) -> int:
    """How many fields a file has filled in -- the best-tagged copy of an
    issue wins."""
    return sum(1 for child in book if (child.text or "").strip() or len(child))


def _comicvine_ids(book) -> tuple[Optional[int], Optional[int]]:
    issue = volume = None
    match = _COMICVINE_ISSUE_URL.search(_text(book, "Web")) or _COMICVINE_NOTE.search(_text(book, "Notes"))
    if match:
        issue = int(match.group(1))
    for pair in _text(book, "CustomValuesStore").split(","):
        key, _, value = pair.partition("=")
        if value.strip().isdigit():
            if key.strip() == "comicvine_issue" and issue is None:
                issue = int(value)
            elif key.strip() == "comicvine_volume":
                volume = int(value)
    return issue, volume


def _cover_size(book) -> tuple[Optional[int], Optional[int]]:
    pages = book.find("Pages")
    if pages is None or not len(pages):
        return None, None
    cover = next((p for p in pages if p.get("Type") == "FrontCover"), pages[0])
    width, height = cover.get("ImageWidth", ""), cover.get("ImageHeight", "")
    return (int(width) if width.isdigit() else None, int(height) if height.isdigit() else None)


@dataclass
class _Series:
    id: int
    name: str
    publisher: str
    volume: str
    years: list[int] = field(default_factory=list)
    languages: Counter = field(default_factory=Counter)
    issues: set = field(default_factory=set)


@dataclass
class ImportSummary:
    books: int = 0
    skipped_no_series: int = 0
    series: int = 0
    issues: int = 0
    merged_copies: int = 0
    rows: dict = field(default_factory=dict)

    def describe(self) -> str:
        text = (
            f"{self.books:,} books read: {self.series:,} series, {self.issues:,} issues"
            f" ({self.merged_copies:,} extra copies of the same issue merged)."
        )
        if self.skipped_no_series:
            text += f" {self.skipped_no_series:,} books without a Series were left out."
        return text


class _Ids:
    """Made-up ids for distinct names (creators, characters, publishers),
    first spelling kept; names compare case-insensitively."""

    def __init__(self):
        self.ids: dict[str, int] = {}
        self.names: list[str] = []

    def get(self, name: str) -> int:
        key = name.casefold()
        found = self.ids.get(key)
        if found is None:
            self.names.append(name)
            found = self.ids[key] = len(self.names)
        return found


def build_comicrack_database(
    source: str,
    dest: str,
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> ImportSummary:
    """Reads the ComicRack library at `source` (ComicDb.xml, or a .zip /
    .gz of it) and writes the GCD-layout database to `dest`, replacing
    any previous build only once this one has succeeded."""
    report = (lambda base: (lambda f: progress(base + f / 2))) if progress else (lambda base: None)
    summary = ImportSummary()
    series: dict[tuple, _Series] = {}
    best: dict[tuple, tuple[int, str]] = {}  # issue key -> (completeness, book id)
    copies: Counter = Counter()

    # Pass 1: which file speaks for each issue; per-series facts.
    with open_dump(source, member_suffix=".xml") as dump:
        for book in iter_xml_records(dump, "Book", report(0.0), cancelled):
            summary.books += 1
            skey = series_key(book)
            if skey is None:
                summary.skipped_no_series += 1
                continue
            entry = series.get(skey)
            if entry is None:
                entry = series[skey] = _Series(
                    len(series) + 1, _text(book, "Series"), _text(book, "Publisher"), _text(book, "Volume")
                )
            ikey = (skey, number_key(_text(book, "Number")))
            entry.issues.add(ikey[1])
            year = _year(_text(book, "Year"))
            if year:
                entry.years.append(year)
            language = _text(book, "LanguageISO").casefold()
            if language:
                entry.languages[language] += 1
            copies[ikey] += 1
            score = (_completeness(book), book.get("Id") or "")
            if ikey not in best or score[0] > best[ikey][0]:
                best[ikey] = score
    if summary.books == 0:
        raise DumpImportError("No books found -- is this a ComicRack ComicDb.xml?")

    chosen = {book_id: ikey for ikey, (_score, book_id) in best.items()}
    publishers, languages, creators, characters = _Ids(), _Ids(), _Ids(), _Ids()
    with SqliteBuilder(dest, TABLES, INDEXES) as out:
        for type_id, name in ((COVER_STORY_TYPE_ID, "cover"), (COMIC_STORY_TYPE_ID, "comic story")):
            out.add("gcd_story_type", (type_id, name))
        for type_id, name in CREDIT_TYPES.items():
            out.add("gcd_credit_type", (type_id, name))
        for entry in series.values():
            language = entry.languages.most_common(1)[0][0] if entry.languages else ""
            began = _year(entry.volume) or (min(entry.years) if entry.years else None)
            out.add("gcd_series", (
                entry.id, entry.name, began, max(entry.years) if entry.years else None,
                publishers.get(entry.publisher) if entry.publisher else None,
                languages.get(language) if language else None, len(entry.issues), 0,
            ))

        # Pass 2: the rows for each issue's chosen file.
        issue_id = story_id = credit_id = issue_credit_id = appearance_id = 0
        with open_dump(source, member_suffix=".xml") as dump:
            for book in iter_xml_records(dump, "Book", report(0.5), cancelled):
                ikey = chosen.get(book.get("Id") or "")
                if ikey is None or series_key(book) != ikey[0]:
                    continue
                issue_id += 1
                number = _text(book, "Number") or NO_NUMBER
                page_count = _text(book, "PageCount")
                out.add("gcd_issue", (
                    issue_id, series[ikey[0]].id, number, _key_date(book), "", "",
                    int(page_count) if page_count.isdigit() else None,
                    _text(book, "ISBN"), _text(book, "GTIN"), None, 0, "",
                ))

                story_id += 1
                story_names = split_list(_text(book, "Characters"))
                out.add("gcd_story", (
                    story_id, issue_id, 1, COMIC_STORY_TYPE_ID, _text(book, "Title"),
                    "; ".join(split_list(_text(book, "Genre"))), "", _text(book, "Summary"),
                    "", "", "", "", "", "", 0,
                ))
                for tag, roles in _STORY_CREDITS:
                    for name in split_list(_text(book, tag)):
                        for role in roles:
                            credit_id += 1
                            out.add("gcd_story_credit", (credit_id, story_id, creators.get(name), role, 0))
                for name in story_names:
                    appearance_id += 1
                    out.add("gcd_story_character", (appearance_id, story_id, characters.get(name), 0))
                cover_artists = split_list(_text(book, "CoverArtist"))
                if cover_artists:
                    story_id += 1
                    out.add("gcd_story", (
                        story_id, issue_id, 0, COVER_STORY_TYPE_ID, "", "", "", "", "", "", "", "", "", "", 0,
                    ))
                    for name in cover_artists:
                        credit_id += 1
                        out.add("gcd_story_credit", (credit_id, story_id, creators.get(name), PENCILS, 0))
                for name in split_list(_text(book, "Editor")):
                    issue_credit_id += 1
                    out.add("gcd_issue_credit", (issue_credit_id, issue_id, creators.get(name), EDITING, 0))

                cv_issue, cv_volume = _comicvine_ids(book)
                width, height = _cover_size(book)
                count = _text(book, "Count")
                out.add("cr_issue", (
                    issue_id, _text(book, "Web"), cv_issue, cv_volume, _text(book, "Teams"),
                    _text(book, "Locations"), _text(book, "StoryArc"), _text(book, "AlternateSeries"),
                    _text(book, "AlternateNumber"), int(count) if count.isdigit() else None,
                    _text(book, "Format"), _text(book, "AgeRating"), _text(book, "Manga"),
                    _text(book, "BlackAndWhite"), width, height, copies[ikey],
                ))

        for ids, table in ((publishers, "gcd_publisher"), (creators, "gcd_creator_name_detail"),
                           (characters, "gcd_character_name_detail")):
            for number_id, name in enumerate(ids.names, start=1):
                out.add(table, (number_id, name, 0))
        for number_id, code in enumerate(languages.names, start=1):
            out.add("stddata_language", (number_id, code, code))

        summary.series = len(series)
        summary.issues = issue_id
        summary.merged_copies = sum(copies.values()) - len(copies)
        summary.rows = out.finish({
            "source": SOURCE_NAME, "recipe": RECIPE, "books": summary.books,
            "skipped_no_series": summary.skipped_no_series,
        })
    return summary


def main(argv: list[str]) -> int:
    """python -m core.comicrack_import ComicDb.xml library.db"""
    if len(argv) != 2:
        print("usage: python -m core.comicrack_import <ComicDb.xml or .zip> <output.db>", file=sys.stderr)
        return 2
    last = [-1]

    def show(fraction: float) -> None:
        percent = int(fraction * 100)
        if percent != last[0]:
            last[0] = percent
            print(f"\r{percent:3d}%", end="", file=sys.stderr, flush=True)

    summary = build_comicrack_database(argv[0], argv[1], show)
    print(f"\n{summary.describe()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
