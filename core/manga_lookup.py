"""
core/manga_lookup.py

Looks a manga SERIES up on AniList (https://anilist.co) and MangaUpdates
(https://www.mangaupdates.com) together and merges what they know into
ComicInfo fields. Neither source needs an API key for reading.

Both are series-level databases: they know a series' titles, start date,
genres, tags, description, credits, publishers, the volume count of a
finished series and a cover, but nothing about one volume (no per-volume
ISBN, date or cover). So a lookup is by series, and the result describes the
whole series -- the same fields suit every volume of it. The volume number a
file already has is never touched.

  AniList      POST https://graphql.anilist.co  (GraphQL; one query gives
               everything, including the staff list)
               Rate limit: 90 requests a minute in the docs, but the API
               currently answers with X-RateLimit-Limit: 30, so requests
               are spaced two seconds apart.
  MangaUpdates POST https://api.mangaupdates.com/v1/series/search, then
               GET /v1/series/<id> for the publishers and the credits (the
               search answer has neither). "Reasonable spacing" is all its
               terms ask for; they also ask that MangaUpdates is credited
               (README / CREDITS.md do that).

The two answers are paired by title: the best AniList result and the
MangaUpdates result whose titles (English, romaji, native, synonyms /
associated names) agree with it and whose start years are within one year.
A pair is a better match than either alone, and MangaUpdates adds what
AniList lacks: the publishers (the English one preferred) and the credits
as separate authors and artists. AniList adds the rated tags, the score and
the volume count. When one source has nothing, the other stands alone.

Network mechanics (injectable `fetch`, User-Agent, error translation) come
from redactor_common's core/lookup_client.py; everything here is unit-tested
against canned answers shaped like the real ones (checked live 2026-10-11).
A fetched cover is only ever shown for confirmation, never written into the
archive.
"""

from __future__ import annotations

import difflib
import html
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from redactor_common.core.lookup_client import (
    FetchFn, Throttle, build_request, fetch_bytes, fetch_json, make_default_fetch,
)

USER_AGENT = "cbzredactor (manga lookup; https://github.com/Erlbon/cbzredactor)"
ANILIST_URL = "https://graphql.anilist.co"
MANGAUPDATES_URL = "https://api.mangaupdates.com/v1"
ANILIST_INTERVAL_SECONDS = 2.0
MANGAUPDATES_INTERVAL_SECONDS = 0.5
RESULTS_PER_SOURCE = 6
MAX_TAGS = 8
MIN_TAG_RANK = 60  # AniList ranks a tag 1-100 by how many voters agree
PAIR_MIN_SIMILARITY = 0.88
EXACT_PAIRED_CONFIDENCE = 0.95
EXACT_SINGLE_CONFIDENCE = 0.85
LOOSE_FACTOR = 0.8  # a non-exact title match is scored below the default 90% threshold
MIN_SIMILARITY = 0.5  # a result whose best title is less alike than this is not a candidate at all

SOURCE_ANILIST = "AniList"
SOURCE_MANGAUPDATES = "MangaUpdates"

_ANILIST_QUERY = """
query ($search: String, $perPage: Int) {
  Page(perPage: $perPage) {
    media(search: $search, type: MANGA, format_in: [MANGA, ONE_SHOT], sort: SEARCH_MATCH) {
      id siteUrl idMal
      title { romaji english native }
      synonyms format status volumes
      startDate { year month day }
      countryOfOrigin isAdult averageScore
      description(asHtml: false)
      genres
      tags { name rank isGeneralSpoiler isMediaSpoiler }
      coverImage { extraLarge large }
      staff(sort: RELEVANCE, perPage: 10) { edges { role node { name { full } } } }
    }
  }
}
"""


class MangaLookupError(Exception):
    """A problem searching AniList or MangaUpdates (unreachable, rate limited, an unreadable answer)."""


# --- reading an answer that may not have the shape the docs promise -------------------------------


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def plain_text(text: str) -> str:
    """A description as plain text: tags and markdown links dropped, entities decoded, line breaks kept."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text or "")
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [text](url) -> text
    text = re.sub(r"[*_]{1,3}([^*_\n]+)[*_]{1,3}", r"\1", text)  # *emphasis* / **bold**
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title or "").casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\((manga|light novel|novel|one[- ]?shot|webtoon)\)", " ", text)
    return re.sub(r"[\W_]+", " ", text).strip()


def title_similarity(a: str, b: str) -> float:
    a, b = normalize_title(a), normalize_title(b)
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def best_similarity(left: list[str], right: list[str]) -> float:
    return max((title_similarity(a, b) for a in left for b in right), default=0.0)


def person_name(name: str) -> str:
    """"MIURA Kentaro" (MangaUpdates: family name in capitals first) -> "Kentaro Miura"; anything else as it is."""
    name = (name or "").strip()
    parts = name.split()
    if len(parts) >= 2 and parts[0].isupper() and len(parts[0]) > 1 and not parts[-1].isupper():
        return " ".join(parts[1:] + [parts[0].capitalize()])
    return name


# --- the two sources' series ----------------------------------------------------------------------


@dataclass
class AniListSeries:
    series_id: str = ""
    url: str = ""
    titles: list[str] = field(default_factory=list)  # english, romaji, native, then the synonyms
    english: str = ""
    romaji: str = ""
    year: str = ""
    month: str = ""
    day: str = ""
    country: str = ""
    adult: bool = False
    score: int | None = None  # 0-100
    volumes: int | None = None
    status: str = ""
    description: str = ""
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # the best-ranked, spoiler-free ones
    cover_url: str = ""
    writers: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.english or self.romaji or (self.titles[0] if self.titles else "")


@dataclass
class MangaUpdatesSeries:
    series_id: str = ""
    url: str = ""
    title: str = ""
    year: str = ""
    kind: str = ""  # Manga, Manhwa, Manhua, ...
    description: str = ""
    genres: list[str] = field(default_factory=list)
    cover_url: str = ""
    associated: list[str] = field(default_factory=list)
    # filled in by the detail request:
    writers: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    publishers: list[tuple[str, str]] = field(default_factory=list)  # (name, "Original" / "English")
    volumes: int | None = None  # only when the series is complete
    detailed: bool = False

    @property
    def titles(self) -> list[str]:
        return [self.title] + self.associated


def _anilist_series(media: dict) -> AniListSeries | None:
    title = _dict(media.get("title"))
    english, romaji, native = _text(title.get("english")), _text(title.get("romaji")), _text(title.get("native"))
    titles = [t for t in (english, romaji, native, *map(_text, _list(media.get("synonyms")))) if t]
    if not titles:
        return None
    start = _dict(media.get("startDate"))
    writers: list[str] = []
    artists: list[str] = []
    for edge in _list(_dict(media.get("staff")).get("edges")):
        edge = _dict(edge)
        name = _text(_dict(_dict(edge.get("node")).get("name")).get("full"))
        role = _text(edge.get("role")).casefold()
        if not name:
            continue
        if "assist" in role:
            continue
        if re.search(r"\bstory\b", role) or "original creator" in role:
            writers.append(name)
        if re.search(r"\bart\b", role):
            artists.append(name)
    tags = [
        _text(_dict(tag).get("name")) for tag in sorted(
            (t for t in _list(media.get("tags")) if isinstance(t, dict)), key=lambda t: -(_int(t.get("rank")) or 0)
        )
        if (_int(_dict(tag).get("rank")) or 0) >= MIN_TAG_RANK
        and not _dict(tag).get("isGeneralSpoiler") and not _dict(tag).get("isMediaSpoiler")
    ]
    cover = _dict(media.get("coverImage"))
    return AniListSeries(
        series_id=_text(media.get("id")), url=_text(media.get("siteUrl")), titles=titles, english=english,
        romaji=romaji, year=_text(start.get("year")), month=_text(start.get("month")), day=_text(start.get("day")),
        country=_text(media.get("countryOfOrigin")), adult=media.get("isAdult") is True,
        score=_int(media.get("averageScore")), volumes=_int(media.get("volumes")), status=_text(media.get("status")),
        description=plain_text(_text(media.get("description"))),
        genres=[g for g in map(_text, _list(media.get("genres"))) if g],
        tags=[t for t in tags if t][:MAX_TAGS], cover_url=_text(cover.get("extraLarge")) or _text(cover.get("large")),
        writers=list(dict.fromkeys(writers)), artists=list(dict.fromkeys(artists)),
    )


def _mangaupdates_series(record: dict) -> MangaUpdatesSeries | None:
    title = _text(record.get("title"))
    if not title:
        return None
    image = _dict(_dict(record.get("image")).get("url"))
    return MangaUpdatesSeries(
        series_id=_text(record.get("series_id")), url=_text(record.get("url")), title=title,
        year=_text(record.get("year")), kind=_text(record.get("type")),
        description=plain_text(_text(record.get("description"))),
        genres=[g for g in (_text(_dict(e).get("genre")) for e in _list(record.get("genres"))) if g],
        cover_url=_text(image.get("original")) or _text(image.get("thumb")),
        associated=[t for t in (_text(_dict(e).get("title")) for e in _list(record.get("associated"))) if t],
    )


def _apply_mangaupdates_detail(series: MangaUpdatesSeries, data: dict) -> None:
    """Adds what only the detail answer has: authors / artists, publishers, the volume count."""
    writers, artists = [], []
    for entry in _list(data.get("authors")):
        entry = _dict(entry)
        name = person_name(_text(entry.get("name")))
        kind = _text(entry.get("type")).casefold()
        if name and kind == "author":
            writers.append(name)
        elif name and kind == "artist":
            artists.append(name)
    series.writers, series.artists = list(dict.fromkeys(writers)), list(dict.fromkeys(artists))
    series.publishers = [
        (_text(_dict(p).get("publisher_name")), _text(_dict(p).get("type")))
        for p in _list(data.get("publishers")) if _text(_dict(p).get("publisher_name"))
    ]
    if data.get("completed") is True:  # "43 Volumes (Ongoing)" says nothing final; a finished series has its count
        match = re.match(r"\s*(\d+)\s+Volumes?", _text(data.get("status")), re.I)
        series.volumes = int(match.group(1)) if match else None
    if not series.description:
        series.description = plain_text(_text(data.get("description")))
    series.detailed = True


# --- the merged result ----------------------------------------------------------------------------


@dataclass
class MangaMatch:
    """One series as the sources know it: an AniList result, a MangaUpdates result, or the pair."""

    anilist: AniListSeries | None = None
    mangaupdates: MangaUpdatesSeries | None = None
    similarity: float = 0.0  # of the best title to the query, 0-1
    exact: bool = False  # a title equals the query once punctuation and case are ignored

    @property
    def title(self) -> str:
        return (self.anilist.title if self.anilist else "") or (self.mangaupdates.title if self.mangaupdates else "")

    @property
    def year(self) -> str:
        return (self.anilist.year if self.anilist else "") or (self.mangaupdates.year if self.mangaupdates else "")

    @property
    def sources(self) -> list[str]:
        return [name for name, found in ((SOURCE_ANILIST, self.anilist), (SOURCE_MANGAUPDATES, self.mangaupdates)) if found]

    @property
    def source_text(self) -> str:
        return " + ".join(self.sources)

    @property
    def confidence(self) -> float:
        """How sure the match is: an exact title found by both sources is the strongest, an exact title in one is
        next; anything looser scores below the default confidence threshold, so it is only a suggestion."""
        if self.exact:
            return EXACT_PAIRED_CONFIDENCE if len(self.sources) == 2 else EXACT_SINGLE_CONFIDENCE
        return round(self.similarity * LOOSE_FACTOR, 2)

    def display_label(self) -> str:
        year = f" ({self.year})" if self.year else ""
        return f"{self.title}{year} [{self.source_text}]"

    @property
    def cover_url(self) -> str:
        return (self.anilist.cover_url if self.anilist else "") or (self.mangaupdates.cover_url if self.mangaupdates else "")


@dataclass
class MangaDetails:
    """What the match says about the series, ready for ComicInfo."""

    match: MangaMatch
    fields: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, str]:
        return dict(self.fields)


def build_details(match: MangaMatch) -> MangaDetails:
    """ComicInfo fields from a match (the MangaUpdates detail must already be fetched). Only fields that have a
    value are present. AniList is preferred for the title, summary, dates, tags and score; MangaUpdates for the
    publisher and the credits (it names authors and artists separately, and its publishers carry their kind)."""
    ani, mu = match.anilist, match.mangaupdates
    fields: dict[str, str] = {}

    def put(name: str, value) -> None:
        text = _text(value) if not isinstance(value, str) else value.strip()
        if text:
            fields[name] = text

    put("series", match.title)
    put("summary", (ani.description if ani else "") or (mu.description if mu else ""))
    put("year", match.year)
    if ani and ani.year:
        put("month", ani.month)
        put("day", ani.day)
    writers = (mu.writers if mu and mu.writers else []) or (ani.writers if ani else [])
    artists = (mu.artists if mu and mu.artists else []) or (ani.artists if ani else [])
    put("writer", ", ".join(writers))
    put("penciller", ", ".join(artists))
    if mu and mu.publishers:
        english = [name for name, kind in mu.publishers if kind.casefold() == "english"]
        original = [name for name, kind in mu.publishers if kind.casefold() == "original"]
        put("publisher", (english or original or [mu.publishers[0][0]])[0])
    genres = list(dict.fromkeys((ani.genres if ani else []) + (mu.genres if mu else [])))
    put("genre", ", ".join(genres))
    put("tags", ", ".join(ani.tags if ani else []))
    put("web", (ani.url if ani else "") or (mu.url if mu else ""))
    japanese = (ani.country == "JP") if ani and ani.country else bool(mu and mu.kind.casefold() == "manga")
    if japanese:
        put("manga", "YesAndRightToLeft")
    if ani and ani.adult:
        put("age_rating", "Adults Only 18+")
    volumes = (ani.volumes if ani and ani.volumes else None) or (mu.volumes if mu else None)
    if volumes:
        put("count", volumes)
    if ani and ani.score:
        put("community_rating", f"{ani.score / 20:.1f}")
    return MangaDetails(match, fields)


# --- the client -----------------------------------------------------------------------------------


class MangaClient:
    """Searches both sources, politely: requests to one service are spaced (AniList's limit is low) and the
    answers of a run are remembered, so a folder of twenty volumes of one series asks once."""

    def __init__(
        self, fetch: FetchFn | None = None, anilist_interval: float = ANILIST_INTERVAL_SECONDS,
        mangaupdates_interval: float = MANGAUPDATES_INTERVAL_SECONDS, sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch or make_default_fetch(USER_AGENT, timeout=20.0)
        self._throttles = {
            SOURCE_ANILIST: Throttle(anilist_interval, clock=clock, sleep=sleep),
            SOURCE_MANGAUPDATES: Throttle(mangaupdates_interval, clock=clock, sleep=sleep),
        }
        self._searches: dict[str, list[MangaMatch]] = {}
        self._covers: dict[str, bytes] = {}

    def _json(self, source: str, request, what: str) -> dict:
        self._throttles[source].wait()
        data = fetch_json(
            request, self._fetch, MangaLookupError, source_name=source,
            status_messages={429: f"{source} says too many requests were made; try again in a minute."},
        )
        if not isinstance(data, dict):
            raise MangaLookupError(f"{source} sent an unexpected answer to the {what}.")
        return data

    # -- the two searches
    def search_anilist(self, title: str) -> list[AniListSeries]:
        request = build_request(
            ANILIST_URL, json_body={"query": _ANILIST_QUERY, "variables": {"search": title, "perPage": RESULTS_PER_SOURCE}},
            headers={"Accept": "application/json"},
        )
        data = self._json(SOURCE_ANILIST, request, "search")
        if data.get("errors") and not _dict(data.get("data")):
            message = _text(_dict(_list(data["errors"])[0] if _list(data["errors"]) else {}).get("message"))
            raise MangaLookupError(f"AniList refused the search ({message or 'an error'}).")
        media = _list(_dict(_dict(data.get("data")).get("Page")).get("media"))
        return [s for s in (_anilist_series(_dict(m)) for m in media) if s]

    def search_mangaupdates(self, title: str) -> list[MangaUpdatesSeries]:
        request = build_request(
            f"{MANGAUPDATES_URL}/series/search", json_body={"search": title, "perpage": RESULTS_PER_SOURCE},
            headers={"Accept": "application/json"},
        )
        data = self._json(SOURCE_MANGAUPDATES, request, "search")
        found = []
        for result in _list(data.get("results")):
            series = _mangaupdates_series(_dict(_dict(result).get("record")))
            if series and series.kind.casefold() not in ("novel", "artbook", "doujinshi"):
                found.append(series)
        return found

    def fetch_mangaupdates_detail(self, series: MangaUpdatesSeries) -> None:
        if series.detailed or not series.series_id:
            return
        data = self._json(SOURCE_MANGAUPDATES, f"{MANGAUPDATES_URL}/series/{series.series_id}", "series request")
        _apply_mangaupdates_detail(series, data)

    # -- the combined search
    def search(self, title: str, year_hint: str = "") -> list[MangaMatch]:
        """Matches for a series title, best first. One source being down is fine while the other answers; both
        down raises MangaLookupError."""
        key = normalize_title(title) + "|" + year_hint
        if key in self._searches:
            return self._searches[key]
        errors: list[str] = []
        anilist: list[AniListSeries] = []
        updates: list[MangaUpdatesSeries] = []
        try:
            anilist = self.search_anilist(title)
        except MangaLookupError as exc:
            errors.append(str(exc))
        try:
            updates = self.search_mangaupdates(title)
        except MangaLookupError as exc:
            errors.append(str(exc))
        if len(errors) == 2:
            raise MangaLookupError(" ".join(errors))
        matches = [m for m in pair_results(title, anilist, updates, year_hint) if m.similarity >= MIN_SIMILARITY]
        self._searches[key] = matches
        return matches

    def details(self, match: MangaMatch) -> MangaDetails:
        """The match's ComicInfo fields; fetches the MangaUpdates detail (publishers, credits) when it has one.
        A failed detail request is not fatal: the fields then come from what the searches already said."""
        if match.mangaupdates and not match.mangaupdates.detailed:
            try:
                self.fetch_mangaupdates_detail(match.mangaupdates)
            except MangaLookupError:
                pass
        return build_details(match)

    def cover(self, match: MangaMatch) -> bytes | None:
        """The series cover for the preview (asked for once per series, however many volumes show it)."""
        url = match.cover_url
        if not url:
            return None
        if url not in self._covers:
            try:
                self._covers[url] = fetch_bytes(url, self._fetch, MangaLookupError, what="the cover image", require_data=True)
            except MangaLookupError:
                return None  # not remembered: a later volume may try again
        return self._covers[url]


def _years_agree(a: str, b: str) -> bool:
    if not (a.isdigit() and b.isdigit()):
        return True  # one side does not know: no reason to refuse
    return abs(int(a) - int(b)) <= 1


def pair_results(
    query: str, anilist: list[AniListSeries], updates: list[MangaUpdatesSeries], year_hint: str = ""
) -> list[MangaMatch]:
    """Each AniList result paired with the MangaUpdates result that is the same series (by title, start years
    within one year); MangaUpdates results nobody claimed follow on their own. Best first."""
    matches: list[MangaMatch] = []
    claimed: set[int] = set()
    for ani in anilist:
        best_index, best_value = None, 0.0
        for index, mu in enumerate(updates):
            if index in claimed or not _years_agree(ani.year, mu.year):
                continue
            value = best_similarity(ani.titles, mu.titles)
            if value >= PAIR_MIN_SIMILARITY and value > best_value:
                best_index, best_value = index, value
        mu = None
        if best_index is not None:
            claimed.add(best_index)
            mu = updates[best_index]
        matches.append(_scored(query, ani, mu))
    for index, mu in enumerate(updates):
        if index not in claimed:
            matches.append(_scored(query, None, mu))

    def rank(match: MangaMatch) -> tuple:
        year_ok = bool(year_hint) and match.year == year_hint
        return (-match.exact, -len(match.sources), -year_ok, -match.similarity)

    return sorted(matches, key=rank)  # stable: AniList's own relevance order breaks ties


def _scored(query: str, ani: AniListSeries | None, mu: MangaUpdatesSeries | None) -> MangaMatch:
    titles = (ani.titles if ani else []) + (mu.titles if mu else [])
    similarity = best_similarity([query], titles)
    return MangaMatch(anilist=ani, mangaupdates=mu, similarity=similarity, exact=similarity >= 0.999)
