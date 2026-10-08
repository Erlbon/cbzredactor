"""Tests for core/comicvine_browse.py and core/cover_hash.py -- all network
access is faked through the injectable `fetch` parameter."""

import io
import json

import pytest
from PIL import Image

from core import comicvine_browse as cb
from core.comicvine_lookup import ComicVineLookupError
from core.cover_hash import MATCH_THRESHOLD, cover_hash, is_same_cover, similarity


def _json(payload: dict):
    return lambda url: json.dumps(payload).encode("utf-8")


def _volume(**kw) -> cb.ComicVineVolume:
    defaults = dict(volume_id="1", name="Batman", start_year="2016", issue_count=50, publisher="DC Comics")
    defaults.update(kw)
    return cb.ComicVineVolume(**defaults)


# ---------------------------------------------------------------------------
# Volume search

def test_search_volumes_reads_the_fields_and_asks_for_volumes():
    seen = []
    payload = {
        "status_code": 1,
        "results": [{
            "id": 796, "name": "Batman", "start_year": "2016", "count_of_issues": 143,
            "publisher": {"id": 10, "name": "DC Comics"},
            "image": {"small_url": "https://x/s.jpg"}, "api_detail_url": "https://x/volume/4050-796/",
            "site_detail_url": "https://x/batman",
        }],
    }

    def fetch(url):
        seen.append(url)
        return json.dumps(payload).encode("utf-8")

    [volume] = cb.search_volumes("KEY", "Batman", fetch=fetch)
    assert "resources=volume" in seen[0] and "query=Batman" in seen[0]
    assert (volume.volume_id, volume.start_year, volume.issue_count, volume.publisher) == ("796", "2016", 143, "DC Comics")
    assert volume.image_url == "https://x/s.jpg" and volume.detail_url.endswith("4050-796/")


def test_search_volumes_tolerates_a_sparse_result():
    [volume] = cb.search_volumes("KEY", "X", fetch=_json({"status_code": 1, "results": [{"id": 5, "publisher": None}]}))
    assert volume.name == "" and volume.publisher == "" and volume.issue_count == 0


def test_search_volumes_needs_a_key_and_a_series():
    with pytest.raises(ComicVineLookupError):
        cb.search_volumes("", "Batman", fetch=_json({}))
    with pytest.raises(ComicVineLookupError):
        cb.search_volumes("KEY", "  ", fetch=_json({}))


def test_search_volumes_reports_an_api_error():
    with pytest.raises(ComicVineLookupError, match="Invalid API key"):
        cb.search_volumes("BAD", "Batman", fetch=_json({"status_code": 100, "results": []}))


# ---------------------------------------------------------------------------
# Ranking

def test_a_series_that_started_after_the_files_year_ranks_far_below():
    old = _volume(volume_id="1", name="Batman", start_year="1940", issue_count=700)
    new = _volume(volume_id="2", name="Batman", start_year="2016", issue_count=140)
    future = _volume(volume_id="3", name="Batman", start_year="2025", issue_count=20)
    ranked = cb.rank_volumes([future, old, new], "Batman", "12", "2017")
    assert ranked[-1] is future


def test_an_issue_count_that_cannot_hold_the_number_ranks_lower():
    short = _volume(volume_id="1", name="Saga", issue_count=6)
    long_run = _volume(volume_id="2", name="Saga", issue_count=66)
    assert cb.rank_volumes([short, long_run], "Saga", "40", "2016")[0] is long_run
    # an unknown number is neutral: the order falls back to the other signals
    assert cb.score_volume(short, "Saga", "", "") == cb.score_volume(
        _volume(volume_id="9", name="Saga", issue_count=66), "Saga", "", ""
    )


def test_name_words_decide_between_similar_titles():
    exact = _volume(volume_id="1", name="Batman")
    beyond = _volume(volume_id="2", name="Batman Beyond")
    assert cb.rank_volumes([beyond, exact], "Batman", "1", "")[0] is exact


def test_a_series_chosen_earlier_in_the_batch_gets_a_boost_and_mirror_publishers_a_penalty():
    plain = _volume(volume_id="1", name="Spider-Man", publisher="Marvel")
    mirror = _volume(volume_id="2", name="Spider-Man", publisher="Panini")
    assert cb.score_volume(mirror, "Spider-Man", "1", "") < cb.score_volume(plain, "Spider-Man", "1", "")
    assert cb.score_volume(mirror, "Spider-Man", "1", "", frozenset({"2"})) > cb.score_volume(
        mirror, "Spider-Man", "1", ""
    )


# ---------------------------------------------------------------------------
# Issues of a volume

def _issue_payload(numbers, total):
    return {
        "status_code": 1, "number_of_total_results": total,
        "results": [{"id": i, "issue_number": n, "name": "", "cover_date": "2016-01-01",
                     "image": {"small_url": f"https://x/{i}.jpg"}, "api_detail_url": f"https://x/issue/{i}/"}
                    for i, n in enumerate(numbers, 1)],
    }


def test_fetch_volume_issues_pages_and_sorts_numerically():
    offsets = []

    def fetch(url):
        offset = int(url.split("offset=")[1].split("&")[0])
        offsets.append(offset)
        assert "filter=volume%3A796" in url
        if offset == 0:
            return json.dumps(_issue_payload([str(n) for n in range(100, 0, -1)], 102)).encode()
        return json.dumps(_issue_payload(["Annual 1", "10.5"], 102)).encode()

    issues = cb.fetch_volume_issues("KEY", "796", fetch=fetch)
    assert offsets == [0, 100]
    numbers = [i.number for i in issues]
    assert numbers[:3] == ["1", "2", "3"]
    assert numbers.index("10") < numbers.index("10.5") < numbers.index("11")
    assert numbers[-1] == "Annual 1"  # non-numeric numbers come after the numeric ones


def test_fetch_volume_issues_of_an_empty_volume_is_an_empty_list():
    assert cb.fetch_volume_issues("KEY", "1", fetch=_json({"status_code": 1, "number_of_total_results": 0, "results": []})) == []


def test_find_issue_by_number_matches_equivalent_numbers():
    issues = [cb.ComicVineIssue(number="1"), cb.ComicVineIssue(number="2.0"), cb.ComicVineIssue(number="Annual 1")]
    assert cb.find_issue_by_number(issues, "2") is issues[1]
    assert cb.find_issue_by_number(issues, "annual 1") is issues[2]
    assert cb.find_issue_by_number(issues, "3") is None


def test_number_summary_shows_runs_and_gaps():
    issues = [cb.ComicVineIssue(number=n) for n in ("1", "2", "3", "5", "7", "8", "Annual 1")]
    assert cb.number_summary(issues) == "1-3, 5, 7-8, Annual 1"
    assert cb.number_summary([]) == ""


def test_candidate_for_carries_what_the_detail_fetch_needs():
    candidate = cb.candidate_for(_volume(detail_url="https://x/volume/1/"), cb.ComicVineIssue(
        issue_id="9", number="4", detail_url="https://x/issue/9/", image_url="https://x/9.jpg"))
    assert (candidate.detail_url, candidate.volume_detail_url, candidate.issue_number) == (
        "https://x/issue/9/", "https://x/volume/1/", "4")


# ---------------------------------------------------------------------------
# Cover hash

def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _gradient(width, height, rising=True) -> Image.Image:
    image = Image.new("L", (width, height))
    for x in range(width):
        value = int(255 * x / (width - 1))
        image.paste(value if rising else 255 - value, (x, 0, x + 1, height))
    return image.convert("RGB")


def test_the_same_cover_at_another_size_is_the_same_cover():
    big = cover_hash(_png(_gradient(600, 900)))
    small = cover_hash(_png(_gradient(100, 150)))
    assert is_same_cover(big, small) and similarity(big, small) >= MATCH_THRESHOLD


def test_a_different_cover_is_not_the_same_cover():
    assert not is_same_cover(cover_hash(_png(_gradient(100, 150, True))), cover_hash(_png(_gradient(100, 150, False))))


def test_a_wraparound_cover_is_hashed_by_its_front_half():
    front = _gradient(100, 150, True)
    wrap = Image.new("RGB", (200, 150))
    wrap.paste(_gradient(100, 150, False), (0, 0))  # the back cover, on the left
    wrap.paste(front, (100, 0))
    assert is_same_cover(cover_hash(_png(wrap)), cover_hash(_png(front)))


def test_unreadable_or_missing_images_never_match():
    assert cover_hash(None) is None and cover_hash(b"not an image") is None
    assert similarity(None, 5) == 0.0 and not is_same_cover(None, None)
