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
    FileName, FolderName, library_name, loose_key, number_key, numbers_agree, parse_file_name, parse_folder_name, series_key,
)
from core.collection_fix import (
    KIND_COMICINFO_FROM_NAME, KIND_CONVERT, KIND_EXTENSION, KIND_NEW_COMICINFO, KIND_PAGECOUNT, KIND_RENAME_FROM_COMICINFO,
    Fix, fields_from_name, name_from_comicinfo,
)
from core.credit_pages import MATCH_DISTANCE, hamming
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
    covers: str = ""  # "" without fingerprints; "same cover" / "covers differ" / "same cover, different names"


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
    fixes: list[Fix] = field(default_factory=list)  # what "Apply" can fix (core/collection_fix.py)


@dataclass
class _Entry:
    row: ScanRow
    name: FileName
    folder: str
    key: tuple[str, str]  # (series_key, volume)
    at_home: bool
    home: str  # the folder (or parent folder) named for its series; else its own folder


def _year(text: str) -> int:
    return int(text) if text.isdigit() else 0


def _describe_key(entry: _Entry) -> str:
    return entry.name.series + (f" v{entry.key[1]}" if entry.key[1] else "")


_PUBLISHER_NOISE = re.compile(r"\b(comics?|publishing|publications?|press|entertainment|inc|ltd|llc|verlag|editions?|"
                              r"ediciones|editorial[e]?|group|studios?|productions?|books?|uitgeverij|s ?l|s ?a)\b")


def _publisher_key(publisher: str) -> str:
    return re.sub(r"[\W_]+", "", _PUBLISHER_NOISE.sub(" ", series_key(publisher)))


def _same_publisher(a: str, b: str) -> bool:
    """Unknown on either side counts as the same; "DC" and "DC Comics"
    match."""
    if not a or not b:
        return True
    return a.startswith(b) or b.startswith(a)


_GENERIC_SERIES = {"vol", "volume", "chapter", "chap", "tome", "band", "issue", "book", "part", "episode", "livre",
                   "tomo", "numero", "no", "nr"}
_LANGUAGE_TAG_RE = re.compile(r"\[([A-Za-z]{2,3})\]\s*$")


class _Folders:
    """What each folder of the collection says about itself: its parsed
    name, its top-level branch ("5973 - US Comics"), the publisher most
    of its files carry, and the years it covers."""

    def __init__(self, entries_by_folder: dict[str, list]):
        self.parsed: dict[str, FolderName] = {}
        self.publisher: dict[str, str] = {}
        for folder, group in entries_by_folder.items():
            self.parse(folder)
            self.publisher[folder] = _majority([_publisher_key(e.name.publisher or e.row.publisher)
                                                for e in group if e.name.publisher or e.row.publisher], 0.5) or ""

    def parse(self, folder: str) -> FolderName:
        if folder not in self.parsed:
            self.parsed[folder] = parse_folder_name(posixpath.basename(folder))
        return self.parsed[folder]

    @staticmethod
    def language(folder: str) -> str:
        """"ES" from "Comix Kiss Comix (1991-2011)(239 issues)[ES]"."""
        tag = _LANGUAGE_TAG_RE.search(posixpath.basename(folder))
        return tag.group(1).upper() if tag else ""

    @staticmethod
    def branch(folder: str) -> str:
        return folder.split("/", 1)[0]

    def years(self, folder: str) -> tuple[int, int]:
        """(first, last) years of a folder, or of the nearest folder above
        it that has them ("3.45 - DC, New Justice (2018-2021)/The Flash");
        last is 9999 for an open run, (0, 0) when nothing says."""
        current = folder
        while current:
            parsed = self.parse(current)
            if parsed.first_year:
                return parsed.first_year, parsed.last_year or 9999
            current = posixpath.dirname(current)
        return 0, 0

    def years_overlap(self, a: str, b: str) -> bool:
        """Two runs share at least a year's span; a relaunch the year the
        old run ended (Green Hornet 2010-2013, 2013-2014) doesn't count."""
        (a1, a2), (b1, b2) = self.years(a), self.years(b)
        if not a1 or not b1:
            return True
        if a1 == a2 or b1 == b2:  # a single year
            return a1 <= b2 and b1 <= a2
        return min(a2, b2) - max(a1, b1) >= 1

    def same_run(self, a: str, b: str) -> bool:
        """Could these two folders be one series split in two? Only side by
        side (or one inside the other): the same name under two eras or
        imprints ("DC, New Justice/Green Lantern", "DC, All In/Green
        Lantern") is the collection's own layout. And not from different
        publishers, languages, or years that don't overlap (Poison Ivy
        2022-2024 vs 2024-)."""
        near = (posixpath.dirname(a) == posixpath.dirname(b)
                or a.startswith(b + "/") or b.startswith(a + "/"))
        return (near
                and _same_publisher(self.publisher.get(a, ""), self.publisher.get(b, ""))
                and self.language(a) == self.language(b)
                and self.years_overlap(a, b))

    def fits(self, folder: str, entry: _Entry) -> bool:
        """Could this folder be home to this file?"""
        publisher = _publisher_key(entry.name.publisher or entry.row.publisher)
        theirs = self.publisher.get(folder, "")
        if self.branch(folder) != self.branch(entry.folder):
            # Another branch of the collection (another country's "Donald
            # Duck") only when the publisher is known to match -- a file
            # waiting in an "Incoming" folder still finds its home.
            if not (publisher and theirs and _same_publisher(theirs, publisher)):
                return False
        elif not _same_publisher(theirs, publisher):
            return False
        return self.language(folder) == self.language(entry.folder)

    def outside_run(self, folder: str, entry: _Entry) -> bool:
        """A closed run that ended before (or began after) this issue."""
        year = _year(entry.name.year or entry.row.year)
        first, last = self.years(folder)
        return bool(year and first and not first <= year <= last)


def _home(folder: str, key_series: str, volume: str, folders: _Folders) -> tuple[str, str]:
    """(home folder, its volume): the nearest folder on the path, the file's
    own first, named for the series -- "Kuifje/1972 (52 issues)/Kuifje
    197245" is at home in "Kuifje". ("", "") when none is."""
    current = folder
    while current:
        parsed = folders.parse(current)
        if key_series and key_series in (series_key(parsed.series), series_key(parsed.base_series)) and (
                not volume or not parsed.volume or parsed.volume == volume):
            return current, parsed.volume
        current = posixpath.dirname(current)
    return "", ""


def _grouping_home(folder: str, folders: _Folders) -> str:
    """A file in a grouping folder ("Le Noveau Pif (1982-1985)/1983 (52
    issues)") belongs with the folder above it."""
    while folder and not folders.parse(folder).series and posixpath.dirname(folder):
        folder = posixpath.dirname(folder)
    return folder


def build_report(info: ScanInfo, rows: list[ScanRow]) -> Report:
    report = Report()
    entries: list[_Entry] = []
    by_folder: dict[str, list[_Entry]] = defaultdict(list)
    for row in rows:
        name = parse_file_name(row.file, row.number if row.comicinfo == "yes" else "")
        key_series = series_key(name.series)
        if key_series in _GENERIC_SERIES:  # "Volume 01 - Nana to Kaoru ...": no series to go by
            key_series = ""
        entry = _Entry(row, name, row.folder, (key_series, name.volume), False, row.folder)
        entries.append(entry)
        by_folder[row.folder].append(entry)
    folders = _Folders(by_folder)
    for entry in entries:
        home, home_volume = _home(entry.folder, entry.key[0], entry.name.volume, folders)
        if home:
            entry.at_home, entry.home = True, home
            entry.key = (entry.key[0], entry.name.volume or home_volume)
        else:
            entry.home = _grouping_home(entry.folder, folders)

    named: dict[str, list[str]] = defaultdict(list)  # series_key -> folders named for it
    for folder in by_folder:
        parsed = folders.parse(folder)
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
    _find_mismatches(report, by_key)
    _find_format_issues(report, info, rows)
    _find_fixes(report, entries)
    return report


def _find_fixes(report, entries) -> None:
    by_path = {e.row.path: e for e in entries}
    taken = {e.row.path for e in entries}
    for issue in report.formats:
        entry = by_path[issue.path]
        row, name = entry.row, entry.name
        if issue.problem.startswith("PageCount says"):
            report.fixes.append(Fix(row.path, KIND_PAGECOUNT, f"PageCount {row.page_count} → {row.pages}"))
        elif issue.problem == "no ComicInfo.xml" and row.container == "zip" and name.series and name.number:
            fields = fields_from_name(name)
            report.fixes.append(Fix(row.path, KIND_NEW_COMICINFO,
                                    "create ComicInfo.xml: " + ", ".join(f"{k} {v}" for k, v in fields.items()), fields))
        elif "not a ZIP" in issue.problem and row.container in ("rar", "7z", "tar"):
            report.fixes.append(Fix(row.path, KIND_CONVERT, f"convert {row.format} to a .cbz; original to the Recycle Bin",
                                    convert=True))
        elif issue.problem.startswith("named ") and issue.problem.endswith("but really ZIP"):
            new_path = posixpath.splitext(row.path)[0] + ".cbz"
            if new_path not in taken:
                report.fixes.append(Fix(row.path, KIND_EXTENSION, "rename to .cbz (it is a ZIP)", new_path=new_path))
    by_file: dict[str, list[Mismatch]] = defaultdict(list)
    for mismatch in report.mismatches:
        by_file[mismatch.path].append(mismatch)
    for path, mismatches in by_file.items():
        entry = by_path[path]
        wanted = {m.field for m in mismatches}
        fields = fields_from_name(entry.name, wanted)
        if fields:
            what = "; ".join(f"{m.field}: {m.in_comicinfo} → {m.in_name}" for m in mismatches)
            report.fixes.append(Fix(path, KIND_COMICINFO_FROM_NAME, "set ComicInfo — " + what, fields, choice=path))
        new_name = name_from_comicinfo(entry.name, entry.row, wanted)
        new_path = posixpath.join(posixpath.dirname(path), new_name) if new_name else ""
        if new_path and new_path != path and new_path not in taken:
            report.fixes.append(Fix(path, KIND_RENAME_FROM_COMICINFO, f"rename to {new_name}",
                                    new_path=new_path, choice=path))
    report.fixes.sort(key=lambda f: (f.path.casefold(), f.kind))


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
            add("Duplicate", row.path,
                f"{group.series} {group.number}: {len(group.rows)} copies" + (f" ({group.covers})" if group.covers else ""),
                f"{row.size / 1048576:.1f} MB, {row.pages or '?'} pages")
    for mismatch in report.mismatches:
        add("Name vs ComicInfo", mismatch.path,
            f"{mismatch.field}: “{mismatch.in_name}” in the name, “{mismatch.in_comicinfo}” in ComicInfo")
    for issue in report.formats:
        add("Format / missing", issue.path, issue.problem)
    return out


def _find_moves(report, entries, folders: _Folders, named, by_key) -> None:
    homes = {key: Counter(e.home for e in siblings) for key, siblings in by_key.items()}
    for entry in entries:
        if entry.at_home or not entry.key[0]:
            continue
        series, volume = entry.key
        candidates = [f for f in named.get(series, []) if f != entry.folder and folders.fits(f, entry)]
        if volume:
            candidates = [f for f in candidates if folders.parse(f).volume == volume]
        elif len(candidates) > 1:
            year = _year(entry.name.year or entry.row.year)
            if year:
                fitting = [f for f in candidates if folders.parse(f).first_year and
                           folders.parse(f).first_year <= year <= (folders.parse(f).last_year or 9999)]
                candidates = fitting or candidates
        if len(candidates) == 1:
            if not folders.outside_run(candidates[0], entry):
                report.moves.append(Move(entry.row.path, candidates[0], "a folder named for this series"))
            continue
        if len(candidates) > 1:
            listed = "; ".join(sorted(candidates)[:4]) + (" …" if len(candidates) > 4 else "")
            report.moves.append(Move(entry.row.path, "", f"several folders fit — which one? {listed}"))
            continue
        counts = Counter({f: n for f, n in homes[entry.key].items() if f == entry.folder or folders.fits(f, entry)})
        if not counts:
            continue
        top, top_count = counts.most_common(1)[0]
        if top != entry.home and top_count >= 2 and top_count * 2 > sum(counts.values()):
            report.moves.append(Move(
                entry.row.path, top, f"{top_count} of the {sum(counts.values())} files of this series are there",
            ))


def _find_splits(report, folders: _Folders, named, by_key) -> None:
    moved = {m.path: m.target_folder for m in report.moves if m.target_folder}
    for key, siblings in by_key.items():
        counts = Counter(moved.get(e.row.path, e.home) for e in siblings)
        if len(counts) < 2:
            continue
        # Strays are Moves; a split is the series living in two of ITS folders.
        # (Issues read in crossover folders -- "Realm Of Kings", "War Of
        # Kings" -- aren't at home anywhere and aren't a split either.)
        homes = {e.home for e in siblings if e.at_home} | {moved[e.row.path] for e in siblings if e.row.path in moved}
        counts = Counter({f: n for f, n in counts.items() if f in homes})
        if len(counts) < 2:
            continue
        top = counts.most_common(1)[0][0]
        together = {f: n for f, n in counts.items() if f == top or folders.same_run(f, top)}
        if len(together) > 1:
            report.splits.append(Split(_describe_key(siblings[0]), sorted(together.items())))
    for series, candidates in named.items():
        by_volume: dict[str, list[str]] = defaultdict(list)
        for folder in candidates:
            by_volume[folders.parse(folder).volume].append(folder)
        for volume, same in by_volume.items():
            # A folder inside the series' own folder isn't a second home.
            same = [f for f in same if not any(g != f and f.startswith(g + "/") for g in same)]
            same = [f for f in same if any(g != f and folders.same_run(f, g) for g in same)]
            if len(same) > 1 and not any({f for f, _n in split.folders} == set(same) for split in report.splits):
                label = folders.parse(same[0]).series + (f" v{volume}" if volume else "")
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
            report.names.append(NameIssue(entry.row.path, problems, _suggested_name(entry, usual)))


def _suggested_name(entry: _Entry, usual_series: str) -> str:
    """The Library Organizer name for a file, changing only what is wrong
    with it: the file's own series, number, title, publisher and date are
    kept, and ComicInfo fills in only what the name lacks. "" when there's
    no safe rewrite -- a name with extra brackets or a "[Series 07]" (which
    would be lost), or nothing to build the date from."""
    name, row = entry.name, entry.row
    if name.extra_brackets or "[" in row.file:
        return ""
    year = name.year or row.year
    month = name.month
    if not month and (not name.year or name.year == row.year):
        month = row.month
    suggested = library_name(
        usual_series or name.series, name.number or row.number, name.publisher or row.publisher,
        year, month, name.volume, name.extensions or ".cbz", name.title,
    )
    return "" if suggested == row.file else suggested


def _variant(entry: _Entry) -> tuple[str, tuple[str, ...]]:
    """What tells two copies of one issue apart on purpose: a title
    ("Part 1" / "Part 2") and extra brackets ("(English)" / "(German)")."""
    return loose_key(entry.name.title), tuple(sorted(loose_key(b) for b in entry.name.extra_brackets))


_DATE_LIKE_RE = re.compile(r"^(19|20)\d\d[-.]\d{1,2}$")  # "2005-09" in a name is a date, not an issue number
_STEM_EXTENSIONS_RE = re.compile(r"(\.(webp|jpe?g|png))?\.(cbz|zip|cbr|rar|cb7|7z|cbt|pdf)$", re.IGNORECASE)


def _find_duplicates(report, by_key) -> None:
    for key, siblings in by_key.items():
        groups: dict[tuple[str, str], list[_Entry]] = defaultdict(list)
        unnumbered: dict[str, list[_Entry]] = defaultdict(list)
        for entry in siblings:
            year = entry.name.year or entry.row.year
            if not entry.name.number_key:
                # No number to tell issues apart by: two files are only the
                # same comic when their whole names match ("x.zip" and
                # "x.cbz"), not when they merely share a series.
                unnumbered[loose_key(_STEM_EXTENSIONS_RE.sub("", entry.row.file))].append(entry)
                continue
            groups[(entry.name.number_key, year)].append(entry)
        undated = {num: g for (num, year), g in groups.items() if not year}
        found = []
        for (num, year), group in groups.items():
            if year and num in undated:
                group = group + undated.pop(num)
            found.append(group)
        found.extend(undated.values())
        found.extend(copies for copies in unnumbered.values() if len(copies) > 1)
        for group in found:
            for copies in _split_variants(group):
                if len(copies) > 1:
                    first = copies[0]
                    report.duplicates.append(DuplicateGroup(
                        _describe_key(first), first.name.number or "(one-shot)", [e.row for e in copies],
                        _cover_verdict([e.row for e in copies]),
                    ))
    if any(e.row.cover for siblings in by_key.values() for e in siblings):
        _find_renamed_copies(report, by_key)
    seen_groups: set[frozenset[str]] = set()
    unique = []
    for group in report.duplicates:
        paths = frozenset(row.path for row in group.rows)
        if paths not in seen_groups:
            seen_groups.add(paths)
            unique.append(group)
    report.duplicates[:] = unique
    report.duplicates.sort(key=lambda d: (d.series.casefold(), d.number))


def _cover_hash(row: ScanRow) -> int | None:
    return int(row.cover, 16) if row.cover else None


def _cover_verdict(rows: list[ScanRow]) -> str:
    """"same cover" when every fingerprinted copy's cover matches the first
    one's, "covers differ" when one doesn't (a variant cover, or two issues
    that share a name); "" when fewer than two have a fingerprint."""
    hashes = [h for h in map(_cover_hash, rows) if h is not None]
    if len(hashes) < 2:
        return ""
    return "same cover" if all(hamming(hashes[0], h) <= MATCH_DISTANCE for h in hashes[1:]) else "covers differ"


def _find_renamed_copies(report, by_key) -> None:
    """Copies of one issue filed under different series names: the same
    number and year, and a matching cover. (A cover alone proves nothing --
    a TPB reuses issue #1's -- so it needs the number and year too.) Not
    reported when the names already put them in one group above."""
    grouped = {row.path for d in report.duplicates for row in d.rows}
    buckets: dict[tuple[str, str], list[_Entry]] = defaultdict(list)
    for siblings in by_key.values():
        for entry in siblings:
            year = entry.name.year or entry.row.year
            if entry.row.cover and year and entry.name.number and not entry.name.is_tpb:
                buckets[(entry.name.number_key, year)].append(entry)
    for members in buckets.values():
        if len(members) < 2:
            continue
        seen: set[int] = set()
        for i, first in enumerate(members):
            if i in seen:
                continue
            cluster = [first]
            for j in range(i + 1, len(members)):
                other = members[j]
                if j not in seen and other.key != first.key and \
                        hamming(_cover_hash(first.row), _cover_hash(other.row)) <= MATCH_DISTANCE:
                    cluster.append(other)
                    seen.add(j)
            if len(cluster) > 1 and not all(e.row.path in grouped for e in cluster):
                report.duplicates.append(DuplicateGroup(
                    _describe_key(first), first.name.number, [e.row for e in cluster], "same cover, different names",
                ))


def _split_variants(group: list[_Entry]) -> list[list[_Entry]]:
    """Copies whose titles or extra brackets differ are different comics;
    one without a title or brackets still matches any of them."""
    variants = {_variant(e) for e in group} - {("", ())}
    if len(variants) < 2:
        return [group]
    plain = [e for e in group if _variant(e) == ("", ())]
    return [[e for e in group if _variant(e) == v] + plain for v in sorted(variants)]


def _months_apart(name: FileName, row: ScanRow) -> int:
    """Months between the date in the name and in ComicInfo (a year apart
    at most when either has no month)."""
    def valid(month: str) -> bool:
        return month.isdigit() and 1 <= int(month) <= 12
    if not (valid(name.month) and valid(row.month)):  # only years to go by: a year's grace
        return max(0, abs(_year(name.year) - _year(row.year)) - 1) * 12
    return abs(_year(name.year) * 12 + int(name.month) - _year(row.year) * 12 - int(row.month))


def _series_agree(name: FileName, in_comicinfo: str) -> bool:
    """The file name's series and ComicInfo's name the same thing, allowing
    for the ways a name legitimately differs: a creator or a word more or
    fewer ("Mari Naomi - I Thought YOU Hated ME", "Sabrina the Teenage
    Witch" vs "Sabrina"), or ComicInfo folding the title into the series
    ("The Ride 006 - Mardi Gras 02" vs "The Ride: Mardi Gras")."""
    ci, ours = loose_key(in_comicinfo), loose_key(name.series)
    if not ci or not ours or ci == ours:
        return True
    ci_words, our_words = ([w for w in series_key(text).split() if not w.isdigit()]
                           for text in (in_comicinfo, name.series))  # "Venom 055 - License to Kill"
    if ci_words == our_words:
        return True
    shorter, longer = sorted((ci_words, our_words), key=len)
    if shorter and (longer[:len(shorter)] == shorter or longer[-len(shorter):] == shorter):
        return True
    rest = ci[len(ours):] if ci.startswith(ours) else ""
    return bool(rest) and rest in loose_key(name.title)


def _find_mismatches(report, by_key) -> None:
    for key, siblings in by_key.items():
        # What ComicInfo consistently says differently for a whole series is
        # a convention, not a mistake -- "Pep" for every "Pep Comics" file,
        # Heavy Metal's Number as year+month, cover vs on-sale months -- so
        # only the files that differ from the rest of their series are
        # reported.
        usual = _majority([loose_key(e.row.series) for e in siblings if e.row.comicinfo == "yes" and e.row.series])
        found: dict[str, list[Mismatch]] = defaultdict(list)
        checked: Counter = Counter()
        for entry in siblings:
            row, name = entry.row, entry.name
            if row.comicinfo != "yes":
                continue
            if row.series and name.series:
                checked["Series"] += 1
                if not _series_agree(name, row.series) and loose_key(row.series) != usual:
                    found["Series"].append(Mismatch(row.path, "Series", name.series, row.series))
            if row.number and name.number and not name.is_tpb and not _DATE_LIKE_RE.match(name.number):
                checked["Number"] += 1
                if not numbers_agree(name.number, row.number) and \
                        number_key(row.number) not in re.findall(r"\b\d+\b", entry.row.file.lower().lstrip("0")) and \
                        number_key(row.number) not in {number_key(n) for n in re.findall(r"\b\d+\b", name.title)}:
                    found["Number"].append(Mismatch(row.path, "Number", name.number, row.number))
            volume = entry.key[1]
            if volume.isdigit() and not name.is_tpb and row.volume.isdigit() and int(row.volume) < 1000:
                checked["Volume"] += 1
                if int(row.volume) != int(volume):
                    found["Volume"].append(Mismatch(row.path, "Volume", f"v{volume}", row.volume))
            # A cover date and an on-sale date are a month or two apart, even
            # across New Year: only a bigger gap is a disagreement.
            if name.year and row.year and _year(name.year) and _year(row.year):
                checked["Year"] += 1
                if _months_apart(name, row) > 2 and _year(name.year) != _year(row.year):
                    found["Year"].append(Mismatch(row.path, "Year", name.year, row.year))
            if name.month and row.month and name.month.isdigit() and row.month.isdigit():
                checked["Month"] += 1
                if _year(name.year) == _year(row.year) and _months_apart(name, row) > 2:
                    found["Month"].append(Mismatch(row.path, "Month", name.month, row.month))
        for field_name, mismatches in found.items():
            if checked[field_name] >= MIN_FOLDER_FILES and len(mismatches) > (1 - PATTERN_SHARE) * checked[field_name]:
                continue  # most of the series disagrees the same way: its convention
            report.mismatches.extend(mismatches)
    report.mismatches.sort(key=lambda m: m.path.casefold())


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
