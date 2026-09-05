"""
core/gcd_lookup.py

Looks up issue metadata from the Grand Comics Database
(https://www.comics.org/api/), the best source for older/obscure
issues Comic Vine misses -- GCD's volunteer catalogers have been at
this since 1994 and cover far more Golden/Silver Age and international
material. No API key or authentication is needed, unlike Comic Vine.

Schema confirmed directly against the live API (2026-09-05) -- GCD
publishes no fixed OpenAPI-style spec, and its own docs warn the field
list "should not be considered stable", so this was verified against
real responses rather than trusted from documentation alone:

  GET /api/series/name/<series>/issue/<number>/?format=json
      -> {"results": [{"api_url", "series_name", "descriptor",
          "publication_date", "price", "page_count", ...}]}
      Lightweight listing -- already filtered by issue number, unlike
      Comic Vine's free-text search, but requires both series AND
      number (see search_gcd()).

  GET <api_url from above>  (already carries "?format=json")
      -> {"series_name", "number", "title", "key_date" ("YYYY-MM-DD",
          "00" for an unknown month/day), "indicia_publisher" (a plain
          string -- no separate publisher request needed, unlike Comic
          Vine), "cover" (image URL), "story_set": [{"type" ("comic
          story"/"cover"/"text story"/"credits, title page"/...),
          "title", "script", "pencils", "inks", "colors", "letters",
          "editing", "genre", "characters", "synopsis"}, ...]}
      Credits are attributed per STORY, not per issue -- an issue can
      contain several stories (the lead feature, a text-prose backup,
      the cover as its own "story", indicia/credits pages...). Only
      type == "comic story" entries are treated as the issue's actual
      content for credit/genre/character aggregation here; covers,
      text stories, and title/credits pages are real GCD data but not
      what a ComicInfo.xml Writer/Penciller/etc field is meant to hold.

      Each credit field is free text, often with trailing annotations
      GCD itself adds -- "Alan Moore (credited)", "Richard Bruning
      (credited) (designer)", "Jotapê Martins (credited) (tradução)" --
      stripped here (see _strip_annotations()) down to the bare name;
      this is a best-effort cleanup of free text, not a structured
      field, same caveat as Comic Vine's role-string parsing.

Network mechanics (injectable `fetch`, User-Agent, HTTPError/URLError/
JSON-decode-error translation) come from redactor_common's
core/lookup_client.py, shared with core/comicvine_lookup.py -- unit-
tested against canned responses shaped exactly like the real API,
independent of live network access.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote

from redactor_common.core.lookup_client import fetch_bytes, fetch_json, make_default_fetch

API_BASE = "https://www.comics.org/api"
USER_AGENT = "cbzredactor (+https://github.com/Erlbon/cbzredactor)"

# Only these story types count as the issue's actual attributable
# content for ComicInfo purposes -- see module docstring.
CONTENT_STORY_TYPES = {"comic story"}


class GcdLookupError(Exception):
    """Raised for any problem searching for, parsing, or downloading
    GCD results."""


@dataclass
class GcdCandidate:
    """One search result -- enough to display and to fetch full
    details for, but not yet story credits (see module docstring)."""

    series_name: str = ""
    issue_number: str = ""
    publication_date: str = ""  # free-text, e.g. "September 1986" -- display only
    detail_url: str = ""

    def display_label(self) -> str:
        bits = [self.series_name or "(unknown series)"]
        if self.issue_number:
            bits.append(f"#{self.issue_number}")
        if self.publication_date:
            bits.append(f"({self.publication_date})")
        return " ".join(bits)


@dataclass
class GcdIssueDetails:
    series_name: str = ""
    issue_number: str = ""
    title: str = ""
    year: str = ""
    month: str = ""
    day: str = ""
    summary: str = ""
    writer: str = ""
    penciller: str = ""
    inker: str = ""
    colorist: str = ""
    letterer: str = ""
    editor: str = ""
    genre: str = ""
    characters: str = ""
    publisher: str = ""
    cover_image_url: str = ""

    def as_dict(self) -> dict:
        """Only the fields that actually came back, keyed to match
        ComicInfoMetadata's own attribute names directly."""
        raw = {
            "series": self.series_name,
            "number": self.issue_number,
            "title": self.title,
            "summary": self.summary,
            "year": self.year,
            "month": self.month,
            "day": self.day,
            "writer": self.writer,
            "penciller": self.penciller,
            "inker": self.inker,
            "colorist": self.colorist,
            "letterer": self.letterer,
            "editor": self.editor,
            "genre": self.genre,
            "characters": self.characters,
            "publisher": self.publisher,
        }
        return {k: v for k, v in raw.items() if v}


_default_fetch = make_default_fetch(USER_AGENT)
_SOURCE_NAME = "the Grand Comics Database"


def _get_json(url: str, fetch) -> dict:
    return fetch_json(url, fetch, error_cls=GcdLookupError, source_name=_SOURCE_NAME)


_SERIES_YEAR_SUFFIX_RE = re.compile(r"\s*\(\d{4}(?:-\d{4})?\s*series\)\s*$")


def _clean_series_name(raw_name: str) -> str:
    """GCD always disambiguates same-named series by publication year,
    e.g. "Watchmen (1986 series)" -- stripped here since ComicInfo's
    own Series field is meant to hold just the name (the year already
    lives in Year/Month/Day)."""
    return _SERIES_YEAR_SUFFIX_RE.sub("", raw_name or "").strip()


def build_search_url(series: str, number: str) -> str:
    series = (series or "").strip()
    number = (number or "").strip()
    if not series or not number:
        raise GcdLookupError(
            "Both a series name and an issue number are required to search the "
            "Grand Comics Database (unlike Comic Vine, GCD has no free-text search)."
        )
    return f"{API_BASE}/series/name/{quote(series)}/issue/{quote(number)}/?format=json"


def parse_search_response(raw: bytes) -> list[GcdCandidate]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GcdLookupError("Received an unreadable response from the Grand Comics Database.") from exc

    return _candidates_from_page(data)


def _candidates_from_page(data: dict) -> list[GcdCandidate]:
    candidates = []
    for result in data.get("results", []) or []:
        candidates.append(
            GcdCandidate(
                series_name=_clean_series_name(result.get("series_name", "") or ""),
                issue_number=result.get("descriptor", "") or "",
                publication_date=result.get("publication_date", "") or "",
                detail_url=result.get("api_url", "") or "",
            )
        )
    return candidates


def _rank_candidates(candidates: list[GcdCandidate], queried_series: str) -> list[GcdCandidate]:
    """Puts an exact (case-insensitive) series-name match first --
    GCD's own results come back alphabetically, NOT ranked by
    relevance, so a common title like "Watchmen" lists dozens of
    same-named spin-offs/variant-covers ("Before Watchmen: ...",
    "Antes de Watchmen (2013 series)", ...) ahead of the actual
    "Watchmen (1986 series)" purely because "A"/"B" sorts before "W".
    A stable sort preserves that alphabetical order among everything
    that isn't an exact match."""
    query = queried_series.strip().lower()
    return sorted(candidates, key=lambda c: c.series_name.strip().lower() != query)


# Safety cap on how many result pages search_gcd will follow looking
# for an exact series-name match before giving up and ranking whatever
# it already has -- GCD paginates at 50 results/page, so this is
# generous (up to 250 results) without risking an unbounded fetch loop
# against a community-run server for a title with no exact match at all.
MAX_SEARCH_PAGES = 5


def search_gcd(series: str, number: str, fetch=None, max_results: int = 8) -> list[GcdCandidate]:
    """Searches for issues matching `series` + `number` exactly (GCD's
    own endpoint shape requires both -- see build_search_url()).

    Follows pagination (up to MAX_SEARCH_PAGES) only until an exact
    series-name match is found or pages run out -- needed because an
    exact match can be pushed past page 1 by purely alphabetical
    sorting (see _rank_candidates()) rather than because it doesn't
    exist. Once collected, results are ranked so an exact match sorts
    first regardless of which page it came from.

    Raises GcdLookupError on a missing series/number, network failure,
    or an unparseable response. Returns an empty list (not an error)
    when the search succeeds but finds nothing (a plain 404 from GCD
    for "no such series/number" is treated the same as an empty
    result set, not an error, since it's the expected outcome of a
    typo or a series GCD simply doesn't have)."""
    fetch = fetch or _default_fetch
    url = build_search_url(series, number)
    query = series.strip().lower()

    all_candidates: list[GcdCandidate] = []
    for _ in range(MAX_SEARCH_PAGES):
        data = fetch_json(url, fetch, error_cls=GcdLookupError, source_name=_SOURCE_NAME, ignore_404=True)
        if data is None:
            break  # no such series/number at all -- not a real error, just nothing to find

        page_candidates = _candidates_from_page(data)
        all_candidates.extend(page_candidates)
        if any(c.series_name.strip().lower() == query for c in page_candidates):
            break
        next_url = data.get("next")
        if not next_url:
            break
        url = next_url

    return _rank_candidates(all_candidates, series)[:max_results]


_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")


def _strip_annotations(name: str) -> str:
    """Strips GCD's own trailing parenthetical annotations -- "(credited)",
    "(uncredited)", "(designer)", "(tradução)", possibly several in a
    row ("Richard Bruning (credited) (designer)") -- down to the bare
    name. Best-effort text cleanup, not a structured field; an
    "(uncredited)" attribution is kept as a name like any other rather
    than being dropped, since GCD's own convention is to still list who
    the work is attributed to either way."""
    name = name.strip()
    while True:
        stripped = _TRAILING_PAREN_RE.sub("", name).strip()
        if stripped == name:
            return name
        name = stripped


def _split_credit_names(raw: str) -> list[str]:
    if not raw or raw == "None":
        return []
    names = []
    for part in raw.split(";"):
        name = _strip_annotations(part)
        if name and name not in names:
            names.append(name)
    return names


def _split_semicolon_list(raw: str) -> list[str]:
    if not raw or raw == "None":
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def _split_key_date(key_date: str) -> tuple[str, str, str]:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", key_date or "")
    if not match:
        return "", "", ""
    year, month, day = match.groups()
    return year, ("" if month == "00" else str(int(month))), ("" if day == "00" else str(int(day)))


def fetch_issue_details(detail_url: str, fetch=None) -> GcdIssueDetails:
    fetch = fetch or _default_fetch
    url = detail_url if "format=" in detail_url else detail_url + ("&format=json" if "?" in detail_url else "?format=json")
    data = _get_json(url, fetch)

    year, month, day = _split_key_date(data.get("key_date", "") or "")
    content_stories = [
        story for story in (data.get("story_set") or []) if story.get("type") in CONTENT_STORY_TYPES
    ]

    def _aggregate_credits(field_name: str) -> str:
        names: list[str] = []
        for story in content_stories:
            for name in _split_credit_names(story.get(field_name, "") or ""):
                if name not in names:
                    names.append(name)
        return ", ".join(names)

    def _aggregate_list_field(field_name: str) -> str:
        values: list[str] = []
        for story in content_stories:
            for value in _split_semicolon_list(story.get(field_name, "") or ""):
                if value not in values:
                    values.append(value)
        return ", ".join(values)

    editor = _aggregate_credits("editing")
    if not editor:
        # Falls back to the whole-issue editing credit (e.g. the
        # editor-in-chief) when no individual story lists its own --
        # exactly the shape seen on real issues (Watchmen #1: the lead
        # story's own "editing" is blank, but the issue-level one has
        # Len Wein credited as editor).
        editor = ", ".join(_split_credit_names(data.get("editing", "") or ""))

    titles = [story.get("title", "") for story in content_stories if story.get("title")]
    title = "; ".join(dict.fromkeys(titles)) or (data.get("title", "") or "")

    summaries = [story.get("synopsis", "") for story in content_stories if story.get("synopsis")]

    return GcdIssueDetails(
        series_name=_clean_series_name(data.get("series_name", "") or ""),
        issue_number=data.get("number", "") or data.get("descriptor", "") or "",
        title=title,
        year=year,
        month=month,
        day=day,
        summary=" ".join(summaries),
        writer=_aggregate_credits("script"),
        penciller=_aggregate_credits("pencils"),
        inker=_aggregate_credits("inks"),
        colorist=_aggregate_credits("colors"),
        letterer=_aggregate_credits("letters"),
        editor=editor,
        genre=_aggregate_list_field("genre"),
        characters=_aggregate_list_field("characters"),
        publisher=data.get("indicia_publisher", "") or "",
        cover_image_url=data.get("cover", "") or "",
    )


def download_cover_image(details: GcdIssueDetails, fetch=None) -> bytes:
    """Downloads the issue's cover image bytes, for display only (see
    core/comicvine_lookup.py's module docstring for why a lookup's
    cover is never written into the archive -- same reasoning applies
    here). Raises GcdLookupError if there's no cover_image_url or the
    download fails."""
    if not details.cover_image_url:
        raise GcdLookupError("This issue has no cover image available.")
    fetch = fetch or _default_fetch
    return fetch_bytes(details.cover_image_url, fetch, error_cls=GcdLookupError, what="cover image")
