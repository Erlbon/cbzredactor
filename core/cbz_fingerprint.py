"""
core/cbz_fingerprint.py

The content fingerprint a scan stamp carries, so a later run can tell
whether the pages changed since the scan. Not redactor_common's
content_fingerprint(): that hashes sampled bytes of the file, and saving a
CBZ rewrites ComicInfo.xml and shifts every later byte.

Instead this hashes the ZIP central directory's (name, CRC32, size) for
every entry EXCEPT the ComicInfo.xml the editor reads (the root one, else a
nested one; core/comicinfo_locate.py), under the entry names the app shows
(core/zip_names.py), so a Save that repairs legacy names keeps a stamp valid.
No entry data is read, so it is
as cheap as listing the archive. A metadata-only save leaves it unchanged;
replacing, adding, removing, renaming or re-encoding any page (or any other
entry) changes it. Entry order is ignored (sorted by name).
"""

from __future__ import annotations

import hashlib
import zipfile

from core.comicinfo_locate import find_comicinfo_entry
from core.zip_names import open_zip

_ZIP_ERRORS = (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError)


def cbz_fingerprint(path: str) -> str:
    """"<entries>-<hash>" for the archive at `path`, or "" when it can't
    be read as a ZIP."""
    try:
        with open_zip(path) as zf:
            infos = zf.infolist()
            skipped = find_comicinfo_entry(info.filename for info in infos)
            entries = sorted(
                (info.filename, info.CRC, info.file_size) for info in infos if info.filename != skipped
            )
    except _ZIP_ERRORS:
        return ""
    digest = hashlib.sha256()
    for name, crc, size in entries:
        digest.update(f"{name}\0{crc}\0{size}\n".encode("utf-8", "surrogatepass"))
    return f"{len(entries)}-{digest.hexdigest()[:16]}"
