"""
core/filename_guess.py

A loose "Series Name 12" / "Series Name #12" filename parser, used by
the online lookup dialogs (Comic Vine, GCD) to seed a search query
when a book has no Series set yet. Not a general-purpose comic
filename parser -- just enough to guess at something worth searching;
the lookup dialogs let the user review and manually correct the guess
before searching, so a wrong guess here costs nothing beyond an
unhelpful default.

Real-world filenames (scene/scanlation releases especially) commonly
trail the issue number with bracketed/parenthesized metadata that
isn't part of the series name or number at all -- year, release group,
"Digital", a hash, etc: "Batman 001 (2016) (Digital) (Empire).cbz".
Those trailing groups are stripped BEFORE looking for the number (see
_strip_trailing_annotations()); without that, no number is found at
all (nothing follows it but bracketed junk), which matters far more
for GCD than Comic Vine -- GCD's search requires both series AND
number to search at all (see core/gcd_lookup.py), so a filename that
fails to yield a number there doesn't just get a worse guess, it can't
search at all.

Pure string logic, no CbzBook/Qt dependency, so it's fully unit-tested
independent of either.
"""

from __future__ import annotations

import os
import re

# One or more trailing "(...)"/"[...]" groups -- year, release group,
# format tags, etc -- stripped repeatedly from the end before number
# extraction, since they'd otherwise sit between the number and the
# end of the string and defeat the end-anchored number pattern below.
_TRAILING_BRACKET_GROUP_RE = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")

# Series name, then a separator, then the issue number -- optionally
# prefixed with "#" (issue) or "v"/"vol" (volume, e.g. trade-paperback-
# collected series like Saga), optionally zero-padded.
_FILENAME_GUESS_RE = re.compile(r"^(.*?)[\s_.-]+(?:v(?:ol)?\.?|#)?0*(\d+)\s*$", re.IGNORECASE)


def _strip_trailing_annotations(stem: str) -> str:
    while True:
        stripped = _TRAILING_BRACKET_GROUP_RE.sub("", stem)
        if stripped == stem:
            return stem
        stem = stripped


def guess_series_and_number(path: str, existing_series: str = "", existing_number: str = "") -> tuple[str, str]:
    """Returns (series, number) -- `existing_series`/`existing_number`
    (typically a book's already-set ComicInfo.xml fields) win outright
    when Series is non-blank; otherwise falls back to parsing `path`'s
    filename."""
    if existing_series.strip():
        return existing_series.strip(), existing_number.strip()

    stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
    core = _strip_trailing_annotations(stem)
    match = _FILENAME_GUESS_RE.match(core)
    if match:
        return match.group(1).strip(" -_."), match.group(2)
    return core.strip(" -_."), ""
