"""
core/gcd_compare.py

Compares a ComicRack library -- converted to GCD's layout by
core/comicrack_import.py -- with the Grand Comics Database dump, and
writes the DIFFERENCE: what the library knows that GCD doesn't. The
plan is to offer that to GCD; until then it also shows where a local
GCD lookup will come up empty.

Output: a SQLite file (redactor_common's SqliteBuilder) with

- diff_series: every library series, matched to a GCD series or "not
  in GCD", with how the match was made;
- diff_issue: every library issue -- matched to its GCD issue, "not in
  the GCD series" (GCD has the series but not this issue), or "series
  not in GCD" -- and, for matched issues, what GCD lacks that the
  library has (credits, characters, summary). Every issue that isn't
  simply matched carries the library's own data (title, credits,
  characters, summary, Comic Vine link), ready to hand over;

plus export_csv() for the same as spreadsheet-friendly CSV files.

Matching a series (GCD has a dozen "Batman"s -- Canadian, Spanish and
Danish reprints -- and names publishers differently: "DC" vs Comic
Vine's "DC Comics"):
1. Same name (compare_name(): punctuation-insensitive, "S.H.I.E.L.D."
   = "SHIELD", a trailing "TPB"/"HC"/"v2" and a leading "The"
   optional) and start year within one. Among those, prefer: same
   publisher (words compared without "Comics", "Publishing", ...), at
   least half the library's issue numbers present in GCD, the same
   language (a library series with none counts as English), the exact
   start year, then more issue numbers in common. Accepted when the
   publisher agrees or the issue numbers do -- or when it's the only
   same-name series of that exact year.
2. Otherwise the same name and publisher with ANY start year, sharing
   at least half the issue numbers: Comic Vine starts a new volume where
   numbering carried on ("Detective Comics (2016)" #934-...), GCD keeps
   those issues in the original series (1937). Several GCD series may
   share the run between them (GCD splits "Doctor Who Magazine" by
   publisher era); its issues are then matched across all of them.
3. Otherwise a GCD series whose name contains all the words, of the
   same publisher and year, sharing at least half the issue numbers.
4. Otherwise "not in GCD". On the real library (28k series) that's
   about one in five; many are genuinely missing (small and European
   publishers, recent collections), but some are naming differences
   no rule catches ("Doctor Strange" vs "Dr. Strange"), so the output
   says "not found", and the matched ones record their method.

GCD's dump has no cover images or per-issue cover flags (only a
series-level has_gallery), so covers aren't compared.
"""

from __future__ import annotations

import csv
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from redactor_common.core.dump_import import ImportCancelled, SqliteBuilder
from redactor_common.core.local_db import LocalDatabase, NameIndex, normalize_words

from core.comicrack_import import (
    COMIC_STORY_TYPE_ID,
    COVER_STORY_TYPE_ID,
    CREDIT_TYPES,
    NO_NUMBER,
    number_key,
)
from core.gcd_local import GcdLocalDatabase, GcdLocalError

MATCHED = "matched"
NOT_IN_GCD_SERIES = "not in GCD series"
SERIES_NOT_IN_GCD = "series not in GCD"
SERIES_NOT_FOUND = "not found"

METHOD_PUBLISHER = "name, year, publisher"
METHOD_ISSUES = "name, year, issue numbers"
METHOD_ONLY_ONE = "name, year (only candidate)"
METHOD_OTHER_YEAR = "name, publisher, issue numbers (other start year)"
METHOD_SPLIT = "name, publisher, issue numbers (split over several GCD series)"
METHOD_WORDS = "all words, publisher, issue numbers"

_PUBLISHER_NOISE = {
    "comics", "comic", "publishing", "publications", "publication", "press", "inc", "ltd", "entertainment",
    "books", "book", "group", "the", "co", "company", "magazines", "magazine", "media", "studios", "studio",
    "llc", "editions", "editora", "editorial", "verlag", "and",
}
_FORMAT_SUFFIX = re.compile(r"\s+(?:tpb|tp|hc|gn|ogn|v\d+|vol \d+)$")
_SPACED_LETTERS = re.compile(r"\b(?:[a-z0-9] )+[a-z0-9]\b")

DIFF_TABLES = {
    "diff_series": [
        "cr_series_id integer primary key", "name text", "year_began integer", "publisher text", "language text",
        "cr_issues integer", "status text", "method text", "gcd_series_id integer", "gcd_name text",
        "gcd_year_began integer", "gcd_publisher text", "gcd_issue_count integer", "issues_not_in_gcd integer",
        "gcd_more_series_ids text",
    ],
    "diff_issue": [
        "cr_issue_id integer primary key", "cr_series_id integer", "series text", "number text", "key_date text",
        "status text", "gcd_issue_id integer", "gcd_lacks text", "title text", "writer text", "penciller text",
        "inker text", "colorist text", "letterer text", "cover_artist text", "editor text", "characters text",
        "genre text", "synopsis text", "page_count integer", "isbn text", "barcode text",
        "comicvine_issue_id integer", "web text",
    ],
}
DIFF_INDEXES = [
    "create index diff_issue_status on diff_issue(status)",
    "create index diff_issue_series on diff_issue(cr_series_id)",
    "create index diff_series_status on diff_series(status)",
]
_DETAIL_COLUMNS = DIFF_TABLES["diff_issue"][8:]
_CREDIT_FIELDS = {1: "writer", 2: "penciller", 3: "inker", 4: "colorist", 5: "letterer"}
assert set(CREDIT_TYPES) >= set(_CREDIT_FIELDS)


def compare_name(name: str) -> str:
    """normalize_words() plus: runs of single letters/digits joined
    ("s h i e l d" -> "shield")."""
    text = normalize_words(name)
    return _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), text)


_BRACKETED = re.compile(r"\s*\[[^\]]*\]")


def name_keys(name: str, gcd: bool = False) -> set[str]:
    """The forms a series name is looked up under: as is, without a
    trailing format word ("TPB", "v2"), and without a leading "the".
    `gcd`: also without GCD's bracketed qualifier ("L'Uomo Ragno
    [Collana Super-Eroi]")."""
    base = compare_name(name)
    keys = {base}
    if gcd and "[" in name:
        keys.add(compare_name(_BRACKETED.sub("", name)))
    stripped = _FORMAT_SUFFIX.sub("", base)
    if stripped:
        keys.add(stripped)
    keys |= {key[4:] for key in list(keys) if key.startswith("the ") and len(key) > 4}
    return keys


def publisher_words(name: Optional[str]) -> frozenset[str]:
    return frozenset(w for w in normalize_words(name or "").split() if w not in _PUBLISHER_NOISE)


_NUMBER_OF_YEAR = re.compile(r"(\d{1,3})\s*/\s*((?:18|19|20)\d\d)")  # GCD: "6/1976"
_YEAR_THEN_NUMBER = re.compile(r"((?:18|19|20)\d\d)[-/ ]?(\d{1,2})")  # library: "1976-06", "197606"
_VOLUME_NUMBER = re.compile(r"v(?:ol\.?\s*)?(\d+)", re.IGNORECASE)  # "v01" -> "1"


def issue_forms(number: str) -> set[str]:
    """Every form an issue number is compared under, so the two sides'
    conventions meet: number_key() ("#007" = "7"); a one-shot's "[nn]" =
    Comic Vine's #1; GCD's bracketed and alternate numbers ("[1]",
    "2 (14)" -> "2" and "14"); a yearly weekly's "6/1976" (GCD) =
    "1976-06" / "197606" (library) -> "1976:6"; "v01" -> "1"."""
    text = (number or "").strip()
    forms = {number_key(text)}
    match = _NUMBER_OF_YEAR.fullmatch(text)
    if match:
        forms.add(f"{match.group(2)}:{int(match.group(1))}")
    match = _YEAR_THEN_NUMBER.fullmatch(text)
    if match and int(match.group(2)) > 0:
        forms.add(f"{match.group(1)}:{int(match.group(2))}")
    match = _VOLUME_NUMBER.fullmatch(text)
    if match:
        forms.add(number_key(match.group(1)))
    if "[" in text or "(" in text:
        forms |= {number_key(part) for part in re.findall(r"[^\[\]()]+", text) if part.strip()}
    if forms & {"1", NO_NUMBER}:
        forms |= {"1", NO_NUMBER}
    return forms


@dataclass
class _GcdSeries:
    id: int
    name: str
    year: Optional[int]
    publisher: str
    publisher_words: frozenset
    language: str
    issue_count: int


@dataclass
class _CrSeries:
    id: int
    name: str
    year: Optional[int]
    publisher: str
    language: str
    numbers: list = field(default_factory=list)  # (issue_forms(), year) of each issue


@dataclass
class CompareSummary:
    series: int = 0
    series_matched: Counter = field(default_factory=Counter)  # method -> count
    series_not_found: int = 0
    issues: int = 0
    issues_matched: int = 0
    issues_not_in_gcd_series: int = 0
    issues_in_unfound_series: int = 0
    gaps: Counter = field(default_factory=Counter)  # "credits"/"characters"/"summary" -> issues

    def describe(self) -> str:
        matched = sum(self.series_matched.values())
        lines = [
            f"{self.series:,} library series: {matched:,} found in GCD, {self.series_not_found:,} not found "
            "(missing from GCD, or named differently there).",
            f"{self.issues:,} issues: {self.issues_matched:,} matched, {self.issues_not_in_gcd_series:,} missing "
            f"from a GCD series that exists, {self.issues_in_unfound_series:,} in series not found.",
        ]
        if self.gaps:
            gaps = ", ".join(f"{what} for {count:,}" for what, count in self.gaps.most_common())
            lines.append(f"Matched issues where GCD lacks what the library has: {gaps}.")
        return "\n".join(lines)


class _Stages:
    """Progress over named stages of fixed weight, with cancellation."""

    def __init__(self, progress, cancelled, weights: dict[str, float]):
        self._progress, self._cancelled = progress, cancelled
        total = sum(weights.values())
        self._start, position = {}, 0.0
        for name, weight in weights.items():
            self._start[name] = (position / total, weight / total)
            position += weight

    def at(self, stage: str, fraction: float = 0.0) -> None:
        if self._cancelled and self._cancelled():
            raise ImportCancelled()
        if self._progress:
            start, width = self._start[stage]
            self._progress(start + width * min(max(fraction, 0.0), 1.0))


def _chunks(ids: list, size: int = 5000):
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def _in_list(chunk) -> str:
    return ",".join("?" * len(chunk))


def compare_with_gcd(
    library_path: str,
    gcd_path: str,
    dest: str,
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> CompareSummary:
    """Compares the converted ComicRack library at `library_path` with the
    GCD dump at `gcd_path` and writes the difference to `dest`."""
    stages = _Stages(progress, cancelled, {
        "gcd series": 2, "library": 1, "gcd issues": 5, "match": 1, "gaps": 6, "details": 4, "write": 2,
    })
    library = GcdLocalDatabase(library_path)
    gcd = GcdLocalDatabase(gcd_path)
    try:
        if not library.is_comicrack:
            raise GcdLocalError("The first file must be a converted ComicRack library.")
        if gcd.is_comicrack:
            raise GcdLocalError("The second file must be the GCD dump, not a converted ComicRack library.")
        return _compare(library, gcd, dest, stages)
    finally:
        library.close()
        gcd.close()


def _compare(library: LocalDatabase, gcd: LocalDatabase, dest: str, stages: _Stages) -> CompareSummary:
    summary = CompareSummary()

    stages.at("gcd series")
    gcd_series: dict[int, _GcdSeries] = {}
    by_name: dict[str, list[_GcdSeries]] = defaultdict(list)
    for sid, name, year, publisher, language, count in gcd.query(
        """select s.id, s.name, s.year_began, p.name, l.code, s.issue_count
           from gcd_series s left join gcd_publisher p on p.id = s.publisher_id
           left join stddata_language l on l.id = s.language_id where s.deleted = 0"""
    ):
        entry = _GcdSeries(sid, name or "", year, publisher or "", publisher_words(publisher), language or "", count or 0)
        gcd_series[sid] = entry
        for key in name_keys(entry.name, gcd=True):
            by_name[key].append(entry)

    stages.at("library")
    cr_series: dict[int, _CrSeries] = {}
    for sid, name, year, publisher, language in library.query(
        """select s.id, s.name, s.year_began, p.name, l.code from gcd_series s
           left join gcd_publisher p on p.id = s.publisher_id
           left join stddata_language l on l.id = s.language_id"""
    ):
        cr_series[sid] = _CrSeries(sid, name or "", year, publisher or "", language or "")
    cr_issues = library.query(
        """select i.id, i.series_id, i.number, i.key_date, i.page_count, i.isbn, i.barcode, c.web, c.comicvine_issue_id
           from gcd_issue i left join cr_issue c on c.issue_id = i.id order by i.id"""
    )
    for row in cr_issues:
        cr_series[row[1]].numbers.append((issue_forms(row[2]), _year_of(row[3])))
    summary.series, summary.issues = len(cr_series), len(cr_issues)

    def close_year(candidate: _GcdSeries, series: _CrSeries) -> bool:
        return series.year is None or candidate.year is None or abs(candidate.year - series.year) <= 1

    # series -> issue number form -> (issue id, issue year)
    gcd_issues: dict[int, dict[str, tuple[int, Optional[int]]]] = defaultdict(dict)
    loaded: set[int] = set()

    def load_issues(series_ids: set[int], stage_from: float, stage_to: float) -> None:
        """Each candidate series' non-variant issues, under every form of
        their numbers (see issue_forms)."""
        wanted = sorted(series_ids - loaded)
        loaded.update(wanted)
        for n, chunk in enumerate(_chunks(wanted)):
            stages.at("gcd issues", stage_from + (stage_to - stage_from) * n * 5000 / max(len(wanted), 1))
            for issue_id, series_id, number, key_date in gcd.query(
                f"""select id, series_id, number, key_date from gcd_issue where series_id in ({_in_list(chunk)})
                    and +deleted = 0 and variant_of_id is null order by id""",
                chunk,
            ):
                for form in issue_forms(number):
                    gcd_issues[series_id].setdefault(form, (issue_id, _year_of(key_date)))

    # Tiers 1 and 2: the same name, a start year within one; the same
    # name and publisher, any start year.
    same_name: dict[int, list[_GcdSeries]] = {}
    other_year: dict[int, list[_GcdSeries]] = {}
    for series in cr_series.values():
        found = {c.id: c for key in name_keys(series.name) for c in by_name.get(key, ())}
        mine = publisher_words(series.publisher)
        same_name[series.id] = [c for c in found.values() if close_year(c, series)]
        other_year[series.id] = [c for c in found.values() if mine & c.publisher_words]
    load_issues({c.id for tier in (same_name, other_year) for found in tier.values() for c in found}, 0.0, 0.8)
    match: dict[int, tuple[list[_GcdSeries], str]] = {}
    for series in cr_series.values():
        chosen = _choose_same_name(series, same_name[series.id], gcd_issues) or _choose_other_year(
            series, other_year[series.id], gcd_issues
        )
        if chosen:
            match[series.id] = chosen

    # Tier 3, for the rest: all the words, same publisher and year.
    word_index = NameIndex(((s.id, s.name) for s in gcd_series.values()), compare_name)
    words_found: dict[int, list[_GcdSeries]] = {}
    try:
        for series in cr_series.values():
            if series.id in match:
                continue
            words = _FORMAT_SUFFIX.sub("", compare_name(series.name))
            mine = publisher_words(series.publisher)
            words_found[series.id] = [
                gcd_series[i] for i in word_index.match(words)
                if close_year(gcd_series[i], series) and mine & gcd_series[i].publisher_words
            ]
    finally:
        word_index.close()
    load_issues({c.id for found in words_found.values() for c in found}, 0.8, 1.0)
    stages.at("match")
    for series_id, found in words_found.items():
        best = _best_by_overlap(cr_series[series_id], found, gcd_issues)
        if best:
            match[series_id] = ([best], METHOD_WORDS)
    for series in cr_series.values():
        if series.id in match:
            summary.series_matched[match[series.id][1]] += 1
        else:
            summary.series_not_found += 1

    # Issue-level status.
    status: dict[int, tuple[str, Optional[int]]] = {}
    not_in_series: Counter = Counter()
    for issue_id, series_id, number, *_rest in cr_issues:
        found = match.get(series_id)
        if found is None:
            status[issue_id] = (SERIES_NOT_IN_GCD, None)
            summary.issues_in_unfound_series += 1
            continue
        # The plain number first, the looser forms only if it's absent;
        # the main GCD series first (a run split in GCD has several).
        forms = [number_key(number), *sorted(issue_forms(number) - {number_key(number)})]
        gcd_id = next((gcd_issues[g.id][f][0] for g in found[0] for f in forms if f in gcd_issues[g.id]), None)
        if gcd_id is None:
            status[issue_id] = (NOT_IN_GCD_SERIES, None)
            not_in_series[series_id] += 1
            summary.issues_not_in_gcd_series += 1
        else:
            status[issue_id] = (MATCHED, gcd_id)
            summary.issues_matched += 1

    # What the library has per issue, and what GCD has for the matched ones.
    has_library = _contents(library, [i for i in status], lambda f: stages.at("gaps", f / 3))
    matched_gcd = [gcd_id for state, gcd_id in status.values() if state == MATCHED]
    has_gcd = _contents(gcd, matched_gcd, lambda f: stages.at("gaps", (1 + 2 * f) / 3))
    lacks: dict[int, list[str]] = {}
    for issue_id, (state, gcd_id) in status.items():
        if state != MATCHED:
            continue
        missing = [what for what in ("credits", "characters", "summary")
                   if what in has_library.get(issue_id, ()) and what not in has_gcd.get(gcd_id, ())]
        if missing:
            lacks[issue_id] = missing
            summary.gaps.update(missing)

    need_details = [i for i, (state, _g) in status.items() if state != MATCHED or i in lacks]
    details = _library_details(library, need_details, lambda f: stages.at("details", f))

    stages.at("write")
    with SqliteBuilder(dest, DIFF_TABLES, DIFF_INDEXES) as out:
        for series in cr_series.values():
            found = match.get(series.id)
            gcd_list, method = found if found else ([], "")
            gcd_entry = gcd_list[0] if gcd_list else None
            out.add("diff_series", (
                series.id, series.name, series.year, series.publisher, series.language, len(series.numbers),
                MATCHED if found else SERIES_NOT_FOUND, method,
                gcd_entry.id if gcd_entry else None, gcd_entry.name if gcd_entry else None,
                gcd_entry.year if gcd_entry else None, gcd_entry.publisher if gcd_entry else None,
                gcd_entry.issue_count if gcd_entry else None,
                not_in_series[series.id] if found else len(series.numbers),
                ", ".join(str(g.id) for g in gcd_list[1:]),
            ))
        for n, (issue_id, series_id, number, key_date, pages, isbn, barcode, web, cv_id) in enumerate(cr_issues):
            if n % 20000 == 0:
                stages.at("write", n / max(len(cr_issues), 1))
            state, gcd_id = status[issue_id]
            detail = details.get(issue_id)
            row = (
                issue_id, series_id, cr_series[series_id].name, number, key_date, state, gcd_id,
                ", ".join(lacks.get(issue_id, ())),
            )
            if detail is None:
                row += (None,) * len(_DETAIL_COLUMNS)
            else:
                row += tuple(detail.get(c.split()[0], "") for c in _DETAIL_COLUMNS[:11]) + (
                    pages, isbn, barcode, cv_id, web,
                )
            out.add("diff_issue", row)
        out.finish({
            "source": "ComicRack vs GCD", "library": os.path.basename(library.path),
            "gcd": os.path.basename(gcd.path), "library_built": library.import_info.get("built", ""),
        })
    return summary


def _year_of(key_date: Optional[str]) -> Optional[int]:
    text = (key_date or "")[:4]
    return int(text) if text.isdigit() and text != "0000" else None


def _covered(series: _CrSeries, gcd_series_ids, gcd_issues) -> set[int]:
    """Which of the library series' issues (by position) the given GCD
    series hold: the same number, published within a year of each other
    (when both dates are known) -- so a relaunch's #1-5 don't "match"
    the 1940 series' #1-5."""
    tables = [gcd_issues.get(i, {}) for i in gcd_series_ids]
    covered = set()
    for position, (forms, year) in enumerate(series.numbers):
        for table in tables:
            hits = [table[f][1] for f in forms if f in table]
            if any(year is None or found is None or abs(found - year) <= 1 for found in hits):
                covered.add(position)
                break
    return covered


def _overlap(series: _CrSeries, gcd_series_ids, gcd_issues) -> float:
    """The share of the library series' issues found in the given GCD series."""
    if not series.numbers:
        return 0.0
    return len(_covered(series, gcd_series_ids, gcd_issues)) / len(series.numbers)


def _choose_same_name(series: _CrSeries, found: list[_GcdSeries], gcd_issues):
    """Tier 1: same name, start year within one."""
    if not found:
        return None
    mine = publisher_words(series.publisher)
    language = series.language or "en"

    def score(candidate: _GcdSeries):
        overlap = _overlap(series, [candidate.id], gcd_issues)
        return (
            bool(mine & candidate.publisher_words), overlap >= 0.5, candidate.language == language,
            candidate.year == series.year, overlap, candidate.issue_count,
        )

    best_score, best = max(((score(c), c) for c in found), key=lambda pair: pair[0])
    if best_score[0]:
        return [best], METHOD_PUBLISHER
    if best_score[1]:
        return [best], METHOD_ISSUES
    same_year = [c for c in found if c.year == series.year]
    if series.year is not None and len(same_year) == 1 and same_year[0] is best:
        return [best], METHOD_ONLY_ONE
    return None


def _choose_other_year(series: _CrSeries, found: list[_GcdSeries], gcd_issues):
    """Tier 2: same name and publisher, any start year -- one GCD series
    holding half the issues, or a run GCD splits over several series
    (by era or publisher) that together do."""
    if not found or not series.numbers:
        return None
    # Greedy cover: the series holding most of the issues, then each
    # further one only while it adds issues the others don't hold.
    covers = {c.id: _covered(series, [c.id], gcd_issues) for c in found}
    chosen, covered = [], set()
    for candidate in sorted(found, key=lambda c: -len(covers[c.id])):
        extra = covers[candidate.id] - covered
        if not extra:
            continue
        chosen.append(candidate)
        covered |= extra
    if len(covered) / len(series.numbers) < 0.5:
        return None
    return chosen, (METHOD_OTHER_YEAR if len(chosen) == 1 else METHOD_SPLIT)


def _best_by_overlap(series: _CrSeries, found: list[_GcdSeries], gcd_issues) -> Optional[_GcdSeries]:
    """The candidate holding the most of the series' issues, if at least half."""
    if not found:
        return None
    overlap, best = max(((_overlap(series, [c.id], gcd_issues), c) for c in found), key=lambda pair: pair[0])
    return best if overlap >= 0.5 else None


def _contents(db: LocalDatabase, issue_ids: list[int], report) -> dict[int, set[str]]:
    """Per issue: which of "credits", "characters", "summary" its comic
    stories have -- as rows (credit/character tables) or as GCD's older
    free-text fields."""
    has: dict[int, set[str]] = defaultdict(set)
    story_issue: dict[int, int] = {}
    ids = sorted(issue_ids)
    for n, chunk in enumerate(_chunks(ids)):
        report(n * 5000 / max(len(ids), 1) / 2)
        for story_id, issue_id, synopsis, credit_text, characters in db.query(
            f"""select id, issue_id, coalesce(synopsis, '') <> '',
                       coalesce(script, '') <> '' or coalesce(pencils, '') <> '' or coalesce(inks, '') <> '',
                       coalesce(characters, '') <> ''
                from gcd_story where issue_id in ({_in_list(chunk)}) and +type_id = ? and +deleted = 0""",
            [*chunk, COMIC_STORY_TYPE_ID],
        ):
            story_issue[story_id] = issue_id
            if synopsis:
                has[issue_id].add("summary")
            if credit_text:
                has[issue_id].add("credits")
            if characters:
                has[issue_id].add("characters")
    stories = sorted(story_issue)
    for n, chunk in enumerate(_chunks(stories)):
        report(0.5 + n * 5000 / max(len(stories), 1) / 2)
        for table, what in (("gcd_story_credit", "credits"), ("gcd_story_character", "characters")):
            for (story_id,) in db.query(
                f"select distinct story_id from {table} where story_id in ({_in_list(chunk)}) and +deleted = 0", chunk
            ):
                has[story_issue[story_id]].add(what)
    return has


def _library_details(library: LocalDatabase, issue_ids: list[int], report) -> dict[int, dict[str, str]]:
    """The library's own data for the issues being handed over, flattened
    to text columns as in ComicInfo."""
    details: dict[int, dict[str, list | str]] = {}
    story_issue: dict[int, tuple[int, int]] = {}
    ids = sorted(issue_ids)
    for issue_id in ids:
        details[issue_id] = defaultdict(list)
    for n, chunk in enumerate(_chunks(ids)):
        report(n * 5000 / max(len(ids), 1) / 2)
        for story_id, issue_id, type_id, title, genre, synopsis in library.query(
            f"select id, issue_id, type_id, title, genre, synopsis from gcd_story where issue_id in ({_in_list(chunk)})",
            chunk,
        ):
            story_issue[story_id] = (issue_id, type_id)
            if type_id == COMIC_STORY_TYPE_ID:
                d = details[issue_id]
                d["title"], d["genre"], d["synopsis"] = title or "", (genre or "").replace("; ", ", "), synopsis or ""
        for issue_id, name in library.query(
            f"""select ic.issue_id, c.name from gcd_issue_credit ic join gcd_creator_name_detail c on c.id = ic.creator_id
                where ic.issue_id in ({_in_list(chunk)}) order by ic.id""",
            chunk,
        ):
            details[issue_id]["editor"].append(name)
    stories = sorted(story_issue)
    for n, chunk in enumerate(_chunks(stories)):
        report(0.5 + n * 5000 / max(len(stories), 1) / 2)
        for story_id, type_id, name in library.query(
            f"""select sc.story_id, sc.credit_type_id, c.name from gcd_story_credit sc
                join gcd_creator_name_detail c on c.id = sc.creator_id
                where sc.story_id in ({_in_list(chunk)}) order by sc.id""",
            chunk,
        ):
            issue_id, story_type = story_issue[story_id]
            column = "cover_artist" if story_type == COVER_STORY_TYPE_ID else _CREDIT_FIELDS.get(type_id)
            if column:
                details[issue_id][column].append(name)
        for story_id, name in library.query(
            f"""select sc.story_id, c.name from gcd_story_character sc
                join gcd_character_name_detail c on c.id = sc.character_id
                where sc.story_id in ({_in_list(chunk)}) order by sc.id""",
            chunk,
        ):
            details[story_issue[story_id][0]]["characters"].append(name)
    return {
        issue_id: {k: (", ".join(dict.fromkeys(v)) if isinstance(v, list) else v) for k, v in d.items()}
        for issue_id, d in details.items()
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

CSV_FILES = {
    "series_not_in_gcd.csv": (
        "select name, year_began, publisher, language, cr_issues from diff_series where status = ? order by name, year_began",
        (SERIES_NOT_FOUND,),
    ),
    "issues_not_in_gcd.csv": (
        f"""select i.status, i.series, s.year_began, s.publisher, s.gcd_series_id, i.number, i.key_date,
                   {", ".join("i." + c.split()[0] for c in _DETAIL_COLUMNS)}
            from diff_issue i join diff_series s on s.cr_series_id = i.cr_series_id
            where i.status <> ? order by i.status, i.series, s.year_began, i.number""",
        (MATCHED,),
    ),
    "gcd_gaps.csv": (
        f"""select i.gcd_issue_id, 'https://www.comics.org/issue/' || i.gcd_issue_id || '/', i.gcd_lacks, i.series,
                   i.number, i.key_date, {", ".join("i." + c.split()[0] for c in _DETAIL_COLUMNS)}
            from diff_issue i where i.status = ? and i.gcd_lacks <> '' order by i.series, i.number""",
        (MATCHED,),
    ),
}


def export_csv(diff_path: str, folder: str) -> list[str]:
    """Writes CSV_FILES from a comparison database into `folder` (UTF-8
    with a BOM, so Excel shows accented names right). Returns the paths."""
    os.makedirs(folder, exist_ok=True)
    db = LocalDatabase(diff_path, set(DIFF_TABLES), kind="a ComicRack/GCD comparison")
    written = []
    try:
        for name, (sql, params) in CSV_FILES.items():
            cursor = db._con.execute(sql, params)
            path = os.path.join(folder, name)
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow([d[0] for d in cursor.description])
                writer.writerows(cursor)
            written.append(path)
    finally:
        db.close()
    return written
