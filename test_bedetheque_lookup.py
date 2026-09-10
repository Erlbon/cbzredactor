"""Tests for core/bedetheque_lookup.py -- all network access is faked
via the injectable `fetch` parameter, using response shapes captured
directly from the live site (2026-09-10): the real /ajax/tout search
JSON, and HTML fixtures modeling the real .infos li structure of a
real issue page (https://www.bedetheque.com/BD-Blacksad-Tome-1-...-15161.html),
including the real two-edition-on-one-page gotcha that page actually
has. See module docstring in bedetheque_lookup.py for why this
couldn't just be trusted from the three community scraper projects it
was analyzed from -- the site's been redesigned since they were
written."""

import json

import pytest

from core.bedetheque_lookup import (
    BedethequeLookupError,
    build_search_url,
    download_cover_image,
    fetch_issue_details,
    search_bedetheque,
)

# Captured 2026-09-10 from https://www.bedetheque.com/ajax/tout?term=Blacksad
SEARCH_RESPONSE = [
    {"id": "S500", "label": "Blacksad", "value": "Blacksad", "desc": "skin/flags/France.png", "category": "Séries"},
    {"id": "S86490", "label": "Blacksad (en allemand)", "value": "Blacksad (en allemand)", "desc": "skin/flags/Germany.png", "category": "Séries"},
    {"id": "S25616", "label": "Blacksad (en anglais, Dark Horse)", "value": "Blacksad (en anglais, Dark Horse)", "desc": "skin/flags/USA.png", "category": "Séries"},
]

# A trimmed, realistic stand-in for https://www.bedetheque.com/albums-500-BD-Blacksad.html --
# real album links follow BD-<series-slug>-Tome-<N>-<title-slug>-<id>.html.
ALBUMS_PAGE_HTML = b"""
<html><body>
<div class="liste-albums">
  <a href="/BD-Blacksad-Tome-1-Quelque-part-entre-les-ombres-15161.html">Tome 1</a>
  <a href="/BD-Blacksad-Tome-2-Arctic-Nation-24600.html">Tome 2</a>
  <a href="/BD-Blacksad-HS-What-s-News-426397.html">Hors-serie</a>
</div>
</body></html>
"""

# A realistic stand-in for the real issue page's <head>/.infos structure,
# INCLUDING the real two-printings-on-one-page gotcha (identifiers 15161
# and 391756 both really appear on that live page).
ISSUE_PAGE_HTML = b"""
<html><head>
<meta name="description" content="Par un moche matin couleur sepia, Blacksad, detective prive...">
<meta property="og:image" content="https://www.bedetheque.com/media/Couvertures/Couv_15161.jpg">
</head><body>
<h1>Blacksad</h1>
<ul class="infos">
  <li>Identifiant : 15161</li>
  <li>Sc&eacute;nario : <a href="/auteur-1-BD-Diaz-Canales.html">Diaz Canales, Juan</a></li>
  <li>Dessin : <a href="/auteur-2-BD-Guarnido.html">Guarnido, Juanjo</a></li>
  <li>Couleurs : <a href="/auteur-2-BD-Guarnido.html">Guarnido, Juanjo</a></li>
  <li>Depot legal : 11/2000</li>
  <li>Editeur : <a href="/editeur-1-BD-Dargaud.html">Dargaud</a></li>
</ul>
<ul class="infos">
  <li>Identifiant : 391756</li>
  <li>Sc&eacute;nario : <a href="/auteur-1-BD-Diaz-Canales.html">Diaz Canales, Juan</a></li>
  <li>Depot legal : 06/2002</li>
  <li>Editeur : <a href="/editeur-1-BD-Dargaud.html">Dargaud</a></li>
</ul>
</body></html>
"""


def _fake_fetch(responses: dict):
    def _fetch(url: str) -> bytes:
        for key, value in responses.items():
            if key in url:
                return value
        raise AssertionError(f"unexpected fetch: {url}")

    return _fetch


def test_build_search_url_encodes_the_series_name():
    assert build_search_url("Blacksad & Co") == "https://www.bedetheque.com/ajax/tout?term=Blacksad%20%26%20Co"


def test_build_search_url_rejects_a_blank_series():
    with pytest.raises(BedethequeLookupError):
        build_search_url("")


def test_search_finds_the_matching_issue_url():
    fetch = _fake_fetch(
        {
            "ajax/tout": json.dumps(SEARCH_RESPONSE).encode(),
            "albums-500-BD-Blacksad.html": ALBUMS_PAGE_HTML,
        }
    )
    candidates = search_bedetheque("Blacksad", "1", fetch=fetch)

    assert len(candidates) == 1
    assert candidates[0].series_name == "Blacksad"
    assert candidates[0].issue_number == "1"
    assert candidates[0].detail_url.endswith("BD-Blacksad-Tome-1-Quelque-part-entre-les-ombres-15161.html")


def test_search_promotes_an_exact_series_name_match_first():
    """The autocomplete response already lists the exact "Blacksad"
    match first in this fixture, but a differently-ordered response
    should still end up with it first -- same "don't trust source
    ordering" insurance as GCD's own ranking."""
    reordered = list(reversed(SEARCH_RESPONSE))
    fetch = _fake_fetch(
        {
            "ajax/tout": json.dumps(reordered).encode(),
            "albums-500-BD-Blacksad.html": ALBUMS_PAGE_HTML,
        }
    )
    candidates = search_bedetheque("Blacksad", "1", fetch=fetch)
    assert candidates[0].series_name == "Blacksad"  # not "Blacksad (en anglais, Dark Horse)"


def test_search_returns_empty_when_the_issue_number_is_not_listed_anywhere():
    """Issue 99 isn't on ANY of the (up to max_series_candidates)
    ranked series' album listings -- must fall through all of them
    before concluding "not found", not just check the top match."""
    fetch = _fake_fetch(
        {
            "ajax/tout": json.dumps(SEARCH_RESPONSE).encode(),
            "albums-500-BD-Blacksad.html": ALBUMS_PAGE_HTML,
            "albums-86490-BD-Blacksad-en-allemand.html": ALBUMS_PAGE_HTML,
            "albums-25616-BD-Blacksad-en-anglais-Dark-Horse.html": ALBUMS_PAGE_HTML,
        }
    )
    candidates = search_bedetheque("Blacksad", "99", fetch=fetch)
    assert candidates == []


def test_search_falls_through_to_the_next_candidate_when_the_first_lacks_the_issue():
    """Issue 2 isn't real in this fixture's first candidate's listing
    on its own -- reuses ALBUMS_PAGE_HTML (which DOES have Tome 2) for
    the second candidate to prove the fallback chain actually works,
    not just that a single-candidate search does."""
    empty_albums_page = b"<html><body>no albums here</body></html>"
    fetch = _fake_fetch(
        {
            "ajax/tout": json.dumps(SEARCH_RESPONSE).encode(),
            "albums-500-BD-Blacksad.html": empty_albums_page,
            "albums-86490-BD-Blacksad-en-allemand.html": ALBUMS_PAGE_HTML,
        }
    )
    candidates = search_bedetheque("Blacksad", "2", fetch=fetch)
    assert len(candidates) == 1
    assert candidates[0].series_name == "Blacksad (en allemand)"


def test_search_requires_both_series_and_number():
    with pytest.raises(BedethequeLookupError):
        search_bedetheque("Blacksad", "", fetch=lambda url: b"")
    with pytest.raises(BedethequeLookupError):
        search_bedetheque("", "1", fetch=lambda url: b"")


def test_fetch_issue_details_reads_the_first_edition_block():
    fetch = _fake_fetch({"Tome-1": ISSUE_PAGE_HTML})
    details = fetch_issue_details(
        "https://www.bedetheque.com/BD-Blacksad-Tome-1-Quelque-part-entre-les-ombres-15161.html", fetch=fetch
    )

    assert details.series_name == "Blacksad"
    assert details.writer == "Diaz Canales, Juan"
    assert details.penciller == "Guarnido, Juanjo"
    assert details.colorist == "Guarnido, Juanjo"
    assert details.publisher == "Dargaud"
    assert details.year == "2000"
    assert details.month == "11"
    assert "detective" in details.summary.lower() or "Blacksad" in details.summary
    assert details.cover_image_url == "https://www.bedetheque.com/media/Couvertures/Couv_15161.jpg"
    assert details.language_iso == "fr"


def test_fetch_issue_details_does_not_blend_the_two_editions():
    """The real regression case: this page genuinely lists two
    printings (15161 and 391756). Scoping to the block matching the
    URL's own id must not average/concatenate values across both --
    year here must come from the FIRST edition (2000), not the second
    (2002)."""
    fetch = _fake_fetch({"Tome-1": ISSUE_PAGE_HTML})
    details = fetch_issue_details(
        "https://www.bedetheque.com/BD-Blacksad-Tome-1-Quelque-part-entre-les-ombres-15161.html", fetch=fetch
    )
    assert details.year == "2000"  # not 2002 (the second Identifiant block's own Depot legal)


def test_download_cover_image_requires_a_url():
    from core.bedetheque_lookup import BedethequeIssueDetails

    with pytest.raises(BedethequeLookupError):
        download_cover_image(BedethequeIssueDetails(), fetch=lambda url: b"")


def test_download_cover_image_fetches_the_url():
    from core.bedetheque_lookup import BedethequeIssueDetails

    details = BedethequeIssueDetails(cover_image_url="https://www.bedetheque.com/media/Couvertures/Couv_15161.jpg")
    fetch = _fake_fetch({"Couv_15161.jpg": b"fake-jpeg-bytes"})
    assert download_cover_image(details, fetch=fetch) == b"fake-jpeg-bytes"
