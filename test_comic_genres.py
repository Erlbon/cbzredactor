"""Tests for core/comic_genres.py's add_genre() -- pure string logic."""

from core.comic_genres import add_genre


def test_add_genre_to_empty():
    assert add_genre("", "Superhero") == "Superhero"


def test_add_genre_appends_after_existing():
    assert add_genre("Superhero", "Crime") == "Superhero, Crime"


def test_add_genre_is_case_insensitive_deduplication():
    assert add_genre("Superhero", "superhero") == "Superhero"


def test_add_genre_strips_whitespace_in_existing_list():
    assert add_genre("Superhero,  Crime  ", "Horror") == "Superhero, Crime, Horror"
