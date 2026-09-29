"""
core/collection_names.py

Reads the names in a finished, organised comic collection -- the
user's own convention (ComicRack's Library Organizer pattern), not the
scene names new downloads arrive with (that's core/scene_name.py):

    files:    {series} {number3} ({publisher}, {year}-{month2}).cbz
              3 Guns 001 (Boom, 2013-08).webp.cbz
              3 Guns TPB (Boom, 2014-10).webp.cbz      -- a TPB, in the series' folder
              3 Guns TPB v02 (Boom, 2015-10).cbz       -- the second TPB
              The Incredible Hulk v2 102 (Marvel, 1968-04).cbz  -- "vN" when needed
              From the World of Minor Threats - The Brood TPB (Dark Horse Comics, 2025-07).cbz
    folders:  The Incredible Hulk v2 (1975-1999) (issues 102-474)
              The Incredible Hulk v1 1962-1963)(6 issues)

A series is its name PLUS its volume ("vN" in the file name, or else
in its folder's name when the folder is named for the same series):
The Incredible Hulk v1 #1 and v3 #1 are different comics.

Pure string logic, no Qt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.scene_name import strip_extensions

_BRACKET_RE = re.compile(r"\(([^()]*)\)")
# The issue number is the LAST number before the brackets (or before a
# " - title"), so "X-Men 2099 001" is series "X-Men 2099", number 001.
_FILE_RE = re.compile(
    r"^(?P<series>.+?)"
    r"(?:\s+v(?P<vol>\d+))?"
    r"\s+#?(?P<num>TPB(?:\s+v\d+)?|-?\d+(?:\.\d+)?[a-z]{0,2})"
    r"(?:\s+-\s+(?P<title>.*))?$",
    re.IGNORECASE,
)
_TPB_RE = re.compile(r"^TPB(?:\s+v0*(\d+))?$", re.IGNORECASE)
_PUB_DATE_RE = re.compile(r"^(?:(?P<pub>[^,]+),\s*)?(?P<year>\d{4})(?:-(?P<month>\d{1,2}))?$")
_FOLDER_VOL_RE = re.compile(r"^(?P<series>.+?)\s+v(?P<vol>\d+)\b(?P<rest>.*)$", re.IGNORECASE)
_YEARS_RE = re.compile(r"\b(\d{4})\s*-\s*(\d{4})?")
_YEAR_RE = re.compile(r"\b(\d{4})\b")


@dataclass
class FileName:
    series: str = ""  # as spelled in the name
    volume: str = ""  # "2" from "v2", "" when the name has none
    number: str = ""  # "001", "TPB", "TPB v02" -- as written
    title: str = ""
    publisher: str = ""
    year: str = ""
    month: str = ""  # "08" as written
    date_form: str = "none"  # "publisher+year-month" / "publisher+year" / "year-month" / "year" / "none"
    extra_brackets: list[str] = field(default_factory=list)  # anything else in brackets
    extensions: str = ""  # ".webp.cbz"

    @property
    def is_tpb(self) -> bool:
        return self.number.upper().startswith("TPB")

    @property
    def number_key(self) -> str:
        """Comparable number: "001" -> "1", "TPB" -> "TPB", "TPB v02" -> "TPB 2"."""
        return number_key(self.number)


@dataclass
class FolderName:
    series: str = ""
    volume: str = ""
    first_year: int = 0
    last_year: int = 0  # 0: open-ended or unknown


def number_key(number: str) -> str:
    number = number.strip().lstrip("#")
    tpb = _TPB_RE.match(number)
    if tpb:
        return f"TPB {int(tpb.group(1))}" if tpb.group(1) and int(tpb.group(1)) > 1 else "TPB"
    if re.match(r"^-?\d", number):
        sign = "-" if number.startswith("-") else ""
        digits = number.lstrip("-")
        stripped = digits.lstrip("0")
        if not stripped or stripped.startswith("."):
            stripped = "0" + stripped
        return (sign + stripped).lower()
    return number.lower()


def series_key(name: str) -> str:
    """Spelling-proof comparison key: case, punctuation, "&"/"and" and a
    leading "The" ignored."""
    text = name.casefold().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def parse_file_name(filename: str) -> FileName:
    stem = strip_extensions(filename)
    result = FileName(extensions=filename[len(stem):] if filename.startswith(stem) else "")
    brackets = _BRACKET_RE.findall(stem)
    head = _BRACKET_RE.split(stem)[0].strip()
    for inner in brackets:
        inner = inner.strip()
        date = _PUB_DATE_RE.match(inner)
        if date and result.date_form == "none":
            result.publisher = (date.group("pub") or "").strip()
            result.year = date.group("year")
            result.month = date.group("month") or ""
            form = "year-month" if result.month else "year"
            result.date_form = f"publisher+{form}" if result.publisher else form
        elif inner:
            result.extra_brackets.append(inner)
    match = _FILE_RE.match(head)
    if match:
        result.series = match.group("series").strip()
        result.volume = str(int(match.group("vol"))) if match.group("vol") else ""
        result.number = match.group("num")
        result.title = (match.group("title") or "").strip()
    else:
        result.series = head  # a one-shot: no number
        vol = _FOLDER_VOL_RE.match(head)
        if vol and not vol.group("rest").strip():
            result.series, result.volume = vol.group("series").strip(), str(int(vol.group("vol")))
    return result


def parse_folder_name(name: str) -> FolderName:
    result = FolderName()
    vol = _FOLDER_VOL_RE.match(name)
    if vol:
        result.series = vol.group("series").strip()
        result.volume = str(int(vol.group("vol")))
        rest = vol.group("rest")
    else:
        result.series = re.split(r"[(\[]", name, maxsplit=1)[0].strip()
        rest = name[len(result.series):]
        result.series = re.sub(r"\s+\d{4}(?:\s*-\s*\d{4})?$", "", result.series).strip()
    years = _YEARS_RE.search(rest) or _YEARS_RE.search(name)
    if years:
        result.first_year = int(years.group(1))
        result.last_year = int(years.group(2)) if years.group(2) else 0
    else:
        single = _YEAR_RE.search(rest)
        if single:
            result.first_year = int(single.group(1))
    return result


def number3(number: str) -> str:
    """ComicRack's {<number3>}: the whole-number part padded to three
    digits ("1" -> "001", "0.5" -> "000.5"); a TPB stays "TPB"."""
    key = number_key(number)
    if key.startswith("TPB"):
        return "TPB" if key == "TPB" else f"TPB v{int(key.split()[1]):02d}"
    match = re.match(r"^(-?)(\d+)(.*)$", key)
    if not match:
        return number
    return f"{match.group(1)}{int(match.group(2)):03d}{match.group(3)}"


def library_name(series: str, number: str, publisher: str, year: str, month: str,
                 volume: str = "", extensions: str = ".cbz") -> str:
    """The Library Organizer pattern
    {<series>} {<number3>} ({<publisher>}, {<year0>}-{<month#2>}),
    "" when a part is missing."""
    if not (series and publisher and year and month):
        return ""
    try:
        month_number = int(month)
    except ValueError:
        return ""
    if not 1 <= month_number <= 12:
        return ""
    vol = f" v{volume}" if volume else ""
    num = f" {number3(number)}" if number else ""
    return f"{series}{vol}{num} ({publisher}, {year}-{month_number:02d}){extensions}"
