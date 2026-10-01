"""
core/cbz_file.py

Pure-logic CBZ engine: opening a CBZ (a plain ZIP of page images plus
an optional ComicInfo.xml at its root), exposing its metadata and page
list, and saving updated metadata back -- the same "rewrite everything
else byte-for-byte into a fresh zip" approach as core/epub_metadata.py's
EpubBook.save() in the sibling EPUB tool, adapted for CBZ's much
simpler structure (no manifest to keep in sync, no cover-image
indirection -- the "cover" shown in the sidebar is just whichever page
sorts first).

CBR/CBT/CB7 (RAR/tar/7-Zip-based) archives are out of scope here
entirely -- see core/foreign_archive_convert.py, which converts one to
a real .cbz *before* this module ever sees it, rather than this module
learning to read RAR/tar/7z.

save() is the only method that runs implicitly (whenever the user hits
Save); it keeps the "images copied byte-for-byte" guarantee. The one
deliberate exception is resize_images() (see core/image_resize.py),
which re-encodes page pixel data -- it only ever runs when the user
explicitly asks for it via Operations > Resize Images..., never as a
side effect of a normal save.
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from redactor_common.core.scan_stamp import ScanStamp, make_stamp, parse_stamp

from core.cbz_fingerprint import cbz_fingerprint
from core.archive_sniff import CONTAINER_UNKNOWN, CONTAINER_ZIP, detect_container
from core.comicinfo import ComicInfoError, ComicInfoMetadata, parse_comicinfo_xml, serialize_comicinfo_xml
from core.image_resize import OUTPUT_FORMAT_EXTENSIONS, resize_page
from core.comicinfo_locate import locate_comicinfo
from core.zip_names import open_zip
from core.zip_rewrite import (
    ZIP_ERRORS,
    Action,
    CbzError,
    NewEntry,
    RewriteCancelled,
    RewritePlan,
    RewriteError,
    now_date_time,
    rewrite_archive,
)

# Entry extensions that already mean "this format" -- a page named
# .jpeg converted to JPEG keeps its name rather than becoming .jpg.
_FORMAT_EXTENSION_FAMILY = {"JPEG": (".jpg", ".jpeg"), "WEBP": (".webp",)}

# Recognized page image types. Readers in the wild are lenient about
# this too (a CBZ is "mostly JPGs" by convention, not by enforced
# rule) -- anything else (ComicInfo.xml itself, a stray Thumbs.db, an
# NFO file some scan groups include) is preserved on save but not
# counted as a page.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
COMICINFO_NAME = "ComicInfo.xml"

_ZIP_ERRORS = ZIP_ERRORS  # see core/zip_rewrite.py


class ResizeCancelled(RewriteCancelled):
    """resize_images()'s `should_cancel` fired; the temp file was removed
    and the source left untouched."""


@dataclass
class ResizeSummary:
    """What CbzBook.resize_images() actually did -- shown to the user
    afterward (Operations > Resize Images...) since there's no preview-
    before-commit step for this operation (see that dialog's own
    docstring for why: scanning every page's dimensions up front is
    exactly the expensive extra pass this feature exists to avoid
    paying twice on the "extremely large files" it's meant for)."""

    pages_resized: int = 0
    pages_skipped: int = 0  # already at or under the target width
    pages_failed: int = 0  # Pillow couldn't decode this page; left untouched
    original_bytes: int = 0
    new_bytes: int = 0


_DIGITS_RE = re.compile(r"(\d+)")


def page_sort_key(name: str) -> list:
    """Reading order of page entries: numbers compared as numbers
    ("2.webp" before "10.webp"), case ignored -- as comic readers do.
    Plain text sorting put "10" before "2", and real archives number
    pages unpadded (seen 2026-09-29: "1.webp", "2.webp", "10.webp")."""
    return [int(part) if part.isdigit() else part.casefold() for part in _DIGITS_RE.split(name)]


def _is_image(name: str) -> bool:
    """A page: an image file -- but not macOS's "__MACOSX/._name.jpg"
    resource-fork leftovers, which end in .jpg without being images (they
    were counted as broken extra pages)."""
    if posixpath.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
        return False
    lowered = name.lower()
    return not (lowered.startswith("__macosx/") or "/__macosx/" in lowered
                or posixpath.basename(lowered).startswith("._"))


def _renamable_pages(names: list[str], output_format: Optional[str]) -> set[str]:
    """Image entries that can safely be converted to `output_format`:
    those whose renamed entry ("001.png" -> "001.webp") wouldn't clash
    with another entry already in the archive, or with another page
    being renamed to the same thing ("001.png" and "001.gif" both
    becoming "001.webp"). Anything else keeps its original format."""
    if not output_format:
        return set()
    lower_names = {n.lower() for n in names}
    safe: set[str] = set()
    planned: dict[str, list[str]] = {}
    for name in names:
        if not _is_image(name):
            continue
        if not _needs_rename(name, output_format):
            safe.add(name)  # already named for that format -- no rename involved
            continue
        new_name = _renamed_for_format(name, output_format).lower()
        if new_name not in lower_names:
            planned.setdefault(new_name, []).append(name)
    safe.update(group[0] for group in planned.values() if len(group) == 1)
    return safe


def _needs_rename(name: str, output_format: str) -> bool:
    return posixpath.splitext(name)[1].lower() not in _FORMAT_EXTENSION_FAMILY[output_format]


def _renamed_for_format(name: str, output_format: str) -> str:
    return posixpath.splitext(name)[0] + OUTPUT_FORMAT_EXTENSIONS[output_format]


def _remap_pages_element(metadata: ComicInfoMetadata, removed_positions: list[int]) -> None:
    """ComicInfo's <Pages><Page Image="N" .../></Pages> refers to pages
    by position. Drops entries for removed pages and shifts later ones
    down so every remaining entry still describes the same page."""
    removed = set(removed_positions)
    for element in metadata.extra_elements:
        if not isinstance(element.tag, str) or element.tag.rsplit("}", 1)[-1] != "Pages":
            continue
        for page in list(element):
            try:
                position = int(page.get("Image", ""))
            except ValueError:
                continue
            if position in removed:
                element.remove(page)
            else:
                page.set("Image", str(position - sum(1 for r in removed_positions if r < position)))


def _needs_conversion(path: str, container: str) -> bool:
    is_cbz_name = posixpath.splitext(path)[1].lower() == ".cbz"
    return not (is_cbz_name and container in (CONTAINER_ZIP, CONTAINER_UNKNOWN))


def path_needs_conversion(path: str) -> bool:
    """CbzBook.needs_conversion for a path not loaded yet (reads only
    the file's first few hundred bytes)."""
    return _needs_conversion(path, detect_container(path))


@dataclass
class CbzBook:
    """One loaded CBZ file. Construct directly (`CbzBook(path)`) --
    loading happens immediately in __post_init__, same pattern as the
    EPUB tool's EpubBook, so a caller never holds a half-initialized
    instance."""

    path: str
    metadata: ComicInfoMetadata = field(default_factory=ComicInfoMetadata)
    page_names: list = field(default_factory=list)  # image entries, filename-sorted reading order
    comicinfo_name: Optional[str] = None  # actual entry name found (original case), or None if absent
    load_error: str = ""
    # Something worth knowing about the file that doesn't stop it loading
    # (several nested ComicInfo.xml files and which one was read).
    load_warning: str = ""
    save_error: str = ""
    dirty: bool = False  # set by the GUI layer when a field is edited; cleared on save()
    # What the file really is (core/archive_sniff.py), whatever its
    # extension says.
    container: str = CONTAINER_ZIP
    # Whether the scan stamp read from the file still matches the archive's
    # pages: True = its fingerprint differs, False = matches, None = can't
    # be told (no fingerprint in the stamp, or the archive is unreadable).
    # Set by _load(), record_scan() and every rewrite; meaningless without
    # a stamp.
    stamp_stale: Optional[bool] = field(default=None, repr=False, compare=False)
    # True while the ONLY unsaved change is a fresh scan stamp (no edits),
    # so Remove Pages / Clean Up aren't refused over it. Any other
    # assignment to `dirty` clears it (see __setattr__).
    stamp_only_dirty: bool = field(default=False, repr=False, compare=False)

    def __setattr__(self, name, value):
        if name == "dirty":
            object.__setattr__(self, "stamp_only_dirty", False)
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        self._load()

    @property
    def needs_conversion(self) -> bool:
        """True for anything that isn't a real ZIP named .cbz: a
        CBR/CB7/CBT, or a mislabeled file (a ".cbr" that's really a ZIP,
        a ".cbz" that's really a RAR). Such a book is listed read-only
        until Convert to CBZ -- save() and resize_images() refuse it.
        A .cbz whose container can't be identified at all is still
        treated as a (probably broken) CBZ, so it gets a normal load
        error rather than a conversion offer."""
        return _needs_conversion(self.path, getattr(self, "container", CONTAINER_ZIP))

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self.container = detect_container(self.path)
        if self.needs_conversion and self.container != CONTAINER_ZIP:
            # Not a ZIP (or unidentifiable under a foreign extension):
            # nothing this module can read. Listed with an empty page
            # list and blank metadata until converted.
            return
        # A ZIP under a foreign name is read normally (it IS a CBZ in
        # all but name), just still flagged needs_conversion.
        try:
            with open_zip(self.path) as zf:
                names = zf.namelist()
                self.page_names = sorted((n for n in names if _is_image(n)), key=page_sort_key)
                found = locate_comicinfo(names)  # core/comicinfo_locate.py: the one lookup rule
                self.comicinfo_name = found.name if found else None
                if found and found.others:
                    self.load_warning = (
                        f"Several ComicInfo.xml files are in subfolders; the one in {found.name!r} was read "
                        f"(also: {', '.join(found.others)})"
                    )
                if self.comicinfo_name:
                    self.metadata = parse_comicinfo_xml(zf.read(self.comicinfo_name))
                else:
                    self.metadata = ComicInfoMetadata()
        except _ZIP_ERRORS as exc:
            self.load_error = str(exc)
        except ComicInfoError as exc:
            # A malformed ComicInfo.xml shouldn't sink the whole file --
            # the pages are still perfectly readable. Surfaced as a load
            # error so the GUI can flag it rather than silently editing
            # blank metadata over top of whatever was actually there.
            self.load_error = str(exc)
        # A stamp read from disk is the last-known scan: it never marks
        # the book dirty, it is only checked against the pages.
        self._refresh_stamp_staleness()

    # ------------------------------------------------------------------
    # Scan stamp (Validate / Fix Issues result kept inside the file)
    # ------------------------------------------------------------------
    # Stored as a <RedactorScan> element in ComicInfo.xml (core/comicinfo.py),
    # so it travels with the file, is written only by Save like any other
    # metadata, and survives every rewrite path (they all carry ComicInfo's
    # content). The ZIP comment is deliberately not used: cover_stamp.py and
    # collection_scan.py already own it and patch it outside Save.

    @property
    def stamp(self) -> Optional[ScanStamp]:
        """The scan stamp recorded in the file (None if none or garbled)."""
        return parse_stamp(self.metadata.scan_stamp)

    def record_scan(self, status: str) -> bool:
        """Stamps a completed validation scan (status, now, the archive's
        fingerprint) into the metadata and marks the book dirty so Save
        writes it. A book that failed to load, needs converting, or whose
        archive can't be fingerprinted gets no stamp (a failed scan never
        describes the file). Returns whether a stamp was recorded."""
        if self.load_error or self.needs_conversion or not status:
            return False
        fingerprint = cbz_fingerprint(self.path)
        if not fingerprint:
            return False
        self.metadata.scan_stamp = make_stamp(status, fingerprint).to_text()
        self.stamp_stale = False
        only_stamp = not self.dirty or self.stamp_only_dirty
        self.dirty = True
        self.stamp_only_dirty = only_stamp
        return True

    def _refresh_stamp_staleness(self) -> None:
        """Compares the stamp's fingerprint with the archive now."""
        stamp = self.stamp
        self.stamp_stale = None
        if stamp is None or not stamp.fingerprint:
            return
        current = cbz_fingerprint(self.path)
        if current:
            self.stamp_stale = current != stamp.fingerprint

    def scan_status(self) -> str:
        """The stamp's status while it still matches the pages ("" when
        unscanned, garbled, stale or unverifiable)."""
        stamp = self.stamp
        return stamp.status if stamp is not None and self.stamp_stale is False else ""

    def stamp_text(self) -> str:
        """The stamp as the Status column shows it ("" without one):
        `<STATUS> · <date time>`, plus "(changed since)" when the pages no
        longer match it, or "(unverified)" when that can't be told."""
        stamp = self.stamp
        if stamp is None:
            return ""
        text = stamp.display()
        if self.stamp_stale:
            return f"{text} (changed since)"
        if self.stamp_stale is None:
            return f"{text} (unverified)"
        return text

    @property
    def first_page_name(self) -> Optional[str]:
        return self.page_names[0] if self.page_names else None

    def read_first_page_bytes(self) -> Optional[bytes]:
        """Reads the first page's raw image bytes, for the sidebar
        thumbnail. Returns None if there are no pages, or on any read
        error (a corrupt/truncated entry) -- the caller shows a blank
        placeholder rather than crashing over a thumbnail."""
        if not self.first_page_name:
            return None
        try:
            with open_zip(self.path) as zf:
                return zf.read(self.first_page_name)
        except _ZIP_ERRORS:
            return None

    @property
    def actual_page_count(self) -> int:
        return len(self.page_names)

    @property
    def page_count_mismatch(self) -> bool:
        """True when the stored PageCount disagrees with the archive's
        actual image count -- surfaced in the GUI rather than silently
        overwritten on save, since a mismatch can mean pages were
        added/removed by something other than this tool since the
        metadata was last written."""
        stored = (self.metadata.page_count or "").strip()
        return bool(stored) and stored.lstrip("-").isdigit() and int(stored) != self.actual_page_count

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save(self, output_path: Optional[str] = None) -> None:
        """Writes updated metadata back into a CBZ file. PageCount is
        always recomputed from the actual archive contents right
        before writing -- this field is never taken from the GUI form,
        only ever from the archive itself (see page_count_mismatch).

        If output_path is None, overwrites self.path in place (via a
        temp file + rename, so a crash mid-write can't corrupt the
        original). Otherwise writes a new file at output_path, leaving
        the source untouched.
        """
        if self.load_error:
            raise CbzError(f"Cannot save, file failed to load: {self.load_error}")
        if self.needs_conversion:
            raise CbzError("Cannot save, this file needs converting to CBZ first (Convert to CBZ)")

        self.metadata.page_count = str(self.actual_page_count)
        new_xml_bytes = serialize_comicinfo_xml(self.metadata)
        target = output_path or self.path
        # Preserve the original entry's name/case if it was at the archive root;
        # otherwise (none, or a nested one) write the spec-correct name at the
        # root. A nested source is dropped, so there are never two. A nested
        # copy beside an existing root one is not the source and is left alone.
        old_name = self.comicinfo_name
        write_name = old_name if old_name and "/" not in old_name else COMICINFO_NAME

        # The archive comment (e.g. the cover fingerprint stamp, core/cover_stamp.py)
        # is carried over by the helper. ComicInfo.xml is rewritten last, with
        # the updated bytes. The helper's temp file (target + ".tmp_write") is 10
        # characters longer than the real target, so a path-too-long error can
        # hit here even when the final, shorter path would have just fit
        # (describe_save_error() words that case).
        plan = RewritePlan(
            decide=lambda entry, read: Action(drop=True) if entry.name == old_name else None,
            after=[NewEntry(write_name, new_xml_bytes)],
        )
        try:
            rewrite_archive(self.path, target, plan, temp_suffix=".tmp_write", error_prefix="Could not save CBZ file")
        except RewriteError as exc:
            self.save_error = exc.detail
            raise

        self.save_error = ""
        # Only treat this as "saved" (clear dirty, adopt new path) when
        # this book's own file was actually overwritten -- saving to a
        # *different* path is a copy, and the original book is still
        # exactly as dirty as before, still at its original path.
        if output_path is None or output_path == self.path:
            self.path = target
            self.comicinfo_name = write_name
            self.load_warning = ""  # a promoted nested ComicInfo.xml is the only one now
            self.dirty = False
            self._refresh_stamp_staleness()

    # ------------------------------------------------------------------
    # Removing pages (scanner credit pages -- see core/credit_pages.py)
    # ------------------------------------------------------------------

    def remove_pages(self, names: list[str], dispose_original: Optional[Callable[[str], None]] = None) -> int:
        """Rewrites the archive without the page entries in `names`,
        keeping everything else byte for byte. ComicInfo.xml is updated
        to match: PageCount recomputed, and <Pages> entries (indexed by
        page position) dropped for removed pages and renumbered for the
        rest, so per-page tags stay on the right pages. Returns the
        number of pages removed.

        `dispose_original(path)` is called on the original file just
        before the new one replaces it -- the GUI passes "move to the
        Recycle Bin", since this can't be undone. If it raises, nothing
        is replaced.

        Refuses a book with unsaved metadata edits: this writes
        ComicInfo.xml too, and must not quietly save someone's
        half-finished edits along with it."""
        if self.load_error:
            raise CbzError(f"Cannot remove pages, file failed to load: {self.load_error}")
        if self.needs_conversion:
            raise CbzError("Cannot remove pages, this file needs converting to CBZ first (Convert to CBZ)")
        if self.dirty and not self.stamp_only_dirty:
            raise CbzError("Save or undo this file's unsaved changes first")
        remove = set(names) & set(self.page_names)
        if not remove:
            return 0

        removed_positions = sorted(i for i, name in enumerate(self.page_names) if name in remove)
        new_pages = [name for name in self.page_names if name not in remove]
        metadata = copy.deepcopy(self.metadata)
        metadata.page_count = str(len(new_pages))
        _remap_pages_element(metadata, removed_positions)

        def decide(entry, read):
            if entry.name in remove:
                return Action(drop=True)
            if entry.name == self.comicinfo_name:
                # Rewritten with the updated bytes; a nested one moves to the root, as on Save.
                return Action(
                    rename=COMICINFO_NAME if "/" in entry.name else None,
                    data=serialize_comicinfo_xml(metadata), date_time=now_date_time(),
                )
            return None

        rewrite_archive(
            self.path, self.path, RewritePlan(decide=decide),
            dispose_original=dispose_original, temp_suffix=".tmp_pages", error_prefix="Could not remove pages",
        )

        self.page_names = new_pages
        if self.comicinfo_name:
            if "/" in self.comicinfo_name:
                self.comicinfo_name = COMICINFO_NAME
                self.load_warning = ""
            self.metadata = metadata
            if self.stamp_only_dirty:
                self.dirty = False  # the pending stamp was written with ComicInfo.xml
        self._refresh_stamp_staleness()
        return len(removed_positions)

    # ------------------------------------------------------------------
    # Cleaning up the archive's contents (core/archive_contents.py)
    # ------------------------------------------------------------------

    def cleanup_plan(self):
        """What Clean Up Archive Contents would change (nothing written)."""
        from core.archive_contents import plan_cleanup

        with open_zip(self.path) as zf:
            return plan_cleanup(zf.namelist(), self.page_names, self.comicinfo_name)

    def clean_contents(self, dispose_original: Optional[Callable[[str], None]] = None):
        """Rewrites the archive with plain numbered page names, no page
        folders and no junk files (core/archive_contents.py); the pages'
        bytes, their order and ComicInfo.xml's content are unchanged.
        Returns the plan that was applied (plan.needed False: nothing
        to do, nothing written). `dispose_original` and the refusal of
        unsaved edits work as in remove_pages()."""
        if self.load_error:
            raise CbzError(f"Cannot clean up, file failed to load: {self.load_error}")
        if self.needs_conversion:
            raise CbzError("Cannot clean up, this file needs converting to CBZ first (Convert to CBZ)")
        if self.dirty and not self.stamp_only_dirty:
            raise CbzError("Save or undo this file's unsaved changes first")
        plan =self.cleanup_plan()
        if not plan.needed:
            return plan

        drop = set(plan.removals)

        def decide(entry, read):
            if entry.name in drop or (plan.drop_folder_entries and entry.name.endswith("/")):
                return Action(drop=True)
            new_name = plan.renames.get(entry.name)
            return Action(rename=new_name) if new_name else None

        # Stored in reading order too (pages, then ComicInfo.xml, then
        # anything else kept), not just named in it.
        first = [*self.page_names, *([self.comicinfo_name] if self.comicinfo_name else [])]
        rank = {name: index for index, name in enumerate(first)}

        def in_reading_order(entries):
            return sorted(entries, key=lambda entry: rank.get(entry.name, len(rank)))  # stable

        rewrite_archive(
            self.path, self.path, RewritePlan(decide=decide, order=in_reading_order),
            dispose_original=dispose_original, temp_suffix=".tmp_clean", error_prefix="Could not clean up the archive",
        )

        self.page_names = [plan.renames.get(name, name) for name in self.page_names]
        if self.comicinfo_name:
            self.comicinfo_name = plan.renames.get(self.comicinfo_name, self.comicinfo_name)
            if self.stamp_only_dirty:
                self.dirty = False  # the pending stamp was written with the archive
        self._refresh_stamp_staleness()
        return plan

    # ------------------------------------------------------------------
    # Resizing (Operations > Resize Images...)
    # ------------------------------------------------------------------

    def resize_images(
        self,
        max_width: int,
        jpeg_quality: int = 90,
        output_path: Optional[str] = None,
        max_height: Optional[int] = None,
        output_format: Optional[str] = None,
        workers: Optional[int] = None,
        progress: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> ResizeSummary:
        """Rewrites every page image down to `max_width` (see
        core/image_resize.py for the double-page-spread doubling rule),
        leaving ComicInfo.xml and every other non-image entry byte-for-
        byte untouched. Same temp-file-then-rename safety as save():
        overwrites self.path in place when output_path is None,
        otherwise writes a new file and leaves the source alone.

        `max_height` and `output_format` are passed through to
        resize_page(). A format change renames each page entry to the
        new extension ("001.png" -> "001.webp"), keeping its place in
        reading order; a page whose new name would collide with another
        entry keeps its original format instead.

        Pages are decoded/encoded on a small thread pool (Pillow
        releases the GIL while it works), a bounded window at a time so
        a huge book is never held in memory all at once. Output order
        always matches the source's.

        `progress(done, total)` is called after each entry is written
        (safe to call from a worker thread: this method touches no GUI).
        `should_cancel()` is polled between chunks; when true the temp
        file is removed, the source is left as it was and ResizeCancelled
        is raised.

        Unlike save() and everything else in this module, this DOES
        re-encode pixel data -- the one deliberate exception to "images
        are always copied byte-for-byte" elsewhere in this project.
        It's also the one operation with no Undo: there's no in-memory
        original to restore once pixels have actually been re-encoded
        and written to disk, unlike a metadata edit. It only ever runs
        when the user explicitly asks for it via the Resize Images...
        dialog, never as a side effect of Save.
        """
        if self.load_error:
            raise CbzError(f"Cannot resize, file failed to load: {self.load_error}")
        if self.needs_conversion:
            raise CbzError("Cannot resize, this file needs converting to CBZ first (Convert to CBZ)")

        summary = ResizeSummary()
        target = output_path or self.path
        workers = workers or min(8, os.cpu_count() or 1)
        convertible: set[str] = set()

        def begin(entries) -> None:
            convertible.update(_renamable_pages([entry.name for entry in entries], output_format))

        def decide(entry, read):
            name = entry.name
            if not _is_image(name):
                summary.original_bytes += entry.info.file_size
                summary.new_bytes += entry.info.file_size
                return None  # copied untouched
            original = read()
            fmt = output_format if name in convertible else None
            future = pool.submit(resize_page, original, max_width, jpeg_quality, max_height, fmt)

            def finish() -> Action:
                result = future.result()
                write_name = None
                if result.extension and _needs_rename(name, output_format):
                    write_name = _renamed_for_format(name, output_format)
                if result.error:
                    summary.pages_failed += 1
                elif result.resized:
                    summary.pages_resized += 1
                else:
                    summary.pages_skipped += 1
                summary.original_bytes += len(original)
                summary.new_bytes += len(result.data)
                return Action(rename=write_name, data=result.data)

            return finish

        # A bounded window of pages is in flight at a time (see RewritePlan.window).
        plan = RewritePlan(decide=decide, begin=begin, window=workers * 2)
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                rewrite_archive(
                    self.path, target, plan, temp_suffix=".tmp_resize", error_prefix="Could not resize CBZ file",
                    progress=progress, should_cancel=should_cancel,
                )
        except RewriteCancelled as exc:
            raise ResizeCancelled("Resize cancelled") from exc

        if output_path is None or output_path == self.path:
            self.path = target
            if output_format:
                # Entries may have been renamed to a new extension --
                # re-read the page list rather than guess at it.
                # comicinfo_name/metadata are unchanged.
                with open_zip(self.path) as zf:
                    self.page_names = sorted((n for n in zf.namelist() if _is_image(n)), key=page_sort_key)
            # Re-encoded pages change their CRCs: an earlier stamp is now stale.
            self._refresh_stamp_staleness()

        return summary
