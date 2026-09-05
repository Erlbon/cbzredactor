"""
core/comicvine_lookup.py

Looks up issue metadata (and a cover image, for visual confirmation
only -- see module note below) from the Comic Vine API
(https://comicvine.gamespot.com/api/documentation), the broadest
general-purpose comic database and what ComicTagger itself defaults to.
Requires a free API key the user registers for themselves at
https://comicvine.gamespot.com/api/ -- this module never creates one.

Comic Vine's search endpoint (/api/search/, resources=issue) returns
one result per matching issue with the lightweight fields needed to
show a candidate list (volume name, issue number, cover date, a short
"deck" blurb, and a thumbnail) but NOT full creator credits -- those
only come back from the issue's own detail endpoint
(/api/issue/<id>/), a second request per issue actually chosen. A
volume's publisher name needs a *third* request (the issue detail's
own "volume" field is itself just a lightweight {id, name} reference,
not the full volume with its publisher) -- kept as a separate,
explicitly best-effort call so a slow/failed publisher lookup never
blocks getting everything else.

This app deliberately does NOT apply a fetched cover image onto the
CBZ -- unlike EPUB's separate cover-image slot, a CBZ's "cover" is
just page 1 of the archive itself; overwriting it would mean actually
replacing image content, which is a different, more invasive feature
than this metadata lookup. The cover thumbnail is fetched and shown in
the lookup dialog purely so the user can visually confirm they picked
the right issue before applying its text fields.

Same injectable-`fetch` design as core/google_books_lookup.py in the
sibling EPUB tool, for the same reason: JSON-parsing logic is unit-
tested with canned responses, independent of real network access or a
real API key.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, urlencode

API_BASE = "https://comicvine.gamespot.com/api"
DEFAULT_TIMEOUT = 8.0
# Comic Vine rejects requests with a generic/default User-Agent (e.g.
# Python's own "Python-urllib/3.x") -- a descriptive one is required,
# not just polite.
USER_AGENT = "cbzredactor (+https://github.com/Erlbon/cbzredactor)"

# Comic Vine's documented status_code values; only 1 means success --
# everything else is an application-level error returned with HTTP 200,
# so it has to be checked explicitly rather than relying on HTTPError.
STATUS_OK = 1
STATUS_MESSAGES = {
    100: "Invalid API key",
    101: "Object not found",
    102: "Error in URL format",
    103: "Could not parse Comic Vine's response format",
    104: "Filter error",
    105: "That resource requires a Comic Vine subscription",
    106: "A required field was missing from the request",
    107: "Too many results requested",
}

# Comic Vine's person_credits role field is free text (e.g. "writer",
# "penciler, inker", "artist", "cover"), not a fixed enum -- these are
# substring keywords checked case-insensitively, in this order, against
# each comma-separated token. A bare "artist" (no more specific pencil/
# ink/color keyword) is treated as penciller, matching the convention
# ComicTagger itself uses for the same ambiguity.
ROLE_KEYWORDS = [
    ("writer", "writer"),
    ("penc", "penciller"),  # penciler/penciller/pencils/pencil
    ("ink", "inker"),
    ("colo", "colorist"),  # colorist/colourist/colors/colours
    ("letter", "letterer"),
    ("cover", "cover_artist"),
    ("editor", "editor"),
    ("artist", "penciller"),  # checked last: only matches a bare "artist"
]


class ComicVineLookupError(Exception):
    """Raised for any problem searching for, parsing, or downloading
    Comic Vine results -- including an application-level error Comic
    Vine itself reports (e.g. an invalid API key)."""


@dataclass
class ComicVineCandidate:
    """One search result -- enough to display and to fetch full
    details for, but not yet the full credit list (see module
    docstring)."""

    issue_id: str = ""
    volume_name: str = ""
    volume_detail_url: str = ""  # needed for fetch_publisher() -- see module docstring
    issue_number: str = ""
    name: str = ""  # the issue's own title, often blank
    cover_date: str = ""  # "YYYY-MM-DD" or ""
    summary: str = ""
    image_url: str = ""
    detail_url: str = ""

    def display_label(self) -> str:
        bits = [self.volume_name or "(unknown series)"]
        if self.issue_number:
            bits.append(f"#{self.issue_number}")
        if self.name:
            bits.append(f'"{self.name}"')
        if self.cover_date:
            bits.append(f"({self.cover_date[:4]})")
        return " ".join(bits)


@dataclass
class ComicVineIssueDetails:
    """Full per-issue data from the detail endpoint, already shaped as
    ComicInfoMetadata-compatible field values (see as_dict())."""

    volume_name: str = ""
    issue_number: str = ""
    name: str = ""
    cover_date: str = ""
    summary: str = ""
    writer: str = ""
    penciller: str = ""
    inker: str = ""
    colorist: str = ""
    letterer: str = ""
    cover_artist: str = ""
    editor: str = ""
    characters: str = ""
    teams: str = ""
    locations: str = ""
    story_arc: str = ""
    publisher: str = ""  # filled in separately by fetch_publisher(); "" until then

    def as_dict(self) -> dict:
        """Only the fields that actually came back, keyed to match
        ComicInfoMetadata's own attribute names directly -- the caller
        can pass this straight to setattr() per key."""
        year, month, day = _split_cover_date(self.cover_date)
        raw = {
            "series": self.volume_name,
            "number": self.issue_number,
            "title": self.name,
            "summary": self.summary,
            "year": year,
            "month": month,
            "day": day,
            "writer": self.writer,
            "penciller": self.penciller,
            "inker": self.inker,
            "colorist": self.colorist,
            "letterer": self.letterer,
            "cover_artist": self.cover_artist,
            "editor": self.editor,
            "characters": self.characters,
            "teams": self.teams,
            "locations": self.locations,
            "story_arc": self.story_arc,
            "publisher": self.publisher,
        }
        return {k: v for k, v in raw.items() if v}


def _default_fetch(url: str, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _split_cover_date(cover_date: str) -> tuple[str, str, str]:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", cover_date or "")
    if not match:
        return "", "", ""
    year, month, day = match.groups()
    return year, str(int(month)), str(int(day))


def _get_json(url: str, fetch) -> dict:
    """Runs one GET request and returns its parsed JSON body, raising
    ComicVineLookupError for a network failure, an unparseable
    response, or an application-level Comic Vine error (status_code
    != 1, e.g. an invalid API key or an unrecognized issue id)."""
    try:
        raw = fetch(url)
    except urllib.error.HTTPError as exc:
        raise ComicVineLookupError(
            f"Comic Vine returned an error (HTTP {exc.code}): {exc.reason}"
        ) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise ComicVineLookupError(
            f"Could not reach Comic Vine (check your internet connection): {exc}"
        ) from exc

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ComicVineLookupError("Received an unreadable response from Comic Vine.") from exc

    status_code = data.get("status_code")
    if status_code != STATUS_OK:
        message = STATUS_MESSAGES.get(status_code, data.get("error", "Unknown error"))
        raise ComicVineLookupError(f"Comic Vine error: {message}")

    return data


def build_search_url(api_key: str, series: str, number: str = "", max_results: int = 8) -> str:
    series = (series or "").strip()
    if not series:
        raise ComicVineLookupError("A series name is required to search Comic Vine.")
    query = f"{series} {number}".strip() if number else series

    params = {
        "api_key": api_key,
        "format": "json",
        "resources": "issue",
        "query": query,
        "limit": max_results,
        "field_list": "id,name,issue_number,cover_date,deck,description,image,volume,api_detail_url",
    }
    return f"{API_BASE}/search/?{urlencode(params, quote_via=quote)}"


def _candidate_from_result(result: dict) -> ComicVineCandidate:
    volume = result.get("volume") or {}
    image = result.get("image") or {}
    summary = result.get("deck") or _strip_html(result.get("description") or "")
    return ComicVineCandidate(
        issue_id=str(result.get("id", "")),
        volume_name=volume.get("name", "") or "",
        volume_detail_url=volume.get("api_detail_url", "") or "",
        issue_number=result.get("issue_number", "") or "",
        name=result.get("name", "") or "",
        cover_date=result.get("cover_date", "") or "",
        summary=summary,
        image_url=image.get("small_url", "") or image.get("thumb_url", "") or "",
        detail_url=result.get("api_detail_url", "") or "",
    )


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Comic Vine's "description" field is full HTML; "deck" (a short
    plain-text blurb) is preferred when present, but description is
    the only fallback for issues that only have the long form. Good
    enough for a Summary field -- not trying to preserve any of the
    markup, just the readable text."""
    return _TAG_RE.sub("", text).strip()


def parse_search_response(raw: bytes) -> list[ComicVineCandidate]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ComicVineLookupError("Received an unreadable response from Comic Vine.") from exc

    status_code = data.get("status_code")
    if status_code != STATUS_OK:
        message = STATUS_MESSAGES.get(status_code, data.get("error", "Unknown error"))
        raise ComicVineLookupError(f"Comic Vine error: {message}")

    return [_candidate_from_result(result) for result in data.get("results", []) or []]


def search_comicvine(
    api_key: str, series: str, number: str = "", fetch=None, max_results: int = 8
) -> list[ComicVineCandidate]:
    """Searches for issues matching `series` (+ `number`, if given).
    Raises ComicVineLookupError on a missing series/API key, network
    failure, or a Comic Vine application error. Returns an empty list
    (not an error) when the search succeeds but finds nothing.

    Note: Comic Vine's /search/ endpoint matches loosely against issue/
    volume text, not a strict issue-number filter -- for a common
    series name, review the candidate list rather than assuming the
    first result is always the right issue.
    """
    if not (api_key or "").strip():
        raise ComicVineLookupError(
            "No Comic Vine API key set -- add one via Settings > Comic Vine API Key..."
        )
    fetch = fetch or _default_fetch
    url = build_search_url(api_key, series, number, max_results)
    try:
        raw = fetch(url)
    except urllib.error.HTTPError as exc:
        raise ComicVineLookupError(
            f"Comic Vine returned an error (HTTP {exc.code}): {exc.reason}"
        ) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise ComicVineLookupError(
            f"Could not reach Comic Vine (check your internet connection): {exc}"
        ) from exc
    return parse_search_response(raw)


def _credits_by_role(person_credits: list) -> dict[str, list[str]]:
    by_role: dict[str, list[str]] = {}
    for person in person_credits or []:
        name = (person.get("name") or "").strip()
        role_text = (person.get("role") or "").lower()
        if not name or not role_text:
            continue
        matched_fields: set[str] = set()
        for token in role_text.split(","):
            token = token.strip()
            for keyword, field_name in ROLE_KEYWORDS:
                if keyword in token:
                    matched_fields.add(field_name)
                    break
        for field_name in matched_fields:
            names = by_role.setdefault(field_name, [])
            if name not in names:
                names.append(name)
    return by_role


def _names(credits_list: list) -> str:
    seen: list[str] = []
    for item in credits_list or []:
        name = (item.get("name") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return ", ".join(seen)


def fetch_issue_details(api_key: str, detail_url: str, fetch=None) -> ComicVineIssueDetails:
    """Fetches full credits for one issue from its own detail endpoint
    (a candidate's `detail_url`). Publisher is deliberately NOT
    included here -- see fetch_publisher()."""
    fetch = fetch or _default_fetch
    field_list = (
        "name,issue_number,cover_date,deck,description,volume,"
        "person_credits,character_credits,team_credits,location_credits,story_arc_credits"
    )
    url = f"{detail_url}?{urlencode({'api_key': api_key, 'format': 'json', 'field_list': field_list}, quote_via=quote)}"
    data = _get_json(url, fetch)
    result = data.get("results") or {}

    volume = result.get("volume") or {}
    roles = _credits_by_role(result.get("person_credits"))
    summary = result.get("deck") or _strip_html(result.get("description") or "")

    return ComicVineIssueDetails(
        volume_name=volume.get("name", "") or "",
        issue_number=result.get("issue_number", "") or "",
        name=result.get("name", "") or "",
        cover_date=result.get("cover_date", "") or "",
        summary=summary,
        writer=", ".join(roles.get("writer", [])),
        penciller=", ".join(roles.get("penciller", [])),
        inker=", ".join(roles.get("inker", [])),
        colorist=", ".join(roles.get("colorist", [])),
        letterer=", ".join(roles.get("letterer", [])),
        cover_artist=", ".join(roles.get("cover_artist", [])),
        editor=", ".join(roles.get("editor", [])),
        characters=_names(result.get("character_credits")),
        teams=_names(result.get("team_credits")),
        locations=_names(result.get("location_credits")),
        story_arc=_names(result.get("story_arc_credits")),
    )


def fetch_publisher(api_key: str, volume_detail_url: str, fetch=None) -> str:
    """Best-effort separate lookup for a volume's publisher name (see
    module docstring for why this needs its own request). Returns ""
    on any failure rather than raising -- a missing publisher name
    should never block applying everything else that was found."""
    if not volume_detail_url:
        return ""
    fetch = fetch or _default_fetch
    try:
        url = f"{volume_detail_url}?{urlencode({'api_key': api_key, 'format': 'json', 'field_list': 'publisher'}, quote_via=quote)}"
        data = _get_json(url, fetch)
        publisher = (data.get("results") or {}).get("publisher") or {}
        return publisher.get("name", "") or ""
    except ComicVineLookupError:
        return ""


def download_cover_image(candidate: ComicVineCandidate, fetch=None) -> bytes:
    """Downloads the candidate's thumbnail image bytes, for display
    only (see module docstring on why this is never written into the
    archive). Raises ComicVineLookupError if there's no image_url or
    the download fails."""
    if not candidate.image_url:
        raise ComicVineLookupError("This result has no cover image available.")
    fetch = fetch or _default_fetch
    try:
        return fetch(candidate.image_url)
    except urllib.error.HTTPError as exc:
        raise ComicVineLookupError(f"Could not download cover image (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise ComicVineLookupError(f"Could not download cover image: {exc}") from exc
