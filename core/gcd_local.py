"""
core/gcd_local.py

Looks issues up in a LOCAL copy of the Grand Comics Database -- the
SQLite data dump GCD publishes for registered users
(https://www.comics.org/download/). The user downloads it themselves
and points the app at the file (Settings > GCD Local Database...);
it is never bundled or redistributed.

Compared with GCD's online API (core/gcd_lookup.py): milliseconds per
lookup instead of seconds, no hourly limit, works offline, and credits
come as structured per-creator rows instead of free text. No cover
images (the dump holds none), so the "Found" cover stays empty.

Two things make it fast without ever writing to the user's file:

1. Series names are matched through an in-memory full-text index
   (SQLite FTS5) of every series' normalized name, built on first use
   in about a second and a half and kept for the rest of the session.
   Matching is "all words present", so a scene name still finds GCD's
   fuller title: "Mangaverse - Ghostlocke" -> "Marvel Mangaverse:
   Ghostlocke", "M1 - Monster Racing League" -> "M1 Monster Racing
   League". Punctuation is ignored, so " - " and ":" match alike.
2. Queries steer SQLite to the right index explicitly. The dump ships
   without SQLite's index statistics, and on its own SQLite picked the
   story-credit index on "deleted" (useless, every row matches) over
   the one on story_id: 35 seconds per credits query instead of 8 ms.
   The "+column" form (unary plus) keeps a column out of index
   selection, so only the selective index is left to use.

Schema notes (verified against the 2026-09-15 dump): gcd_series ->
gcd_issue (series_id) -> gcd_story (issue_id; type_id 19 = "comic
story") -> gcd_story_credit (story_id; creator_id is a
gcd_creator_name_detail id; credit_type_id -> gcd_credit_type, which
includes combined roles such as "pencils and inks"). Older stories
often have no credit rows, only free-text fields on gcd_story
(script, pencils, ...) -- used as a fallback.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Optional

from core.gcd_lookup import (
    GcdIssueDetails,
    _split_credit_names,
    _split_key_date,
    _split_semicolon_list,
)

GCD_DOWNLOAD_URL = "https://www.comics.org/download/"
# GCD's issue "number" for an unnumbered issue, e.g. most one-shots.
NO_NUMBER = "[nn]"
COMIC_STORY_TYPE_ID = 19  # gcd_story_type "comic story"
ISSUE_PAGE_URL = "https://www.comics.org/issue/{}/"

_REQUIRED_TABLES = {"gcd_series", "gcd_issue", "gcd_story", "gcd_story_credit", "gcd_publisher"}

# gcd_credit_type names (some combined) -> ComicInfo credit fields.
_ROLE_WORDS = [
    ("script", "writer"),
    ("pencils", "penciller"),
    ("inks", "inker"),
    ("colors", "colorist"),
    ("letters", "letterer"),
    ("editing", "editor"),
]
# Painted art covers what pencils, inks and colors would.
_PAINTING_FIELDS = ("penciller", "inker", "colorist")


class GcdLocalError(Exception):
    """The local GCD database file is missing, unreadable or not a GCD dump."""


def normalize_name(name: str) -> str:
    """Lower-case words only: punctuation and "&"/"and" differences
    removed, so "G.I. Joe - A Real American Hero" and "G.I. Joe: A Real
    American Hero" compare equal."""
    text = (name or "").casefold().replace("&", " and ")
    return re.sub(r"[^0-9a-z]+", " ", text).strip()


def _normalize_number(number: str) -> str:
    text = (number or "").strip().lstrip("#")
    stripped = text.lstrip("0")
    return stripped if stripped else ("0" if text else "")


@dataclass
class LocalCandidate:
    issue_id: int
    series_id: int
    series_name: str
    series_year: Optional[int]
    issue_count: int
    publisher: str
    number: str
    key_date: str
    exact_name: bool = False
    language: str = ""

    def display_label(self) -> str:
        bits = [self.series_name]
        if self.series_year:
            bits[0] += f" ({self.series_year})"
        bits.append("(one-shot)" if self.number == NO_NUMBER else f"#{self.number}")
        if self.language and self.language != "en":
            bits.append(f"[{self.language}]")
        if self.key_date:
            bits.append(self.key_date[:7].replace("-00", ""))
        if self.publisher:
            bits.append(f"-- {self.publisher}")
        return " ".join(bits)


class GcdLocalDatabase:
    """One opened dump. Thread-safe for this app's use: lookups run on
    worker threads (redactor_common's call_in_background), one at a
    time, so a single connection guarded by a lock is enough."""

    def __init__(self, path: str):
        if not path or not os.path.isfile(path):
            raise GcdLocalError(f"GCD database file not found: {path or '(not set)'}")
        self.path = path
        try:
            self._con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            tables = {r[0] for r in self._con.execute("select name from sqlite_master where type='table'")}
        except sqlite3.DatabaseError as exc:
            raise GcdLocalError(f"Not a readable SQLite database: {exc}") from exc
        missing = _REQUIRED_TABLES - tables
        if missing:
            raise GcdLocalError(
                "This doesn't look like a GCD SQLite dump (missing tables: " + ", ".join(sorted(missing)) + ")."
            )
        self._lock = threading.Lock()
        self._names: Optional[sqlite3.Connection] = None

    def close(self) -> None:
        self._con.close()
        if self._names is not None:
            self._names.close()

    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Counts and the newest change date, for the Settings dialog."""
        with self._lock:
            series = self._con.execute("select count(*) from gcd_series where deleted = 0").fetchone()[0]
            issues = self._con.execute("select count(*) from gcd_issue where +deleted = 0").fetchone()[0]
            newest = self._con.execute("select max(modified) from gcd_issue").fetchone()[0] or ""
        return {"series": series, "issues": issues, "newest": str(newest)[:10]}

    def _name_index(self) -> sqlite3.Connection:
        """In-memory FTS5 index of every live series' normalized name,
        built once per session (about 1.5 s for ~230k series)."""
        if self._names is None:
            names = sqlite3.connect(":memory:", check_same_thread=False)
            names.execute("create virtual table names using fts5(norm, series_id unindexed, tokenize='unicode61')")
            names.executemany(
                "insert into names(norm, series_id) values (?, ?)",
                ((normalize_name(name), sid) for sid, name in self._con.execute(
                    "select id, name from gcd_series where deleted = 0"
                )),
            )
            names.commit()
            self._names = names
        return self._names

    def prepare(self) -> None:
        """Builds the name index now (e.g. behind a progress dialog)
        instead of on the first search."""
        with self._lock:
            self._name_index()

    # ------------------------------------------------------------------

    def search(
        self, series: str, number: str, year: str = "", series_year: str = "", limit: int = 8
    ) -> list[LocalCandidate]:
        """Issues numbered `number` in every series whose name contains
        all of `series`'s words. A blank `number` or "1" also matches an
        unnumbered issue (GCD's "[nn]", typical for one-shots).

        Ranked by: exact series name; the series' start year matching
        `series_year` ("Batman (2016)"); the issue's year closest to
        `year`; English editions ahead of translated reprints of the
        same title; numbered ahead of "[nn]"; then the series with more
        issues. Variant-cover records are skipped."""
        words = normalize_name(series).split()
        wanted = _normalize_number(number)
        if not words:
            return []
        numbers = [wanted, *(wanted.zfill(width) for width in (2, 3, 4))] if wanted else []
        if wanted in ("", "1"):
            numbers.append(NO_NUMBER)
        numbers = list(dict.fromkeys(numbers))
        query = " ".join(f'"{w}"' for w in words)
        with self._lock:
            # No LIMIT: a common word like "Batman" matches thousands of
            # series, and the right one must not be cut off.
            series_ids = [r[0] for r in self._name_index().execute(
                "select series_id from names where names match ?", (query,)
            )]
            rows = []
            for start in range(0, len(series_ids), 5000):  # stay under SQLite's variable limit
                chunk = series_ids[start:start + 5000]
                rows += self._con.execute(
                    f"""select i.id, s.id, s.name, s.year_began, s.issue_count, p.name, i.number, i.key_date, l.code
                        from gcd_issue i
                        join gcd_series s on s.id = i.series_id
                        left join gcd_publisher p on p.id = s.publisher_id
                        left join stddata_language l on l.id = s.language_id
                        where i.series_id in ({",".join("?" * len(chunk))})
                          and +i.number in ({",".join("?" * len(numbers))})
                          and +i.deleted = 0 and i.variant_of_id is null""",
                    [*chunk, *numbers],
                ).fetchall()

        wanted_name = normalize_name(series)
        candidates = [
            LocalCandidate(
                issue_id=r[0], series_id=r[1], series_name=r[2] or "", series_year=r[3],
                issue_count=r[4] or 0, publisher=r[5] or "", number=r[6] or "", key_date=r[7] or "",
                exact_name=normalize_name(r[2]) == wanted_name, language=r[8] or "",
            )
            for r in rows
        ]

        def gap(a, b) -> int:
            return abs(int(a) - int(b)) if str(a).isdigit() and str(b).isdigit() else 9999

        def rank(c: LocalCandidate):
            return (
                not c.exact_name,
                gap(c.series_year, series_year) if series_year else 0,
                gap(c.key_date[:4], year) if year else 0,
                c.language not in ("en", ""),
                c.number == NO_NUMBER,
                -c.issue_count,
            )

        return sorted(candidates, key=rank)[:limit]

    # ------------------------------------------------------------------

    def details(self, issue_id: int) -> GcdIssueDetails:
        """ComicInfo-shaped details for one issue (same shape as the
        online lookup's, so the GUI treats both alike)."""
        with self._lock:
            issue = self._con.execute(
                """select i.number, i.key_date, i.title, i.editing, s.name, p.name, l.code
                   from gcd_issue i join gcd_series s on s.id = i.series_id
                   left join gcd_publisher p on p.id = s.publisher_id
                   left join stddata_language l on l.id = s.language_id
                   where i.id = ?""",
                (issue_id,),
            ).fetchone()
            if issue is None:
                raise GcdLocalError(f"Issue {issue_id} not found in the local database.")
            stories = self._con.execute(
                """select st.id, st.title, st.genre, st.characters, st.synopsis,
                          st.script, st.pencils, st.inks, st.colors, st.letters, st.editing
                   from gcd_story st
                   where st.issue_id = ? and +st.type_id = ? and +st.deleted = 0
                   order by st.sequence_number""",
                (issue_id, COMIC_STORY_TYPE_ID),
            ).fetchall()
            story_ids = [s[0] for s in stories]
            credit_rows = []
            character_rows = []
            if story_ids:
                marks = ",".join("?" * len(story_ids))
                credit_rows = self._con.execute(
                    f"""select sc.story_id, ct.name, cnd.name
                        from gcd_story_credit sc
                        join gcd_credit_type ct on ct.id = sc.credit_type_id
                        join gcd_creator_name_detail cnd on cnd.id = sc.creator_id
                        where sc.story_id in ({marks}) and +sc.deleted = 0
                        order by sc.id""",
                    story_ids,
                ).fetchall()
                character_rows = self._con.execute(
                    # character_id points at gcd_character_name_detail
                    # (the name as used in that story), not gcd_character.
                    f"""select distinct c.name
                        from gcd_story_character sch join gcd_character_name_detail c on c.id = sch.character_id
                        where sch.story_id in ({marks}) and +sch.deleted = 0""",
                    story_ids,
                ).fetchall()
            issue_editors = self._con.execute(
                """select cnd.name from gcd_issue_credit ic
                   join gcd_credit_type ct on ct.id = ic.credit_type_id
                   join gcd_creator_name_detail cnd on cnd.id = ic.creator_id
                   where ic.issue_id = ? and +ic.deleted = 0 and ct.name = 'editing'""",
                (issue_id,),
            ).fetchall()

        number, key_date, issue_title, issue_editing, series_name, publisher, language = issue
        credits = _collect_credits(stories, credit_rows)
        if not credits["editor"]:
            credits["editor"] = _dedupe([r[0] for r in issue_editors] or _split_credit_names(issue_editing or ""))

        characters = [r[0] for r in character_rows]
        if not characters:
            for story in stories:
                characters.extend(re.sub(r"\s*\[[^\]]*\]", "", c) for c in _split_semicolon_list(story[3] or ""))
        genres = []
        for story in stories:
            genres.extend(_split_semicolon_list(story[2] or ""))
        titles = [s[1] for s in stories if s[1]]
        year, month, day = _split_key_date(key_date or "")

        details = GcdIssueDetails(
            series_name=series_name or "",
            # "[nn]" is GCD's internal "no number" marker -- never
            # written to ComicInfo; the file keeps its own Number.
            issue_number="" if number == NO_NUMBER else (number or ""),
            title=issue_title or "; ".join(dict.fromkeys(titles)),
            year=year, month=month, day=day,
            summary=" ".join(s[4] for s in stories if s[4]),
            writer=", ".join(credits["writer"]),
            penciller=", ".join(credits["penciller"]),
            inker=", ".join(credits["inker"]),
            colorist=", ".join(credits["colorist"]),
            letterer=", ".join(credits["letterer"]),
            editor=", ".join(credits["editor"]),
            genre=", ".join(_dedupe(genres)),
            characters=", ".join(_dedupe(characters)),
            publisher=publisher or "",
            web=ISSUE_PAGE_URL.format(issue_id),
            language_iso=language or "",
        )
        return details


def _dedupe(items) -> list[str]:
    return list(dict.fromkeys(i.strip() for i in items if i and i.strip()))


def _collect_credits(stories, credit_rows) -> dict[str, list[str]]:
    """Structured credit rows where a story has any; that story's
    free-text fields otherwise (older GCD entries)."""
    fields = {field: [] for _word, field in _ROLE_WORDS}
    stories_with_rows = {row[0] for row in credit_rows}
    for _story_id, role, name in credit_rows:
        for field in _fields_for_role(role or ""):
            fields[field].append(name)
    text_columns = {"writer": 5, "penciller": 6, "inker": 7, "colorist": 8, "letterer": 9, "editor": 10}
    for story in stories:
        if story[0] in stories_with_rows:
            continue
        for field, column in text_columns.items():
            fields[field].extend(_split_credit_names(story[column] or ""))
    return {field: _dedupe(names) for field, names in fields.items()}


def _fields_for_role(role: str) -> list[str]:
    role = role.casefold()
    if "painting" in role:
        return list(_PAINTING_FIELDS)
    return [field for word, field in _ROLE_WORDS if word in role]


# One opened database per path for the whole session, so the name index
# is only ever built once.
_open: dict[str, GcdLocalDatabase] = {}
_open_lock = threading.Lock()


def open_database(path: str) -> GcdLocalDatabase:
    key = os.path.normcase(os.path.abspath(path)) if path else ""
    with _open_lock:
        db = _open.get(key)
        if db is None:
            db = GcdLocalDatabase(path)
            _open[key] = db
        return db
