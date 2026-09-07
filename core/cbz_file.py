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

CBR (RAR-based) archives are out of scope here entirely -- see
core/cbr_convert.py, which converts a .cbr to a real .cbz *before* this
module ever sees it, rather than this module learning to read RAR.

save() is the only method that runs implicitly (whenever the user hits
Save); it keeps the "images copied byte-for-byte" guarantee. The one
deliberate exception is resize_images() (see core/image_resize.py),
which re-encodes page pixel data -- it only ever runs when the user
explicitly asks for it via Operations > Resize Images..., never as a
side effect of a normal save.
"""

from __future__ import annotations

import posixpath
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Optional

from redactor_common.core.save_errors import describe_save_error

from core.comicinfo import ComicInfoError, ComicInfoMetadata, parse_comicinfo_xml, serialize_comicinfo_xml
from core.image_resize import resize_page

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

    def __post_init__(self) -> None:
        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
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
    # Resizing (Operations > Resize Images...)
    # ------------------------------------------------------------------

    def resize_images(
        self, max_width: int, jpeg_quality: int = 90, output_path: Optional[str] = None
    ) -> ResizeSummary:
        """Rewrites every page image down to `max_width` (see
        core/image_resize.py for the double-page-spread doubling rule),
        leaving ComicInfo.xml and every other non-image entry byte-for-
        byte untouched. Same temp-file-then-rename safety as save():
        overwrites self.path in place when output_path is None,
        otherwise writes a new file and leaves the source alone.

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

        summary = ResizeSummary()
        target = output_path or self.path
        tmp_path = target + ".tmp_resize"

        try:
            with zipfile.ZipFile(self.path, "r") as src:
                names = src.namelist()
                infos = {info.filename: info for info in src.infolist()}

                with zipfile.ZipFile(tmp_path, "w") as dst:
                    for name in names:
                        original = src.read(name)
                        data_to_write = original

                        if _is_image(name):
                            result = resize_page(original, max_width, jpeg_quality)
                            data_to_write = result.data
                            if result.error:
                                summary.pages_failed += 1
                            elif result.resized:
                                summary.pages_resized += 1
                            else:
                                summary.pages_skipped += 1

                        summary.original_bytes += len(original)
                        summary.new_bytes += len(data_to_write)

                        info = infos[name]
                        new_info = zipfile.ZipInfo(name, date_time=info.date_time)
                        new_info.compress_type = info.compress_type
                        new_info.external_attr = info.external_attr
                        dst.writestr(new_info, data_to_write)
            shutil.move(tmp_path, target)
        except (zipfile.BadZipFile, KeyError, OSError, zlib.error) as exc:
            raise CbzError(f"Could not resize CBZ file: {describe_save_error(exc)}") from exc

        if output_path is None or output_path == self.path:
            self.path = target
            # page_names/comicinfo_name/metadata are all unchanged --
            # resizing only ever shrinks existing pages' pixels, it
            # never adds, removes, or renames an archive entry.

        return summary
