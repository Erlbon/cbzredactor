"""
core/scene_name.py

Parses a comic's filename into its parts -- series, issue number,
count, volume, title, years -- and sorts every bracketed tag into
scan info / format / notes (see core/scene_tags.py). Built for the
scene-style names new comics usually arrive with:

    G.I. Joe - A Real American Hero 332 (2026) (Digital) (Mephisto-Empire).webp.cbz
    Havana Split v01 - Welcome to Cuba (2026) (digital) (Knight Ripper-Empire).webp.cbz
    Lady Death (Chapter 23) - Primordial Havoc 001 (2026) (Digital) (DR & Quinch-Empire).webp.cbz
    Batman (2016) 045 (Digital) (Zone-Empire).cbz

Steps:
1. Strip the archive extension, then any image-format marker a
   converter added before it -- CbxConverter names its output
   "AAA.webp.cbz" -- which otherwise glues every trailing tag to the
   name.
2. Clean-ups modeled on ZenCBR's "Clean Names" (URL escapes, "+" or
   "_" for spaces, [] and {} brackets, spacing).
3. Pull out every bracketed phrase and classify it: a year, a date,
   "x of y", a chapter marker, or a scene tag (core/scene_tags.py).
4. Parse what's left: "Series vNN - Title", "Series 012 - Title",
   "Series 3 of 6", "Series #12", or a one-shot with no number.

A year in brackets BEFORE the issue number ("Batman (2016) 045") is
the series' start year -- ComicRack's convention for the Volume field
-- while one after it ("Batman 045 (2026)") is the issue's own year.

Pure string logic, no CbzBook/Qt dependency.
"""

from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass, field
from urllib.parse import unquote

from core import scene_tags

ARCHIVE_EXTENSIONS = {".cbz", ".cbr", ".cb7", ".cbt", ".zip", ".rar", ".7z", ".tar"}
# Added by converters before the real extension ("AAA.webp.cbz").
IMAGE_MARKER_EXTENSIONS = {".webp", ".jpg", ".jpeg", ".png", ".avif", ".jxl", ".gif"}

_BRACKET_RE = re.compile(r"\(([^()]*)\)")
_YEAR_RE = re.compile(r"^\d{4}$")
_DATE_RE = re.compile(r"^[\d\s\-/.]+$")
_MONTH_YEAR_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*,?\s*\d{4}$", re.IGNORECASE
)
_OF_RE = re.compile(r"^(?:(\d+)\s*)?of\s*(\d+)$", re.IGNORECASE)
_CHAPTER_RE = re.compile(r"^(?:chapter|chap|ch)\.?\s*0*(\d+(?:\.\d+)?)$", re.IGNORECASE)

_NUMBER = r"#?(\d+(?:\.\d+)?[a-z]?)"
_OF_TAIL_RE = re.compile(rf"^(.*?)\s+{_NUMBER}\s*of\s*(\d+)$", re.IGNORECASE)
_VOLUME_RE = re.compile(r"^(.*?)\s+v(?:ol(?:ume)?)?\.?\s*0*(\d+)\b\s*(.*)$", re.IGNORECASE)
_ISSUE_RE = re.compile(rf"^(.*?)\s+{_NUMBER}(?:\s+-\s+(.*))?$", re.IGNORECASE)
_LEADING_ISSUE_RE = re.compile(rf"^{_NUMBER}(?:\s+-\s+(.*))?$", re.IGNORECASE)


@dataclass
class ParsedName:
    series: str = ""
    number: str = ""
    count: str = ""
    volume: str = ""  # "1" from "v01", or a start year from "Series (2016) 045"
    title: str = ""
    year: str = ""  # the issue's own year
    chapter: str = ""  # "(Chapter 23)" reading-order marker -- a hint, not stored
    scan_info: list[str] = field(default_factory=list)  # original spelling, in filename order
    formats: list[str] = field(default_factory=list)  # canonical Format values
    notes: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)  # creator/publisher/date tags -- not written anywhere
    unknown: list[str] = field(default_factory=list)  # bracketed phrases nothing recognised

    @property
    def scan_information(self) -> str:
        """ScanInformation field value: "Digital, Zone-Empire"."""
        return ", ".join(self.scan_info)


def strip_extensions(filename: str) -> str:
    """"Name (2026) (Group).webp.cbz" -> "Name (2026) (Group)"."""
    stem = os.path.basename(filename)
    base, ext = os.path.splitext(stem)
    if ext.lower() in ARCHIVE_EXTENSIONS:
        stem = base
    while True:
        base, ext = os.path.splitext(stem)
        if ext.lower() not in IMAGE_MARKER_EXTENSIONS:
            return stem
        stem = base


def clean_name(stem: str) -> str:
    """Typographic clean-ups for downloaded names, after ZenCBR's
    "Clean Names": URL escapes decoded, "+" (only when there are no
    spaces at all) and "_" turned into spaces, [] and {} into (),
    spacing around brackets and runs of spaces tidied."""
    if re.search(r"%[0-9A-Fa-f]{2}", stem):
        stem = unquote(stem)
    if "+" in stem and " " not in stem:
        stem = stem.replace("+", " ")
    stem = stem.replace("_", " ")
    stem = stem.translate(str.maketrans("[]{}", "()()"))
    stem = re.sub(r"\s*\(\s*", " (", stem)
    stem = re.sub(r"\s*\)", ")", stem)
    stem = stem.replace("()", "")
    return re.sub(r"\s+", " ", stem).strip()


def _plausible_year(text: str) -> bool:
    return bool(_YEAR_RE.match(text)) and 1900 <= int(text) <= datetime.datetime.now().year + 1


def _strip_zeros(number: str) -> str:
    stripped = number.lstrip("0")
    return stripped if stripped and not stripped.startswith(".") else "0" + stripped


def parse_filename(path: str, extra_tags: dict[str, str] | None = None) -> ParsedName:
    """Parses `path`'s filename. `extra_tags` is a user's own tag
    classifications (normalized phrase -> category), which win over the
    built-in list."""
    result = ParsedName()
    text = clean_name(strip_extensions(path))

    # --- bracketed phrases -------------------------------------------
    years: list[tuple[int, str]] = []  # (position in text, year)
    for match in _BRACKET_RE.finditer(text):
        phrase = match.group(1).strip()
        if not phrase:
            continue
        if _plausible_year(phrase):
            years.append((match.start(), phrase))
            continue
        if _DATE_RE.match(phrase) or _MONTH_YEAR_RE.match(phrase):
            result.hints.append(phrase)
            continue
        of_match = _OF_RE.match(phrase)
        if of_match:
            if of_match.group(1):
                result.number = _strip_zeros(of_match.group(1))
            result.count = str(int(of_match.group(2)))
            continue
        chapter_match = _CHAPTER_RE.match(phrase)
        if chapter_match:
            result.chapter = chapter_match.group(1)
            continue

        category = scene_tags.classify(phrase, extra_tags)
        if category == scene_tags.SCAN:
            # A lone lower-case word ("digital") -> "Digital"; anything
            # else ("c2c", "DR & Quinch-Empire") is kept as written.
            result.scan_info.append(phrase.capitalize() if phrase.isalpha() and phrase.islower() else phrase)
        elif category == scene_tags.FORMAT:
            value = scene_tags.canonical_format(phrase)
            if value not in result.formats:
                result.formats.append(value)
        elif category == scene_tags.NOTE:
            result.notes.append(phrase)
        elif category in (scene_tags.HINT, scene_tags.ORDER):
            result.hints.append(phrase)
        else:
            result.unknown.append(phrase)

    # Each bracket is replaced by a marker so we can tell afterwards
    # whether a year stood before or after the issue number.
    core = _BRACKET_RE.sub(lambda m: f" \x00{m.start()}\x00 ", text)
    positions = {int(p) for p in re.findall(r"\x00(\d+)\x00", core)}
    core_plain = re.sub(r"\s*\x00\d+\x00\s*", " ", core)
    core_plain = re.sub(r"\s+-\s*|\s*-\s+", " - ", core_plain)
    core_plain = re.sub(r"\s+", " ", core_plain).strip(" -_.")

    _parse_core(core_plain, result)

    # --- which year is which -------------------------------------------
    for position, year in years:
        if position not in positions:
            continue
        before_number = result.number and _number_follows(core, position, result.number)
        if before_number and not result.volume:
            result.volume = year
        elif not result.year:
            result.year = year
    return result


def proposed_fields(parsed: ParsedName, current_notes: str = "") -> dict[str, str]:
    """ComicInfo field values (attribute names) the filename supports --
    only non-blank ones. Notes are APPENDED to `current_notes` (never
    replacing what's there), and only phrases not already in it.
    Chapter markers, hints and unknown tags are never written."""
    fields = {
        "series": parsed.series,
        "number": parsed.number,
        "count": parsed.count,
        "volume": parsed.volume,
        "title": parsed.title,
        "year": parsed.year,
        "scan_information": parsed.scan_information,
        "format": parsed.formats[0] if parsed.formats else "",
    }
    new_notes = [n for n in parsed.notes if n.casefold() not in (current_notes or "").casefold()]
    if new_notes:
        joined = ", ".join(new_notes)
        fields["notes"] = f"{current_notes.rstrip()}\n{joined}" if current_notes.strip() else joined
    return {key: value for key, value in fields.items() if value}


def _number_follows(core: str, position: int, number: str) -> bool:
    """True if the issue number appears in the text after the bracket
    that started at `position`."""
    marker = f"\x00{position}\x00"
    after = core.split(marker, 1)[1] if marker in core else ""
    after = re.sub(r"\x00\d+\x00", " ", after)
    return bool(re.search(rf"(?<![\w.])#?0*{re.escape(number)}(?![\w.])", after))


def _parse_core(core: str, result: ParsedName) -> None:
    of_match = _OF_TAIL_RE.match(core)
    if of_match:
        result.series = of_match.group(1).strip(" -_.")
        result.number = _strip_zeros(of_match.group(2))
        result.count = str(int(of_match.group(3)))
        return

    volume_match = _VOLUME_RE.match(core)
    if volume_match and volume_match.group(1).strip():
        result.series = volume_match.group(1).strip(" -_.")
        result.volume = str(int(volume_match.group(2)))
        rest = volume_match.group(3).strip(" -")
        issue = _LEADING_ISSUE_RE.match(rest) if rest else None
        if issue:
            result.number = _strip_zeros(issue.group(1))
            result.title = (issue.group(2) or "").strip()
        else:
            result.title = rest
        return

    issue_match = _ISSUE_RE.match(core)
    if issue_match and issue_match.group(1).strip(" -_."):
        result.series = issue_match.group(1).strip(" -_.")
        if not result.number:
            result.number = _strip_zeros(issue_match.group(2))
        result.title = (issue_match.group(3) or "").strip()
        return

    result.series = core.strip(" -_.")
