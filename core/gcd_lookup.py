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

import base64
import json
import re
import urllib.request
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


class GcdRateLimitError(GcdLookupError):
    """GCD's hourly request limit was reached (HTTP 429). Anonymous
    access has a lower limit than a logged-in account -- see
    make_gcd_fetch(). A batch should stop at this point rather than
    fail every remaining file one request at a time."""


class GcdAuthError(GcdLookupError):
    """GCD rejected the configured username/password (HTTP 401/403)."""


RATE_LIMIT_MESSAGE = (
    "The Grand Comics Database's hourly request limit was reached. Try again later"
)
AUTH_MESSAGE = (
    "The Grand Comics Database didn't accept your username/password -- check "
    "Tools > API Keys..."
)


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
    web: str = ""  # the issue's GCD page, e.g. https://www.comics.org/issue/12345/
    language_iso: str = ""  # only the local dump has this (core/gcd_local.py)
    # ComicInfo's Volume, the ComicRack / Comic Vine way: the year the SERIES began ("1986"), not a volume number.
    volume: str = ""
    # GCD writes a legacy number after the series' own: "1 (707)" -> issue_number "1", alternate_number "707".
    alternate_number: str = ""
    cover_artist: str = ""  # the cover "story"'s artist (GCD keeps the cover as its own story, type "cover")
    translator: str = ""  # script credits GCD marks "(translation)", "oversetter", "Übersetzung"...: never the Writer
    gtin: str = ""  # the ISBN, else the barcode, digits only (a barcode's 2/5-digit add-on dropped)
    imprint: str = ""  # GCD's brand (Vertigo, Marvel Knights, Icon), unless that is just the publisher itself
    black_and_white: str = ""  # "Yes" / "No" from the series' colour note (local dump only)
    # Only the ComicRack Library (core/comicrack_import.py's cr_issue) carries these: the user's own values.
    alternate_series: str = ""
    alternate_count: str = ""
    count: str = ""
    teams: str = ""
    locations: str = ""
    story_arc: str = ""
    format: str = ""
    age_rating: str = ""
    manga: str = ""
    series_group: str = ""
    main_character_or_team: str = ""
    tags: str = ""

    def as_dict(self) -> dict:
        """Only the fields that actually came back, keyed to match
        ComicInfoMetadata's own attribute names directly."""
        raw = {
            "series": self.series_name,
            "volume": self.volume,
            "number": self.issue_number,
            "alternate_series": self.alternate_series,
            "alternate_number": self.alternate_number,
            "alternate_count": self.alternate_count,
            "count": self.count,
            "cover_artist": self.cover_artist,
            "translator": self.translator,
            "gtin": self.gtin,
            "imprint": self.imprint,
            "black_and_white": self.black_and_white,
            "teams": self.teams,
            "locations": self.locations,
            "story_arc": self.story_arc,
            "format": self.format,
            "age_rating": self.age_rating,
            "manga": self.manga,
            "series_group": self.series_group,
            "main_character_or_team": self.main_character_or_team,
            "tags": self.tags,
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
            "web": self.web,
            "language_iso": self.language_iso,
        }
        return {k: v for k, v in raw.items() if v}


def make_gcd_fetch(username: str = "", password: str = "", timeout: float = 30.0):
    """A fetch(url) -> bytes for GCD's API, optionally logged in.

    GCD's API wiki: anonymous access is limited per hour, a logged-in
    account gets larger limits, and anonymous access "will likely be
    turned off at some point". Logging in is plain HTTP Basic
    authentication with the user's comics.org account.

    Translates GCD's own refusals into specific errors: HTTP 429 ->
    GcdRateLimitError, and (only when credentials were sent) 401/403 ->
    GcdAuthError. The fetch is marked with `gcd_logged_in`; the
    translation itself is done by redactor_common's fetch_json()/
    fetch_bytes() `status_errors` (see _gcd_status()), so a fetch not
    made here (a test double, the anonymous default) gets the plain
    GcdLookupError, as before."""
    base_fetch = make_default_fetch(USER_AGENT, timeout=timeout)
    auth_header = ""
    if username and password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        auth_header = f"Basic {token}"

    def _fetch(url) -> bytes:
        request = url if isinstance(url, urllib.request.Request) else urllib.request.Request(url)
        if auth_header:
            request.add_header("Authorization", auth_header)
        return base_fetch(request)

    _fetch.gcd_logged_in = bool(auth_header)
    return _fetch


def _gcd_status(fetch) -> dict:
    """fetch_json()/fetch_bytes() keyword arguments turning GCD's refusals
    into GcdRateLimitError/GcdAuthError -- only for a fetch made by
    make_gcd_fetch(), and 401/403 only when it sends a login."""
    logged_in = getattr(fetch, "gcd_logged_in", None)
    if logged_in is None:
        return {}
    hint = "" if logged_in else ", or add a free GCD account in Tools > API Keys... for a higher limit"
    errors = {429: GcdRateLimitError}
    messages = {429: f"{RATE_LIMIT_MESSAGE}{hint}."}
    if logged_in:
        errors.update({401: GcdAuthError, 403: GcdAuthError})
        messages.update({401: AUTH_MESSAGE, 403: AUTH_MESSAGE})
    return {"status_errors": errors, "status_messages": messages}


def check_login(username: str, password: str, fetch=None) -> None:
    """Checks a username/password against GCD's API. GCD answers an
    anonymous request normally, but a request carrying WRONG
    credentials is refused -- so any small authenticated request tells
    us whether the login works. Raises GcdAuthError, GcdRateLimitError
    or GcdLookupError; returns normally when the login is accepted."""
    fetch = fetch or make_gcd_fetch(username, password)
    _get_json(f"{API_BASE}/series/7096/?format=json", fetch)


_default_fetch = make_default_fetch(USER_AGENT)
_SOURCE_NAME = "the Grand Comics Database"


def _get_json(url: str, fetch) -> dict:
    return fetch_json(url, fetch, error_cls=GcdLookupError, source_name=_SOURCE_NAME, **_gcd_status(fetch))


_SERIES_YEAR_SUFFIX_RE = re.compile(r"\s*\(\d{4}(?:-\d{4})?\s*series\)\s*$")


def _clean_series_name(raw_name: str) -> str:
    """GCD always disambiguates same-named series by publication year,
    e.g. "Watchmen (1986 series)" -- stripped here since ComicInfo's
    own Series field is meant to hold just the name (the year already
    lives in Year/Month/Day)."""
    return _SERIES_YEAR_SUFFIX_RE.sub("", raw_name or "").strip()


def _series_start_year(raw_name: str) -> str:
    """The year in GCD's "Watchmen (1986 series)" (the series' start year: ComicInfo's Volume), or ""."""
    match = re.search(r"\((\d{4})(?:-\d{4})?\s*series\)\s*$", raw_name or "")
    return match.group(1) if match else ""


def build_search_url(series: str, number: str, year: str = "") -> str:
    """GCD's series+issue search. With `year`, adds GCD's own
    ".../year/<year>/" filter (matched against the issue's key date,
    per GCD's API wiki) -- cuts a common title's dozens of
    alphabetically-sorted results down to a handful in one request."""
    series = (series or "").strip()
    number = (number or "").strip()
    year = (year or "").strip()
    if not series or not number:
        raise GcdLookupError(
            "Both a series name and an issue number are required to search the "
            "Grand Comics Database (unlike Comic Vine, GCD has no free-text search)."
        )
    year_part = f"year/{quote(year)}/" if re.fullmatch(r"\d{4}", year) else ""
    return f"{API_BASE}/series/name/{quote(series)}/issue/{quote(number)}/{year_part}?format=json"


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
    query = _comparable(queried_series)
    return sorted(candidates, key=lambda c: _comparable(c.series_name) != query)


# Safety cap on how many result pages search_gcd will follow looking
# for an exact series-name match before giving up and ranking whatever
# it already has -- GCD paginates at 50 results/page, so this is
# generous (up to 250 results) without risking an unbounded fetch loop
# against a community-run server for a title with no exact match at all.
MAX_SEARCH_PAGES = 5


def search_gcd(
    series: str, number: str, fetch=None, max_results: int = 8, year: str = ""
) -> list[GcdCandidate]:
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
    build_search_url(series, number)  # validates series + number up front

    # Narrowest first: the year filter, then without it (a filename's
    # year can be a cover year that GCD's key date files a month
    # apart, or plain wrong); the series as written, then with scene
    # naming's " - " turned back into the ":" Windows filenames can't
    # hold ("G.I. Joe - A Real ..." -> "G.I. Joe: A Real ..."), since
    # GCD's name search is a plain "contains".
    for name in series_name_variants(series):
        for year_filter in ([year, ""] if year else [""]):
            candidates = _search_pages(name, number, year_filter, fetch)
            if candidates:
                return _rank_candidates(candidates, name)[:max_results]
    return []


def series_name_variants(series: str) -> list[str]:
    """The series to try, in order. A scene-style " - " subtitle
    separator almost always stands for a ":" in the real title, so the
    colon form goes first, then the name exactly as given."""
    series = series.strip()
    if " - " in series:
        return [series.replace(" - ", ": ", 1), series]
    return [series]


def _search_pages(series: str, number: str, year: str, fetch) -> list[GcdCandidate]:
    url = build_search_url(series, number, year)
    query = _comparable(series)
    all_candidates: list[GcdCandidate] = []
    for _ in range(MAX_SEARCH_PAGES):
        data = fetch_json(url, fetch, error_cls=GcdLookupError, source_name=_SOURCE_NAME, ignore_404=True,
                          **_gcd_status(fetch))
        if data is None:
            break  # no such series/number at all -- not a real error, just nothing to find

        page_candidates = _candidates_from_page(data)
        all_candidates.extend(page_candidates)
        if any(_comparable(c.series_name) == query for c in page_candidates):
            break
        next_url = data.get("next")
        if not next_url:
            break
        url = next_url
    return all_candidates


def _comparable(name: str) -> str:
    """Case-folded, with ":" and " - " treated alike."""
    return re.sub(r"\s*(:|\s-)\s*", ": ", (name or "").strip()).casefold()


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


# A credit note or role that means translation, in the languages GCD's indexers use (checked against the dump:
# "translation", "oversetter", "Übersetzung", "vertaling", "översättning", "traduzione", "tradução"...).
_TRANSLATION_RE = re.compile(
    r"translat|tradu|[uü]bersetz|oversett|[oö]vers[aä]tt|vertal|k[aä][aä]nn|t[lł]umacz|przek[lł]ad|p[rř]eklad|prevod|ford[ií]t",
    re.IGNORECASE,
)


def is_translation(note: str) -> bool:
    return bool(_TRANSLATION_RE.search(note or ""))


def _split_script_names(raw: str) -> tuple[list[str], list[str]]:
    """A free-text script credit split into (writers, translators): "Alan Moore; Jane Roe (translation)"."""
    writers: list[str] = []
    translators: list[str] = []
    if not raw or raw == "None":
        return writers, translators
    for part in raw.split(";"):
        name = _strip_annotations(part)
        if not name:
            continue
        target = translators if is_translation(part.strip()[len(name):]) else writers
        if name not in target:
            target.append(name)
    return writers, translators


_LEGACY_NUMBER_RE = re.compile(r"^\s*([^\s()\[\]]+)\s*\(\s*(\d+)\s*\)\s*$")


def split_legacy_number(number: str) -> tuple[str, str]:
    """GCD's "1 (707)" (the series' own number, then the legacy one) -> ("1", "707"); anything else unchanged."""
    match = _LEGACY_NUMBER_RE.match(number or "")
    return (match.group(1), match.group(2)) if match else ((number or "").strip(), "")


def gtin_from(isbn: str, barcode: str) -> str:
    """The ISBN when there is one, else the barcode -- digits only. A barcode's 2- or 5-digit add-on (the
    issue-number part printed beside a UPC/EAN, e.g. "76194120401700111") is dropped."""
    digits = re.sub(r"[^0-9Xx]", "", isbn or "").upper()
    if len(digits) in (10, 13):
        return digits
    digits = re.sub(r"\D", "", barcode or "")
    trimmed = {15: 13, 17: 12, 18: 13}.get(len(digits))
    digits = digits[:trimmed] if trimmed else digits
    return digits if len(digits) in (8, 12, 13, 14) else ""


_COMPANY_WORDS = {"comics", "comic", "inc", "publishing", "publications", "publication", "group", "entertainment",
                  "llc", "ltd", "co", "company", "corp", "corporation", "the"}


def _company_core(name: str) -> str:
    words = re.sub(r"[^\w\s]", " ", (name or "").casefold()).split()
    return " ".join(w for w in words if w not in _COMPANY_WORDS)


def imprint_from_brands(brands, publishers) -> str:
    """The brands that name an imprint (Vertigo, Marvel Knights, Icon): GCD's emblem notes in brackets are
    dropped ("DC [bullet]" -> "DC"), and so is a brand that is only the publisher itself (DC, Image) or an
    anniversary emblem with a year in it ("20 1992-2012 Image")."""
    own = {_company_core(p) for p in publishers if p}
    found: list[str] = []
    for brand in brands:
        name = _strip_annotations(re.sub(r"\[[^\]]*\]", "", brand or "")).strip()
        core = _company_core(name)
        # The publisher itself, also when its full name goes on ("Marvel" vs "Marvel Worldwide Inc.").
        if not name or not core or re.search(r"\b\d{4}\b", name) or any(core == p or p.startswith(core + " ") for p in own):
            continue
        if name not in found:
            found.append(name)
    return ", ".join(found)


_BW_RE = re.compile(r"black\s*(and|&)\s*white|b\s*&\s*w|schwarz\s*-?\s*wei|zwart\s*-?\s*wit|noir\s*et\s*blanc|"
                    r"svart\s*-?\s*hvit|svartvit|bianco\s*e\s*nero|blanco\s*y\s*negro|preto\s*e\s*branco|grayscale|greyscale",
                    re.IGNORECASE)
_COLOR_RE = re.compile(r"colou?r|farb|kleur|couleur|colorid|farge|f[aä]rg|colore", re.IGNORECASE)


def black_and_white_from(color_note: str) -> str:
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


# GCD's free-text rating -> ComicInfo's AgeRating list (decision page question 3, answer A, 2026-11-21). Only the
# common texts in the dump are translated; anything else writes nothing. A Comics Code seal is not an age.
_AGE_RATINGS = [
    (re.compile(r"comics code|conforms to the|a\.\s*c\.\s*m\.\s*p", re.I), ""),
    (re.compile(r"^adults? only\b", re.I), "Adults Only 18+"),
    (re.compile(r"mature|^rated m\b|ages 17\+|parental advisory|^psr$", re.I), "Mature 17+"),
    (re.compile(r"^(rated )?(t\+?|teen\+?|teen plus)\b|ages 13\+|^teen 13\+", re.I), "Teen"),
    (re.compile(r"^all ages$|^rated a$", re.I), "Everyone"),
]


def age_rating_from(rating: str) -> str:
    text = " ".join((rating or "").split())
    if not text:
        return ""
    for pattern, value in _AGE_RATINGS:
        if pattern.search(text):
            return value
    return ""


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


def _series_info(series_url: str, fetch) -> dict:
    """The issue's series record (end year, issue list, colour note): one more request, best effort -- a
    failure only leaves Count and Black & White empty, never the whole lookup."""
    if not series_url.startswith("http"):
        return {}
    url = series_url if "format=" in series_url else series_url + ("&format=json" if "?" in series_url else "?format=json")
    try:
        data = _get_json(url, fetch)
    except Exception:  # noqa: BLE001 -- best effort, see above
        return {}
    return data if isinstance(data, dict) else {}


def _distinct_issue_count(series: dict) -> int:
    """How many issues a series has: its issue list has one entry per COVER ("1 (707) [Mike Del Mundo]",
    "1 [Blank Cover]"...), so distinct numbers are counted. Thor (2018): 55 entries, 16 issues."""
    descriptors = series.get("issue_descriptors") or []
    if not descriptors:
        return len(series.get("active_issues") or [])
    numbers = set()
    for descriptor in descriptors:
        match = re.match(r"^\s*([^\s(\[]+)", str(descriptor or ""))
        if match:
            numbers.add(match.group(1))
    return len(numbers)


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

    writers: list[str] = []
    translators: list[str] = []
    for story in content_stories:
        story_writers, story_translators = _split_script_names(story.get("script", "") or "")
        writers.extend(n for n in story_writers if n not in writers)
        translators.extend(n for n in story_translators if n not in translators)
    cover_artists: list[str] = []
    for story in data.get("story_set") or []:
        if story.get("type") == "cover":
            cover_artists.extend(n for n in _split_credit_names(story.get("pencils", "") or "") if n not in cover_artists)
    number, legacy = split_legacy_number(data.get("number", "") or data.get("descriptor", "") or "")
    publisher = data.get("indicia_publisher", "") or ""
    series_name = _clean_series_name(data.get("series_name", "") or "")
    series = _series_info(data.get("series", "") or "", fetch)
    # Count only for a series that has ended (question 1, answer A): a running series' count goes out of date.
    count = str(_distinct_issue_count(series)) if series.get("year_ended") else ""

    return GcdIssueDetails(
        series_name=_clean_series_name(data.get("series_name", "") or ""),
        volume=_series_start_year(data.get("series_name", "") or ""),
        issue_number=number,
        alternate_number=legacy,
        # The legacy number belongs to the same series' longer run (question 2, answer A): Thor, 707.
        alternate_series=series_name if legacy else "",
        count=count if count != "0" else "",
        black_and_white=black_and_white_from(series.get("color", "") or ""),
        age_rating=age_rating_from(data.get("rating", "") or ""),
        cover_artist=", ".join(cover_artists),
        translator=", ".join(translators),
        gtin=gtin_from(data.get("isbn", "") or "", data.get("barcode", "") or ""),
        imprint=imprint_from_brands((data.get("brand_emblem", "") or "").split(";"), [publisher]),
        title=title,
        year=year,
        month=month,
        day=day,
        summary=" ".join(summaries),
        writer=", ".join(writers),
        penciller=_aggregate_credits("pencils"),
        inker=_aggregate_credits("inks"),
        colorist=_aggregate_credits("colors"),
        letterer=_aggregate_credits("letters"),
        editor=editor,
        genre=_aggregate_list_field("genre"),
        characters=_aggregate_list_field("characters"),
        publisher=publisher,
        cover_image_url=data.get("cover", "") or "",
        web=issue_page_url(detail_url),
    )


_ISSUE_API_URL_RE = re.compile(r"^(https?://[^/]+)/api/issue/(\d+)/")


def issue_page_url(detail_url: str) -> str:
    """The issue's public GCD page for its API URL:
    https://www.comics.org/api/issue/12345/?format=json ->
    https://www.comics.org/issue/12345/ ("" if it doesn't look like one)."""
    match = _ISSUE_API_URL_RE.match(detail_url or "")
    return f"{match.group(1)}/issue/{match.group(2)}/" if match else ""


def download_cover_image(details: GcdIssueDetails, fetch=None) -> bytes:
    """Downloads the issue's cover image bytes, for display only (see
    core/comicvine_lookup.py's module docstring for why a lookup's
    cover is never written into the archive -- same reasoning applies
    here). Raises GcdLookupError if there's no cover_image_url or the
    download fails."""
    if not details.cover_image_url:
        raise GcdLookupError("This issue has no cover image available.")
    fetch = fetch or _default_fetch
    return fetch_bytes(details.cover_image_url, fetch, error_cls=GcdLookupError, what="cover image",
                       **_gcd_status(fetch))
