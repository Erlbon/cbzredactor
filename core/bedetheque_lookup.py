"""
core/bedetheque_lookup.py

Looks up issue metadata from Bedetheque (https://www.bedetheque.com),
the reference database for French-language "bande dessinée" (BD) --
useful for francophone comics Comic Vine and GCD tend to have thin or
no coverage of.

Analyzed (not copied from) three community scraper projects before
writing this -- givka/bedetheque-scraper (TypeScript/Cheerio,
archived), maforget/Bedetheque-Scrapper-2 (an old ComicRack plugin),
and vsoeiro/bedetheque (modern async Python) -- then verified directly
against the live site (2026-09-10), since two of the three are years
old and the site has clearly been redesigned since: all three
documented an individual-issue URL scheme (`album-<id>-....html`)
that no longer exists. What's still accurate, cross-validated by all
three independently and then confirmed live, is the underlying
`.infos li` "Label : Value" field structure a real issue page uses.

Bedetheque sits behind Cloudflare -- a plain HTTP request gets 403 on
every page, search included (confirmed live; it's why maforget's old
plugin needed a whole separate cookie/User-Agent-owning daemon just to
work at all). `make_bedetheque_fetch()` wraps a `cloudscraper` session
(solves Cloudflare's JS challenge, then behaves like a normal session)
into the same `fetch(url) -> bytes` shape every other lookup module
uses, translating its exceptions into the `urllib.error` ones
redactor_common.core.lookup_client already knows how to turn into a
friendly message -- Bedetheque is the only source needing this
adapter. Deliberately chose `cloudscraper` over bundling a real
headless browser (Playwright): lighter dependency, no browser binary
to ship in the PyInstaller build, an acceptable tradeoff for one-
issue-at-a-time lookups rather than a bulk crawl. Known and accepted
risk: `cloudscraper`-style libraries are a running cat-and-mouse game
against Cloudflare and can need a version bump if Cloudflare changes
its challenge -- Playwright is the fallback if it ever stops working
outright.

The actual lookup pipeline, verified live end to end:

  GET https://www.bedetheque.com/ajax/tout?term=<series>
      -> JSON array of {"id", "label", "value", "desc", "category"}.
      A real, modern autocomplete API (found via the network tab, not
      documented by any of the three tools) -- no HTML results page to
      scrape. `id` is prefixed by type ("S" for "Séries"); this module
      only ever looks at Séries entries.

  GET https://www.bedetheque.com/albums-<series_id>-BD-<slug>.html
      -> the series' full issue listing (a separate page from the
      "about this series" card at serie-<id>-BD-<slug>.html -- the
      site's own tabbed UI, ALBUMS/AVIS/VENTES/PARA-BD/GALERIE, are
      each their own page). Issue links look like
      BD-<Series-Slug>-Tome-<N>-<Title-Slug>-<album_id>.html; matched
      here by the "-Tome-<N>-" segment against the requested number.

  GET <album detail URL from above>
      -> the actual issue page. Fields come from a `.infos li` list of
      "Label : Value" entries (Scénario/Dessin/Couleurs/Encrage/
      Lettrage/Préface/Editeur/Dépot légal/EAN-ISBN/...), a summary via
      the standard <meta name="description">, a cover image via
      <meta property="og:image">, and a rating via the schema.org
      itemprop="ratingValue" microdata.

      Real gotcha, confirmed live: a single page can list `.infos li`
      blocks for MORE than one printing/edition of the same content,
      each starting its own "Identifiant : <id>" line. Extraction here
      is scoped to the block whose own Identifiant matches the page's
      own album id (parsed from the URL) -- grabbing every `.infos li`
      on the page indiscriminately would blend two different
      printings' data together.

Some field-mapping decisions are deliberately conservative given how
few real pages this was verified against (one series, one issue):
credits are joined as plain comma-separated text with no attempt to
reformat "Lastname, Firstname" (Bedetheque's own convention) the way
GCD's free-text credits get minimal cleanup, not restructuring;
Bedetheque's own "Format" field (a physical print-size descriptor,
e.g. "Grand format") is deliberately NOT mapped onto ComicInfo's
Format field, which means something different (digital/paperback/
hardcover) in the schema this app otherwise follows -- forcing that
mapping risks mistagging every issue from this source. `language_iso`
defaults to "fr" unconditionally (there's no explicit per-issue
language field to read; Bedetheque's entire scope is francophone BD,
so this is a reasonable default for a source chosen specifically for
French-language coverage, not a verified per-issue fact).
"""

from __future__ import annotations

import re
import urllib.error
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import lxml.html

from redactor_common.core.lookup_client import fetch_bytes, fetch_json

DEFAULT_TIMEOUT = 12.0  # generous vs. the other sources' 8s -- cloudscraper's challenge-solve adds real latency
SEARCH_URL = "https://www.bedetheque.com/ajax/tout"
_SOURCE_NAME = "Bedetheque"


class BedethequeLookupError(Exception):
    """Raised for any problem searching for, parsing, or downloading
    Bedetheque results."""


@dataclass
class BedethequeCandidate:
    """One search result -- enough to display and to fetch full
    details for. Title/summary/credits aren't known until
    fetch_issue_details() runs, same two-step shape as GCD's own
    GcdCandidate."""

    series_name: str = ""
    issue_number: str = ""
    detail_url: str = ""

    def display_label(self) -> str:
        bits = [self.series_name or "(unknown series)"]
        if self.issue_number:
            bits.append(f"#{self.issue_number}")
        return " ".join(bits)


@dataclass
class BedethequeIssueDetails:
    series_name: str = ""
    issue_number: str = ""
    title: str = ""
    year: str = ""
    month: str = ""
    summary: str = ""
    writer: str = ""
    penciller: str = ""
    inker: str = ""
    colorist: str = ""
    letterer: str = ""
    publisher: str = ""
    language_iso: str = "fr"
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
            "writer": self.writer,
            "penciller": self.penciller,
            "inker": self.inker,
            "colorist": self.colorist,
            "letterer": self.letterer,
            "publisher": self.publisher,
            "language_iso": self.language_iso,
        }
        return {k: v for k, v in raw.items() if v}


def make_bedetheque_fetch(session=None):
    """Wraps a cloudscraper session into a fetch(url) -> bytes
    callable -- see module docstring for why Bedetheque specifically
    needs this instead of redactor_common.core.lookup_client.
    make_default_fetch()'s plain urllib GET. `session` is a real
    cloudscraper session for production use (see BedethequeLookupDialog,
    which owns exactly one for its whole lifetime -- solving
    Cloudflare's challenge fresh on every single request would be slow
    and needlessly conspicuous); pass a stand-in with a `.get()` method
    for tests, so nothing here needs cloudscraper installed to unit-test
    the parsing logic.

    cloudscraper is a real (if lighter-weight than a browser) HTTP
    client, not a browser -- import is lazy so this module (and its
    parsing logic) can be imported and tested without cloudscraper
    installed, same reasoning as this project's other optional-
    dependency modules (core/cbr_convert.py's rarfile import)."""
    if session is None:
        import cloudscraper

        session = cloudscraper.create_scraper()

    def _fetch(url: str) -> bytes:
        try:
            response = session.get(url, timeout=DEFAULT_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- cloudscraper/requests raise several exception
            # types (Timeout, ConnectionError, its own challenge-solving
            # failures); all of them mean "couldn't reach the site,"
            # which is exactly what urllib.error.URLError already
            # means to fetch_json()/fetch_bytes() below.
            raise urllib.error.URLError(exc) from exc
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, response.reason, None, None)
        return response.content

    return _fetch


def build_search_url(series: str) -> str:
    series = (series or "").strip()
    if not series:
        raise BedethequeLookupError("A series name is required to search Bedetheque.")
    return f"{SEARCH_URL}?term={quote(series)}"


def _parse_series_entries(data: list) -> list[tuple[str, str]]:
    """(series_id, series_label) pairs for every "Séries" entry in the
    autocomplete response -- ignores Auteurs/Revues/other categories,
    which this module doesn't look up."""
    entries = []
    for entry in data or []:
        if entry.get("category") != "Séries":
            continue
        raw_id = entry.get("id", "") or ""
        if raw_id.startswith("S") and raw_id[1:].isdigit():
            entries.append((raw_id[1:], entry.get("label", "") or ""))
    return entries


def _rank_series_entries(entries: list[tuple[str, str]], queried_series: str) -> list[tuple[str, str]]:
    """Puts an exact (case-insensitive) name match first -- same
    "don't trust the source's own ordering" reasoning as GCD's
    _rank_candidates(), though Bedetheque's autocomplete already tends
    to rank reasonably; cheap insurance either way."""
    query = queried_series.strip().lower()
    return sorted(entries, key=lambda e: e[1].strip().lower() != query)


ALBUM_LINK_RE = re.compile(r"-Tome-(\d+)-", re.IGNORECASE)


def _find_album_url(albums_page_html: bytes, base_url: str, number: str) -> Optional[str]:
    """Finds the album link matching `number` (a "Tome <N>" segment) on
    a /albums-<id>-BD-<slug>.html listing page. Returns an absolute
    URL, or None if that issue number isn't listed for this series."""
    tree = lxml.html.fromstring(albums_page_html)
    tree.make_links_absolute(base_url)
    wanted = (number or "").strip().lstrip("0") or "0"

    for link in tree.xpath("//a[@href]"):
        href = link.get("href", "")
        match = ALBUM_LINK_RE.search(href)
        if match and match.group(1).lstrip("0") == wanted:
            return href
    return None


def search_bedetheque(series: str, number: str, fetch=None, max_series_candidates: int = 3) -> list[BedethequeCandidate]:
    """Searches for the issue matching `series` + `number` -- like GCD
    (and unlike Comic Vine), Bedetheque has no free-text "search
    issues directly" endpoint; this module finds the SERIES via the
    autocomplete API, then looks for the requested issue number on
    that series' own album listing page. Tries up to
    `max_series_candidates` ranked series matches (not just the top
    one) before giving up, since the requested number might not exist
    under the single best-ranked name match (e.g. a same-named
    original-language vs. translated edition).

    Raises BedethequeLookupError on a missing series/number or a
    network/parsing failure. Returns an empty list (not an error) when
    the series is found but doesn't have the requested issue number,
    or when nothing matches the series name at all."""
    if fetch is None:
        fetch = make_bedetheque_fetch()
    series = (series or "").strip()
    number = (number or "").strip()
    if not series or not number:
        raise BedethequeLookupError("Both a series name and an issue number are required to search Bedetheque.")

    search_data = fetch_json(build_search_url(series), fetch, error_cls=BedethequeLookupError, source_name=_SOURCE_NAME)
    entries = _rank_series_entries(_parse_series_entries(search_data), series)

    # Stops at the first series candidate that actually has the
    # requested issue number, rather than checking every ranked
    # candidate unconditionally -- there's no downstream UI for
    # presenting more than one match per file anyway (LookupDialogBase
    # takes the single best result), so continuing to fetch further
    # candidates' album listings after a hit would just be extra,
    # unused requests against a site this module is already careful
    # about not hammering.
    for series_id, series_label in entries[:max_series_candidates]:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", series_label).strip("-") or "serie"
        albums_url = f"https://www.bedetheque.com/albums-{series_id}-BD-{slug}.html"
        raw = fetch_bytes(albums_url, fetch, error_cls=BedethequeLookupError, what="the series' album listing")
        album_url = _find_album_url(raw, albums_url, number)
        if album_url:
            return [BedethequeCandidate(series_name=series_label, issue_number=number, detail_url=album_url)]

    return []


def _split_info_blocks(info_lis: list, li_texts: list[str]) -> list[tuple[dict[str, str], list]]:
    """Groups a flat <li> list into one (fields_dict, li_elements) pair
    per printing/edition -- Bedetheque can list more than one on the
    same page, each starting its own "Identifiant : <id>" line (see
    module docstring). `li_elements` are that block's own <li> nodes
    (not just their text), so _credit_text() can still follow each
    credited person's <a> link within the right block."""
    boundaries = [i for i, text in enumerate(li_texts) if text.lower().startswith("identifiant")] or [0]
    boundaries.append(len(li_texts))

    blocks: list[tuple[dict[str, str], list]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        fields: dict[str, str] = {}
        for text in li_texts[start:end]:
            if ":" not in text:
                continue
            label, _, value = text.partition(":")
            fields[label.strip().lower()] = value.strip()
        blocks.append((fields, info_lis[start:end]))
    return blocks


def _credit_text(label_variants: list[str], info_lis: list) -> str:
    """Finds the <li> whose own label matches one of `label_variants`
    (case-insensitive) and returns every linked name in it joined with
    ", " -- Bedetheque links each credited person to their own author
    page, so a <li> with several <a> tags means several people credited
    for that role; falls back to the <li>'s own plain text if there are
    no links at all (a name Bedetheque hasn't cross-referenced yet)."""
    for li in info_lis:
        text = " ".join(li.text_content().split())
        label, _, value = text.partition(":")
        if label.strip().lower() in label_variants:
            names = [a.text_content().strip() for a in li.xpath(".//a") if a.text_content().strip()]
            if names:
                return ", ".join(dict.fromkeys(names))
            return value.strip()
    return ""


_DEPOT_LEGAL_RE = re.compile(r"(\d{1,2})/(\d{4})")
_ALBUM_ID_RE = re.compile(r"-(\d+)\.html(?:[?#].*)?$")


def fetch_issue_details(detail_url: str, fetch=None) -> BedethequeIssueDetails:
    if fetch is None:
        fetch = make_bedetheque_fetch()
    raw = fetch_bytes(detail_url, fetch, error_cls=BedethequeLookupError, what="the issue page")

    try:
        tree = lxml.html.fromstring(raw)
    except Exception as exc:  # noqa: BLE001 -- lxml raises its own parser errors
        raise BedethequeLookupError("Received an unreadable response from Bedetheque.") from exc

    album_id_match = _ALBUM_ID_RE.search(detail_url)
    album_id = album_id_match.group(1) if album_id_match else ""

    info_lis = tree.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " infos ")]//li')
    li_texts = [" ".join(li.text_content().split()) for li in info_lis]
    blocks = _split_info_blocks(info_lis, li_texts)
    info, block_lis = next(
        ((fields, lis) for fields, lis in blocks if fields.get("identifiant") == album_id),
        blocks[0] if blocks else ({}, []),
    )

    series_name = ""
    h1 = tree.xpath("//h1")
    if h1:
        series_name = h1[0].text_content().strip()

    depot_legal = info.get("dépot légal", "") or info.get("depot legal", "")
    date_match = _DEPOT_LEGAL_RE.search(depot_legal)
    month, year = (date_match.group(1), date_match.group(2)) if date_match else ("", "")
    month = str(int(month)) if month else ""

    meta_description = tree.xpath('//meta[@name="description"]/@content')
    og_image = tree.xpath('//meta[@property="og:image"]/@content')

    return BedethequeIssueDetails(
        series_name=series_name,
        issue_number="",  # filled in by the caller, which already knows the number it searched for
        title="",  # Bedetheque doesn't give the individual issue a title distinct from the series name
        year=year,
        month=month,
        summary=(meta_description[0].strip() if meta_description else ""),
        writer=_credit_text(["scénario", "scenario"], block_lis),
        penciller=_credit_text(["dessin"], block_lis),
        inker=_credit_text(["encrage"], block_lis),
        colorist=_credit_text(["couleurs"], block_lis),
        letterer=_credit_text(["lettrage"], block_lis),
        publisher=info.get("editeur", ""),
        cover_image_url=(og_image[0].strip() if og_image else ""),
    )


def download_cover_image(details: BedethequeIssueDetails, fetch=None) -> bytes:
    """Downloads the issue's cover image bytes, for display only (see
    core/comicvine_lookup.py's module docstring for why a lookup's
    cover is never written into the archive -- same reasoning applies
    here). Raises BedethequeLookupError if there's no cover_image_url
    or the download fails."""
    if not details.cover_image_url:
        raise BedethequeLookupError("This issue has no cover image available.")
    if fetch is None:
        fetch = make_bedetheque_fetch()
    return fetch_bytes(details.cover_image_url, fetch, error_cls=BedethequeLookupError, what="cover image")
