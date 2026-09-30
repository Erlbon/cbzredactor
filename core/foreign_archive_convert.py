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
import shutil
import tarfile
import tempfile
import zipfile
import zlib
from typing import Optional

from core.archive_sniff import (
    CONTAINER_RAR,
    CONTAINER_TAR,
    CONTAINER_UNKNOWN,
    CONTAINER_ZIP,
    EXPECTED_CONTAINER,
    detect_container,
)

from core.cbz_file import IMAGE_EXTENSIONS

FOREIGN_ARCHIVE_EXTENSIONS = (".cbr", ".cbt", ".cb7")


class ForeignArchiveConversionError(Exception):
    """Raised when a CBR/CBT/CB7 file can't be converted -- either the
    archive itself is bad, or a needed optional dependency/binary is
    missing on this machine."""


def convert_to_cbz(source_path: str, output_path: Optional[str] = None) -> str:
    """Converts a comic archive to a .cbz at `output_path` (default: same
    name/location with a .cbz extension) -- the original file is left
    untouched; deleting it afterward (if the caller wants that) is the
    caller's own explicit, separate step, not something this function
    ever does itself. Returns the path written.

    The method comes from what the file really contains
    (core/archive_sniff.py), falling back to the extension only when
    the content isn't recognized: a ".cbr" that's really a ZIP is simply
    copied under the new name, never unpacked and repacked.

    Refuses to overwrite an existing file at `output_path`, and checks
    the written CBZ opens and holds as many page images as were
    extracted -- a caller may delete the original on the strength of
    this function returning normally.

    Raises ForeignArchiveConversionError, with a specific and
    actionable message for each way this can fail, rather than letting
    a raw rarfile/tarfile/py7zr/zipfile exception surface unexplained.
    """
    ext = os.path.splitext(source_path)[1].lower()
    container = detect_container(source_path)
    if container == CONTAINER_UNKNOWN:
        if ext not in FOREIGN_ARCHIVE_EXTENSIONS:
            raise ForeignArchiveConversionError(f"Unsupported archive format: {ext}")
        container = EXPECTED_CONTAINER[ext]

    if output_path is None:
        output_path = os.path.splitext(source_path)[0] + ".cbz"
    if os.path.normcase(os.path.abspath(output_path)) == os.path.normcase(os.path.abspath(source_path)):
        raise ForeignArchiveConversionError(
            "The file is already named .cbz but isn't a ZIP inside -- rename it "
            "to its real format first (see relabel_mislabeled_cbz())."
        )
    if os.path.exists(output_path):
        raise ForeignArchiveConversionError(
            f"{os.path.basename(output_path)} already exists -- not overwriting it."
        )

    if container == CONTAINER_ZIP:
        _verify_cbz(source_path, None)
        # Copy to a temp name first: a copy cut short (disk full) must
        # never leave a truncated file under the final .cbz name.
        tmp_copy = output_path + ".tmp_convert"
        try:
            shutil.copy2(source_path, tmp_copy)
            os.replace(tmp_copy, output_path)
        except OSError as exc:
            try:
                os.remove(tmp_copy)
            except OSError:
                pass
            raise ForeignArchiveConversionError(f"Could not write CBZ file: {exc}") from exc
        return output_path

    tmp_output = output_path + ".tmp_convert"
    with tempfile.TemporaryDirectory(prefix="cbzredactor_convert_") as tmp_dir:
        if container == CONTAINER_RAR:
            _extract_cbr(source_path, tmp_dir)
        elif container == CONTAINER_TAR:
            _extract_cbt(source_path, tmp_dir)
        else:  # CONTAINER_7Z
            _extract_cb7(source_path, tmp_dir)

        extracted_pages = sum(
            1 for _root, _dirs, files in os.walk(tmp_dir) for name in files if _is_page_name(name)
        )

        try:
            with zipfile.ZipFile(tmp_output, "w", zipfile.ZIP_DEFLATED) as zf:
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
            _verify_cbz(tmp_output, extracted_pages)
            os.replace(tmp_output, output_path)
        except (OSError, zipfile.BadZipFile, ForeignArchiveConversionError) as exc:
            try:
                os.remove(tmp_output)
            except OSError:
                pass
            if isinstance(exc, ForeignArchiveConversionError):
                raise
            raise ForeignArchiveConversionError(f"Could not write CBZ file: {exc}") from exc

    return output_path


def relabel_mislabeled_cbz(path: str) -> str:
    """A ".cbz" that's really a RAR/7z/tar: renames it to the extension
    its content actually is ("book.cbz" -> "book.cbr"), so it can then be
    converted normally to a real "book.cbz". Returns the new path.
    Raises ForeignArchiveConversionError if the content is unrecognized
    or the new name is taken."""
    container = detect_container(path)
    new_ext = {v: k for k, v in EXPECTED_CONTAINER.items()}.get(container)
    if container in (CONTAINER_ZIP, CONTAINER_UNKNOWN) or new_ext is None:
        raise ForeignArchiveConversionError("Can't tell what format this file really is.")
    new_path = os.path.splitext(path)[0] + new_ext
    if os.path.exists(new_path):
        raise ForeignArchiveConversionError(
            f"{os.path.basename(new_path)} already exists -- can't rename the mislabeled file to it."
        )
    try:
        os.rename(path, new_path)
    except OSError as exc:
        raise ForeignArchiveConversionError(f"Could not rename mislabeled file: {exc}") from exc
    return new_path


def _is_page_name(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS


def _verify_cbz(path: str, expected_pages: Optional[int]) -> None:
    """The ZIP at `path` opens, its entries pass CRC checks, and (when
    `expected_pages` is given) it holds exactly that many page images."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            pages = sum(1 for n in zf.namelist() if _is_page_name(n))
    except (zipfile.BadZipFile, OSError, zlib.error) as exc:
        raise ForeignArchiveConversionError(f"Converted CBZ failed its check: {exc}") from exc
    if bad is not None:
        raise ForeignArchiveConversionError(f"Converted CBZ failed its check: corrupt entry {bad}")
    if expected_pages is not None and pages != expected_pages:
        raise ForeignArchiveConversionError(
            f"Converted CBZ failed its check: {pages} page(s) written, {expected_pages} extracted"
        )


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


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
    except ForeignArchiveConversionError:
        raise
    except rarfile.Error as exc:
        raise ForeignArchiveConversionError(f"Could not read CBR file: {exc}") from exc
    except Exception as exc:
        # Plain OSError (disk full, an entry name Windows can't hold) and
        # anything the library doesn't wrap: fail this file, not the batch.
        raise ForeignArchiveConversionError(f"Could not extract CBR file: {_describe(exc)}") from exc


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
                _extract_tar_safely(tf, dest_dir)
    except tarfile.TarError as exc:
        raise ForeignArchiveConversionError(f"Could not read CBT file: {exc}") from exc
    except Exception as exc:
        raise ForeignArchiveConversionError(f"Could not extract CBT file: {_describe(exc)}") from exc


def _extract_tar_safely(tf: tarfile.TarFile, dest_dir: str) -> None:
    """Stand-in for extractall(filter="data") on Python < 3.12: only
    regular files and folders whose resolved path stays under dest_dir
    are extracted; links, devices and "../" or absolute names are
    skipped (a hostile .cbt could otherwise write anywhere)."""
    root = os.path.realpath(dest_dir)
    safe = []
    for member in tf.getmembers():
        if not (member.isfile() or member.isdir()):
            continue
        target = os.path.realpath(os.path.join(root, member.name))
        if target != root and not target.startswith(root + os.sep):
            continue
        safe.append(member)
    tf.extractall(dest_dir, members=safe)


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
    except Exception as exc:
        # lzma.LZMAError, OSError, ... -- not py7zr's own error classes.
        raise ForeignArchiveConversionError(f"Could not extract CB7 file: {_describe(exc)}") from exc
