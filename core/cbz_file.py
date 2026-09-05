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
"""

from __future__ import annotations

import posixpath
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Optional

from core.comicinfo import ComicInfoError, ComicInfoMetadata, parse_comicinfo_xml, serialize_comicinfo_xml

# Recognized page image types. Readers in the wild are lenient about
# this too (a CBZ is "mostly JPGs" by convention, not by enforced
# rule) -- anything else (ComicInfo.xml itself, a stray Thumbs.db, an
# NFO file some scan groups include) is preserved on save but not
# counted as a page.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
COMICINFO_NAME = "ComicInfo.xml"


class CbzError(Exception):
    """Raised for a problem reading or writing a CBZ file."""


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
        except (zipfile.BadZipFile, KeyError, OSError, zlib.error) as exc:
            self.save_error = str(exc)
            raise CbzError(f"Could not save CBZ file: {exc}") from exc

        shutil.move(tmp_path, target)
        self.save_error = ""
        # Only treat this as "saved" (clear dirty, adopt new path) when
        # this book's own file was actually overwritten -- saving to a
        # *different* path is a copy, and the original book is still
        # exactly as dirty as before, still at its original path.
        if output_path is None or output_path == self.path:
            self.path = target
            self.comicinfo_name = write_name
            self.dirty = False
