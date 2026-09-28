"""
core/filename_guess.py

Series/number/year guesses for the online lookup dialogs (Comic Vine,
GCD, Bedetheque), used to seed a search when a book has no Series set
yet. The lookup dialogs let the user review and correct the guess
before searching, so a wrong guess costs nothing beyond an unhelpful
default.

Since 2026-09-28 this is a thin layer over core/scene_name.py's full
filename parser. The old guesser here only stripped the last extension
and trailing brackets, so every name ending in a converter's
".webp.cbz" (CbxConverter) came out with no issue number at all -- GCD
and Bedetheque can't search without one.
"""

from __future__ import annotations

from core.scene_name import parse_filename


def guess_series_and_number(path: str, existing_series: str = "", existing_number: str = "") -> tuple[str, str]:
    """Returns (series, number) -- `existing_series`/`existing_number`
    (typically a book's already-set ComicInfo.xml fields) win outright
    when Series is non-blank; otherwise falls back to parsing `path`'s
    filename. A collected volume with no issue number ("Saga v01")
    returns the volume number, which is how the lookup sources number
    collected editions."""
    if existing_series.strip():
        return existing_series.strip(), existing_number.strip()
    parsed = parse_filename(path)
    return parsed.series, parsed.number or (parsed.volume if len(parsed.volume) < 4 else "")


def guess_year(path: str, existing_year: str = "") -> str:
    """Returns a best-guess 4-digit publication year as a soft ranking
    hint for online lookups (see core/comicvine_lookup.py's scoring) --
    never written into ComicInfo.xml automatically by this function.
    `existing_year` (typically a book's already-set ComicInfo.xml
    field) wins outright when set; otherwise the issue's own year from
    the filename, falling back to the series' start year
    ("Batman (2016) 045")."""
    if existing_year.strip():
        return existing_year.strip()
    parsed = parse_filename(path)
    return parsed.year or (parsed.volume if len(parsed.volume) == 4 else "")
