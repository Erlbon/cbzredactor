"""
core/comicvine_browse.py

What the Comic Vine browser (gui/comicvine_browse_dialog.py) needs from the
API, in two steps like the ComicRack "Comic Vine Scraper" plugin
(cbanack/comic-vine-scraper, Apache 2.0; the code here is our own):

1. VOLUMES: /search/ with resources=volume returns every series (volume)
   matching a name, with its start year, issue count and publisher. They
   are re-ranked here by how well each fits the file (name words, an
   issue count that could contain the issue number, a start year not
   after the file's year, series chosen earlier in the same batch).
2. ISSUES: /issues/ filtered to one volume lists all of its issues, which
   are shown in number order so a missing number is visible at once.

Full credits for the issue finally chosen still come from
core/comicvine_lookup.py's fetch_issue_details(). Network access goes
through the same injectable `fetch` as that module, so every function
here can be tested offline.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from core.comicvine_lookup import (
    API_BASE,
    ComicVineCandidate,
    ComicVineLookupError,
    _check_status,
    _default_fetch,
    _numbers_equivalent,
    _split_words,
)
from redactor_common.core.lookup_client import fetch_bytes, fetch_json

PAGE_SIZE = 100  # Comic Vine's maximum per request
MAX_PAGES = 30  # a runaway volume (3000 issues) is cut off rather than looping forever

# Mirror publishers that reprint the common US series in other countries;
# a small penalty so the original wins a tie (the same list the plugin uses).
_MIRROR_PUBLISHERS = ("panini", "deagostini", "de agostini", "marvel italia", "marvel uk", "semic", "abril")


@dataclass
class ComicVineVolume:
    volume_id: str = ""
    name: str = ""
    start_year: str = ""
    issue_count: int = 0
    publisher: str = ""
    image_url: str = ""
    detail_url: str = ""
    site_url: str = ""

    def year_number(self) -> int:
        match = re.match(r"^\d{4}", self.start_year or "")
        return int(match.group(0)) if match else 0


@dataclass
class ComicVineIssue:
    issue_id: str = ""
    number: str = ""
    name: str = ""
    cover_date: str = ""
    image_url: str = ""
    detail_url: str = ""
    site_url: str = ""


def _need_key(api_key: str) -> None:
    if not (api_key or "").strip():
        raise ComicVineLookupError("No Comic Vine API key set -- add one via Tools > API Keys...")


def _url(path: str, api_key: str, **params) -> str:
    query = {"api_key": api_key, "format": "json", **params}
    return f"{API_BASE}/{path}/?{urlencode(query, quote_via=quote)}"


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------

def _volume_from_result(result: dict) -> ComicVineVolume:
    image = result.get("image") or {}
    publisher = result.get("publisher") or {}
    try:
        count = int(result.get("count_of_issues") or 0)
    except (TypeError, ValueError):
        count = 0
    return ComicVineVolume(
        volume_id=str(result.get("id", "")),
        name=result.get("name", "") or "",
        start_year=str(result.get("start_year") or "").strip().rstrip("- "),
        issue_count=count,
        publisher=(publisher.get("name") if isinstance(publisher, dict) else "") or "",
        image_url=image.get("small_url", "") or image.get("thumb_url", "") or "",
        detail_url=result.get("api_detail_url", "") or "",
        site_url=result.get("site_detail_url", "") or "",
    )


def search_volumes(api_key: str, series: str, fetch=None, max_results: int = 100) -> list[ComicVineVolume]:
    """Every Comic Vine volume matching `series` (up to `max_results`), in
    Comic Vine's own relevance order. Raises ComicVineLookupError on a
    missing series/key or an API/network failure; an empty list means no match."""
    _need_key(api_key)
    series = (series or "").strip()
    if not series:
        raise ComicVineLookupError("A series name is required to search Comic Vine.")
    fetch = fetch or _default_fetch
    url = _url(
        "search", api_key, resources="volume", query=series, limit=min(max_results, PAGE_SIZE),
        field_list="id,name,start_year,count_of_issues,publisher,image,api_detail_url,site_detail_url",
    )
    data = fetch_json(url, fetch, error_cls=ComicVineLookupError, source_name="Comic Vine")
    _check_status(data)
    return [_volume_from_result(result) for result in data.get("results") or []]


def number_as_float(number: str):
    """The number as a float, or None when it isn't numeric ("Annual 1", "")."""
    cleaned = re.sub(r"[^\d.\-]+", "", number or "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def score_volume(
    volume: ComicVineVolume, series: str, number: str, year_hint: str, prior_volume_ids: frozenset = frozenset()
) -> float:
    """How well `volume` fits a file named `series` #`number` from `year_hint`.
    Higher is better; scores can be negative. Components, as in the plugin:
    name-word overlap (+5 per shared word, -1 per missing, -1 per extra);
    a series chosen earlier in this batch (+7); a mirror publisher (-6);
    an issue count that can't contain the number (-100, else +100; very
    long series are always fine, and an unknown number is neutral);
    a series that started after the file's year (-500); and a small
    preference for newer series as the tie-breaker."""
    score = 0.0

    remaining = _split_words(volume.name)
    for word in _split_words(series):
        if word in remaining:
            score += 5
            remaining.remove(word)
        else:
            score -= 1
    score -= len(remaining)

    if volume.volume_id and volume.volume_id in prior_volume_ids:
        score += 7

    publisher = (volume.publisher or "").lower()
    if any(mirror in publisher for mirror in _MIRROR_PUBLISHERS):
        score -= 6

    wanted = number_as_float(number)
    if wanted is not None:
        # The database often lacks recent issues, so one more than the count is still fine.
        score += 100 if volume.issue_count > 100 or wanted - 1 <= volume.issue_count else -100

    this_year = datetime.date.today().year
    try:
        year = int(year_hint)
    except (TypeError, ValueError):
        year = 0
    start = volume.year_number()
    if 1900 < year <= this_year + 1:
        if not start:
            score -= 100
        elif start > year:
            score -= 500
    score -= (this_year - start) / 100.0 if start else 1.0
    return score


def rank_volumes(
    volumes: list[ComicVineVolume], series: str, number: str, year_hint: str,
    prior_volume_ids: frozenset = frozenset(),
) -> list[ComicVineVolume]:
    """Best fit first; ties keep Comic Vine's own order (stable sort)."""
    return sorted(
        volumes, key=lambda v: score_volume(v, series, number, year_hint, prior_volume_ids), reverse=True
    )


# ---------------------------------------------------------------------------
# Issues of one volume
# ---------------------------------------------------------------------------

def _issue_from_result(result: dict) -> ComicVineIssue:
    image = result.get("image") or {}
    return ComicVineIssue(
        issue_id=str(result.get("id", "")),
        number=str(result.get("issue_number") or "").strip(),
        name=result.get("name", "") or "",
        cover_date=result.get("cover_date", "") or "",
        image_url=image.get("small_url", "") or image.get("thumb_url", "") or "",
        detail_url=result.get("api_detail_url", "") or "",
        site_url=result.get("site_detail_url", "") or "",
    )


_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def plain_number(number: str):
    """The number as a float only when the whole text is a number: "12" and
    "10.5" yes, "Annual 1" and "1a" no (unlike number_as_float(), which is for
    the file's own number and reads digits out of anything)."""
    text = (number or "").strip()
    return float(text) if _PLAIN_NUMBER.match(text) else None


def issue_sort_key(issue: ComicVineIssue):
    """Numeric numbers in numeric order first, then everything else by text
    ("1", "2", "10", then "Annual 1")."""
    value = plain_number(issue.number)
    return (0, value, issue.number) if value is not None else (1, 0.0, issue.number.lower())


def fetch_volume_issues(api_key: str, volume_id: str, fetch=None) -> list[ComicVineIssue]:
    """All issues of one volume in number order (paged: 100 per request)."""
    _need_key(api_key)
    fetch = fetch or _default_fetch
    issues: list[ComicVineIssue] = []
    for page in range(MAX_PAGES):
        url = _url(
            "issues", api_key, filter=f"volume:{volume_id}", limit=PAGE_SIZE, offset=page * PAGE_SIZE,
            field_list="id,name,issue_number,cover_date,image,api_detail_url,site_detail_url",
        )
        data = fetch_json(url, fetch, error_cls=ComicVineLookupError, source_name="Comic Vine")
        _check_status(data)
        results = data.get("results") or []
        issues.extend(_issue_from_result(result) for result in results)
        total = int(data.get("number_of_total_results") or 0)
        if not results or len(issues) >= total:
            break
    return sorted(issues, key=issue_sort_key)


def find_issue_by_number(issues: list[ComicVineIssue], number: str):
    """The issue whose number equals `number` ("1" matches "1.0"), or None."""
    for issue in issues:
        if _numbers_equivalent(issue.number, number):
            return issue
    return None


def number_summary(issues: list[ComicVineIssue]) -> str:
    """"1-12" / "1-4, 6-9, Annual 1" -- which numbers the volume has, so a
    missing one is plain to see."""
    numeric = sorted({int(v) for i in issues if (v := plain_number(i.number)) is not None and v == int(v) and v >= 0})
    others = [i.number for i in issues if (v := plain_number(i.number)) is None or v != int(v) or v < 0]
    others = [number for number in others if number]
    runs, start = [], None
    for value in numeric:
        if start is None:
            start = previous = value
        elif value == previous + 1:
            previous = value
        else:
            runs.append((start, previous))
            start = previous = value
    if start is not None:
        runs.append((start, previous))
    parts = [f"{a}" if a == b else f"{a}-{b}" for a, b in runs] + others
    return ", ".join(parts)


def candidate_for(volume: ComicVineVolume, issue: ComicVineIssue) -> ComicVineCandidate:
    """The shape core/comicvine_lookup.fetch_issue_details() takes."""
    return ComicVineCandidate(
        issue_id=issue.issue_id,
        volume_name=volume.name,
        volume_detail_url=volume.detail_url,
        issue_number=issue.number,
        name=issue.name,
        cover_date=issue.cover_date,
        image_url=issue.image_url,
        detail_url=issue.detail_url,
    )


def download_image(url: str, fetch=None) -> bytes:
    """A cover thumbnail's bytes, for display and hashing only."""
    if not url:
        raise ComicVineLookupError("No cover image is available.")
    return fetch_bytes(url, fetch or _default_fetch, error_cls=ComicVineLookupError, what="cover image")
