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
import shutil
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from redactor_common.core.save_errors import describe_save_error

from core.archive_sniff import CONTAINER_UNKNOWN, CONTAINER_ZIP, detect_container
from core.comicinfo import ComicInfoError, ComicInfoMetadata, parse_comicinfo_xml, serialize_comicinfo_xml
from core.image_resize import OUTPUT_FORMAT_EXTENSIONS, resize_page

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


class CbzError(Exception):
    """Raised for a problem reading or writing a CBZ file."""


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


def _is_image(name: str) -> bool:
    return posixpath.splitext(name)[1].lower() in IMAGE_EXTENSIONS


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


def _find_comicinfo_name(names: list[str]) -> Optional[str]:
    """Case-insensitive match at the archive root only -- some writers
    use lowercase comicinfo.xml; a handful nest one in a subfolder,
    which is treated here as "not found" since readers that expect it
    at the root wouldn't see it there either."""
    for name in names:
        if "/" not in name and name.lower() == COMICINFO_NAME.lower():
            return name
    return None


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
    save_error: str = ""
    dirty: bool = False  # set by the GUI layer when a field is edited; cleared on save()
    # What the file really is (core/archive_sniff.py), whatever its
    # extension says.
    container: str = CONTAINER_ZIP

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
            with zipfile.ZipFile(self.path, "r") as zf:
                names = zf.namelist()
                self.page_names = sorted(n for n in names if _is_image(n))
                self.comicinfo_name = _find_comicinfo_name(names)
                if self.comicinfo_name:
                    self.metadata = parse_comicinfo_xml(zf.read(self.comicinfo_name))
                else:
                    self.metadata = ComicInfoMetadata()
        except (zipfile.BadZipFile, KeyError, OSError, zlib.error) as exc:
            self.load_error = str(exc)
        except ComicInfoError as exc:
            # A malformed ComicInfo.xml shouldn't sink the whole file --
            # the pages are still perfectly readable. Surfaced as a load
            # error so the GUI can flag it rather than silently editing
            # blank metadata over top of whatever was actually there.
            self.load_error = str(exc)

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
            with zipfile.ZipFile(self.path, "r") as zf:
                return zf.read(self.first_page_name)
        except (zipfile.BadZipFile, KeyError, OSError, zlib.error):
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
        tmp_path = target + ".tmp_write"
        # Preserve the original entry's name/case if there was one;
        # otherwise write the spec-correct name fresh.
        write_name = self.comicinfo_name or COMICINFO_NAME

        try:
            with zipfile.ZipFile(self.path, "r") as src:
                names = src.namelist()
                infos = {info.filename: info for info in src.infolist()}

                with zipfile.ZipFile(tmp_path, "w") as dst:
                    for name in names:
                        if name == self.comicinfo_name:
                            continue  # rewritten below with updated bytes
                        info = infos[name]
                        new_info = zipfile.ZipInfo(name, date_time=info.date_time)
                        new_info.compress_type = info.compress_type
                        new_info.external_attr = info.external_attr
                        dst.writestr(new_info, src.read(name))
                    dst.writestr(write_name, new_xml_bytes)
            shutil.move(tmp_path, target)
        except (zipfile.BadZipFile, KeyError, OSError, zlib.error) as exc:
            # describe_save_error() recognizes Windows' path-too-long
            # limit specifically -- worth knowing here since tmp_path
            # (target + ".tmp_write") is 10 characters longer than the
            # real target, so this can fail even when the final,
            # shorter path would have just barely fit.
            message = describe_save_error(exc)
            self.save_error = message
            raise CbzError(f"Could not save CBZ file: {message}") from exc

        self.save_error = ""
        # Only treat this as "saved" (clear dirty, adopt new path) when
        # this book's own file was actually overwritten -- saving to a
        # *different* path is a copy, and the original book is still
        # exactly as dirty as before, still at its original path.
        if output_path is None or output_path == self.path:
            self.path = target
            self.comicinfo_name = write_name
            self.dirty = False

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
        if self.dirty:
            raise CbzError("Save or undo this file's unsaved changes first")
        remove = set(names) & set(self.page_names)
        if not remove:
            return 0

        removed_positions = sorted(i for i, name in enumerate(self.page_names) if name in remove)
        new_pages = [name for name in self.page_names if name not in remove]
        metadata = copy.deepcopy(self.metadata)
        metadata.page_count = str(len(new_pages))
        _remap_pages_element(metadata, removed_positions)

        tmp_path = self.path + ".tmp_pages"
        try:
            with zipfile.ZipFile(self.path, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
                for info in src.infolist():
                    if info.filename in remove:
                        continue
                    if info.filename == self.comicinfo_name:
                        dst.writestr(info.filename, serialize_comicinfo_xml(metadata))
                        continue
                    new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    new_info.compress_type = info.compress_type
                    new_info.external_attr = info.external_attr
                    dst.writestr(new_info, src.read(info.filename))
            if dispose_original is not None:
                dispose_original(self.path)
            os.replace(tmp_path, self.path)
        except Exception as exc:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            if isinstance(exc, (zipfile.BadZipFile, KeyError, OSError, zlib.error)):
                raise CbzError(f"Could not remove pages: {describe_save_error(exc)}") from exc
            raise

        self.page_names = new_pages
        if self.comicinfo_name:
            self.metadata = metadata
        return len(removed_positions)

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
        tmp_path = target + ".tmp_resize"
        workers = workers or min(8, os.cpu_count() or 1)

        def _process(name: str, original: bytes, fmt: Optional[str]):
            return resize_page(original, max_width, jpeg_quality, max_height, fmt)

        try:
            with zipfile.ZipFile(self.path, "r") as src:
                names = src.namelist()
                infos = {info.filename: info for info in src.infolist()}
                convertible = _renamable_pages(names, output_format)

                with zipfile.ZipFile(tmp_path, "w") as dst, ThreadPoolExecutor(max_workers=workers) as pool:
                    window = workers * 2
                    for start in range(0, len(names), window):
                        chunk = names[start:start + window]
                        pending = []
                        for name in chunk:
                            original = src.read(name)
                            future = None
                            if _is_image(name):
                                fmt = output_format if name in convertible else None
                                future = pool.submit(_process, name, original, fmt)
                            pending.append((name, original, future))

                        for name, original, future in pending:
                            data_to_write = original
                            write_name = name
                            if future is not None:
                                result = future.result()
                                data_to_write = result.data
                                if result.extension and _needs_rename(name, output_format):
                                    write_name = _renamed_for_format(name, output_format)
                                if result.error:
                                    summary.pages_failed += 1
                                elif result.resized:
                                    summary.pages_resized += 1
                                else:
                                    summary.pages_skipped += 1

                            summary.original_bytes += len(original)
                            summary.new_bytes += len(data_to_write)

                            info = infos[name]
                            new_info = zipfile.ZipInfo(write_name, date_time=info.date_time)
                            new_info.compress_type = info.compress_type
                            new_info.external_attr = info.external_attr
                            dst.writestr(new_info, data_to_write)
            shutil.move(tmp_path, target)
        except (zipfile.BadZipFile, KeyError, OSError, zlib.error) as exc:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise CbzError(f"Could not resize CBZ file: {describe_save_error(exc)}") from exc

        if output_path is None or output_path == self.path:
            self.path = target
            if output_format:
                # Entries may have been renamed to a new extension --
                # re-read the page list rather than guess at it.
                # comicinfo_name/metadata are unchanged.
                with zipfile.ZipFile(self.path, "r") as zf:
                    self.page_names = sorted(n for n in zf.namelist() if _is_image(n))

        return summary
