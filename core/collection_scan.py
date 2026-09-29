"""
core/collection_scan.py

Collection > Scan Collection Folder...: a snapshot of the user's whole
organised collection, taken only when they ask for it -- cbzredactor is
a tool for incoming files, not a library manager, so nothing is
watched or kept in sync. Each comic becomes one CSV row (where it is,
its size and date, its format, its page count and the key ComicInfo
fields); core/collection_report.py finds patterns and irregularities
in it.

The CSV is saved zipped -- collection_scan.zip, holding one
collection_scan.csv (UTF-8 with a BOM, so Excel opens it as-is) --
since a big collection makes a big CSV that packs well. The scan's own
facts (the folder, when, on which computer, whether it finished) are
in the zip's comment as JSON. A new scan replaces the file only when
it has been written completely.

Paths are stored relative to the scanned folder with "/" separators,
so a scan taken on the collection's own computer can be read anywhere.

Rescanning re-reads only files whose size or date changed; a stopped
scan keeps what it read (marked unfinished), and the next scan carries
on from there.

Only a ZIP's table of contents and its ComicInfo.xml are read, never
the pages. Several files are read at once (read_comics()): opening an
archive is mostly waiting -- on Windows, for the virus scanner to check
the whole file on first open -- and those waits overlap. Other containers (RAR, 7z, PDF) are listed but not opened --
that needs outside tools, and the report flags them anyway.
"""

from __future__ import annotations

import csv
import glob
import hashlib
import io
import json
import os
import platform
import posixpath
import time
import zipfile
import zlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, fields
from typing import Callable, Iterator, Optional

from core.archive_sniff import CONTAINER_ZIP, detect_container, extension_label
from core.cbz_file import _is_image, page_sort_key
from core.comicinfo import ComicInfoError, parse_comicinfo_xml
from core.cover_stamp import make_stamp, read_stamp, write_comment

SCAN_FILE_NAME = "collection_scan.zip"
CSV_NAME = "collection_scan.csv"
COMIC_EXTENSIONS = {".cbz", ".cbr", ".cb7", ".cbt", ".zip", ".rar", ".7z", ".pdf"}
_SKIP_DIRS = {"$recycle.bin", "system volume information", "__macosx", ".git"}
WINDOWS_PATH_LIMIT = 260
# Files read at once. Measured 2026-09-29 on fresh 8 MB CBZs on an SSD
# with Defender's real-time protection on: 1 -> 27 ms/file, 4 -> 5.5,
# 8 -> 2.7, 16 -> 1.9. A user's first scan read ~1 file/s one at a time.
READ_WORKERS = 8


@dataclass
class ScanRow:
    path: str  # relative to the scanned folder, "/" separators
    size: int = 0
    modified: str = ""  # "2026-09-29 14:02:11", local time
    format: str = ""  # "CBZ", "CBR → ZIP", "PDF"
    container: str = ""  # core/archive_sniff.py's CONTAINER_*
    pages: str = ""  # images in the archive; "" when it wasn't opened
    comicinfo: str = ""  # "yes" / "no" / "bad"; "" when it wasn't opened
    series: str = ""
    number: str = ""
    volume: str = ""
    year: str = ""
    month: str = ""
    publisher: str = ""
    ci_format: str = ""
    page_count: str = ""
    error: str = ""
    cover: str = ""  # the cover's visual fingerprint (16 hex digits); "" when not taken, or a blank cover
    cover_key: str = ""  # the cover entry's CRC32 and size (free from the ZIP): the same image, wherever the file goes

    @property
    def folder(self) -> str:
        return posixpath.dirname(self.path)

    @property
    def file(self) -> str:
        return posixpath.basename(self.path)


COLUMNS = [f.name for f in fields(ScanRow)]


@dataclass
class ScanInfo:
    root: str
    scanned: str = ""  # "2026-09-29 14:02"
    computer: str = ""
    complete: bool = True
    app_version: str = ""


@dataclass
class Listed:
    path: str  # relative, "/"
    size: int
    modified: str


class ScanFileError(Exception):
    """collection_scan.zip is missing its CSV or can't be read."""


def long_path(path: str) -> str:
    """Windows refuses paths over 260 characters unless they carry the
    "\\\\?\\" prefix -- deep collections have them."""
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def full_path(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def _stamp(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(seconds)))


def list_comics(root: str, cancelled: Callable[[], bool] = lambda: False) -> Iterator[Listed]:
    """Every comic under `root`, depth first (unsorted). Unreadable
    folders are skipped; stops early when `cancelled()` says so."""
    start = long_path(os.path.abspath(root))
    stack = [(start, "")]
    while stack:
        if cancelled():
            return
        directory, rel = stack.pop()
        try:
            with os.scandir(directory) as entries:
                items = list(entries)
        except OSError:
            continue
        for entry in items:
            name = entry.name
            child_rel = f"{rel}/{name}" if rel else name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name.lower() not in _SKIP_DIRS:
                        stack.append((entry.path, child_rel))
                    continue
                if os.path.splitext(name)[1].lower() not in COMIC_EXTENSIONS:
                    continue
                stat = entry.stat()
            except OSError:
                continue
            yield Listed(child_rel, stat.st_size, _stamp(stat.st_mtime))


def cover_fingerprint(archive: zipfile.ZipFile, names: list[str],
                      known: Optional[dict[str, str]] = None) -> tuple[str, str]:
    """(fingerprint, key) of the first page. The fingerprint is its dHash
    (core/credit_pages.py, robust to resizing and re-encoding) as 16 hex
    digits, "" when there is no readable, non-blank first page. The key is
    the entry's CRC32 and size; a cover whose key is in `known` (key ->
    fingerprint, from earlier scans) isn't decoded again -- a file that was
    moved, renamed or had its ComicInfo edited keeps its fingerprint."""
    from PIL import UnidentifiedImageError

    from core.credit_pages import dhash, is_plain, open_page_image

    pages = sorted((n for n in names if _is_image(n)), key=page_sort_key)
    if not pages:
        return "", ""
    info = archive.getinfo(pages[0])
    key = f"{info.CRC:08x}-{info.file_size}"
    stamp = read_stamp(archive.comment)  # written into the file by an earlier scan
    if stamp and stamp[1] == key:
        return stamp[0], key
    if known and key in known:
        return known[key], key
    try:
        image = open_page_image(archive.read(pages[0]))
        return ("" if is_plain(image) else f"{dhash(image):016x}"), key
    except (KeyError, OSError, UnidentifiedImageError, zlib.error, ValueError, zipfile.BadZipFile,
            EOFError, RuntimeError):
        return "", key


def read_comic(root: str, listed: Listed, cover: bool = False,
               known_covers: Optional[dict[str, str]] = None, stamp: bool = False) -> ScanRow:
    """_read_comic(), then -- with `stamp` -- the cover fingerprint written
    into the CBZ itself (core/cover_stamp.py) when it isn't there yet.
    Only an archive with no comment at all is touched."""
    row = _read_comic(root, listed, cover, known_covers)
    if stamp and row.cover and row.cover_key and not row.error and row.container == CONTAINER_ZIP:
        path = long_path(full_path(root, listed.path))
        try:
            with zipfile.ZipFile(path) as archive:
                empty = not archive.comment
            if empty:
                write_comment(path, make_stamp(row.cover, row.cover_key))
                status = os.stat(path)
                row.size, row.modified = status.st_size, _stamp(status.st_mtime)
        except (OSError, zipfile.BadZipFile):
            pass  # not stamped; the scan still has its fingerprint
    return row


def _read_comic(root: str, listed: Listed, cover: bool = False,
                known_covers: Optional[dict[str, str]] = None) -> ScanRow:
    """One comic's row: format, and for a ZIP its page count and the
    key ComicInfo fields. Problems end up in `error`, never raised."""
    row = ScanRow(listed.path, listed.size, listed.modified)
    path = long_path(full_path(root, listed.path))
    if listed.path.lower().endswith(".pdf"):
        row.format = "PDF"
        return row
    row.container = detect_container(path)
    row.format = extension_label(listed.path, row.container)
    if row.container != CONTAINER_ZIP:
        return row
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            row.pages = str(sum(1 for n in names if _is_image(n)))
            if cover:
                row.cover, row.cover_key = cover_fingerprint(archive, names, known_covers)
            info_names = [n for n in names if posixpath.basename(n).lower() == "comicinfo.xml"]
            if not info_names:
                row.comicinfo = "no"
                return row
            info_names.sort(key=lambda n: n.count("/"))  # the one at the top level first
            data = archive.read(info_names[0])
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, EOFError, zlib.error) as exc:
        # zlib.error / EOFError: a damaged or cut-off ComicInfo.xml entry.
        # Raised here, it ended the whole collection scan.
        row.error = f"can't be opened: {exc}"
        return row
    try:
        meta = parse_comicinfo_xml(data)
    except ComicInfoError as exc:
        row.comicinfo = "bad"
        row.error = str(exc)
        return row
    row.comicinfo = "yes"
    for attr in ("series", "number", "volume", "year", "month", "publisher", "page_count"):
        setattr(row, attr, (getattr(meta, attr) or "").strip())
    row.ci_format = (meta.format or "").strip()
    return row


def read_comics(
    root: str,
    items: list[Listed],
    progress: Callable[[int, int], None] = lambda done, total: None,
    should_cancel: Callable[[], bool] = lambda: False,
    workers: int = READ_WORKERS,
    cover: bool = False,
    known_covers: Optional[dict[str, str]] = None,
    stamp: bool = False,
) -> tuple[dict[str, ScanRow], bool]:
    """read_comic() for every item, `workers` at a time. Returns the rows
    by path and whether every item was read -- after a cancel, only the
    ones finished so far (the few already being read are waited for).
    `progress(done, total)` is called on this thread, so it can pump
    the GUI's event loop."""
    rows: dict[str, ScanRow] = {}
    done = 0
    queue = iter(items)
    running: set = set()
    with ThreadPoolExecutor(max(1, workers)) as pool:
        def top_up() -> None:
            # A bounded queue, so a cancel only waits for what's running.
            while len(running) < workers * 2:
                item = next(queue, None)
                if item is None:
                    return
                running.add(pool.submit(read_comic, root, item, cover, known_covers, stamp))

        top_up()
        while running:
            if should_cancel():
                for future in running:
                    future.cancel()
                break
            finished, _ = wait(running, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in finished:
                running.discard(future)
                row = future.result()
                rows[row.path] = row
                done += 1
            progress(done, len(items))
            top_up()
    return rows, done == len(items)

def reusable(previous: dict[str, ScanRow], listed: Listed, need_cover: bool = False) -> Optional[ScanRow]:
    """The previous scan's row for this file, if it hasn't changed -- and,
    when covers are wanted, already has one (or has no cover to take: not
    a ZIP, or no pages)."""
    old = previous.get(listed.path)
    if old is not None and old.size == listed.size and old.modified == listed.modified:
        if need_cover and not old.cover and old.container == CONTAINER_ZIP and old.pages != "0":
            return None
        return old
    return None


def new_info(root: str, complete: bool, app_version: str = "") -> ScanInfo:
    return ScanInfo(
        root=os.path.abspath(root), scanned=time.strftime("%Y-%m-%d %H:%M"),
        computer=platform.node(), complete=complete, app_version=app_version,
    )


def write_scan(zip_path: str, info: ScanInfo, rows: list[ScanRow]) -> None:
    """Writes the zip next to the old one first, then swaps it in -- a
    crash mid-write leaves the previous scan untouched."""
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(COLUMNS)
    for row in sorted(rows, key=lambda r: r.path.casefold()):
        writer.writerow([getattr(row, c) for c in COLUMNS])
    temp = zip_path + ".tmp"
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(CSV_NAME, "﻿" + text.getvalue())
        archive.comment = json.dumps(asdict(info)).encode("utf-8")
    os.replace(temp, zip_path)


def stash_path(zip_path: str, root: str) -> str:
    """Where the scan of `root` is kept while another folder's scan is the
    current one: next to `zip_path`, named after the folder."""
    key = hashlib.sha1(os.path.normcase(os.path.abspath(root)).encode("utf-8")).hexdigest()[:10]
    base, ext = os.path.splitext(zip_path)
    return f"{base}_{key}{ext}"


def stash_current(zip_path: str, info: ScanInfo) -> None:
    """Sets the current scan (of info.root) aside under its own name, so a
    scan of another folder doesn't overwrite it."""
    os.replace(zip_path, stash_path(zip_path, info.root))


def stashed_scans(zip_path: str) -> list[tuple[str, ScanInfo]]:
    """(path, info) of every set-aside scan, newest first."""
    base, ext = os.path.splitext(zip_path)
    found = []
    for path in glob.glob(f"{glob.escape(base)}_*{ext}"):
        try:
            with zipfile.ZipFile(path) as archive:
                raw = json.loads(archive.comment.decode("utf-8") or "{}")
            info = ScanInfo(**{k: v for k, v in raw.items() if k in ScanInfo.__dataclass_fields__})
        except (OSError, zipfile.BadZipFile, ValueError, TypeError):
            continue
        found.append((path, info))
    return sorted(found, key=lambda item: item[1].scanned, reverse=True)


def read_scan(zip_path: str) -> tuple[ScanInfo, list[ScanRow]]:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            try:
                raw = json.loads(archive.comment.decode("utf-8") or "{}")
            except ValueError:
                raw = {}
            info = ScanInfo(**{k: v for k, v in raw.items() if k in ScanInfo.__dataclass_fields__})
            with archive.open(CSV_NAME) as handle:
                reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline=""))
                rows = []
                for record in reader:
                    values = {c: record.get(c) or "" for c in COLUMNS}
                    values["size"] = int(values["size"] or 0)
                    rows.append(ScanRow(**values))
    except (OSError, KeyError, zipfile.BadZipFile, ValueError, TypeError) as exc:
        raise ScanFileError(f"{os.path.basename(zip_path)} can't be read: {exc}") from exc
    return info, rows
