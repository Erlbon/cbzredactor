"""
core/comicinfo_check.py

Operations > Validate & Fix...: checks each file's ComicInfo.xml for
mistakes that creep in from scene filenames, other tools and hand edits,
and proposes a fix per finding -- the same idea as epubredactor's
Validate & Fix, applied here as ordinary in-memory edits (reviewed,
one Undo step, written on Save).

Checks (check_book):
- PageCount doesn't match the pages in the archive (fix: the real count).
- An impossible date: Year outside 1800..next year, Month not 1-12, Day
  not valid for that month (fix: clear it -- the right value can't be
  guessed).
- Scene junk left in Series or Title: bracketed scan-group, format or
  note tags ("(Zone-Empire)", "(Digital)", "(2 covers)" -- the same
  whole-phrase rules as Read Filename Tags, so title words are never
  touched) and file extensions (".cbz", ".webp") (fix: removed).
- An issue Number with GCD's "[nn]" (no number) marker (fix: clear), a
  leading "#" or leading zeros ("#007" -> "7").
- Stray whitespace in any text field (fix: trimmed, runs of spaces
  collapsed in single-line fields).
- A LanguageISO in capitals or with a region ("EN", "en-US") (fix: "en").
- Suggestions: Black & White when the tags or scan information say
  "b&w"/"black and white"; Manga when the genre or tags say "manga"
  (only when the field is empty).
- Count lower than Number (reported; which one is wrong can't be told).
"""

from __future__ import annotations

import calendar
import datetime
import re
from dataclasses import dataclass
from typing import Optional

from core.scene_tags import FORMAT, NOTE, SCAN, classify

# Fields holding one line of text (runs of spaces collapsed) vs. free text
# (only trimmed): everything else in ComicInfoMetadata is left alone.
_SINGLE_LINE_FIELDS = (
    "title", "series", "number", "count", "volume", "alternate_series", "alternate_number",
    "alternate_count", "writer", "penciller", "inker", "colorist", "letterer", "cover_artist",
    "editor", "translator", "publisher", "imprint", "genre", "tags", "web", "language_iso",
    "format", "characters", "teams", "locations", "scan_information", "story_arc",
    "story_arc_number", "series_group", "age_rating", "main_character_or_team", "gtin",
)
_FREE_TEXT_FIELDS = ("summary", "notes", "review")
_BRACKETED = re.compile(r"\s*[\(\[]([^\(\)\[\]]+)[\)\]]")
_EXTENSION_TAIL = re.compile(r"(?:\.(?:cbz|cbr|cb7|cbt|zip|rar|webp|jpe?g|png))+\s*$", re.IGNORECASE)
_BW = re.compile(r"\b(?:b\s*&\s*w|b/w|black\s*(?:and|&)\s*white)\b", re.IGNORECASE)
_MANGA = re.compile(r"\bmanga\b", re.IGNORECASE)


@dataclass
class Finding:
    field: str  # ComicInfoMetadata attribute
    label: str  # shown to the user, e.g. "Series"
    message: str
    fix: Optional[str] = None  # the proposed new value; None = report only

    @property
    def fixable(self) -> bool:
        return self.fix is not None


def _label(field: str) -> str:
    special = {"language_iso": "LanguageISO", "page_count": "PageCount", "black_and_white": "Black & White"}
    return special.get(field, field.replace("_", " ").title())


def _without_scene_junk(text: str) -> str:
    def keep_or_drop(match: re.Match) -> str:
        return "" if classify(match.group(1).strip()) in (SCAN, FORMAT, NOTE) else match.group(0)

    cleaned = _BRACKETED.sub(keep_or_drop, text)
    cleaned = _EXTENSION_TAIL.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" -_")


def _number_fix(number: str) -> Optional[str]:
    if number.strip() == "[nn]":
        return ""
    text = number.strip().lstrip("#").strip()
    match = re.fullmatch(r"0+(\d.*)", text)
    if match and not re.fullmatch(r"0+(\.\d+)?", text):
        text = match.group(1)
    return text if text != number else None


def check_metadata(meta, actual_pages: Optional[int] = None, today: Optional[datetime.date] = None) -> list[Finding]:
    today = today or datetime.date.today()
    findings: list[Finding] = []

    def add(field: str, message: str, fix: Optional[str] = None) -> None:
        findings.append(Finding(field, _label(field), message, fix))

    # Whitespace first: the other checks look at the trimmed text.
    trimmed: dict[str, str] = {}
    for field in _SINGLE_LINE_FIELDS + _FREE_TEXT_FIELDS:
        value = getattr(meta, field, "") or ""
        clean = value.strip() if field in _FREE_TEXT_FIELDS else re.sub(r"\s+", " ", value).strip()
        trimmed[field] = clean
        if clean != value:
            add(field, "stray spaces", clean)

    if actual_pages is not None and meta.page_count and meta.page_count != str(actual_pages):
        add("page_count", f"says {meta.page_count} pages, the archive has {actual_pages}", str(actual_pages))

    year, month, day = trimmed.get("year") or meta.year, meta.month, meta.day
    year_ok = year.isdigit() and 1800 <= int(year) <= today.year + 1
    if year and not year_ok:
        add("year", f"{year!r} isn't a plausible year", "")
    month_ok = month.isdigit() and 1 <= int(month) <= 12
    if month and not month_ok:
        add("month", f"{month!r} isn't a month (1-12)", "")
    if day:
        limit = calendar.monthrange(int(year), int(month))[1] if year_ok and month_ok else 31
        if not (day.isdigit() and 1 <= int(day) <= limit):
            add("day", f"{day!r} isn't a day of that month", "")

    for field in ("series", "title"):
        value = trimmed[field]
        clean = _without_scene_junk(value)
        if value and clean != value:
            add(field, "scene tags or a file extension left in it", clean)

    number = trimmed["number"]
    if number:
        fix = _number_fix(number)
        if fix == "":
            add("number", '"[nn]" is GCD\'s "no number" marker, not an issue number', "")
        elif fix is not None:
            add("number", f"{number!r} -- written without a # or leading zeros", fix)
    count = trimmed["count"]
    if number.isdigit() and count.isdigit() and int(count) and int(count) < int(number):
        add("count", f"Count {count} is lower than Number {number}")

    language = trimmed["language_iso"]
    if language:
        base = language.split("-")[0].split("_")[0].lower()
        if language != base and re.fullmatch(r"[a-z]{2}", base):
            add("language_iso", f"{language!r} -- ComicInfo uses the plain lower-case code", base)

    if not meta.black_and_white and any(_BW.search(trimmed[f]) for f in ("tags", "scan_information", "notes", "genre")):
        add("black_and_white", "the tags say black and white", "Yes")
    if not meta.manga and any(_MANGA.search(trimmed[f]) for f in ("genre", "tags")):
        add("manga", "the genre or tags say manga", "Yes")
    return _merge_by_field(findings)


def _merge_by_field(findings: list[Finding]) -> list[Finding]:
    """One line per field: a later fix already includes an earlier one
    (the scene-junk fix is computed from the trimmed text), so the
    messages are joined and the last fix kept."""
    merged: dict[str, Finding] = {}
    for finding in findings:
        earlier = merged.get(finding.field)
        if earlier is None:
            merged[finding.field] = finding
        else:
            merged[finding.field] = Finding(
                finding.field, finding.label, f"{earlier.message}; {finding.message}",
                finding.fix if finding.fix is not None else earlier.fix,
            )
    return list(merged.values())


def check_book(book) -> list[Finding]:
    """Findings for one loaded CbzBook (none for a file that didn't load)."""
    if book.load_error or book.needs_conversion:
        return []
    return check_metadata(book.metadata, book.actual_page_count)


# The two results a validation scan is stamped with (CbzBook.record_scan):
# no findings left, or at least one (fixable or not).
SCAN_OK = "OK"
SCAN_ISSUES = "ISSUES"


def scan_status(findings: list[Finding]) -> str:
    return SCAN_ISSUES if findings else SCAN_OK
