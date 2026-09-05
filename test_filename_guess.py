"""Tests for core/filename_guess.py -- pure string logic, no CbzBook
or Qt involved."""

from core.filename_guess import guess_series_and_number


def test_existing_series_wins_outright():
    series, number = guess_series_and_number("/x/Some File.cbz", "Amazing Test Comics", "5")
    assert (series, number) == ("Amazing Test Comics", "5")


def test_falls_back_to_filename_with_space_separator():
    series, number = guess_series_and_number("/x/Amazing Test Comics 012.cbz", "", "")
    assert (series, number) == ("Amazing Test Comics", "12")


def test_falls_back_to_filename_with_hash_number():
    series, number = guess_series_and_number("/x/Amazing Test Comics #12.cbz", "", "")
    assert (series, number) == ("Amazing Test Comics", "12")


def test_falls_back_to_filename_with_underscore_separator():
    series, number = guess_series_and_number("/x/Amazing_Test_Comics_012.cbz", "", "")
    assert (series, number) == ("Amazing Test Comics", "12")


def test_no_number_found_returns_whole_stem_as_series():
    series, number = guess_series_and_number("/x/Just A Title.cbz", "", "")
    assert (series, number) == ("Just A Title", "")
