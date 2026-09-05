"""Tests for core/gcd_lookup.py -- all network access is faked via the
injectable `fetch` parameter, using response shapes captured directly
from the live GCD API (see module docstring in gcd_lookup.py for why:
GCD publishes no fixed schema to trust from documentation alone)."""

import json

import pytest

from core.gcd_lookup import (
    GcdIssueDetails,
    GcdLookupError,
    build_search_url,
    download_cover_image,
    fetch_issue_details,
    parse_search_response,
    search_gcd,
)

# Captured 2026-09-05 from https://www.comics.org/api/series/name/Watchmen/issue/1/year/1986/?format=json
SEARCH_RESPONSE = {
    "count": 1,
    "next": None,
    "previous": None,
    "results": [
        {
            "api_url": "https://www.comics.org/api/issue/41815/?format=json",
            "series_name": "Watchmen (1986 series)",
            "descriptor": "1",
            "publication_date": "September 1986",
            "price": "1.50 USD; 2.10 CAD",
            "page_count": "36.000",
            "variant_of": None,
            "series": "https://www.comics.org/api/series/3172/?format=json",
        }
    ],
}

# Captured 2026-09-05 from https://www.comics.org/api/issue/41815/?format=json
# (trimmed to the fields this module actually reads)
ISSUE_DETAIL_RESPONSE = {
    "api_url": "https://www.comics.org/api/issue/41815/?format=json",
    "series_name": "Watchmen (1986 series)",
    "descriptor": "1",
    "number": "1",
    "title": "",
    "key_date": "1986-09-00",
    "editing": "Len Wein (credited) (editor); Richard Bruning (credited) (designer); Dick Giordano (credited) (executive editor)",
    "indicia_publisher": "DC Comics Inc.",
    "cover": "https://files1.comics.org//img/gcd/covers_by_id/17/w400/17183.jpg",
    "story_set": [
        {
            "type": "cover", "title": "", "feature": "Watchmen", "sequence_number": 0,
            "script": None, "pencils": "Dave Gibbons", "inks": "Dave Gibbons", "colors": "John Higgins",
            "letters": None, "editing": None, "genre": "superhero", "characters": "", "synopsis": "",
        },
        {
            "type": "credits, title page", "title": "Indicia", "feature": "", "sequence_number": 1,
            "script": None, "pencils": None, "inks": None, "colors": None, "letters": "typeset",
            "editing": None, "genre": "", "characters": "", "synopsis": "",
        },
        {
            "type": "comic story", "title": "At Midnight, All the Agents...", "feature": "Watchmen", "sequence_number": 2,
            "script": "Alan Moore (credited)", "pencils": "Dave Gibbons (credited)", "inks": "Dave Gibbons (credited)",
            "colors": "John Higgins (credited)", "letters": "Dave Gibbons (credited)", "editing": None,
            "genre": "superhero",
            "characters": "Rorschach [Walter Kovacs]; Edward Blake (death); Doctor Manhattan [Jonathan Osterman]",
            "synopsis": "",
        },
        {
            "type": "text story", "title": "Under the Hood, Chapters 1 and 2", "feature": "Watchmen", "sequence_number": 3,
            "script": "Alan Moore", "pencils": "Dave Gibbons (illustrations)", "inks": "Dave Gibbons (illustrations)",
            "colors": None, "letters": "typeset", "editing": None, "genre": "superhero",
            "characters": "Hooded Justice; Nite Owl [Hollis Mason]",
            "synopsis": "",
        },
    ],
}

# Captured 2026-09-05 from a localized/translated edition -- exercises
# multi-annotation stripping ("(credited) (tradução)") and a decimal-
# formatted page_count/price shape.
TRANSLATED_ISSUE_RESPONSE = {
    "series_name": "Antes de Watchmen (2013 series)",
    "number": "1",
    "title": "",
    "key_date": "2013-05-00",
    "editing": "Will Dennis (credited) (editor original)",
    "indicia_publisher": "Panini Brasil Ltda.",
    "cover": "",
    "story_set": [
        {
            "type": "comic story", "title": "Não existe mais almoço de graça", "feature": "Watchmen",
            "script": "J. Michael Straczynski (credited); Jotapê Martins (credited) (tradução)",
            "pencils": "Andy Kubert (credited)", "inks": "Joe Kubert (credited)",
            "colors": "Brad Anderson (credited)", "letters": "Donizeti Amorim (credited)",
            "editing": None, "genre": "superhero", "characters": "Coruja [Dan Dreiberg]", "synopsis": "",
        },
    ],
}


def _fetch_returning(payload: dict):
    def _fetch(url: str) -> bytes:
        return json.dumps(payload).encode("utf-8")

    return _fetch


def test_build_search_url_requires_both_series_and_number():
    with pytest.raises(GcdLookupError):
        build_search_url("Watchmen", "")
    with pytest.raises(GcdLookupError):
        build_search_url("", "1")


def test_build_search_url_shape():
    url = build_search_url("Watchmen", "1")
    assert url == "https://www.comics.org/api/series/name/Watchmen/issue/1/?format=json"


def test_parse_search_response_strips_series_year_suffix():
    raw = json.dumps(SEARCH_RESPONSE).encode("utf-8")
    candidates = parse_search_response(raw)
    assert len(candidates) == 1
    assert candidates[0].series_name == "Watchmen"  # "(1986 series)" stripped
    assert candidates[0].issue_number == "1"
    assert candidates[0].detail_url == "https://www.comics.org/api/issue/41815/?format=json"


def test_search_gcd_end_to_end():
    candidates = search_gcd("Watchmen", "1", fetch=_fetch_returning(SEARCH_RESPONSE))
    assert len(candidates) == 1
    assert candidates[0].display_label() == "Watchmen #1 (September 1986)"


def test_search_gcd_404_returns_empty_list_not_error():
    import urllib.error

    def _fetch(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    assert search_gcd("Nonexistent Series", "999", fetch=_fetch) == []


def test_fetch_issue_details_only_aggregates_comic_story_entries():
    details = fetch_issue_details(
        "https://comics.org/api/issue/41815/?format=json", fetch=_fetch_returning(ISSUE_DETAIL_RESPONSE)
    )
    # Cover/credits-page/text-story entries have their own script/pencils
    # etc, but only the "comic story" entry should be aggregated.
    assert details.writer == "Alan Moore"
    assert details.penciller == "Dave Gibbons"
    assert details.inker == "Dave Gibbons"
    assert details.colorist == "John Higgins"
    assert details.letterer == "Dave Gibbons"
    assert details.title == "At Midnight, All the Agents..."


def test_fetch_issue_details_editor_falls_back_to_issue_level():
    details = fetch_issue_details(
        "https://comics.org/api/issue/41815/?format=json", fetch=_fetch_returning(ISSUE_DETAIL_RESPONSE)
    )
    # The comic-story entry's own "editing" is None -- falls back to the
    # whole-issue editing credit.
    assert details.editor == "Len Wein, Richard Bruning, Dick Giordano"


def test_fetch_issue_details_splits_key_date_treating_00_as_unknown():
    details = fetch_issue_details(
        "https://comics.org/api/issue/41815/?format=json", fetch=_fetch_returning(ISSUE_DETAIL_RESPONSE)
    )
    assert details.year == "1986"
    assert details.month == "9"
    assert details.day == ""  # "00" day -- unknown, not a literal 0


def test_fetch_issue_details_reads_publisher_directly_no_extra_call():
    details = fetch_issue_details(
        "https://comics.org/api/issue/41815/?format=json", fetch=_fetch_returning(ISSUE_DETAIL_RESPONSE)
    )
    assert details.publisher == "DC Comics Inc."


def test_fetch_issue_details_series_name_cleaned():
    details = fetch_issue_details(
        "https://comics.org/api/issue/41815/?format=json", fetch=_fetch_returning(ISSUE_DETAIL_RESPONSE)
    )
    assert details.series_name == "Watchmen"


def test_multi_annotation_and_translation_credits_stripped():
    details = fetch_issue_details(
        "https://comics.org/api/issue/999/?format=json", fetch=_fetch_returning(TRANSLATED_ISSUE_RESPONSE)
    )
    assert details.writer == "J. Michael Straczynski, Jotapê Martins"
    assert details.editor == "Will Dennis"  # "(credited) (editor original)" both stripped
    assert details.month == "5"
    assert details.day == ""  # "00" day -- unknown


def test_as_dict_omits_empty_fields():
    details = GcdIssueDetails(series_name="Some Series", writer="Jane Doe")
    result = details.as_dict()
    assert result == {"series": "Some Series", "writer": "Jane Doe"}


def test_download_cover_image_requires_url():
    details = GcdIssueDetails(cover_image_url="")
    with pytest.raises(GcdLookupError):
        download_cover_image(details)


def test_download_cover_image_returns_bytes():
    details = GcdIssueDetails(cover_image_url="https://example.com/cover.jpg")
    fake_bytes = b"\xff\xd8\xff\xe0fakejpeg"
    assert download_cover_image(details, fetch=lambda url: fake_bytes) == fake_bytes


# Real-world shape confirmed live 2026-09-05: searching "Watchmen" #1
# returns 62 results across 2 pages, alphabetically ordered -- dozens
# of "Antes de Watchmen"/"Before Watchmen: ..." spin-offs sort ahead of
# the actual "Watchmen (1986 series)" purely because A/B < W.
_SPINOFF_PAGE_1 = {
    "count": 62,
    "next": "https://www.comics.org/api/series/name/Watchmen/issue/1/?format=json&page=2",
    "results": [
        {"series_name": "Antes de Watchmen (2013 series)", "descriptor": "1 - Coruja",
         "publication_date": "maio 2013", "api_url": "https://x/issue/1/?format=json"},
        {"series_name": "Before Watchmen: Comedian (2012 series)", "descriptor": "1",
         "publication_date": "2012", "api_url": "https://x/issue/2/?format=json"},
    ],
}
_SPINOFF_PAGE_2_WITH_EXACT_MATCH = {
    "count": 62,
    "next": "https://www.comics.org/api/series/name/Watchmen/issue/1/?format=json&page=3",
    "results": [
        {"series_name": "DC Comics Essentials: Watchmen (2014 series)", "descriptor": "1",
         "publication_date": "2014", "api_url": "https://x/issue/3/?format=json"},
        {"series_name": "Watchmen (1986 series)", "descriptor": "1",
         "publication_date": "September 1986", "api_url": "https://www.comics.org/api/issue/41815/?format=json"},
        {"series_name": "Watchmen (1987 series)", "descriptor": "1",
         "publication_date": "1987", "api_url": "https://x/issue/4/?format=json"},
    ],
}


def test_search_gcd_ranks_exact_match_first_even_from_a_later_page():
    pages = [_SPINOFF_PAGE_1, _SPINOFF_PAGE_2_WITH_EXACT_MATCH]
    calls = []

    def _fetch(url):
        calls.append(url)
        return json.dumps(pages[len(calls) - 1]).encode("utf-8")

    candidates = search_gcd("Watchmen", "1", fetch=_fetch)
    assert candidates[0].series_name == "Watchmen"
    assert candidates[0].detail_url == "https://www.comics.org/api/issue/41815/?format=json"
    # Stops paginating once the exact match is found -- doesn't fetch a
    # hypothetical page 3, even though page 2's "next" pointed at one.
    assert len(calls) == 2


def test_search_gcd_stops_after_max_pages_if_never_found():
    from core.gcd_lookup import MAX_SEARCH_PAGES

    no_match_page = {
        "count": 999,
        "next": "https://x/next/",
        "results": [{"series_name": "Something Else Entirely", "descriptor": "1",
                     "publication_date": "", "api_url": "https://x/issue/9/?format=json"}],
    }
    calls = []

    def _fetch(url):
        calls.append(url)
        return json.dumps(no_match_page).encode("utf-8")

    candidates = search_gcd("Watchmen", "1", fetch=_fetch)
    assert len(calls) == MAX_SEARCH_PAGES  # gave up, didn't loop forever
    assert len(candidates) == MAX_SEARCH_PAGES  # one candidate collected per page
    assert candidates[0].series_name == "Something Else Entirely"  # no exact match -- original order kept


def test_search_gcd_single_page_no_pagination_needed():
    def _fetch(url):
        return json.dumps(SEARCH_RESPONSE).encode("utf-8")

    candidates = search_gcd("Watchmen", "1", fetch=_fetch)
    assert len(candidates) == 1
    assert candidates[0].series_name == "Watchmen"
