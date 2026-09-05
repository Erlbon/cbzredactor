"""Tests for the pure-logic helpers in gui/app_settings.py -- the
merge/exclude/dedupe functions don't touch QSettings, so they're
testable without a live Qt platform backend."""

from gui.app_settings import (
    _dedupe_and_trim,
    _exclude_hidden_genres,
    _exclude_hidden_languages,
    _merge_genres,
    _merge_languages,
)


def test_dedupe_and_trim_moves_pattern_to_front():
    history = ["%series% %number%", "%title%"]
    assert _dedupe_and_trim(history, "%title%") == ["%title%", "%series% %number%"]


def test_dedupe_and_trim_ignores_blank_pattern():
    history = ["%title%"]
    assert _dedupe_and_trim(history, "   ") == history


def test_dedupe_and_trim_caps_length():
    history = [f"pattern{i}" for i in range(20)]
    result = _dedupe_and_trim(history, "new", max_history=15)
    assert len(result) == 15
    assert result[0] == "new"


def test_merge_genres_skips_duplicates_case_insensitively():
    defaults = ["Superhero", "Crime"]
    custom = ["superhero", "Noir"]
    assert _merge_genres(defaults, custom) == ["Superhero", "Crime", "Noir"]


def test_exclude_hidden_genres_case_insensitive():
    defaults = ["Superhero", "Crime", "Horror"]
    assert _exclude_hidden_genres(defaults, ["crime"]) == ["Superhero", "Horror"]


def test_merge_languages_defaults_win_on_code_conflict():
    defaults = [("en", "English")]
    custom = [("en", "Should Be Ignored"), ("pt", "Portuguese")]
    assert _merge_languages(defaults, custom) == [("en", "English"), ("pt", "Portuguese")]


def test_exclude_hidden_languages():
    defaults = [("en", "English"), ("de", "German")]
    assert _exclude_hidden_languages(defaults, ["de"]) == [("en", "English")]
