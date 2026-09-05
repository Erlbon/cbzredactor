"""
core/filename_guess.py

A loose "Series Name 12" / "Series Name #12" filename parser, used by
the online lookup dialogs (Comic Vine, GCD) to seed a search query
when a book has no Series set yet. Not a general-purpose comic
filename parser -- just enough to guess at something worth searching;
the user reviews the actual match before anything is applied, so a
wrong guess here costs nothing beyond an unhelpful search result.

Pure string logic, no CbzBook/Qt dependency, so it's fully unit-tested
independent of either.
"""

from __future__ import annotations

import os
import re

_FILENAME_GUESS_RE = re.compile(r"^(.*?)[\s_.-]+#?0*(\d+)\s*$")


def guess_series_and_number(path: str, existing_series: str = "", existing_number: str = "") -> tuple[str, str]:
    """Returns (series, number) -- `existing_series`/`existing_number`
    (typically a book's already-set ComicInfo.xml fields) win outright
    when Series is non-blank; otherwise falls back to parsing `path`'s
    filename."""
    if existing_series.strip():
        return existing_series.strip(), existing_number.strip()
    stem = os.path.splitext(os.path.basename(path))[0]
    match = _FILENAME_GUESS_RE.match(stem.replace("_", " "))
    if match:
        return match.group(1).strip(" -_."), match.group(2)
    return stem, ""
