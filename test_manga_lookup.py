"""core/manga_lookup.py with canned answers shaped like the real AniList / MangaUpdates ones (checked live
2026-10-11 with Berserk). No network."""

import io
import json
import urllib.error
import urllib.request

import pytest

from core import manga_lookup as ml

ANILIST_BERSERK = {
    "id": 30002, "siteUrl": "https://anilist.co/manga/30002",
    "title": {"romaji": "Berserk", "english": "Berserk", "native": "ベルセルク"},
    "synonyms": ["Beruseruku", "Berserk Max"], "format": "MANGA", "status": "RELEASING", "volumes": None,
    "startDate": {"year": 1989, "month": 8, "day": 25}, "countryOfOrigin": "JP", "isAdult": False, "averageScore": 92,
    "description": "His name is Guts.<br><br>(Source: Dark Horse)",
    "genres": ["Action", "Adventure", "Drama"],
    "tags": [
        {"name": "Tragedy", "rank": 97, "isGeneralSpoiler": False, "isMediaSpoiler": False},
        {"name": "Seinen", "rank": 95, "isGeneralSpoiler": False, "isMediaSpoiler": False},
        {"name": "A Big Spoiler", "rank": 90, "isGeneralSpoiler": False, "isMediaSpoiler": True},
        {"name": "Obscure", "rank": 20, "isGeneralSpoiler": False, "isMediaSpoiler": False},
    ],
    "coverImage": {"extraLarge": "https://img.anilist.co/berserk.jpg", "large": "https://img.anilist.co/small.jpg"},
    "staff": {"edges": [
        {"role": "Story & Art (vols 1-41)", "node": {"name": {"full": "Kentarou Miura"}}},
        {"role": "Story & Art (vols 41- )", "node": {"name": {"full": "Studio Gaga"}}},
        {"role": "Supervisor (vols 41- )", "node": {"name": {"full": "Kouji Mori"}}},
        {"role": "Translator (Portuguese)", "node": {"name": {"full": "Drik Sada"}}},
        {"role": "Assistant", "node": {"name": {"full": "Some Assistant"}}},
    ]},
}
ANILIST_OTHER = dict(
    ANILIST_BERSERK, id=2, siteUrl="https://anilist.co/manga/2", title={"romaji": "Berserk of Gluttony", "english": None, "native": "x"},
    synonyms=[], startDate={"year": 2018, "month": 1, "day": 1}, averageScore=None, staff={"edges": []},
)
MU_SEARCH = {"total_hits": 2, "results": [
    {"record": {
        "series_id": 51239621230, "title": "Berserk", "url": "https://www.mangaupdates.com/series/njeqwry/berserk",
        "description": "From Dark Horse:  \nHis name is **Guts**, the [Black Swordsman](https://x).", "type": "Manga",
        "year": "1989", "genres": [{"genre": "Action"}, {"genre": "Seinen"}], "image": {"url": {"original": "https://cdn.mu/b.jpg"}},
    }},
    {"record": {"series_id": 7, "title": "Berserk (Novel)", "type": "Novel", "year": "2001"}},
]}
MU_DETAIL = {
    "series_id": 51239621230, "title": "Berserk", "status": "43 Volumes (Ongoing)\n\n*a long note*", "completed": False,
    "publishers": [
        {"publisher_name": "Hakusensha", "type": "Original"}, {"publisher_name": "Dark Horse", "type": "English"},
    ],
    "authors": [
        {"name": "MIURA Kentaro", "type": "Author"}, {"name": "MORI Kouji", "type": "Author"},
        {"name": "MIURA Kentaro", "type": "Artist"}, {"name": "Studio Gaga", "type": "Artist"},
    ],
    "associated": [{"title": "Berserk Max"}],
}


class FakeNet:
    """A fetch() that answers the three kinds of request, records them, and can fail per host."""

    def __init__(self, anilist=None, search=None, detail=None, fail=()):
        self.anilist = {"data": {"Page": {"media": [ANILIST_BERSERK, ANILIST_OTHER]}}} if anilist is None else anilist
        self.search = MU_SEARCH if search is None else search
        self.detail = MU_DETAIL if detail is None else detail
        self.fail = fail
        self.requests = []

    def __call__(self, request):
        url = request if isinstance(request, str) else request.full_url
        self.requests.append(url)
        for host in self.fail:
            if host in url:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b""))
        if "graphql.anilist.co" in url:
            return json.dumps(self.anilist).encode()
        if url.endswith("/series/search"):
            return json.dumps(self.search).encode()
        if "/series/" in url:
            return json.dumps(self.detail).encode()
        return b"\xff\xd8\xff\xe0cover"


def client(net, **kw):
    return ml.MangaClient(fetch=net, anilist_interval=0, mangaupdates_interval=0, **kw)


# --- reading text -----------------------------------------------------------------------------------


def test_plain_text_drops_markup_and_markdown():
    assert ml.plain_text("A<br>B<br /><br>C &amp; D") == "A\nB\n\nC & D"
    assert ml.plain_text("His name is **Guts**, the [Black Swordsman](https://x).") == "His name is Guts, the Black Swordsman."


def test_person_names_put_the_family_name_last_only_when_it_is_in_capitals():
    assert ml.person_name("MIURA Kentaro") == "Kentaro Miura"
    assert ml.person_name("Studio Gaga") == "Studio Gaga"
    assert ml.person_name("Kentaro Miura") == "Kentaro Miura" and ml.person_name("CLAMP") == "CLAMP"


def test_titles_compare_without_case_punctuation_or_a_format_suffix():
    assert ml.title_similarity("Berserk (Manga)", "BERSERK!") == 1.0
    assert ml.title_similarity("Berserk", "Berserk of Gluttony") < 0.8
    assert ml.title_similarity("", "x") == 0.0


# --- the two sources' answers -----------------------------------------------------------------------


def test_an_anilist_series_has_the_credits_tags_and_dates_in_order():
    series = ml._anilist_series(ANILIST_BERSERK)
    assert series.title == "Berserk" and series.year == "1989" and series.volumes is None and series.score == 92
    assert series.writers == ["Kentarou Miura", "Studio Gaga"] and series.artists == ["Kentarou Miura", "Studio Gaga"]
    assert series.tags == ["Tragedy", "Seinen"]  # ranked, no spoilers, nothing weak
    assert series.description == "His name is Guts.\n\n(Source: Dark Horse)"
    assert series.cover_url == "https://img.anilist.co/berserk.jpg"


def test_a_mangaupdates_detail_adds_publishers_credits_and_a_finished_series_count():
    series = ml._mangaupdates_series(MU_SEARCH["results"][0]["record"])
    assert series.title == "Berserk" and series.year == "1989" and series.genres == ["Action", "Seinen"]
    ml._apply_mangaupdates_detail(series, MU_DETAIL)
    assert series.writers == ["Kentaro Miura", "Kouji Mori"] and series.artists == ["Kentaro Miura", "Studio Gaga"]
    assert series.publishers == [("Hakusensha", "Original"), ("Dark Horse", "English")] and series.volumes is None
    done = ml._mangaupdates_series(MU_SEARCH["results"][0]["record"])
    ml._apply_mangaupdates_detail(done, dict(MU_DETAIL, completed=True, status="16 Volumes (Complete)"))
    assert done.volumes == 16


def test_odd_shapes_give_less_not_a_crash():
    assert ml._anilist_series({"title": None}) is None and ml._mangaupdates_series({"title": ["x"]}) is None
    odd = ml._anilist_series({"title": {"english": "X"}, "tags": "no", "staff": [], "synonyms": None, "startDate": 5, "averageScore": "high"})
    assert odd.title == "X" and odd.tags == [] and odd.writers == [] and odd.year == "" and odd.score is None
    series = ml._mangaupdates_series({"title": "Y", "genres": [None, {"genre": 3}], "image": [], "associated": "x"})
    ml._apply_mangaupdates_detail(series, {"authors": "no", "publishers": [None, {"publisher_name": None}], "completed": True, "status": None})
    assert series.genres == ["3"] and series.publishers == [] and series.volumes is None


# --- pairing and the merged result ------------------------------------------------------------------


def test_the_same_series_in_both_sources_is_paired_and_scores_highest():
    ani = [ml._anilist_series(ANILIST_BERSERK), ml._anilist_series(ANILIST_OTHER)]
    mu = [ml._mangaupdates_series(r["record"]) for r in MU_SEARCH["results"][:1]]
    matches = ml.pair_results("Berserk", ani, mu)
    assert [m.source_text for m in matches] == ["AniList + MangaUpdates", "AniList"]
    assert matches[0].exact and matches[0].confidence == ml.EXACT_PAIRED_CONFIDENCE
    assert matches[1].confidence < 0.85  # a different series that merely starts the same way


def test_a_single_source_exact_match_scores_lower_and_a_year_far_apart_is_not_paired():
    far = ml._mangaupdates_series(dict(MU_SEARCH["results"][0]["record"], year="2010"))
    matches = ml.pair_results("Berserk", [ml._anilist_series(ANILIST_BERSERK)], [far])
    assert sorted(m.source_text for m in matches) == ["AniList", "MangaUpdates"]
    assert all(m.confidence == ml.EXACT_SINGLE_CONFIDENCE for m in matches)


def test_details_merge_the_best_of_both():
    net = FakeNet()
    found = client(net).search("Berserk")[0]
    fields = client(net).details(found).as_dict()
    assert fields["series"] == "Berserk" and fields["year"] == "1989" and fields["month"] == "8" and fields["day"] == "25"
    assert fields["writer"] == "Kentaro Miura, Kouji Mori" and fields["penciller"] == "Kentaro Miura, Studio Gaga"
    assert fields["publisher"] == "Dark Horse"  # the English one
    assert fields["genre"] == "Action, Adventure, Drama, Seinen" and fields["tags"] == "Tragedy, Seinen"
    assert fields["manga"] == "YesAndRightToLeft" and fields["community_rating"] == "4.6"
    assert fields["web"] == "https://anilist.co/manga/30002" and "count" not in fields  # still running: no final count
    assert fields["summary"].startswith("His name is Guts.") and "age_rating" not in fields


def test_a_finished_series_has_its_count_and_an_adult_one_its_rating():
    ani = dict(ANILIST_BERSERK, volumes=16, isAdult=True, countryOfOrigin="KR")
    fields = client(FakeNet(anilist={"data": {"Page": {"media": [ani]}}})).details(
        client(FakeNet(anilist={"data": {"Page": {"media": [ani]}}})).search("Berserk")[0]
    ).as_dict()
    assert fields["count"] == "16" and fields["age_rating"] == "Adults Only 18+" and "manga" not in fields  # a manhwa is read left to right


# --- the client -------------------------------------------------------------------------------------


def test_one_search_is_one_request_per_source_and_is_remembered():
    net = FakeNet()
    c = client(net)
    c.search("Berserk")
    assert len([u for u in net.requests if "anilist" in u]) == 1 and len([u for u in net.requests if u.endswith("/series/search")]) == 1
    c.search("BERSERK")  # the same series however it is written
    assert len(net.requests) == 2
    c.details(c.search("Berserk")[0])
    c.details(c.search("Berserk")[0])
    assert len([u for u in net.requests if "/series/" in u and not u.endswith("search")]) == 1  # the detail, once


def test_requests_to_one_service_are_spaced():
    slept, now = [], [0.0]
    c = ml.MangaClient(fetch=FakeNet(), anilist_interval=2.0, mangaupdates_interval=0.5,
                       sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)), clock=lambda: now[0])
    c.search("Berserk")
    c.search("Another Series")
    assert any(abs(s - 2.0) < 1e-9 for s in slept)  # AniList's second request waited the two seconds


def test_one_source_down_still_answers_and_both_down_is_an_error():
    only_mu = client(FakeNet(fail=("graphql.anilist.co",))).search("Berserk")
    assert [m.source_text for m in only_mu] == ["MangaUpdates"]
    only_ani = client(FakeNet(fail=("mangaupdates",))).search("Berserk")
    assert only_ani[0].source_text == "AniList"
    with pytest.raises(ml.MangaLookupError, match="AniList.*MangaUpdates|MangaUpdates.*AniList"):
        client(FakeNet(fail=("graphql.anilist.co", "mangaupdates"))).search("Berserk")


def test_a_rate_limit_says_so():
    class Limited(FakeNet):
        def __call__(self, request):
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b""))

    with pytest.raises(ml.MangaLookupError, match="too many requests"):
        client(Limited()).search_anilist("x")


def test_a_failed_detail_request_still_gives_the_rest():
    net = FakeNet()
    c = client(net)
    found = c.search("Berserk")[0]
    net.fail = ("/series/5123",)
    fields = c.details(found).as_dict()
    assert fields["series"] == "Berserk" and "publisher" not in fields  # no detail: no publisher, but not an error


def test_a_cover_is_fetched_only_when_asked_and_a_failure_is_none():
    net = FakeNet()
    c = client(net)
    found = c.search("Berserk")[0]
    assert c.cover(found).startswith(b"\xff\xd8")
    assert c.cover(found) is not None and len([u for u in net.requests if "img.anilist" in u]) == 1  # remembered
    net.fail = ("img.anilist.co",)
    assert client(net).cover(found) is None  # a fresh client: the download fails, which is only no preview


def test_an_empty_title_finds_nothing_to_pair():
    assert ml.pair_results("x", [], []) == []
