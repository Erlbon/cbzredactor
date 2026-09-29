"""
core/collection_fix.py

Collection Report > Apply: the report's findings that can be fixed
mechanically. Nothing here is applied unless the user ticks it.

Two kinds, applied in this order:
- edits inside the archive (`fields`): PageCount recounted, a missing
  ComicInfo.xml created from the file name, or ComicInfo's Series /
  Number / Volume / Year / Month set to what the file name says. Written
  through CbzBook.save() (temp file, then swap), so a crash can't leave
  half an archive. These can't be undone from File > Undo Last Rename.
- conversions (`convert`): CBR/CB7/CBT to a verified CBZ; the original goes
  to the Recycle Bin, never deleted outright.
- renames (`new_path`): a mislabeled .cbr that is really a ZIP renamed to
  .cbz, or a file renamed to match its ComicInfo. These go through the
  same path as the report's moves, so Undo Last Rename takes them back.

A name-vs-ComicInfo disagreement offers both ways round as two rows
sharing a `choice` key: the dialog lets only one of them be ticked.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Optional

from core.cbz_file import CbzBook, CbzError
from core.collection_names import FileName, library_name
from core.collection_scan import ScanRow, full_path, long_path

KIND_PAGECOUNT = "PageCount"
KIND_NEW_COMICINFO = "New ComicInfo"
KIND_COMICINFO_FROM_NAME = "ComicInfo from name"
KIND_RENAME_FROM_COMICINFO = "Rename from ComicInfo"
KIND_EXTENSION = "Extension"
KIND_CONVERT = "Convert to CBZ"

# ScanRow / Mismatch field -> ComicInfoMetadata attribute
_ATTRIBUTE = {"Series": "series", "Number": "number", "Volume": "volume", "Year": "year", "Month": "month"}


@dataclass
class Fix:
    path: str  # relative to the scanned folder
    kind: str
    what: str  # shown to the user: what will change
    fields: dict = field(default_factory=dict)  # ComicInfoMetadata attribute -> new value; {} when pagecount only
    new_path: str = ""  # a rename instead of an edit
    choice: str = ""  # fixes sharing a choice are alternatives: only one may be applied
    convert: bool = False  # a CBR/CB7/CBT to convert to CBZ (the original goes to the Recycle Bin)

    @property
    def is_rename(self) -> bool:
        return bool(self.new_path)


def _number_for_comicinfo(number: str) -> str:
    text = number.strip().lstrip("#")
    if text.lstrip("0").isdigit():
        return text.lstrip("0")
    return text.lstrip("0") or text if text.isdigit() else text


_SIMPLE_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?[a-z]{0,2}$|^TPB", re.IGNORECASE)


def _looks_like_year(number: str) -> bool:
    return number.isdigit() and 1900 <= int(number) <= 2100


def fields_from_name(name: FileName, only: Optional[set[str]] = None) -> dict:
    """ComicInfo values the file name states. `only` limits it to some of
    "Series", "Number", "Volume", "Year", "Month"."""
    values = {
        "Series": name.series,
        "Number": "" if name.is_tpb or _looks_like_year(name.number) else _number_for_comicinfo(name.number),
        "Volume": name.volume,
        "Year": name.year,
        "Month": str(int(name.month)) if name.month.isdigit() and name.month else "",
    }
    return {_ATTRIBUTE[k]: v for k, v in values.items() if v and (only is None or k in only)}


def name_from_comicinfo(name: FileName, row: ScanRow, wanted: set[str]) -> str:
    """The file's name with the disagreeing number and date taken from
    ComicInfo instead; everything else in it stays. "" when a Series or
    Volume disagrees (a series name from ComicInfo may hold characters a
    file name can't), when ComicInfo lacks the value, or when the name
    holds brackets that would be lost."""
    if wanted - {"Number", "Year", "Month"} or name.extra_brackets or "[" in row.file:
        return ""
    number = row.number if "Number" in wanted else name.number
    if number and not _SIMPLE_NUMBER.match(number):
        return ""  # "1988-01", "233-234", "II/04": no safe way to write it back
    year = row.year if "Year" in wanted else name.year
    month = row.month if "Month" in wanted else name.month
    if not (number or name.is_tpb):
        return ""
    return library_name(
        name.series, number, name.publisher or row.publisher, year, month or "",
        name.volume, name.extensions or ".cbz", name.title,
    )


def apply_edits(root: str, path: str, fields: dict) -> None:
    """Writes `fields` into the ComicInfo of the archive at root/path and
    recounts PageCount (CbzBook.save() always does). Raises CbzError."""
    book = CbzBook(long_path(full_path(root, path)))
    if book.load_error:
        raise CbzError(book.load_error)
    if book.needs_conversion:
        raise CbzError("not a real CBZ -- convert it first")
    for attribute, value in fields.items():
        setattr(book.metadata, attribute, value)
    book.save()


def group_edits(fixes: list[Fix]) -> dict[str, dict]:
    """The in-archive fixes by file, merged: one rewrite per archive."""
    merged: dict[str, dict] = {}
    for fix in fixes:
        if not fix.is_rename and not fix.convert:
            merged.setdefault(fix.path, {}).update(fix.fields)
    return merged


def drop_conflicts(fixes: list[Fix]) -> list[Fix]:
    """Of alternatives (same `choice`) keep the first ticked; a file that
    is edited AND renamed is edited first, renamed after."""
    seen, kept = set(), []
    for fix in fixes:
        if fix.choice:
            if fix.choice in seen:
                continue
            seen.add(fix.choice)
        kept.append(fix)
    return kept


def dirname(path: str) -> str:
    return posixpath.dirname(path)
