"""Tests for core/comicvine_lookup.py -- all network access is faked
via the injectable `fetch` parameter, using response shapes matching
the real, documented Comic Vine API schema."""

import json

import pytest

from core.comicvine_lookup import (
    ComicVineLookupError,
    build_search_url,
    download_cover_image,
    fetch_issue_details,
    fetch_publisher,
    fetch_volume_info,
    filter_candidates_by_series,
    parse_search_response,
    search_comicvine,
)
from core.comicvine_lookup import ComicVineCandidate

SEARCH_RESPONSE = {
    "error": "OK",
    "status_code": 1,
    "number_of_total_results": 2,
    "results": [
        {
            "id": 111,
            "name": None,
            "issue_number": "1",
            "cover_date": "1985-01-01",
            "deck": "The origin story.",
            "description": "<p>A longer HTML description.</p>",
            "image": {"small_url": "https://example.com/111-small.jpg", "thumb_url": "https://example.com/111-thumb.jpg"},
            "volume": {"id": 999, "name": "Amazing Test Comics", "api_detail_url": "https://comicvine.gamespot.com/api/volume/4050-999/"},
            "api_detail_url": "https://comicvine.gamespot.com/api/issue/4000-111/",
        },
        {
            # Sparse entry: no name/deck/image/volume at all -- must not crash.
            "id": 222,
            "issue_number": "2",
            "cover_date": "",
            "api_detail_url": "https://comicvine.gamespot.com/api/issue/4000-222/",
        },
    ],
}

ISSUE_DETAIL_RESPONSE = {
    "error": "OK",
    "status_code": 1,
    "results": {
        "name": None,
        "issue_number": "1",
        "cover_date": "1985-01-01",
        "deck": "The origin story.",
        "description": "<p>ignored since deck is present</p>",
        "volume": {"id": 999, "name": "Amazing Test Comics", "api_detail_url": "https://comicvine.gamespot.com/api/volume/4050-999/"},
        "person_credits": [
            {"id": 1, "name": "Jane Doe", "role": "writer"},
            {"id": 2, "name": "John Smith", "role": "penciler, inker"},
            {"id": 3, "name": "Ann Lee", "role": "colorist"},
            {"id": 4, "name": "Sam Cover", "role": "cover"},
            {"id": 5, "name": "No Role Person", "role": ""},
        ],
        "character_credits": [{"id": 10, "name": "Captain Test"}, {"id": 11, "name": "Doctor Fixture"}],
        "team_credits": [{"id": 20, "name": "The Testers"}],
        "location_credits": [{"id": 30, "name": "Test City"}],
        "story_arc_credits": [],
    },
}

VOLUME_RESPONSE = {
    "error": "OK",
    "status_code": 1,
    "results": {
        "id": 999, "name": "Amazing Test Comics",
        "publisher": {"id": 5, "name": "Test Publisher"}, "start_year": "1985",
    },
}

# Two same-named volumes, different publisher/start_year -- the kind
# of ambiguity filter_candidates_by_series() exists to resolve.
VOLUME_RESPONSE_2016 = {
    "error": "OK",
    "status_code": 1,
    "results": {"id": 100, "name": "Batman", "publisher": {"id": 1, "name": "DC Comics"}, "start_year": "2016"},
}
VOLUME_RESPONSE_2011 = {
    "error": "OK",
    "status_code": 1,
    "results": {"id": 200, "name": "Batman", "publisher": {"id": 2, "name": "Some Other Press"}, "start_year": "2011"},
}

INVALID_KEY_RESPONSE = {"error": "Invalid API Key", "status_code": 100, "results": []}


def _fetch_returning(payload: dict):
    def _fetch(url: str) -> bytes:
        return json.dumps(payload).encode("utf-8")

    return _fetch


def _fetch_dispatch(mapping: dict[str, dict]):
    """Routes to a different fixture payload depending on which
    volume's detail URL was actually requested -- for
    filter_candidates_by_series(), which fetches each candidate's own
    volume separately."""
    def _fetch(url: str) -> bytes:
        for prefix, payload in mapping.items():
            if url.startswith(prefix):
                return json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected url in test: {url}")

    return _fetch


def _never_fetch(url: str) -> bytes:
    raise AssertionError("should not have made a network request")


def test_build_search_url_includes_series_and_number():
    url = build_search_url("KEY123", "Amazing Test Comics", "1")
    assert "api_key=KEY123" in url
    assert "resources=issue" in url
    assert "Amazing" in url


def test_build_search_url_requires_series():
    with pytest.raises(ComicVineLookupError):
        build_search_url("KEY123", "", "1")


def test_search_requires_api_key():
    with pytest.raises(ComicVineLookupError, match="API key"):
        search_comicvine("", "Amazing Test Comics", fetch=_fetch_returning(SEARCH_RESPONSE))


def test_parse_search_response_reads_candidates():
    raw = json.dumps(SEARCH_RESPONSE).encode("utf-8")
    candidates = parse_search_response(raw)
    assert len(candidates) == 2

    first = candidates[0]
    assert first.volume_name == "Amazing Test Comics"
    assert first.issue_number == "1"
    assert first.summary == "The origin story."  # deck preferred over HTML description
    assert first.image_url == "https://example.com/111-small.jpg"
    assert first.detail_url == "https://comicvine.gamespot.com/api/issue/4000-111/"
    assert first.volume_detail_url == "https://comicvine.gamespot.com/api/volume/4050-999/"


def test_parse_search_response_handles_sparse_entry():
    raw = json.dumps(SEARCH_RESPONSE).encode("utf-8")
    candidates = parse_search_response(raw)
    second = candidates[1]
    assert second.volume_name == ""
    assert second.issue_number == "2"
    assert second.image_url == ""


def test_search_comicvine_end_to_end():
    candidates = search_comicvine(
        "KEY123", "Amazing Test Comics", "1", fetch=_fetch_returning(SEARCH_RESPONSE)
    )
    assert len(candidates) == 2
    assert candidates[0].display_label() == "Amazing Test Comics #1 (1985)"


def test_invalid_api_key_raises_clear_error():
    with pytest.raises(ComicVineLookupError, match="Invalid API [Kk]ey"):
        search_comicvine("BADKEY", "Amazing Test Comics", fetch=_fetch_returning(INVALID_KEY_RESPONSE))


# Ranking -- Comic Vine's own /search/ order is closer to text
# relevance than to "which of these is actually the right release", so
# search_comicvine() re-ranks the raw results itself (see
# core/comicvine_lookup.py's _score_candidate()/_rank_candidates()).

RANKING_RESPONSE = {
    "error": "OK",
    "status_code": 1,
    "results": [
        {
            # Ranks first by raw text relevance (a substring hit on the
            # query), but it's issue #1 of an unrelated same-named
            # series from the wrong year -- the wrong release.
            "id": 1,
            "issue_number": "1",
            "cover_date": "1962-01-01",
            "volume": {"id": 100, "name": "Amazing Spider-Man Annual", "api_detail_url": "https://x/volume/100/"},
            "api_detail_url": "https://x/issue/1/",
        },
        {
            # The actual release: right series words, right issue
            # number, right year -- ranks first only once scored.
            "id": 2,
            "issue_number": "12",
            "cover_date": "2016-03-01",
            "volume": {"id": 200, "name": "Amazing Spider-Man", "api_detail_url": "https://x/volume/200/"},
            "api_detail_url": "https://x/issue/2/",
        },
        {
            # Right series, right year, but the wrong issue number.
            "id": 3,
            "issue_number": "13",
            "cover_date": "2016-04-01",
            "volume": {"id": 200, "name": "Amazing Spider-Man", "api_detail_url": "https://x/volume/200/"},
            "api_detail_url": "https://x/issue/3/",
        },
    ],
}


def test_ranking_prefers_matching_issue_number_and_year_over_raw_order():
    candidates = search_comicvine(
        "KEY123",
        "Amazing Spider-Man",
        "12",
        year_hint="2016",
        fetch=_fetch_returning(RANKING_RESPONSE),
    )
    assert [c.issue_id for c in candidates] == ["2", "3", "1"]


def test_ranking_without_a_number_or_year_hint_still_favors_name_match():
    # No number/year signal available -- word-overlap alone should
    # still keep the exact-name "Amazing Spider-Man" candidates ahead
    # of the "Amazing Spider-Man Annual" one (an extra, unmatched word).
    candidates = search_comicvine(
        "KEY123", "Amazing Spider-Man", fetch=_fetch_returning(RANKING_RESPONSE)
    )
    assert candidates[0].issue_id in ("2", "3")
    assert candidates[-1].issue_id == "1"


def test_fetch_issue_details_parses_credits_by_role():
    details = fetch_issue_details(
        "KEY123",
        "https://comicvine.gamespot.com/api/issue/4000-111/",
        fetch=_fetch_returning(ISSUE_DETAIL_RESPONSE),
    )
    assert details.writer == "Jane Doe"
    assert details.penciller == "John Smith"
    assert details.inker == "John Smith"  # credited for both roles in one comma-separated string
    assert details.colorist == "Ann Lee"
    assert details.cover_artist == "Sam Cover"
    assert details.letterer == ""  # nobody credited
    assert details.characters == "Captain Test, Doctor Fixture"
    assert details.teams == "The Testers"
    assert details.locations == "Test City"
    assert details.story_arc == ""
    assert details.summary == "The origin story."


def test_issue_details_as_dict_omits_empty_fields_and_splits_date():
    details = fetch_issue_details(
        "KEY123", "https://x/issue/4000-111/", fetch=_fetch_returning(ISSUE_DETAIL_RESPONSE)
    )
    result = details.as_dict()
    assert result["year"] == "1985"
    assert result["month"] == "1"
    assert result["day"] == "1"
    assert "letterer" not in result  # empty fields excluded
    assert "publisher" not in result  # never set by fetch_issue_details itself


def test_fetch_publisher_success():
    name = fetch_publisher(
        "KEY123", "https://comicvine.gamespot.com/api/volume/4050-999/", fetch=_fetch_returning(VOLUME_RESPONSE)
    )
    assert name == "Test Publisher"


def test_fetch_publisher_swallows_errors():
    def _broken_fetch(url):
        raise ConnectionError("network is down")

    # Never raises -- publisher lookup is best-effort only.
    assert fetch_publisher("KEY123", "https://x/volume/4050-999/", fetch=_broken_fetch) == ""


def test_fetch_publisher_with_no_url_returns_empty():
    assert fetch_publisher("KEY123", "", fetch=_fetch_returning(VOLUME_RESPONSE)) == ""


def test_fetch_volume_info_returns_publisher_and_start_year():
    publisher, start_year = fetch_volume_info(
        "KEY123", "https://comicvine.gamespot.com/api/volume/4050-999/", fetch=_fetch_returning(VOLUME_RESPONSE)
    )
    assert (publisher, start_year) == ("Test Publisher", "1985")


def test_fetch_volume_info_with_no_url_returns_empty_tuple():
    assert fetch_volume_info("KEY123", "", fetch=_fetch_returning(VOLUME_RESPONSE)) == ("", "")


def test_fetch_volume_info_swallows_errors():
    def _broken_fetch(url):
        raise ConnectionError("network is down")

    assert fetch_volume_info("KEY123", "https://x/volume/4050-999/", fetch=_broken_fetch) == ("", "")


# filter_candidates_by_series() -- disambiguating same-named volumes by
# publisher and/or the volume's own start year (distinct from one
# issue's own cover_date year).

_CANDIDATE_2016 = ComicVineCandidate(
    issue_id="1", volume_name="Batman", volume_detail_url="https://x/volume/100/", issue_number="1",
)
_CANDIDATE_2011 = ComicVineCandidate(
    issue_id="2", volume_name="Batman", volume_detail_url="https://x/volume/200/", issue_number="1",
)
_VOLUME_FETCH = _fetch_dispatch({
    "https://x/volume/100/": VOLUME_RESPONSE_2016,
    "https://x/volume/200/": VOLUME_RESPONSE_2011,
})


def test_filter_candidates_by_series_is_noop_when_nothing_given():
    candidates = [_CANDIDATE_2016, _CANDIDATE_2011]
    # No publisher/year fetch should even be attempted -- _never_fetch
    # would raise if it were.
    result = filter_candidates_by_series(candidates, "", "", "KEY123", fetch=_never_fetch)
    assert result == candidates


def test_filter_candidates_by_series_filters_by_publisher():
    result = filter_candidates_by_series(
        [_CANDIDATE_2016, _CANDIDATE_2011], "Other Press", "", "KEY123", fetch=_VOLUME_FETCH
    )
    assert result == [_CANDIDATE_2011]


def test_filter_candidates_by_series_filters_by_series_year():
    result = filter_candidates_by_series(
        [_CANDIDATE_2016, _CANDIDATE_2011], "", "2016", "KEY123", fetch=_VOLUME_FETCH
    )
    assert result == [_CANDIDATE_2016]


def test_filter_candidates_by_series_requires_both_when_both_given():
    # DC Comics published the 2016 volume, not the 2011 one -- a
    # candidate has to satisfy both filters, not just either one.
    result = filter_candidates_by_series(
        [_CANDIDATE_2016, _CANDIDATE_2011], "DC Comics", "2016", "KEY123", fetch=_VOLUME_FETCH
    )
    assert result == [_CANDIDATE_2016]


def test_filter_candidates_by_series_falls_back_when_nothing_matches():
    candidates = [_CANDIDATE_2016, _CANDIDATE_2011]
    result = filter_candidates_by_series(candidates, "Marvel", "", "KEY123", fetch=_VOLUME_FETCH)
    assert result == candidates


def test_download_cover_image_requires_url():
    candidate = ComicVineCandidate(image_url="")
    with pytest.raises(ComicVineLookupError):
        download_cover_image(candidate)


def test_download_cover_image_returns_bytes():
    candidate = ComicVineCandidate(image_url="https://example.com/cover.jpg")
    fake_bytes = b"\xff\xd8\xff\xe0fakejpeg"
    result = download_cover_image(candidate, fetch=lambda url: fake_bytes)
    assert result == fake_bytes
