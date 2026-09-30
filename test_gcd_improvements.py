"""Tests for the 2026-09-28 GCD lookup improvements (per GCD's own API
wiki): year-filtered search, scene " - " -> ":" series variants,
optional login, hourly-limit / bad-login handling that stops a batch,
the GCD issue page in Web, and the scrambled password in settings."""

import io
import json
import sys
import urllib.error

import pytest
from PyQt6.QtWidgets import QApplication

from core.gcd_lookup import (
    GcdAuthError,
    GcdRateLimitError,
    build_search_url,
    check_login,
    issue_page_url,
    make_gcd_fetch,
    search_gcd,
    series_name_variants,
)

# Kept at module level: an unreferenced QApplication is garbage-collected
# at once, and Qt then crashes the whole test process.
_app = QApplication.instance() or QApplication(sys.argv)


def _page(*series_names):
    return {
        "next": None,
        "results": [
            {"series_name": f"{name} (2023 series)", "descriptor": "332", "publication_date": "September 2026",
             "api_url": f"https://www.comics.org/api/issue/{i + 1}/?format=json"}
            for i, name in enumerate(series_names)
        ],
    }


def _http_error(code):
    return urllib.error.HTTPError("https://www.comics.org/api/x", code, "err", {}, io.BytesIO(b""))


class _Recorder:
    """A fake fetch: returns a page for URLs containing a key, 404 otherwise."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def __call__(self, url):
        url = getattr(url, "full_url", url)
        self.urls.append(url)
        for key, payload in self.pages.items():
            if key in url:
                return json.dumps(payload).encode()
        raise _http_error(404)


def test_search_url_adds_the_year_filter():
    url = build_search_url("Watchmen", "1", "1986")
    assert url.endswith("/series/name/Watchmen/issue/1/year/1986/?format=json")
    assert "/year/" not in build_search_url("Watchmen", "1", "")
    assert "/year/" not in build_search_url("Watchmen", "1", "86")  # only a real 4-digit year


def test_scene_dash_is_tried_as_a_colon_first():
    assert series_name_variants("G.I. Joe - A Real American Hero") == [
        "G.I. Joe: A Real American Hero",
        "G.I. Joe - A Real American Hero",
    ]
    assert series_name_variants("Saga") == ["Saga"]


def test_year_filtered_colon_search_hits_in_one_request():
    fetch = _Recorder({"G.I.%20Joe%3A%20A%20Real%20American%20Hero/issue/332/year/2026/": _page("G.I. Joe: A Real American Hero")})
    candidates = search_gcd("G.I. Joe - A Real American Hero", "332", fetch=fetch, year="2026")
    assert candidates[0].series_name == "G.I. Joe: A Real American Hero"
    assert len(fetch.urls) == 1


def test_falls_back_to_no_year_when_the_year_finds_nothing():
    fetch = _Recorder({"Saga/issue/1/?format": _page("Saga")})
    candidates = search_gcd("Saga", "1", fetch=fetch, year="1999")
    assert candidates and "/year/1999/" in fetch.urls[0] and "/year/" not in fetch.urls[1]


def test_exact_match_ranking_treats_dash_and_colon_alike():
    fetch = _Recorder({"issue/332": _page("G.I. Joe: Special Missions", "G.I. Joe: A Real American Hero")})
    candidates = search_gcd("G.I. Joe - A Real American Hero", "332", fetch=fetch)
    assert candidates[0].series_name == "G.I. Joe: A Real American Hero"


def test_issue_page_url_from_api_url():
    assert issue_page_url("https://www.comics.org/api/issue/2865927/?format=json") == "https://www.comics.org/issue/2865927/"
    assert issue_page_url("not a url") == ""


# ---------------------------------------------------------------------------
# make_gcd_fetch(): login header and refusals
# ---------------------------------------------------------------------------

def _patched_base(monkeypatch, behaviour):
    seen = []

    def factory(user_agent, timeout=30.0):
        def base(request):
            seen.append(request)
            return behaviour(request)
        return base

    monkeypatch.setattr("core.gcd_lookup.make_default_fetch", factory)
    return seen


def test_login_is_sent_as_basic_auth(monkeypatch):
    seen = _patched_base(monkeypatch, lambda r: b"{}")
    make_gcd_fetch("reader", "s3cret")("https://www.comics.org/api/series/1/")
    assert seen[0].get_header("Authorization") == "Basic cmVhZGVyOnMzY3JldA=="


def test_anonymous_sends_no_auth(monkeypatch):
    seen = _patched_base(monkeypatch, lambda r: b"{}")
    make_gcd_fetch()("https://www.comics.org/api/series/1/")
    assert seen[0].get_header("Authorization") is None


def test_429_becomes_a_rate_limit_error_suggesting_an_account(monkeypatch):
    def refuse(_request):
        raise _http_error(429)

    _patched_base(monkeypatch, refuse)
    with pytest.raises(GcdRateLimitError, match="GCD Account"):
        make_gcd_fetch()("https://www.comics.org/api/x")


def test_401_with_a_login_is_an_auth_error(monkeypatch):
    def refuse(_request):
        raise _http_error(401)

    _patched_base(monkeypatch, refuse)
    with pytest.raises(GcdAuthError):
        check_login("reader", "wrong")


# ---------------------------------------------------------------------------
# The dialog stops the batch at the hourly limit
# ---------------------------------------------------------------------------

def test_dialog_skips_remaining_files_after_the_rate_limit(tmp_path, monkeypatch):
    import zipfile
    from core.cbz_file import CbzBook
    from gui.gcd_lookup_dialog import GcdLookupDialog

    books = []
    for i in range(3):
        path = tmp_path / f"Saga {i + 1:03} (2012).cbz"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("001.jpg", b"page")
        books.append(CbzBook(str(path)))

    calls = []

    def fetch(url):
        calls.append(url)
        raise GcdRateLimitError("limit reached")

    dialog = GcdLookupDialog(books, fetch=fetch)
    assert len(calls) == 1  # only the first file ever asked GCD
    errors = [r.error for r in dialog._row_results.values()]
    assert errors[0] == "limit reached"
    assert all(e.startswith("skipped") for e in errors[1:])


# ---------------------------------------------------------------------------
# Settings: legacy scrambled password (read-only, for migration)
# ---------------------------------------------------------------------------

def test_unscramble_reads_legacy_value_and_rejects_garbage():
    import base64

    from gui.app_settings import _SCRAMBLE_KEY, unscramble

    data = "s3cret pässword".encode("utf-8")
    mixed = bytes(b ^ _SCRAMBLE_KEY[i % len(_SCRAMBLE_KEY)] for i, b in enumerate(data))
    assert unscramble("s1:" + base64.urlsafe_b64encode(mixed).decode("ascii")) == "s3cret pässword"
    assert unscramble("garbage") == ""
