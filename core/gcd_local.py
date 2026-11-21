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

The generic machinery -- read-only opening, the in-memory name index,
chunked queries, the session cache -- is redactor_common's
core/local_db.py (promoted from this module 2026-09-28). What's here is
GCD's schema: the queries, the ranking, and the mapping to ComicInfo.

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

The same class also reads a ComicRack library converted to GCD's layout
(core/comicrack_import.py): it recognises one by the import-info table
the converter writes, and then links each issue to its own Web link
(usually Comic Vine) instead of a comics.org page that doesn't exist.

Schema notes (verified against the 2026-09-15 dump): gcd_series ->
gcd_issue (series_id) -> gcd_story (issue_id; type_id 19 = "comic
story") -> gcd_story_credit (story_id; creator_id is a
gcd_creator_name_detail id; credit_type_id -> gcd_credit_type, which
includes combined roles such as "pencils and inks"). Older stories
often have no credit rows, only free-text fields on gcd_story
(script, pencils, ...) -- used as a fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from redactor_common.core.dump_import import read_import_info
from redactor_common.core.local_db import (
    LocalDatabase,
    LocalDatabaseError,
    normalize_words,
    open_cached,
    year_gap,
)

from core.gcd_lookup import (
    GcdIssueDetails,
    _split_credit_names,
    _split_key_date,
    _split_script_names,
    _split_semicolon_list,
    gtin_from,
    imprint_from_brands,
    is_translation,
    split_legacy_number,
)

GCD_DOWNLOAD_URL = "https://www.comics.org/download/"
# GCD's issue "number" for an unnumbered issue, e.g. most one-shots.
NO_NUMBER = "[nn]"
COMIC_STORY_TYPE_ID = 19  # gcd_story_type "comic story"
COVER_STORY_TYPE_ID = 6  # gcd_story_type "cover"
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


class GcdLocalError(LocalDatabaseError):
    """The local GCD database file is missing, unreadable or not a GCD dump."""


# Punctuation-insensitive name comparison -- redactor_common's, kept
# under this module's own name for its callers.
normalize_name = normalize_words


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


class GcdLocalDatabase(LocalDatabase):
    """One opened GCD dump (read-only; see redactor_common's LocalDatabase
    for the connection, locking and error handling)."""

    def __init__(self, path: str):
        super().__init__(path, _REQUIRED_TABLES, kind="a GCD SQLite dump", error_cls=GcdLocalError)
        with self.lock:
            self.import_info = read_import_info(self._con)
        # A ComicRack library converted by core/comicrack_import.py.
        self.is_comicrack = self.import_info.get("source") == "ComicRack"
        # Columns a ComicRack conversion (or an older one) may lack: every query below asks only for what exists.
        self._credit_cols = set(self.table_columns("gcd_story_credit"))
        self._issue_cols = set(self.table_columns("gcd_issue"))
        self._series_cols = set(self.table_columns("gcd_series"))
        self._cr_cols = set(self.table_columns("cr_issue")) if self.is_comicrack else set()
        self._has_brands = all(self.has_table(t) for t in
                               ("gcd_issue_brand_emblem", "gcd_brand_emblem_group", "gcd_brand_group"))

    def summary(self) -> dict:
        """Counts and the newest change date, for the Settings dialog."""
        series = self.query_one("select count(*) from gcd_series where deleted = 0")[0]
        issues = self.query_one("select count(*) from gcd_issue where +deleted = 0")[0]
        if self.import_info:
            newest = self.import_info.get("built", "")
        else:
            newest = self.query_one("select max(modified) from gcd_issue")[0] or ""
        return {"series": series, "issues": issues, "newest": str(newest)[:10]}

    def _series_index(self):
        """Every live series name, indexed in memory on first use (about
        1.5 s for ~230k series), kept for the session."""
        return self.name_index("series", "select id, name from gcd_series where deleted = 0")

    def prepare(self) -> None:
        """Builds the name index now (e.g. behind a progress dialog)
        instead of on the first search."""
        self._series_index()

    # ------------------------------------------------------------------

    def search(
        self, series: str, number: str, year: str = "", series_year: str = "", limit: int = 8
    ) -> list[LocalCandidate]:
        """Issues numbered `number` in every series whose name contains
        all of `series`'s words. A blank `number` means "1"; "1" also
        matches an unnumbered issue (GCD's "[nn]", typical for one-shots).

        Ranked by: exact series name; the series' start year matching
        `series_year` ("Batman (2016)"); the issue's year closest to
        `year`; English editions ahead of translated reprints of the
        same title; numbered ahead of "[nn]"; then the series with more
        issues. Variant-cover records are skipped."""
        if not normalize_name(series):
            return []
        # A blank number is searched as "1" as well as "[nn]": a one-shot
        # is #1 on Comic Vine (and so in a ComicRack library) and often in
        # GCD too, while a scene filename rarely carries its number.
        wanted = _normalize_number(number) or "1"
        numbers = [wanted, *(wanted.zfill(width) for width in (2, 3, 4))]
        if wanted == "1":
            numbers.append(NO_NUMBER)
        numbers = list(dict.fromkeys(numbers))
        # Every series whose name contains all the words -- no limit: a
        # common word like "Batman" matches thousands of series, and the
        # right one must not be cut off (query_in chunks the id list).
        series_ids = self._series_index().match(series)
        rows = self.query_in(
            f"""select i.id, s.id, s.name, s.year_began, s.issue_count, p.name, i.number, i.key_date, l.code
                from gcd_issue i
                join gcd_series s on s.id = i.series_id
                left join gcd_publisher p on p.id = s.publisher_id
                left join stddata_language l on l.id = s.language_id
                where i.series_id in ({{ids}})
                  and +i.number in ({",".join("?" * len(numbers))})
                  and +i.deleted = 0 and i.variant_of_id is null""",
            series_ids,
            after=numbers,
        ) if series_ids else []

        wanted_name = normalize_name(series)
        candidates = [
            LocalCandidate(
                issue_id=r[0], series_id=r[1], series_name=r[2] or "", series_year=r[3],
                issue_count=r[4] or 0, publisher=r[5] or "", number=r[6] or "", key_date=r[7] or "",
                exact_name=normalize_name(r[2]) == wanted_name, language=r[8] or "",
            )
            for r in rows
        ]

        def rank(c: LocalCandidate):
            return (
                not c.exact_name,
                year_gap(c.series_year, series_year) if series_year else 0,
                year_gap(c.key_date[:4], year) if year else 0,
                c.language not in ("en", ""),
                c.number == NO_NUMBER,
                -c.issue_count,
            )

        return sorted(candidates, key=rank)[:limit]

    # ------------------------------------------------------------------

    def details(self, issue_id: int) -> GcdIssueDetails:
        """ComicInfo-shaped details for one issue (same shape as the
        online lookup's, so the GUI treats both alike)."""
        isbn_col = "i.isbn" if "isbn" in self._issue_cols else "''"
        barcode_col = "i.barcode" if "barcode" in self._issue_cols else "''"
        color_col = "s.color" if "color" in self._series_cols else "''"
        credit_name_col = "sc.credit_name" if "credit_name" in self._credit_cols else "''"
        with self.lock:
            issue = self.query_one(
                f"""select i.number, i.key_date, i.title, i.editing, s.name, p.name, l.code, s.year_began,
                          {isbn_col}, {barcode_col}, {color_col}
                   from gcd_issue i join gcd_series s on s.id = i.series_id
                   left join gcd_publisher p on p.id = s.publisher_id
                   left join stddata_language l on l.id = s.language_id
                   where i.id = ?""",
                (issue_id,),
            )
            if issue is None:
                raise GcdLocalError(f"Issue {issue_id} not found in the local database.")
            stories = self.query(
                """select st.id, st.title, st.genre, st.characters, st.synopsis,
                          st.script, st.pencils, st.inks, st.colors, st.letters, st.editing
                   from gcd_story st
                   where st.issue_id = ? and +st.type_id = ? and +st.deleted = 0
                   order by st.sequence_number""",
                (issue_id, COMIC_STORY_TYPE_ID),
            )
            story_ids = [s[0] for s in stories]
            credit_rows = []
            character_rows = []
            if story_ids:
                marks = ",".join("?" * len(story_ids))
                credit_rows = self.query(
                    f"""select sc.story_id, ct.name, cnd.name, {credit_name_col}
                        from gcd_story_credit sc
                        join gcd_credit_type ct on ct.id = sc.credit_type_id
                        join gcd_creator_name_detail cnd on cnd.id = sc.creator_id
                        where sc.story_id in ({marks}) and +sc.deleted = 0
                        order by sc.id""",
                    story_ids,
                )
                character_rows = self.query(
                    # character_id points at gcd_character_name_detail
                    # (the name as used in that story), not gcd_character.
                    f"""select distinct c.name
                        from gcd_story_character sch join gcd_character_name_detail c on c.id = sch.character_id
                        where sch.story_id in ({marks}) and +sch.deleted = 0""",
                    story_ids,
                )
            issue_editors = self.query(
                """select cnd.name from gcd_issue_credit ic
                   join gcd_credit_type ct on ct.id = ic.credit_type_id
                   join gcd_creator_name_detail cnd on cnd.id = ic.creator_id
                   where ic.issue_id = ? and +ic.deleted = 0 and ct.name = 'editing'""",
                (issue_id,),
            )
            # The cover is its own "story" (type 6): its artist is ComicInfo's Cover Artist.
            cover_stories = self.query(
                """select st.id, st.pencils from gcd_story st
                   where st.issue_id = ? and +st.type_id = ? and +st.deleted = 0 order by st.sequence_number""",
                (issue_id, COVER_STORY_TYPE_ID),
            )
            cover_rows = []
            if cover_stories:
                marks = ",".join("?" * len(cover_stories))
                cover_rows = self.query(
                    f"""select sc.story_id, ct.name, cnd.name from gcd_story_credit sc
                        join gcd_credit_type ct on ct.id = sc.credit_type_id
                        join gcd_creator_name_detail cnd on cnd.id = sc.creator_id
                        where sc.story_id in ({marks}) and +sc.deleted = 0 order by sc.id""",
                    [s[0] for s in cover_stories],
                )
            brand_rows = []
            if self._has_brands:
                brand_rows = self.query(
                    """select distinct bg.name from gcd_issue_brand_emblem ibe
                       join gcd_brand_emblem_group eg on eg.brand_id = ibe.brand_id
                       join gcd_brand_group bg on bg.id = eg.brandgroup_id
                       where ibe.issue_id = ? and +bg.deleted = 0""",
                    (issue_id,),
                )
            cr_row = None
            if self._cr_cols:
                cr_row = self.query_one(f"select * from cr_issue where issue_id = ?", (issue_id,))

        (number, key_date, issue_title, issue_editing, series_name, publisher, language, year_began,
         isbn, barcode, color) = issue
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
        cover_with_rows = {row[0] for row in cover_rows}
        cover_artists = [name for _sid, role, name in cover_rows if _fields_for_role(role or "")[:1] == ["penciller"]
                         or "painting" in (role or "").casefold()]
        for story_id, pencils in cover_stories:
            if story_id not in cover_with_rows:
                cover_artists.extend(_split_credit_names(pencils or ""))
        # "[nn]" is GCD's internal "no number" marker -- never written to ComicInfo; the file keeps its own Number.
        main_number, legacy = split_legacy_number("" if number == NO_NUMBER else (number or ""))

        details = GcdIssueDetails(
            series_name=series_name or "",
            # ComicInfo's Volume = the series' start year (the ComicRack / Comic Vine convention).
            volume=str(year_began) if year_began else "",
            issue_number=main_number,
            alternate_number=legacy,
            cover_artist=", ".join(_dedupe(cover_artists)),
            translator=", ".join(credits["translator"]),
            gtin=gtin_from(isbn or "", barcode or ""),
            imprint=imprint_from_brands([r[0] for r in brand_rows], [publisher or ""]),
            black_and_white=_black_and_white(color or ""),
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
            web=self._web_link(issue_id),
            language_iso=language or "",
        )
        if cr_row is not None:
            self._apply_comicrack_fields(details, dict(zip(self.table_columns("cr_issue"), cr_row)))
        return details

    @staticmethod
    def _apply_comicrack_fields(details: GcdIssueDetails, row: dict) -> None:
        """A ComicRack Library's own values (cr_issue) win over anything derived: they are what the user
        tagged. Columns an older conversion lacks are simply absent."""
        def text(key: str) -> str:
            value = row.get(key)
            return "" if value is None else str(value).strip()

        for attr, column in (
            ("teams", "teams"), ("locations", "locations"), ("story_arc", "story_arc"),
            ("alternate_series", "alternate_series"), ("alternate_number", "alternate_number"),
            ("alternate_count", "alternate_count"), ("count", "series_count"), ("format", "format"),
            ("age_rating", "age_rating"), ("manga", "manga"), ("black_and_white", "black_and_white"),
            ("imprint", "imprint"), ("series_group", "series_group"),
            ("main_character_or_team", "main_character_or_team"), ("tags", "tags"), ("translator", "translator"),
        ):
            value = text(column)
            if value and not (attr in ("manga", "black_and_white") and value.casefold() == "unknown"):
                setattr(details, attr, value)


    def _web_link(self, issue_id: int) -> str:
        if not self.is_comicrack:
            return ISSUE_PAGE_URL.format(issue_id)
        row = self.query_one("select web from cr_issue where issue_id = ?", (issue_id,))
        return (row[0] or "") if row else ""


def _dedupe(items) -> list[str]:
    return list(dict.fromkeys(i.strip() for i in items if i and i.strip()))


def _collect_credits(stories, credit_rows) -> dict[str, list[str]]:
    """Structured credit rows where a story has any; that story's
    free-text fields otherwise (older GCD entries).
    A credit noted as a translation ("translation", "oversetter",
    "Übersetzung"...) goes to Translator, never to Writer."""
    fields = {field: [] for _word, field in _ROLE_WORDS}
    fields["translator"] = []
    stories_with_rows = {row[0] for row in credit_rows}
    for row in credit_rows:
        _story_id, role, name = row[:3]
        note = row[3] if len(row) > 3 else ""
        if is_translation(note or ""):
            fields["translator"].append(name)
            continue
        for field in _fields_for_role(role or ""):
            fields[field].append(name)
    text_columns = {"penciller": 6, "inker": 7, "colorist": 8, "letterer": 9, "editor": 10}
    for story in stories:
        if story[0] in stories_with_rows:
            continue
        writers, translators = _split_script_names(story[5] or "")
        fields["writer"].extend(writers)
        fields["translator"].extend(translators)
        for field, column in text_columns.items():
            fields[field].extend(_split_credit_names(story[column] or ""))
    return {field: _dedupe(names) for field, names in fields.items()}


_BW_RE = re.compile(r"black\s*(and|&)\s*white|b\s*&\s*w|schwarz\s*-?\s*wei|zwart\s*-?\s*wit|noir\s*et\s*blanc|"
                    r"svart\s*-?\s*hvit|svartvit|bianco\s*e\s*nero|blanco\s*y\s*negro|preto\s*e\s*branco|grayscale|greyscale",
                    re.IGNORECASE)
_COLOR_RE = re.compile(r"colou?r|farb|kleur|couleur|colorid|farge|f[aä]rg|colore", re.IGNORECASE)


def _black_and_white(color_note: str) -> str:
    """ComicInfo's BlackAndWhite from GCD's free-text series colour note: about the interior when the note
    says ("color cover; black and white interior" -> Yes). "" when unclear."""
    note = color_note or ""
    parts = [p for p in re.split(r"[;,]", note) if "interior" in p.casefold() or "innen" in p.casefold()]
    text = parts[0] if parts else note
    if _BW_RE.search(text):
        return "Yes"
    if _COLOR_RE.search(text):
        return "No"
    return ""


def _fields_for_role(role: str) -> list[str]:
    role = role.casefold()
    if "painting" in role:
        return list(_PAINTING_FIELDS)
    return [field for word, field in _ROLE_WORDS if word in role]


def open_database(path: str) -> GcdLocalDatabase:
    """The session's opened dump for `path` -- opened once, so the name
    index is built once (redactor_common's open_cached)."""
    return open_cached(path, GcdLocalDatabase)
