"""
core/collection_report.py

Collection > Collection Report...: patterns and irregularities in a
collection scan (core/collection_scan.py). Reads only the scan, never
the files, so it works on any computer the zip is carried to.

The folder layout is never configured: it's learned from the
collection itself. A series is its name plus its volume
(core/collection_names.py), and a file is "at home" in a folder named
for its series. Spin-offs living in their parent series' folder
("From the World of Minor Threats" in "Minor Threats") are normal and
never reported.

Checks:
- Moves: a file away from its series, when there's exactly ONE folder
  named for that series and volume elsewhere (for a file whose name has
  no volume, the folder's years pick between volumes), or else when
  most of the series already lives in one other folder. Several
  candidate folders are reported as a question, never guessed.
- Split series: one series (name + volume) spread over several folders
  once the suggested moves are made, or two folders named for it.
- File names: a name that breaks its folder's own pattern -- number
  width, the "(Publisher, year-month)" bracket, extra brackets, the
  series spelled differently from the rest of its issues, stray spaces
  -- with the Library Organizer name built from ComicInfo when possible.
- Duplicates: the same series, volume, number (and year, when known).
- Name vs ComicInfo: series, number, volume, year or month disagree.
- Formats & missing: not a real ZIP, a PDF, no or unreadable
  ComicInfo.xml, no pages, PageCount out of step with the pages, a path
  over Windows' 260-character limit.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from core.collection_names import (
    FileName, FolderName, library_name, number_key, parse_file_name, parse_folder_name, series_key,
)
from core.collection_scan import WINDOWS_PATH_LIMIT, ScanInfo, ScanRow

MIN_FOLDER_FILES = 3  # a folder needs this many files before it has a "pattern"
PATTERN_SHARE = 0.6  # ...and this share of them must agree on it

_DATE_FORM_TEXT = {
    "publisher+year-month": "(Publisher, year-month)",
    "publisher+year": "(Publisher, year)",
    "year-month": "(year-month)",
    "year": "(year)",
    "none": "no date",
}


@dataclass
class Move:
    path: str
    target_folder: str  # "" when it's a question, not a suggestion
    reason: str

    @property
    def target(self) -> str:
        return f"{self.target_folder}/{posixpath.basename(self.path)}" if self.target_folder else ""


@dataclass
class Split:
    series: str
    folders: list[tuple[str, int]]  # (folder, files there)
    note: str = ""


@dataclass
class NameIssue:
    path: str
    problems: list[str]
    suggested: str = ""  # a new file name in the same folder, "" when none

    @property
    def target(self) -> str:
        return posixpath.join(posixpath.dirname(self.path), self.suggested) if self.suggested else ""


@dataclass
class DuplicateGroup:
    series: str
    number: str
    rows: list[ScanRow]


@dataclass
class Mismatch:
    path: str
    field: str
    in_name: str
    in_comicinfo: str


@dataclass
class FormatIssue:
    path: str
    problem: str


@dataclass
class Report:
    moves: list[Move] = field(default_factory=list)
    splits: list[Split] = field(default_factory=list)
    names: list[NameIssue] = field(default_factory=list)
    duplicates: list[DuplicateGroup] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    formats: list[FormatIssue] = field(default_factory=list)


@dataclass
class _Entry:
    row: ScanRow
    name: FileName
    folder: str
    key: tuple[str, str]  # (series_key, volume)
    at_home: bool


def _year(text: str) -> int:
    return int(text) if text.isdigit() else 0


def _describe_key(entry: _Entry) -> str:
    return entry.name.series + (f" v{entry.key[1]}" if entry.key[1] else "")


def build_report(info: ScanInfo, rows: list[ScanRow]) -> Report:
    report = Report()
    folders: dict[str, FolderName] = {}
    entries: list[_Entry] = []
    for row in rows:
        folder = row.folder
        if folder not in folders:
            folders[folder] = parse_folder_name(posixpath.basename(folder))
        home = folders[folder]
        name = parse_file_name(row.file)
        key_series = series_key(name.series)
        same_series = bool(key_series) and series_key(home.series) == key_series
        volume = name.volume or (home.volume if same_series else "")
        at_home = same_series and (not name.volume or not home.volume or home.volume == name.volume)
        entries.append(_Entry(row, name, folder, (key_series, volume), at_home))

    named: dict[str, list[str]] = defaultdict(list)  # series_key -> folders named for it
    for folder, parsed in folders.items():
        if series_key(parsed.series):
            named[series_key(parsed.series)].append(folder)
    by_key: dict[tuple[str, str], list[_Entry]] = defaultdict(list)
    for entry in entries:
        if entry.key[0]:
            by_key[entry.key].append(entry)

    _find_moves(report, entries, folders, named, by_key)
    _find_splits(report, folders, named, by_key)
    _find_name_issues(report, entries)
    _find_duplicates(report, by_key)
    _find_mismatches(report, entries)
    _find_format_issues(report, info, rows)
    return report


LIST_COLUMNS = ["Check", "File", "Folder", "Problem", "Suggestion"]


def report_list(report: Report) -> list[list[str]]:
    """Every finding as one flat row, for Save List... (a CSV to work
    through elsewhere -- e.g. read here, moved on the collection's own
    computer)."""
    out = []

    def add(check, path, problem, suggestion=""):
        out.append([check, posixpath.basename(path), posixpath.dirname(path), problem, suggestion])

    for move in report.moves:
        add("Move", move.path, move.reason, move.target_folder)
    for split in report.splits:
        folders = "; ".join(f"{f} ({n})" if n else f for f, n in split.folders)
        out.append(["Split series", split.series, "", split.note or "spread over several folders", folders])
    for issue in report.names:
        add("File name", issue.path, "; ".join(issue.problems), issue.suggested)
    for group in report.duplicates:
        for row in group.rows:
            add("Duplicate", row.path, f"{group.series} {group.number}: {len(group.rows)} copies",
                f"{row.size / 1048576:.1f} MB, {row.pages or '?'} pages")
    for mismatch in report.mismatches:
        add("Name vs ComicInfo", mismatch.path,
            f"{mismatch.field}: “{mismatch.in_name}” in the name, “{mismatch.in_comicinfo}” in ComicInfo")
    for issue in report.formats:
        add("Format / missing", issue.path, issue.problem)
    return out


def _find_moves(report, entries, folders, named, by_key) -> None:
    for entry in entries:
        if entry.at_home or not entry.key[0]:
            continue
        series, volume = entry.key
        candidates = [f for f in named.get(series, []) if f != entry.folder]
        if volume:
            candidates = [f for f in candidates if folders[f].volume == volume]
        elif len(candidates) > 1:
            year = _year(entry.name.year or entry.row.year)
            if year:
                fitting = [f for f in candidates if folders[f].first_year and
                           folders[f].first_year <= year <= (folders[f].last_year or 9999)]
                candidates = fitting or candidates
        if len(candidates) == 1:
            report.moves.append(Move(entry.row.path, candidates[0], "a folder named for this series"))
            continue
        if len(candidates) > 1:
            listed = "; ".join(sorted(candidates)[:4]) + (" …" if len(candidates) > 4 else "")
            report.moves.append(Move(entry.row.path, "", f"several folders fit — which one? {listed}"))
            continue
        siblings = by_key[entry.key]
        counts = Counter(e.folder for e in siblings)
        top, top_count = counts.most_common(1)[0]
        if top != entry.folder and top_count >= 2 and top_count * 2 > len(siblings):
            report.moves.append(Move(
                entry.row.path, top, f"{top_count} of the {len(siblings)} files of this series are there",
            ))


def _find_splits(report, folders, named, by_key) -> None:
    moved = {m.path: m.target_folder for m in report.moves if m.target_folder}
    for key, siblings in by_key.items():
        counts = Counter(moved.get(e.row.path, e.folder) for e in siblings)
        if len(counts) > 1:
            report.splits.append(Split(_describe_key(siblings[0]), sorted(counts.items())))
    for series, candidates in named.items():
        by_volume: dict[str, list[str]] = defaultdict(list)
        for folder in candidates:
            by_volume[folders[folder].volume].append(folder)
        for volume, same in by_volume.items():
            if len(same) > 1:
                label = folders[same[0]].series + (f" v{volume}" if volume else "")
                report.splits.append(Split(label, [(f, 0) for f in sorted(same)], "several folders named for it"))
    report.splits.sort(key=lambda s: s.series.casefold())


def _majority(values: list, share: float = PATTERN_SHARE):
    if not values:
        return None
    value, count = Counter(values).most_common(1)[0]
    return value if count >= share * len(values) else None


def _number_width(number: str) -> int:
    match = re.match(r"^-?(\d+)", number)
    return len(match.group(1)) if match else 0


def _find_name_issues(report, entries) -> None:
    by_folder: dict[str, list[_Entry]] = defaultdict(list)
    for entry in entries:
        by_folder[entry.folder].append(entry)
    for folder, group in by_folder.items():
        issues = [e for e in group if _number_width(e.name.number)]
        width = _majority([_number_width(e.name.number) for e in issues]) if len(issues) >= MIN_FOLDER_FILES else None
        big = len(group) >= MIN_FOLDER_FILES
        date_form = _majority([e.name.date_form for e in group]) if big else None
        plain = _majority([not e.name.extra_brackets for e in group]) if big else None
        spellings: dict[tuple, str] = {}
        for key in {e.key for e in group}:
            names = [e.name.series for e in group if e.key == key]
            if len(names) >= 2:
                spellings[key] = _majority(names, 0.5 + 1e-9) or ""
        for entry in group:
            problems = []
            name = entry.name
            if width and _number_width(name.number) and _number_width(name.number) != width:
                problems.append(f"number {name.number} — the others here use {width} digits")
            if date_form and name.date_form != date_form:
                problems.append(f"{_DATE_FORM_TEXT[name.date_form]} — the others here use {_DATE_FORM_TEXT[date_form]}")
            if plain is True and name.extra_brackets:
                problems.append("extra bracket(s): " + ", ".join(f"({b})" for b in name.extra_brackets))
            usual = spellings.get(entry.key, "")
            if usual and name.series != usual:
                problems.append(f"series spelled “{name.series}” — the others use “{usual}”")
            stem = entry.row.file[: len(entry.row.file) - len(name.extensions)] if name.extensions else entry.row.file
            if "  " in stem or stem != stem.strip():
                problems.append("stray spaces")
            if not problems:
                continue
            row = entry.row
            series = usual or name.series
            suggested = library_name(
                series, name.number or row.number, row.publisher or name.publisher,
                row.year or name.year, row.month or name.month, name.volume, name.extensions or ".cbz",
            )
            if suggested == row.file:
                suggested = ""
            report.names.append(NameIssue(row.path, problems, suggested))


def _find_duplicates(report, by_key) -> None:
    for key, siblings in by_key.items():
        groups: dict[tuple[str, str], list[_Entry]] = defaultdict(list)
        for entry in siblings:
            year = entry.name.year or entry.row.year
            groups[(entry.name.number_key, year)].append(entry)
        undated = {num: g for (num, year), g in groups.items() if not year}
        for (num, year), group in groups.items():
            if year and num in undated:
                group = group + undated.pop(num)
            if len(group) > 1:
                first = group[0]
                report.duplicates.append(DuplicateGroup(
                    _describe_key(first), first.name.number or "(one-shot)", [e.row for e in group],
                ))
        for num, group in undated.items():
            if len(group) > 1:
                report.duplicates.append(DuplicateGroup(
                    _describe_key(group[0]), group[0].name.number or "(one-shot)", [e.row for e in group],
                ))
    report.duplicates.sort(key=lambda d: (d.series.casefold(), d.number))


def _find_mismatches(report, entries) -> None:
    for entry in entries:
        row, name = entry.row, entry.name
        if row.comicinfo != "yes":
            continue
        if row.series and name.series and series_key(row.series) != entry.key[0]:
            report.mismatches.append(Mismatch(row.path, "Series", name.series, row.series))
        if row.number and name.number and not name.is_tpb and number_key(row.number) != name.number_key:
            report.mismatches.append(Mismatch(row.path, "Number", name.number, row.number))
        volume = entry.key[1]
        if volume and row.volume.isdigit() and int(row.volume) < 1000 and int(row.volume) != int(volume):
            report.mismatches.append(Mismatch(row.path, "Volume", f"v{volume}", row.volume))
        if name.year and row.year and _year(name.year) != _year(row.year):
            report.mismatches.append(Mismatch(row.path, "Year", name.year, row.year))
        if (name.month and row.month and name.month.isdigit() and row.month.isdigit()
                and int(name.month) != int(row.month)):
            report.mismatches.append(Mismatch(row.path, "Month", name.month, row.month))


def _find_format_issues(report, info: ScanInfo, rows: list[ScanRow]) -> None:
    root = info.root.rstrip("\\/")
    for row in rows:
        problems = []
        if row.error and row.comicinfo != "bad":
            problems.append(row.error)
        if row.format == "PDF":
            problems.append("a PDF, not a comic archive")
        elif "→" in row.format:
            problems.append(f"named {row.format.split('→')[0].strip()} but really {row.format.split('→')[1].strip()}")
        elif row.container and row.container != "zip":
            problems.append(f"{row.format} ({row.container.upper()}) — not a ZIP, so it can't carry ComicInfo reliably")
        if row.comicinfo == "no":
            problems.append("no ComicInfo.xml")
        elif row.comicinfo == "bad":
            problems.append(f"ComicInfo.xml can't be read: {row.error}")
        if row.pages == "0":
            problems.append("no pages")
        elif row.pages and row.page_count.isdigit() and int(row.page_count) != int(row.pages):
            problems.append(f"PageCount says {row.page_count}, the archive has {row.pages} pages")
        length = len(root) + 1 + len(row.path)
        if length >= WINDOWS_PATH_LIMIT:
            problems.append(f"path is {length} characters — over Windows' {WINDOWS_PATH_LIMIT} limit")
        for problem in problems:
            report.formats.append(FormatIssue(row.path, problem))
