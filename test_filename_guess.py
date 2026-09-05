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


# Real-world scene/scanlation release naming -- these trailing bracketed
# groups (year, release group, format tags) are common and, unstripped,
# previously defeated number extraction entirely (nothing followed the
# number but bracketed junk, and the end-anchored pattern requires the
# number to actually be at the end). This mattered most for GCD, whose
# search requires both series AND number to search at all.

def test_strips_single_trailing_year_annotation():
    series, number = guess_series_and_number("/x/Watchmen 001 (1986).cbz", "", "")
    assert (series, number) == ("Watchmen", "1")


def test_strips_multiple_trailing_bracket_groups():
    series, number = guess_series_and_number(
        "/x/Batman 001 (2016) (Digital) (Empire).cbz", "", ""
    )
    assert (series, number) == ("Batman", "1")


def test_strips_trailing_groups_with_hash_number():
    series, number = guess_series_and_number(
        "/x/Amazing Spider-Man #005 (2018).cbz", "", ""
    )
    assert (series, number) == ("Amazing Spider-Man", "5")


def test_hyphen_inside_series_name_not_mistaken_for_separator():
    series, number = guess_series_and_number("/x/The Boys - 001 (2019).cbz", "", "")
    assert (series, number) == ("The Boys", "1")


def test_volume_prefix_treated_as_number():
    series, number = guess_series_and_number(
        "/x/Saga v01 (2012) (Digital-Empire).cbz", "", ""
    )
    assert (series, number) == ("Saga", "1")


def test_bracket_groups_stripped_even_with_no_number_found():
    series, number = guess_series_and_number("/x/Batman Beyond (2022) (Complete).cbz", "", "")
    assert (series, number) == ("Batman Beyond", "")
