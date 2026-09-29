"""
core/collection_names.py

Reads the names in a finished, organised comic collection -- the
user's own convention (ComicRack's Library Organizer pattern), not the
scene names new downloads arrive with (that's core/scene_name.py):

    files:    {series} {number3} ({publisher}, {year}-{month2}).cbz
              3 Guns 001 (Boom, 2013-08).webp.cbz
              3 Guns TPB (Boom, 2014-10).webp.cbz      -- a TPB, in the series' folder
              3 Guns TPB v02 (Boom, 2015-10).cbz       -- the second TPB
              Zombie Tramp v3 TPB - v03 - Sleazy Rider (ALE, 2015-05).cbz  -- also the third TPB
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
import unicodedata
from dataclasses import dataclass, field

from core.scene_name import strip_extensions

_BRACKET_RE = re.compile(r"\(([^()]*)\)")
# The issue number is the LAST number before the brackets (or before a
# " - title"), so "X-Men 2099 001" is series "X-Men 2099", number 001.
_FILE_RE = re.compile(
    r"^(?P<series>.+?)"
    r"(?:\s+v(?P<vol>\d+))?"
    r"\s+#?(?P<num>TPB(?:\s+(?:-\s+)?(?:v|vol\.?\s*)\d+)?"  # "TPB", "TPB v02", "TPB - v03", "TPB Vol. 2"
    r"|vol\.?\s*\d+"  # "The Unwritten Vol. 10"
    r"|\d{1,4}-\d{1,4}"  # "El Vibora 233-234", "Fix und Foxi 1982-24" (year-issue)
    r"|-?\d+(?:\.\d+)?[a-z]{0,2})"
    r"(?:\s+v\d+\s+\d+)?"  # "Heavy Metal 005 v01 05": whole number, then volume and issue
    r"(?:\s+-\s*(?P<title>.*))?$",
    re.IGNORECASE,
)
_TPB_RE = re.compile(r"^TPB(?:\s+(?:-\s+)?(?:v|vol\.?\s*)0*(\d+))?$", re.IGNORECASE)
# "Greg - [Bernard Prince 09] - Guérilla pour un fantôme": creator, [series number], title.
_BRACKETED_SERIES_RE = re.compile(
    r"^.*?\[(?P<series>[^\]]+?)\s+#?[A-Z]?(?P<num>\d+)\]\s*(?:-\s*(?P<title>.*))?$")  # "[Asterix T17]"
_PUB_DATE_RE = re.compile(r"^(?:(?P<pub>[^,]+),\s*)?(?P<year>\d{4})(?:-(?P<month>\d{1,2})?)?$")  # "(Marvel, 2026-)"
_FOLDER_VOL_RE = re.compile(r"^(?P<series>.+?)\s+v(?P<vol>\d+(?:\.\d+)?)\b(?P<rest>.*)$", re.IGNORECASE)
# "Warlands v2 - The Age Of Ice (2001-2002)": series "Warlands - The Age Of Ice", v2.
_FOLDER_SUBTITLE_RE = re.compile(r"^\s+-\s+(?P<sub>[^(\[]+?)\s*(?=[(\[]|$)")
# Folders that group, rather than hold, a series: "_Spider-Verse", "2019 (52 issues)", "Specials".
_GROUPING_FOLDER_RE = re.compile(r"^(_|\d{4}\b|\d+\s*-\s*\d+$|specials?$|one[- ]?shots?$|tpbs?$|annuals?$|vol(ume)?\b)",
                                 re.IGNORECASE)
# "v2 Sensational She-Hulk (1989-1994)": the volume written first.
_FOLDER_LEADING_VOL_RE = re.compile(r"^v(?P<vol>\d+)\s+(?P<series>[^(\[]+?)\s*(?P<rest>[(\[].*)?$", re.IGNORECASE)
# A title ending in a number: "Evil Dead 2 - Dark Ones Rising 001".
_TITLE_NUMBER_RE = re.compile(r"^(?P<more>.+?)\s+#?(?P<num>-?\d+(?:\.\d+)?[a-z]{0,2})$", re.IGNORECASE)
_YEARS_RE = re.compile(r"\b(\d{4})\s*-\s*(\d{4})?")
_YEAR_RE = re.compile(r"\b(\d{4})\b")


@dataclass
class FileName:
    series: str = ""  # as spelled in the name
    volume: str = ""  # "2" from "v2", "" when the name has none
    number: str = ""  # "001", "TPB", "TPB v02", "TPB - v03" -- as written
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
    base_series: str = ""  # "Lazarus" for "Lazarus v2 - Risen": files may use either
    volume: str = ""
    first_year: int = 0
    last_year: int = 0  # 0: open-ended or unknown


def numbers_agree(in_name: str, in_comicinfo: str) -> bool:
    """A name's number and ComicInfo's say the same: "003" and "3", but
    also "1982-24" (year-issue) and "24", "233-234" and "233", "Vol. 10"
    and "10"."""
    ci = number_key(in_comicinfo)
    if number_key(in_name) == ci:
        return True
    digits = re.sub(r"\D", "", in_name).lstrip("0")
    if digits and digits == re.sub(r"\D", "", in_comicinfo).lstrip("0"):  # "2009-21" and "200921"
        return True
    parts = re.findall(r"\d+(?:\.\d+)?", in_name)
    return ((len(parts) > 1 or in_name.lower().startswith("vol"))
            and ci in {number_key(p) for p in parts})


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
    text = name.casefold().replace("&", " and ").replace("_", " ")  # "Batman_Superman": "/" in a folder name
    text = re.sub(r"(?<=\w)\.(?=\w)", "", text)  # "U.N.C.L.E" -> "uncle"
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def loose_key(name: str) -> str:
    """A looser key for comparing a file name's series with ComicInfo's:
    also ignores accents, spaces, any "vN" (a volume, which ComicInfo
    often puts in Series) and a trailing "TPB" -- so "Batman/Superman:
    World's Finest" matches "BatmanSuperman - World's Finest" (Windows
    can't name a file with "/" or ":"), "Gen 13" matches "Gen13",
    "Sláine" matches "Slaine" and "Thor v5" matches "Thor"."""
    text = re.sub(r"\([^()]*\)", " ", name)  # "Star Wars: Darth Vader (2020-)"
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = series_key(text)
    text = re.sub(r"\bv\d+\b", " ", text)
    text = re.sub(r"\s+(tpb|one shot|1 shot)$", "", text.strip())
    if text.startswith("the "):
        text = text[4:]
    return re.sub(r"[\W_]+", "", text)


def parse_file_name(filename: str, number_hint: str = "") -> FileName:
    """`number_hint` (ComicInfo's Number, when known) settles a name
    like "Evil Dead 2 - Dark Ones Rising 001": read as series "Evil
    Dead", number 2 unless ComicInfo says 1, then as series "Evil Dead 2
    - Dark Ones Rising", number 001."""
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
    bracketed = _BRACKETED_SERIES_RE.match(head)
    if bracketed:
        result.series, result.number = bracketed.group("series").strip(), bracketed.group("num")
        result.title = (bracketed.group("title") or "").strip()
        return result
    match = _FILE_RE.match(head)
    if match:
        result.series = match.group("series").strip()
        result.volume = str(int(match.group("vol"))) if match.group("vol") else ""
        result.number = match.group("num")
        result.title = (match.group("title") or "").strip()
        in_title = _TITLE_NUMBER_RE.match(result.title) if number_hint and not result.is_tpb else None
        if (in_title and number_key(number_hint) == number_key(in_title.group("num"))
                and number_key(number_hint) != number_key(result.number)):
            result.series = f"{result.series}{' v' + match.group('vol') if match.group('vol') else ''} " \
                            f"{result.number} - {in_title.group('more')}"
            result.volume = ""
            result.number, result.title = in_title.group("num"), ""
    else:
        result.series = head  # a one-shot: no number
        vol = _FOLDER_VOL_RE.match(head)
        if vol and not vol.group("rest").strip():
            result.series, result.volume = vol.group("series").strip(), _volume(vol.group("vol"))
    return result


def _volume(text: str) -> str:
    """"02" -> "2"; "05.5" -> "5.5" (a collector's in-between volume)."""
    whole, dot, part = text.partition(".")
    return f"{int(whole)}{dot}{part}"


def parse_folder_name(name: str) -> FolderName:
    result = FolderName()
    if _GROUPING_FOLDER_RE.match(name.strip()):
        years = _YEARS_RE.search(name)
        if years:
            result.first_year = int(years.group(1))
            result.last_year = int(years.group(2)) if years.group(2) else 0
        return result
    vol = _FOLDER_VOL_RE.match(name) or _FOLDER_LEADING_VOL_RE.match(name)
    if vol:
        result.series = vol.group("series").strip()
        result.volume = _volume(vol.group("vol"))
        rest = vol.group("rest") or ""
        subtitle = _FOLDER_SUBTITLE_RE.match(rest)
        if subtitle:
            result.base_series = result.series
            result.series = f"{result.series} - {subtitle.group('sub').strip()}"
            rest = rest[subtitle.end():]
    else:
        result.series = re.split(r"[(\[]", name, maxsplit=1)[0].strip()
        rest = name[len(result.series):]
        # "Series 1975-1999" -- but not "Fantastic Four 2099", a name.
        result.series = re.sub(r"\s+\d{4}\s*-\s*(?:\d{4})?$", "", result.series).strip()
    years = _YEARS_RE.search(rest) or _YEARS_RE.search(name)
    if years:
        result.first_year = int(years.group(1))
        result.last_year = int(years.group(2)) if years.group(2) else 0
    else:
        single = _YEAR_RE.search(rest)
        if single:
            # "Carnage (1996)": that one year. An open run is written "(2026-)".
            result.first_year = result.last_year = int(single.group(1))
    return result


def number3(number: str) -> str:
    """ComicRack's {<number3>}: the whole-number part padded to three
    digits ("1" -> "001", "0.5" -> "000.5"); a TPB stays "TPB"."""
    if re.match(r"^TPB\s+(-|vol)", number, re.IGNORECASE) or re.match(r"^vol", number, re.IGNORECASE):
        return number  # the collection's own way of writing it, kept as is
    key = number_key(number)
    if key.startswith("TPB"):
        return "TPB" if key == "TPB" else f"TPB v{int(key.split()[1]):02d}"
    match = re.match(r"^(-?)(\d+)(.*)$", key)
    if not match:
        return number
    return f"{match.group(1)}{int(match.group(2)):03d}{match.group(3)}"


def library_name(series: str, number: str, publisher: str, year: str, month: str,
                 volume: str = "", extensions: str = ".cbz", title: str = "") -> str:
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
    suffix = f" - {title}" if title else ""
    return f"{series}{vol}{num}{suffix} ({publisher}, {year}-{month_number:02d}){extensions}"
