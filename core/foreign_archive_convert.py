"""
core/foreign_archive_convert.py

Read-only support for comic archive formats other than CBZ (a plain
ZIP): CBR (RAR), CBT (tar), CB7 (7-Zip). None of these is ever written
back to in its own format -- each is converted to a real .cbz once, up
front, and every edit from then on happens on that CBZ. See README
"Foreign archive formats" for the full rationale (RAR5/7z compression
is proprietary/can't be safely round-tripped the way ZIP can; tar
*could* technically be rewritten, but comic readers and ComicInfo.xml
tooling all expect a ZIP-based container regardless, so there's no
reason to special-case it as writable).

Dependencies, per format:
- **CBR** needs the optional `rarfile` package, which itself shells
  out to a real unrar/unar/bsdtar-compatible binary on PATH -- none of
  which ships with this tool. Fails with a clear, actionable error on
  a machine without one, rather than a cryptic traceback.
- **CBT** needs nothing extra -- Python's stdlib `tarfile`.
- **CB7** needs the optional `py7zr` package -- unlike CBR, this is a
  normal pip-installable dependency with no separate binary required.

Originally just core/cbr_convert.py (CBR only); generalized here
2026-09-14 once CBT/CB7 needed the same treatment, since duplicating
the "extract to a temp dir, then zip it up" logic three times wasn't
worth it for what's really one shared shape.

Each format extracts to a temporary directory first (rather than
reading bytes into memory) -- lets zipfile stream straight from disk
via ZipFile.write() instead of holding a whole comic's worth of page
images in memory at once, and gives CBT a clean place to apply
tarfile's own path-traversal protection (see _extract_cbt) uniformly
before anything touches a real path.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
import zipfile
from typing import Optional

FOREIGN_ARCHIVE_EXTENSIONS = (".cbr", ".cbt", ".cb7")


class ForeignArchiveConversionError(Exception):
    """Raised when a CBR/CBT/CB7 file can't be converted -- either the
    archive itself is bad, or a needed optional dependency/binary is
    missing on this machine."""


def convert_to_cbz(source_path: str, output_path: Optional[str] = None) -> str:
    """Converts a .cbr/.cbt/.cb7 file to a .cbz at `output_path`
    (default: same name/location with a .cbz extension) -- the
    original file is left untouched; deleting it afterward (if the
    caller wants that) is the caller's own explicit, separate step, not
    something this function ever does itself. Returns the path written.

    Raises ForeignArchiveConversionError, with a specific and
    actionable message for each way this can fail, rather than letting
    a raw rarfile/tarfile/py7zr/zipfile exception surface unexplained.
    """
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in FOREIGN_ARCHIVE_EXTENSIONS:
        raise ForeignArchiveConversionError(f"Unsupported archive format: {ext}")

    if output_path is None:
        output_path = os.path.splitext(source_path)[0] + ".cbz"

    with tempfile.TemporaryDirectory(prefix="cbzredactor_convert_") as tmp_dir:
        if ext == ".cbr":
            _extract_cbr(source_path, tmp_dir)
        elif ext == ".cbt":
            _extract_cbt(source_path, tmp_dir)
        else:  # .cb7
            _extract_cb7(source_path, tmp_dir)

        try:
            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(tmp_dir):
                    for name in files:
                        full_path = os.path.join(root, name)
                        arcname = os.path.relpath(full_path, tmp_dir)
                        # zf.write(full_path, arcname) would use the
                        # extracted file's own mtime -- and ZIP can't
                        # represent anything before 1980, which a
                        # source archive's entries can easily be (tar
                        # in particular often defaults to the epoch,
                        # 1970, when nothing else was specified).
                        # writestr() with a plain string arcname always
                        # stamps the current time instead, sidestepping
                        # that entirely; per-page timestamps aren't
                        # meaningful for a comic archive's contents
                        # anyway.
                        with open(full_path, "rb") as f:
                            zf.writestr(arcname, f.read())
        except OSError as exc:
            raise ForeignArchiveConversionError(f"Could not write CBZ file: {exc}") from exc

    return output_path


def _extract_cbr(path: str, dest_dir: str) -> None:
    try:
        import rarfile
    except ImportError as exc:
        raise ForeignArchiveConversionError(
            "CBR support needs the optional 'rarfile' package, which "
            "isn't installed. Run: pip install rarfile"
        ) from exc
    try:
        with rarfile.RarFile(path) as rf:
            rf.extractall(dest_dir)
    except rarfile.RarCannotExec as exc:
        raise ForeignArchiveConversionError(
            "No RAR-reading tool (unrar, unar, or bsdtar) was found on "
            "PATH. Install one -- e.g. WinRAR or 7-Zip's unrar.exe -- "
            "and try again. See the project README's 'Foreign archive "
            "formats' section."
        ) from exc
    except rarfile.Error as exc:
        raise ForeignArchiveConversionError(f"Could not read CBR file: {exc}") from exc


def _extract_cbt(path: str, dest_dir: str) -> None:
    try:
        with tarfile.open(path, "r:*") as tf:
            # "data" filter (Python 3.12+) rejects path-traversal
            # members (e.g. "../../evil"), absolute paths, symlinks
            # escaping dest_dir, and device files -- exactly what a
            # hostile .cbt could otherwise use to write outside
            # dest_dir. Falls back to no filter on Python 3.10/3.11,
            # which never had this option; the app still requires an
            # OS-level unpack anyway, same residual risk any tar
            # extraction on those versions already carries.
            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest_dir, filter="data")
            else:
                tf.extractall(dest_dir)
    except tarfile.TarError as exc:
        raise ForeignArchiveConversionError(f"Could not read CBT file: {exc}") from exc


def _extract_cb7(path: str, dest_dir: str) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise ForeignArchiveConversionError(
            "CB7 support needs the optional 'py7zr' package, which "
            "isn't installed. Run: pip install py7zr"
        ) from exc
    try:
        with py7zr.SevenZipFile(path, "r") as zf:
            zf.extractall(path=dest_dir)
    except py7zr.exceptions.PasswordRequired as exc:
        raise ForeignArchiveConversionError(
            "This CB7 file is password-protected -- extract it with a "
            "password-aware tool first, then load the result."
        ) from exc
    except py7zr.exceptions.ArchiveError as exc:
        raise ForeignArchiveConversionError(f"Could not read CB7 file: {exc}") from exc
