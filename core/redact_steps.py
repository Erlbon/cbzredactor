"""
core/redact_steps.py

The steps behind the "Redact" button (redactor_common's pipeline engine,
core/pipeline.py): run a recipe on each loaded/selected comic with no
operator input and leave the corrected file IN PLACE, the original in the
Recycle Bin. Qt-free; gui/main_window.py wires it to the menu/toolbar.

Every step wraps code the app already has (convert_to_cbz, clean_contents,
the credit-page scan, resize_images, Validate / Fix, the lookups, the
rename pattern) without the dialogs. How one file flows:

  1. CbzCtx reads the file into a WORKING book (metadata edits happen in
     memory; nothing on disk changes yet). A step that must rewrite the
     archive first makes a scratch copy beside the original and works on
     that (CbzCtx.ensure_scratch); a CBR/CB7/CBT is converted straight
     into a scratch .cbz by the convert step.
  2. Steps run. Measurements and fixes are APPLIED; guesses (filename
     tags, online/local lookup, credit pages) come back as SUGGESTIONs
     with a confidence, which the engine applies only at or above the
     recipe's threshold and lists as "Needs review" otherwise.
  3. save_stage() is the engine's finalize hook. Only if something
     changed it writes the archive to a new temp file in the original's
     folder (CbzBook.save(output_path=...) -- nothing is replaced yet),
     verifies it (reopens, every entry's CRC, page count, ComicInfo.xml
     present) and commit_in_place()s it, the original going to the
     Recycle Bin. A converted CBR/CB7/CBT is moved to its new .cbz name
     and the foreign original trashed instead.
  4. The rename and move-into-folders steps are pinned last
     (position="last") but only PLAN the new name/location: the engine
     runs finalize after every step, so the save stage performs them on
     the finished file. The live row is then reloaded from disk.

A file with unsaved edits or a load error is SKIPPED by the internal
guard step (never overwrite what is only in memory). Steps that cannot do
their job (offline, nothing configured) answer NOTHING with a note.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Callable

from redactor_common.core.filename_parser import parse_filename as parse_pattern_filename
from redactor_common.core.move_plan import execute_move, plan_moves
from redactor_common.core.os_utils import rename_no_clobber
from redactor_common.core.pipeline import (
    CommitError,
    OptionSpec,
    Recipe,
    Step,
    StepResult,
    commit_in_place,
)
from redactor_common.core.rename_pattern import render_filename, unique_path, zero_pad_numeric_value
from redactor_common.core.trash import move_to_trash

from core.cbz_file import CbzBook, CbzError
from core.comicinfo import TAG_TO_ATTR, serialize_comicinfo_xml
from core.comicinfo_check import check_metadata
from core.comicvine_lookup import (
    ComicVineLookupError,
    fetch_issue_details,
    fetch_publisher,
    search_comicvine,
)
from core.credit_pages import KnownCreditPages, credit_matches, hamming, scan_book
from core.foreign_archive_convert import ForeignArchiveConversionError, convert_to_cbz
from core.gcd_local import GcdLocalError, normalize_name, open_database
from core.image_resize import DEFAULT_MAX_WIDTH, target_size
from core.page_dimensions import SIZE_LOW, scan_page_sizes
from core.scan_quality_tag import LOW_RES_TAG, add_tag, has_tag, remove_tag
from core.scene_name import parse_filename as parse_scene_name, proposed_fields

# Same field sets as the Rename/Export and Parse Filename dialogs (gui/main_window.py
# FILENAME_PLACEHOLDERS / NUMERIC_FILENAME_FIELDS; PageCount is never hand-edited).
FILENAME_FIELD_KEYS = frozenset(attr for attr in TAG_TO_ATTR.values() if attr != "page_count")
NUMERIC_FILENAME_FIELDS = frozenset(
    {"number", "count", "volume", "alternate_number", "alternate_count", "year", "month", "day", "community_rating"}
)

# Fields that, all present, mean there is nothing worth a lookup round trip.
LOOKUP_TRIGGER_FIELDS = ("series", "number", "year", "publisher", "writer", "summary")

# Filename parsing confidences: the pattern was named explicitly in the recipe / picked
# from the pattern history / the scene-name parser (no pattern at all) was used.
PATTERN_CONFIDENCE = 0.95
HISTORY_PATTERN_CONFIDENCE = 0.85
SCENE_CONFIDENCE = 0.7

# Lookup confidences by match quality.
EXACT_CONFIDENCE = 0.95  # exact series + number + year
SERIES_NUMBER_CONFIDENCE = 0.85  # exact series + number
NUMBER_ONLY_CONFIDENCE = 0.6  # number agrees, series only loosely
WEAK_CONFIDENCE = 0.4
EARLY_STOP_CONFIDENCE = 0.9  # a local hit this good spares the next source


# --- run environment -------------------------------------------------------


@dataclass
class RedactEnv:
    """What the steps of one Redact run share: the settings the app read
    for it (so this module stays Qt-free), the rename log, the trash
    function (injectable for tests) and the lookup hooks."""

    rename_log: object | None = None  # anything with .record(label, [(old, new)], ...)
    trash: Callable[[str], None] | None = None  # None: the Recycle Bin (move_to_trash)
    pattern_history: list[str] = field(default_factory=list)  # newest first (Rename / Parse Filename)
    ascii_filenames: bool = False
    zero_pad: tuple[bool, int] = (False, 2)  # the Rename/Export dialog's Number padding
    library_root: str = ""  # the dialog's "Move into folders" root
    comicvine_key: str = ""
    local_databases: list[str] = field(default_factory=list)  # GCD dump, ComicRack library; in this order
    known_credits: KnownCreditPages | None = None
    open_database: Callable[[str], object] = open_database
    comicvine_down: bool = False  # set by the first network failure, skips the rest of the run

    def begin(self) -> None:
        """Call before each run."""
        self.comicvine_down = False

    def _newest(self, with_separator: bool) -> str:
        for pattern in self.pattern_history:
            if pattern.strip() and (("/" in pattern or "\\" in pattern) == with_separator):
                return pattern
        return ""

    def rename_pattern(self) -> str:
        """The newest saved pattern for a plain rename (no folder separator)."""
        return self._newest(False)

    def move_pattern(self) -> str:
        """The newest saved pattern with a "/" -- one meant for Move into folders."""
        return self._newest(True)


# --- per-file context --------------------------------------------------------


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _side_path(original: str, tag: str) -> str:
    """`<stem>.<tag>.cbz` beside the original (numbered if taken). Always
    .cbz so CbzBook treats it as a real CBZ."""
    stem = os.path.splitext(original)[0]
    candidate = f"{stem}.{tag}.cbz"
    n = 2
    while os.path.lexists(candidate):
        candidate = f"{stem}.{tag}{n}.cbz"
        n += 1
    return candidate


class CbzCtx:
    """make_context() result for one file. `skip_reason` is set for a file
    that must not be touched (load error, unsaved edits); GuardStep turns
    it into StepResult.skipped. `work` is None until a CBR/CB7/CBT has been
    converted (or forever, if the convert step is off)."""

    def __init__(self, book: CbzBook, env: RedactEnv):
        self.book = book
        self.env = env
        self.step_options: dict = {}  # set by the engine; read through Step.options_for(ctx)
        self.original = os.path.realpath(book.path)  # work through a symlink, don't replace it
        self.current_path = self.original
        self.target_path = self.original  # where the saved file goes (a converted CBR: its .cbz name)
        self.work: CbzBook | None = None
        self.baseline = b""
        self.skip_reason = ""
        self.scratch = ""
        self.converted = False
        self.structure_changed = False  # an archive rewrite happened (cleanup, pages, resize)
        self.rename_to = ""
        self.move = None  # a move_plan.PlannedMove
        self.temps: list[str] = []
        self.path_changed = False
        if book.load_error:
            self.skip_reason = f"the file could not be read ({book.load_error})"
        elif book.dirty:
            self.skip_reason = "it has unsaved edits (save or undo them first)"
        elif not os.path.isfile(self.original):
            self.skip_reason = "the file is missing"
        elif not book.needs_conversion:
            self.work = CbzBook(self.original)
            if self.work.load_error:
                self.skip_reason = f"the file could not be read ({self.work.load_error})"
                self.work = None
            else:
                self.baseline = serialize_comicinfo_xml(self.work.metadata)

    # -- state ----------------------------------------------------------------

    @property
    def metadata_changed(self) -> bool:
        return self.work is not None and serialize_comicinfo_xml(self.work.metadata) != self.baseline

    @property
    def changed(self) -> bool:
        return self.converted or self.structure_changed or self.metadata_changed

    def final_path(self) -> str:
        """Where the file will be once saved and renamed (the move is planned from here)."""
        return self.rename_to or self.target_path

    def ensure_scratch(self) -> None:
        """Steps that rewrite the archive work on a copy beside the original."""
        if self.scratch:
            return
        scratch = _side_path(self.original, "redact-work")
        self.temps.append(scratch)
        shutil.copy2(self.original, scratch)
        self.scratch = scratch
        self.work.path = scratch

    def need_work(self) -> StepResult | None:
        """NOTHING-with-a-note for a file that is still a CBR/CB7/CBT."""
        if self.work is None:
            return StepResult.nothing("needs converting to CBZ first (the Convert to CBZ step is off)")
        return None

    def values(self) -> dict[str, str]:
        """Placeholder values for the rename/move patterns, padded like the dialog does."""
        values = {attr: getattr(self.work.metadata, attr, "") or "" for attr in FILENAME_FIELD_KEYS}
        enabled, width = self.env.zero_pad
        if enabled and values.get("number"):
            values["number"] = zero_pad_numeric_value(values["number"], width)
        if values.get("month"):
            values["month"] = zero_pad_numeric_value(values["month"], 2)
        return values

    def close(self) -> None:
        for path in self.temps:
            try:
                os.remove(path)
            except OSError:
                pass


def _fill_empty(ctx: CbzCtx, fields: dict[str, str]) -> dict[str, str]:
    """Sets the fields the metadata has EMPTY; returns what was set."""
    meta = ctx.work.metadata
    filled = {}
    for attr, value in fields.items():
        if attr in FILENAME_FIELD_KEYS and value and not (getattr(meta, attr, "") or "").strip():
            setattr(meta, attr, value)
            filled[attr] = value
    return filled


def _empty_fields(ctx: CbzCtx, fields: dict[str, str]) -> dict[str, str]:
    meta = ctx.work.metadata
    return {
        a: v for a, v in fields.items()
        if a in FILENAME_FIELD_KEYS and v and not (getattr(meta, a, "") or "").strip()
    }


def _short(value: str, limit: int = 40) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


@dataclass
class FieldFill:
    """A suggestion's value: the empty fields it would fill, and where it came from."""

    fields: dict[str, str]
    source: str

    def __str__(self) -> str:
        shown = ", ".join(f"{k}={_short(v)}" for k, v in list(self.fields.items())[:6])
        more = f" +{len(self.fields) - 6} more" if len(self.fields) > 6 else ""
        return f"{self.source}: {shown}{more}"


# --- steps -------------------------------------------------------------------


class GuardStep(Step):
    """Internal, always first (see recipe_for_run); not offered in the
    recipe editor. Skips a file that must not be touched."""

    key = "guard"
    label = "Check the file"

    def run(self, ctx: CbzCtx) -> StepResult:
        if ctx.skip_reason:
            return StepResult.skipped(ctx.skip_reason)
        return StepResult.nothing()


class ConvertStep(Step):
    key = "convert_to_cbz"
    label = "Convert CBR/CB7/CBT to CBZ"
    description = (
        "Converts a CBR, CB7 or CBT (or a file whose extension doesn't match what it really is) to a real "
        "CBZ, checked before anything else happens. The finished file is saved under the .cbz name and the "
        "foreign original goes to the Recycle Bin. Needs rarfile + an unrar tool for CBR and py7zr for CB7."
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if ctx.work is not None:
            return StepResult.nothing()
        target = os.path.splitext(ctx.original)[0] + ".cbz"
        if not _same_path(target, ctx.original) and os.path.lexists(target):
            return StepResult.failed(f"{os.path.basename(target)} already exists -- not overwriting it")
        scratch = _side_path(ctx.original, "redact-work")
        ctx.temps.append(scratch)
        try:
            convert_to_cbz(ctx.original, output_path=scratch)
        except ForeignArchiveConversionError as exc:
            return StepResult.failed(str(exc))
        work = CbzBook(scratch)
        if work.load_error:
            return StepResult.failed(f"the converted file could not be read ({work.load_error})")
        ctx.work, ctx.scratch, ctx.target_path, ctx.converted = work, scratch, target, True
        ctx.baseline = serialize_comicinfo_xml(work.metadata)
        ext = os.path.splitext(ctx.original)[1].lower().lstrip(".") or "archive"
        return StepResult.applied(f"converted {ext.upper()} to CBZ, {work.actual_page_count} page(s)")


class CleanContentsStep(Step):
    key = "clean_contents"
    label = "Clean up archive contents"
    description = (
        "Removes junk inside the archive (Thumbs.db, desktop.ini, .DS_Store, __MACOSX, release-group .nfo/.sfv/"
        ".url/.txt files) and, as Operations > Clean Up Archive Contents does, gives the pages plain numbered "
        "names out of any folders. Page bytes, order and ComicInfo.xml content are unchanged."
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        if not ctx.work.cleanup_plan().needed:
            return StepResult.nothing()
        ctx.ensure_scratch()
        plan = ctx.work.clean_contents()
        ctx.structure_changed = True
        return StepResult.applied(plan.summary())


@dataclass
class CreditPageFinding:
    """A suggestion's value: the pages that match a learned credit page."""

    names: list[str]

    def __str__(self) -> str:
        return "remove " + ", ".join(self.names[:4]) + (f" +{len(self.names) - 4} more" if len(self.names) > 4 else "")


class RemoveCreditPagesStep(Step):
    key = "remove_credit_pages"
    label = "Remove known credit pages"
    default_enabled = False  # destructive
    description = (
        "Finds pages that match a scanner credit page you taught the app (Settings > Known Credit Pages / right-click "
        "> Credit Pages...). Removing a page can't be undone except from the Recycle Bin copy, so it is only a "
        "suggestion: applied when the page fingerprint matches at or above the confidence threshold (an identical "
        "page is 99%, each step of difference costs 2%), otherwise listed under Needs review. Off by default."
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        known = ctx.env.known_credits
        if known is None or not known.pages:
            return StepResult.nothing("no credit pages have been learned yet")
        work = ctx.work
        found = credit_matches(scan_book(work.path, list(work.page_names)), known)
        if not found:
            return StepResult.nothing()
        if len(found) >= work.actual_page_count:
            return StepResult.nothing("every page looks like a credit page -- left alone")
        closest = max(min(hamming(p.hash, c.hash) for p in known.pages) for c in found)
        confidence = 0.99 - 0.02 * closest
        return StepResult.suggestion(
            CreditPageFinding([c.name for c in found]), confidence,
            f"{len(found)} page(s) match a learned credit page (least similar differs by {closest})",
        )

    def apply_suggestion(self, ctx: CbzCtx, result: StepResult) -> str:
        ctx.ensure_scratch()
        removed = ctx.work.remove_pages(list(result.value.names))
        ctx.structure_changed = True
        return f"removed {removed} credit page(s): {', '.join(result.value.names[:4])}"


class ResizeImagesStep(Step):
    key = "resize_images"
    label = "Resize oversized page images"
    default_enabled = False  # lossy
    description = (
        "Shrinks page images wider than the limit (double-page spreads get twice the width), as Operations > "
        "Resize Images does. This re-encodes the pixels: the original is only recoverable from the Recycle "
        "Bin. A book with no oversized page is left untouched. Off by default."
    )
    options = (
        OptionSpec("max_width", "Max page width (px)", "int", DEFAULT_MAX_WIDTH, 200, 10000),
        OptionSpec("max_height", "Max page height (px, 0 = no limit)", "int", 0, 0, 20000),
        OptionSpec("jpeg_quality", "JPEG / WebP quality", "int", 90, 30, 100),
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        opts = self.options_for(ctx)
        work = ctx.work
        stats = scan_page_sizes(work.path, list(work.page_names))
        if not any(
            target_size(w, h, opts["max_width"], opts["max_height"] or None) != (w, h) for w, h in stats.sizes
        ):
            return StepResult.nothing()
        ctx.ensure_scratch()
        before = os.path.getsize(work.path)
        summary = work.resize_images(
            opts["max_width"], opts["jpeg_quality"], None, max_height=opts["max_height"] or None,
        )
        ctx.structure_changed = True
        mb = 1024 * 1024
        return StepResult.applied(
            f"resized {summary.pages_resized} page(s) to {opts['max_width']}px wide, "
            f"{before / mb:.1f} MB -> {os.path.getsize(work.path) / mb:.1f} MB"
        )


class FilenameTagsStep(Step):
    key = "filename_tags"
    label = "Fill empty fields from the filename"
    description = (
        "Fills ComicInfo fields that are EMPTY by parsing the filename with a pattern. Leave the pattern blank "
        "to use the most recent saved Rename / Parse Filename pattern that fits the name; a pattern typed here "
        "is used as is. A pattern that matches the whole name is a guess about which pattern is right "
        f"({HISTORY_PATTERN_CONFIDENCE:.0%} from history, {PATTERN_CONFIDENCE:.0%} when named here); with no "
        f"matching pattern the scene-name reader is tried ({SCENE_CONFIDENCE:.0%}). Below the threshold the "
        "result is listed under Needs review. Existing values are never replaced."
    )
    options = (
        OptionSpec("pattern", "Pattern (blank = saved patterns)", "str", "", max_length=200,
                   tooltip="e.g. %series% %number% - %title%"),
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        stem = os.path.splitext(os.path.basename(ctx.original))[0]
        named = self.options_for(ctx)["pattern"].strip()
        candidates = [(named, PATTERN_CONFIDENCE)] if named else [
            (p, HISTORY_PATTERN_CONFIDENCE) for p in ctx.env.pattern_history if p.strip()
        ]
        for pattern, confidence in candidates:
            parsed = parse_pattern_filename(
                stem, pattern, set(FILENAME_FIELD_KEYS), set(NUMERIC_FILENAME_FIELDS),
                strip_leading_zeros_fields={"number"},
            )
            if parsed is None:
                continue
            fill = _empty_fields(ctx, parsed)
            if not fill:
                return StepResult.nothing()  # the first pattern that fits has nothing left to fill
            return StepResult.suggestion(
                FieldFill(fill, f"pattern {pattern!r}"), confidence, f"the filename matches the pattern {pattern!r}",
            )
        scene = _empty_fields(ctx, proposed_fields(parse_scene_name(ctx.original), ctx.work.metadata.notes))
        if scene:
            return StepResult.suggestion(
                FieldFill(scene, "scene-name reader"), SCENE_CONFIDENCE,
                "no saved pattern fits the filename; read it as a scene-style name",
            )
        return StepResult.nothing()

    def apply_suggestion(self, ctx: CbzCtx, result: StepResult) -> str:
        filled = _fill_empty(ctx, result.value.fields)
        return f"filled {', '.join(filled)} from the filename ({result.value.source})"


@dataclass
class _Match:
    fields: dict[str, str]
    confidence: float
    label: str
    source: str


def _year_of(text: str) -> str:
    return (text or "")[:4]


def _match_confidence(exact_series: bool, number_ok: bool, year_ok: bool) -> float:
    if exact_series and number_ok and year_ok:
        return EXACT_CONFIDENCE
    if exact_series and number_ok:
        return SERIES_NUMBER_CONFIDENCE
    return NUMBER_ONLY_CONFIDENCE if number_ok else WEAK_CONFIDENCE


def _normalized_number(number: str) -> str:
    text = (number or "").strip().lstrip("#").lstrip("0")
    return text or ("0" if (number or "").strip() else "")


class LookupStep(Step):
    key = "lookup"
    label = "Fill empty fields from a database"
    description = (
        "Looks the comic up by Series + Number (+ year) from ComicInfo or the filename and fills fields that "
        "are EMPTY. Sources, in order: your local GCD database and ComicRack library (when set up under "
        "Settings), then Comic Vine (only when an API key is saved). Confidence comes from the match: exact "
        f"series + number + year {EXACT_CONFIDENCE:.0%}, exact series + number {SERIES_NUMBER_CONFIDENCE:.0%}, "
        "anything looser lower (Needs review under the default threshold). Offline, or with no database and "
        "no key, the step does nothing and says so in the report."
    )
    options = (
        OptionSpec("use_local", "Use local databases (GCD, ComicRack)", "bool", True),
        OptionSpec("use_comicvine", "Use Comic Vine (needs an API key)", "bool", True),
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        meta = ctx.work.metadata
        if all((getattr(meta, k, "") or "").strip() for k in LOOKUP_TRIGGER_FIELDS):
            return StepResult.nothing()
        parsed = parse_scene_name(ctx.original)
        series = (meta.series or parsed.series).strip()
        number = (meta.number or parsed.number or (parsed.volume if len(parsed.volume) < 4 else "")).strip()
        year = (meta.year or parsed.year).strip()
        series_year = meta.volume if len(meta.volume or "") == 4 else (parsed.volume if len(parsed.volume) == 4 else "")
        if not series:
            return StepResult.nothing("lookup skipped: no series in ComicInfo or the filename")

        opts = self.options_for(ctx)
        env, notes, best = ctx.env, [], None
        sources = []
        if opts["use_local"]:
            sources += [("local", path) for path in env.local_databases]
        if opts["use_comicvine"] and env.comicvine_key:
            sources.append(("comicvine", env.comicvine_key))
        if not sources:
            return StepResult.nothing("lookup skipped: no local database set up and no Comic Vine key saved")

        for kind, where in sources:
            if kind == "comicvine" and env.comicvine_down:
                notes.append("Comic Vine skipped (unreachable earlier in this run)")
                continue
            try:
                found = (
                    self._local(env, where, series, number, year, series_year)
                    if kind == "local" else self._comicvine(where, series, number, year)
                )
            except ComicVineLookupError as exc:
                env.comicvine_down = True
                notes.append(f"Comic Vine unavailable: {exc}")
                continue
            except GcdLocalError as exc:
                notes.append(f"local database unavailable: {exc}")
                continue
            if found is not None and (best is None or found.confidence > best.confidence):
                best = found
            if best is not None and best.confidence >= EARLY_STOP_CONFIDENCE:
                break

        note = "; ".join(notes)
        if best is None:
            return StepResult.nothing(note or "lookup found no match")
        fill = _empty_fields(ctx, best.fields)
        if not fill:
            return StepResult.nothing(note)
        result = StepResult.suggestion(
            FieldFill(fill, f"{best.source} {best.label}"), best.confidence,
            f"{best.source} matched {best.label}",
        )
        result.note = note
        return result

    @staticmethod
    def _local(env: RedactEnv, path: str, series: str, number: str, year: str, series_year: str) -> _Match | None:
        database = env.open_database(path)
        candidates = database.search(series, number, year, series_year)
        if not candidates:
            return None
        top = candidates[0]
        confidence = _match_confidence(
            top.exact_name,
            bool(number) and _normalized_number(top.number) == _normalized_number(number),
            bool(year) and _year_of(top.key_date) == year,
        )
        source = "ComicRack library" if getattr(database, "is_comicrack", False) else "GCD (local)"
        return _Match(database.details(top.issue_id).as_dict(), confidence, top.display_label(), source)

    @staticmethod
    def _comicvine(key: str, series: str, number: str, year: str) -> _Match | None:
        candidates = search_comicvine(key, series, number, year_hint=year, max_results=6)
        if not candidates:
            return None
        top = candidates[0]
        confidence = _match_confidence(
            normalize_name(top.volume_name) == normalize_name(series),
            bool(number) and _normalized_number(top.issue_number) == _normalized_number(number),
            bool(year) and _year_of(top.cover_date) == year,
        )
        details = fetch_issue_details(key, top.detail_url)
        details.publisher = fetch_publisher(key, top.volume_detail_url)
        return _Match(details.as_dict(), confidence, top.display_label(), "Comic Vine")

    def apply_suggestion(self, ctx: CbzCtx, result: StepResult) -> str:
        filled = _fill_empty(ctx, result.value.fields)
        return f"filled {', '.join(filled)} from {result.value.source}"


class ValidateFixStep(Step):
    key = "validate_fix"
    label = "Fix ComicInfo issues"
    description = (
        "Applies every fixable problem Operations > Validate / Fix Issues finds: stray spaces, scene tags or an "
        "extension left in Series/Title, '#' or leading zeros in Number, an implausible Year/Month/Day cleared, "
        "a wrong PageCount, LanguageISO written as 'en-US'. Issues that have no automatic fix are left for you."
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        work = ctx.work
        fixes = [f for f in check_metadata(work.metadata, work.actual_page_count) if f.fixable]
        if not fixes:
            return StepResult.nothing()
        for finding in fixes:
            setattr(work.metadata, finding.field, finding.fix)
        return StepResult.applied(*(f"{f.label}: {f.message}" for f in fixes))


class TagLowResStep(Step):
    key = "tag_low_res"
    label = "Tag low-res scans"
    description = (
        f'Adds the "{LOW_RES_TAG}" tag to a book whose pages are under 1000px wide, and removes it from one '
        "that no longer is (as Operations > Tag Low-Res Scans does). Other tags are kept."
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        work = ctx.work
        category = scan_page_sizes(work.path, list(work.page_names)).category
        if category is None:
            return StepResult.nothing()  # couldn't measure: leave the tags alone
        tagged = has_tag(work.metadata.tags)
        if category == SIZE_LOW and not tagged:
            work.metadata.tags = add_tag(work.metadata.tags)
            return StepResult.applied(f'added "{LOW_RES_TAG}"')
        if category != SIZE_LOW and tagged:
            work.metadata.tags = remove_tag(work.metadata.tags)
            return StepResult.applied(f'removed "{LOW_RES_TAG}" (no longer low-res)')
        return StepResult.nothing()


class RenameStep(Step):
    key = "rename"
    label = "Rename by pattern"
    position = "last"
    description = (
        "Renames the saved file with a pattern, without overwriting anything; logged for File > Undo Last "
        "Rename. Leave the pattern blank to use the newest saved Rename / Export pattern without a '/'. It "
        "runs after the file is saved and before Move into folders. On only once such a pattern exists."
    )
    options = (
        OptionSpec("pattern", "Pattern (blank = newest saved)", "str", "", max_length=200,
                   tooltip="e.g. %series% %number% - %title%"),
    )

    def __init__(self, env: RedactEnv | None = None):
        super().__init__(default_enabled=bool(env and env.rename_pattern()))

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        pattern = self.options_for(ctx)["pattern"].strip() or ctx.env.rename_pattern()
        if not pattern:
            return StepResult.nothing("rename skipped: no rename pattern saved yet (use File > Rename / Export once)")
        planned = ctx.target_path
        stem, ext = os.path.splitext(os.path.basename(planned))
        new_stem = render_filename(ctx.values(), pattern, fallback=stem, ascii_only=ctx.env.ascii_filenames)
        new_path = unique_path(os.path.dirname(planned), new_stem, ext, set(), own_path=planned)
        if new_path == planned:
            return StepResult.nothing()
        ctx.rename_to = new_path
        return StepResult.applied(f"rename to {os.path.basename(new_path)!r}")


class MoveIntoFoldersStep(Step):
    key = "move_into_folders"
    label = "Move into folders"
    position = "last"
    default_enabled = False  # moves files around a library
    description = (
        "Moves the saved (and renamed) file into a folder tree under the library root, from the Rename / Export "
        "dialog's 'Move into folders' mode. Leave the pattern blank to use the newest saved pattern that has a "
        "'/'. Needs a library root chosen in that dialog. Never overwrites; logged for File > Undo Last Rename. "
        "Off by default."
    )
    options = (
        OptionSpec("pattern", "Pattern (blank = newest saved with '/')", "str", "", max_length=200,
                   tooltip="e.g. %publisher%/%series%/%series% %number%"),
    )

    def run(self, ctx: CbzCtx) -> StepResult:
        if (missing := ctx.need_work()) is not None:
            return missing
        env = ctx.env
        pattern = self.options_for(ctx)["pattern"].strip() or env.move_pattern()
        if not pattern:
            return StepResult.nothing("move skipped: no folder pattern saved yet (use 'Move into folders' in Rename / Export)")
        if not env.library_root or not os.path.isdir(env.library_root):
            return StepResult.nothing("move skipped: no library root folder chosen in Rename / Export")
        move = plan_moves(
            [ctx], env.library_root, pattern, lambda c: c.values(), lambda c: c.final_path(),
            ascii_only=env.ascii_filenames,
        )[0]
        if move.blocking:
            return StepResult.failed(f"can't move: {move.warning}")
        if move.is_noop:
            return StepResult.nothing()
        ctx.move = move
        return StepResult.applied(f"move to {move.relative_path()}")


# --- final stage ----------------------------------------------------------------


def verify_written_file(path: str, expected_pages: int) -> bool:
    """The new archive opens, every entry passes its CRC, it holds the
    expected pages and has a ComicInfo.xml."""
    try:
        with zipfile.ZipFile(path) as zf:
            if zf.testzip() is not None:
                return False
    except (zipfile.BadZipFile, OSError, zlib.error, RuntimeError, NotImplementedError, ValueError):
        return False
    fresh = CbzBook(path)
    return (
        not fresh.load_error and not fresh.needs_conversion
        and fresh.actual_page_count == expected_pages and fresh.comicinfo_name is not None
    )


def reload_book(book: CbzBook, path: str) -> None:
    """The live row takes on what is on disk at `path`, nothing unsaved."""
    fresh = CbzBook(path)
    for f in dataclasses.fields(CbzBook):
        setattr(book, f.name, getattr(fresh, f.name))


def _write_and_commit(ctx: CbzCtx, done: list[str], notes: list[str]) -> str:
    """Saves the working book beside the original and swaps it in. Returns
    an error message ('' on success); appends to `done`/`notes`."""
    env, work = ctx.env, ctx.work
    expected = work.actual_page_count
    temp = _side_path(ctx.original, "redact-new")
    ctx.temps.append(temp)
    try:
        work.save(output_path=temp)
    except CbzError as exc:
        return f"NOT SAVED, the original is untouched: {exc}"
    trash = env.trash or move_to_trash
    if _same_path(ctx.target_path, ctx.original):
        try:
            result = commit_in_place(ctx.original, temp, trash=trash, verify=lambda p: verify_written_file(p, expected))
        except CommitError as exc:
            return f"NOT SAVED, the original is untouched: {exc}"
        if result.backup_kept:
            notes.append(result.warning)
            done.append(f"saved; the original is kept at {result.backup}")
        else:
            done.append("saved in place; the original is in the Recycle Bin")
        return ""
    # A converted CBR/CB7/CBT: a new .cbz name, so the engine's replace-in-place doesn't apply;
    # same order, though -- verify, put the new file in place, only then trash the original.
    if not verify_written_file(temp, expected):
        return "NOT SAVED, the original is untouched: the new file failed verification"
    try:
        rename_no_clobber(temp, ctx.target_path)
    except OSError as exc:
        return f"NOT SAVED, the original is untouched: couldn't create {os.path.basename(ctx.target_path)} ({exc})"
    ctx.current_path, ctx.path_changed = ctx.target_path, True
    try:
        trash(ctx.original)
    except Exception as exc:  # noqa: BLE001 -- any trash failure keeps the original
        notes.append(f"the original {os.path.basename(ctx.original)} is kept: {exc}")
        done.append(f"saved as {os.path.basename(ctx.target_path)}; the original is kept")
        return ""
    if os.path.lexists(ctx.original):
        notes.append(f"the Recycle Bin didn't take {os.path.basename(ctx.original)}, so it is kept")
        done.append(f"saved as {os.path.basename(ctx.target_path)}; the original is kept")
    else:
        done.append(f"saved as {os.path.basename(ctx.target_path)}; the original is in the Recycle Bin")
    return ""


def save_stage(ctx: CbzCtx, entry) -> StepResult | None:
    """The engine's finalize hook (see the module docstring)."""
    if ctx.skip_reason:
        return StepResult.skipped(ctx.skip_reason)
    if ctx.work is None:
        return None  # still a CBR/CB7/CBT: the steps already said why
    done: list[str] = []
    notes: list[str] = []
    error = ""
    env = ctx.env
    try:
        if ctx.changed:
            error = _write_and_commit(ctx, done, notes)
        if not error and ctx.rename_to:
            try:
                rename_no_clobber(ctx.current_path, ctx.rename_to)
            except OSError as exc:
                error = f"couldn't rename to {os.path.basename(ctx.rename_to)!r}: {exc}"
            else:
                if env.rename_log is not None:
                    env.rename_log.record("Redact: rename by pattern", [(ctx.current_path, ctx.rename_to)])
                ctx.current_path, ctx.path_changed = ctx.rename_to, True
                done.append(f"renamed to {os.path.basename(ctx.rename_to)!r}")
        if not error and ctx.move is not None:
            error = _execute_move(ctx, done, notes)
    finally:
        if done:
            # Whatever did happen is on disk: show it in the row even if a later part failed.
            reload_book(ctx.book, ctx.current_path if ctx.path_changed else ctx.book.path)
    if error:
        entry.applied += [f"Save: {c}" for c in done]
        return StepResult.failed(error)
    result = StepResult.applied(*done) if done else StepResult.nothing()
    result.note = "; ".join(notes)
    return result


def _execute_move(ctx: CbzCtx, done: list[str], notes: list[str]) -> str:
    env, move = ctx.env, ctx.move
    try:
        result = execute_move(ctx.current_path, move.new_path, copy=False, trash=env.trash or move_to_trash)
    except (OSError, ValueError) as exc:
        return f"couldn't move to {move.relative_path()}: {exc}"
    if result.original_kept:
        notes.append(f"moved as a copy; {result.warning}")
        done.append(f"copied to {move.relative_path()}; the original is kept")
        return ""
    if env.rename_log is not None:
        trashed = [(ctx.current_path, result.new_path)] if result.original_trashed else []
        env.rename_log.record(
            "Redact: move into folders", [(ctx.current_path, result.new_path)],
            created_dirs=result.created_dirs, trashed=trashed, root=move.root,
        )
    ctx.current_path, ctx.path_changed = result.new_path, True
    done.append(f"moved to {move.relative_path()}")
    return ""


# --- catalogue, recipe ---------------------------------------------------------

FINALIZE_LABEL = "Save"


def build_catalogue(env: RedactEnv | None = None) -> list[Step]:
    """The steps the recipe editor offers, in default order: structure
    first (convert, cleanup, pages, resize), then metadata (filename,
    lookup, fixes, tag), then the two pinned-last steps. `env` decides
    whether Rename starts enabled (only once a pattern exists)."""
    return [
        ConvertStep(),
        CleanContentsStep(),
        RemoveCreditPagesStep(),
        ResizeImagesStep(),
        FilenameTagsStep(),
        LookupStep(),
        ValidateFixStep(),
        TagLowResStep(),
        RenameStep(env),
        MoveIntoFoldersStep(),
    ]


def run_catalogue(env: RedactEnv | None = None) -> list[Step]:
    """build_catalogue() plus the internal guard, for the engine."""
    return [GuardStep()] + build_catalogue(env)


def recipe_for_run(recipe: Recipe) -> Recipe:
    """The recipe as the engine runs it: the guard is not the user's to
    switch off (the engine puts it first, a catalogue step the stored
    order lacks), and Move plans from the renamed file, so Rename always
    comes right before it whatever order the editor stored for the two."""
    pinned = ("rename", "move_into_folders")
    return Recipe(
        order=[k for k in recipe.order if k != "guard" and k not in pinned] + list(pinned),
        enabled={k: v for k, v in recipe.enabled.items() if k != "guard"},
        options={k: dict(v) for k, v in recipe.options.items()},
        confidence_threshold=recipe.confidence_threshold,
    )


def recipe_to_setting(recipe: Recipe) -> str:
    return recipe.to_json()


def recipe_from_setting(text: str) -> Recipe:
    """Garbage or empty text is the default recipe (Recipe.from_json)."""
    return Recipe.from_json(text)
