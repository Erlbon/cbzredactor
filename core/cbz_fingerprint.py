"""
core/cbz_fingerprint.py

The content fingerprint a scan stamp carries, so a later run can tell
whether the pages changed since the scan. Not redactor_common's
content_fingerprint(): that hashes sampled bytes of the file, and saving a
CBZ rewrites ComicInfo.xml and shifts every later byte.

Instead this hashes the ZIP central directory's (name, CRC32, size) for
every entry EXCEPT the root ComicInfo.xml. No entry data is read, so it is
as cheap as listing the archive. A metadata-only save leaves it unchanged;
replacing, adding, removing, renaming or re-encoding any page (or any other
entry) changes it. Entry order is ignored (sorted by name).
"""

from __future__ import annotations

import hashlib
import zipfile

_COMICINFO_LOWER = "comicinfo.xml"
_ZIP_ERRORS = (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError)


def cbz_fingerprint(path: str) -> str:
    """"<entries>-<hash>" for the archive at `path`, or "" when it can't
    be read as a ZIP."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            entries = sorted(
                (info.filename, info.CRC, info.file_size)
                for info in zf.infolist()
                if not ("/" not in info.filename and info.filename.lower() == _COMICINFO_LOWER)
            )
    except _ZIP_ERRORS:
        return ""
    digest = hashlib.sha256()
    for name, crc, size in entries:
        digest.update(f"{name}\0{crc}\0{size}\n".encode("utf-8", "surrogatepass"))
    return f"{len(entries)}-{digest.hexdigest()[:16]}"
