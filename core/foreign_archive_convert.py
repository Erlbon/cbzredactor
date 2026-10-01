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
import threading
import time
import zipfile
import zlib
from collections import deque
from dataclasses import fields
from typing import Callable, Optional

from core.archive_sniff import (
    CONTAINER_RAR,
    CONTAINER_TAR,
    CONTAINER_UNKNOWN,
    CONTAINER_ZIP,
    EXPECTED_CONTAINER,
    detect_container,
)

from core.cbz_file import (
    IMAGE_EXTENSIONS,
    STORED_EXTENSIONS,
    PageResizer,
    ResizeCancelled,
    ResizeSummary,
    _is_image,
    resize_zip,
)
from core.image_resize import ResizeOptions
from core.zip_rewrite import CbzError

FOREIGN_ARCHIVE_EXTENSIONS = (".cbr", ".cbt", ".cb7")

# STORED_EXTENSIONS (page formats stored, not deflated again) lives in core/cbz_file.py,
# shared with Resize; re-exported here.

_CHUNK = 1024 * 1024
_REPORT_INTERVAL = 0.1  # seconds between progress callbacks


class ForeignArchiveConversionError(Exception):
    """Raised when a CBR/CBT/CB7 file can't be converted -- either the
    archive itself is bad, or a needed optional dependency/binary is
    missing on this machine."""


class ConversionCancelled(ForeignArchiveConversionError):
    """The caller's should_cancel() asked to stop. Nothing is left behind: no
    half-written .cbz (it is only ever written under a temporary name and
    renamed into place once complete and verified) and no extracted files."""


# progress(done_bytes, total_bytes, stage): total_bytes is 0 when the archive's
# size could not be learned up front (show a busy indicator instead of a bar).
ProgressCallback = Callable[[int, int, str], None]


class _Progress:
    """Byte-level progress over the three passes of a conversion (extract,
    pack into the zip, check the result), plus the cancel check. Safe to call
    from the worker thread; callbacks are throttled."""

    STAGES = ("Extracting", "Packing", "Checking")

    def __init__(self, callback: Optional[ProgressCallback], should_cancel: Optional[Callable[[], bool]]):
        self._callback = callback
        self._should_cancel = should_cancel
        self.expected = 0  # uncompressed bytes the archive holds (0: unknown)
        self._last = 0.0
        self._stage = -1

    @property
    def active(self) -> bool:
        return self._callback is not None or self._should_cancel is not None

    def check(self) -> None:
        if self._should_cancel is not None and self._should_cancel():
            raise ConversionCancelled("Conversion cancelled.")

    def report(self, stage: int, done: int, label: str = "") -> None:
        if self._callback is None:
            return
        now = time.monotonic()
        if stage == self._stage and now - self._last < _REPORT_INTERVAL:
            return
        self._stage, self._last = stage, now
        label = label or self.STAGES[stage]
        if self.expected <= 0:
            self._callback(0, 0, label)
            return
        total = len(self.STAGES) * self.expected
        self._callback(stage * self.expected + min(done, self.expected), total, label)


def convert_to_cbz(
    source_path: str,
    output_path: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    resize: Optional[ResizeOptions] = None,
    resize_summary: Optional[ResizeSummary] = None,
) -> str:
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

    `progress(done, total, stage)` and `should_cancel()` are optional and meant
    for a worker thread behind a progress dialog: progress is in bytes over
    extract / pack / check, and cancelling raises ConversionCancelled after
    removing every temporary file (the .cbz only appears, whole and verified,
    at the very end). Extracting a CBR with an external RAR tool is one call
    that cannot be interrupted (nor can py7zr's), so a cancel there takes
    effect when the extraction ends; tar stops between files.

    With `resize` (a ResizeOptions) the pages are resized in the SAME pass --
    extract, then each page is resized as it is packed (core/image_resize.py's
    rules, on a thread pool), then one check -- instead of converting and
    rewriting the whole archive a second time. `resize_summary`, when given, is
    filled in with what the resize did.
    """
    report = _Progress(progress, should_cancel)
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

    if container == CONTAINER_ZIP and resize is not None:
        _resize_copy(source_path, output_path, resize, report, resize_summary)
        return output_path

    if container == CONTAINER_ZIP:
        report.expected = _file_size(source_path)
        _verify_cbz(source_path, None, report, stage=0)
        # Copy to a temp name first: a copy cut short (disk full) must
        # never leave a truncated file under the final .cbz name.
        tmp_copy = output_path + ".tmp_convert"
        try:
            _copy_file(source_path, tmp_copy, report)
            report.check()
            os.replace(tmp_copy, output_path)
        except (OSError, ForeignArchiveConversionError) as exc:
            try:
                os.remove(tmp_copy)
            except OSError:
                pass
            if isinstance(exc, ForeignArchiveConversionError):
                raise
            raise ForeignArchiveConversionError(f"Could not write CBZ file: {exc}") from exc
        return output_path

    tmp_output = output_path + ".tmp_convert"
    with tempfile.TemporaryDirectory(prefix="cbzredactor_convert_") as tmp_dir:
        # Extracted exactly once, straight to disk; the pages are then
        # streamed from there into the zip (see _write_zip).
        _run_extraction(container, source_path, tmp_dir, report)
        report.check()

        extracted_pages = sum(
            1 for _root, _dirs, files in os.walk(tmp_dir) for name in files if _is_page_name(name)
        )

        try:
            summary = _write_zip(tmp_dir, tmp_output, report, resize)
            if resize_summary is not None and summary is not None:
                _copy_summary(summary, resize_summary)
            _verify_cbz(tmp_output, extracted_pages, report)
            report.check()
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


def _copy_summary(source: ResizeSummary, target: ResizeSummary) -> None:
    for f in fields(ResizeSummary):
        setattr(target, f.name, getattr(source, f.name))


def _resize_copy(source_path: str, output_path: str, resize: ResizeOptions, report: "_Progress",
                 resize_summary: Optional[ResizeSummary]) -> None:
    """A mislabeled .cbz (already a ZIP) that is also to be resized: one rewrite of the
    source into the new file, never a copy followed by a rewrite."""
    report.expected = _file_size(source_path)
    _verify_cbz(source_path, None, report, stage=0)
    with zipfile.ZipFile(source_path) as zf:
        expected_pages = sum(1 for n in zf.namelist() if _is_page_name(n))
    tmp_output = output_path + ".tmp_convert"

    def on_entry(done: int, total: int) -> None:
        report.report(1, int(report.expected * done / max(total, 1)), "Resizing")

    try:
        summary = resize_zip(
            source_path, tmp_output, resize, progress=on_entry,
            should_cancel=lambda: report._should_cancel is not None and report._should_cancel(),
            temp_suffix=".tmp_resize", error_prefix="Could not write CBZ file",
        )
        _verify_cbz(tmp_output, expected_pages, report)
        report.check()
        os.replace(tmp_output, output_path)
    except ResizeCancelled as exc:
        _remove_quietly(tmp_output)
        raise ConversionCancelled("Conversion cancelled.") from exc
    except (OSError, zipfile.BadZipFile, CbzError, ForeignArchiveConversionError) as exc:
        _remove_quietly(tmp_output)
        if isinstance(exc, ForeignArchiveConversionError):
            raise
        raise ForeignArchiveConversionError(f"Could not write CBZ file: {exc}") from exc
    if resize_summary is not None:
        _copy_summary(summary, resize_summary)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _copy_file(source: str, target: str, report: "_Progress") -> None:
    """shutil.copy2() in chunks, so progress and Cancel work on a big file."""
    with open(source, "rb") as src, open(target, "wb") as dst:
        done = 0
        while True:
            report.check()
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            report.report(1, done, "Copying")
    shutil.copystat(source, target)


def _write_zip(
    tmp_dir: str, tmp_output: str, report: "_Progress", resize: Optional[ResizeOptions] = None
) -> Optional[ResizeSummary]:
    """Packs everything under `tmp_dir` into a new zip, streaming each file in
    chunks (never a whole page in memory) and storing already-compressed page
    images instead of deflating them again.

    With `resize`, each page image is resized on its way in (a bounded window of
    pages in flight on a thread pool, written in order) and the ResizeSummary is
    returned; everything else is packed as it is."""
    files: list[tuple[str, str]] = []
    for root, _dirs, names in os.walk(tmp_dir):
        for name in names:
            full_path = os.path.join(root, name)
            files.append((full_path, os.path.relpath(full_path, tmp_dir).replace(os.sep, "/")))
    # os.walk order is filesystem order (unsorted on Linux): sort so the pages
    # land in the zip in name order, whatever the platform.
    files.sort(key=lambda item: item[1])

    resizer = (
        PageResizer(resize, [arc for _full, arc in files if _is_image(arc)]) if resize is not None else None
    )
    pending: deque = deque()  # (arcname, finish, source size) of pages being resized, oldest first
    done = 0

    def new_info(arcname: str, compress_type: Optional[int] = None) -> zipfile.ZipInfo:
        # zf.write(full_path, arcname) would use the extracted file's own mtime --
        # and ZIP can't represent anything before 1980, which a source archive's
        # entries can easily be (tar in particular often defaults to the epoch,
        # 1970, when nothing else was specified). Stamping the current time
        # instead (what writestr() with a plain string arcname did) sidesteps that
        # entirely; per-page timestamps aren't meaningful for a comic archive's
        # contents anyway.
        info = zipfile.ZipInfo(arcname, date_time=time.localtime(time.time())[:6])
        if compress_type is None:
            compress_type = (
                zipfile.ZIP_STORED if os.path.splitext(arcname)[1].lower() in STORED_EXTENSIONS
                else zipfile.ZIP_DEFLATED
            )
        info.compress_type = compress_type
        info.external_attr = 0o600 << 16
        return info

    def write_resized(zf: zipfile.ZipFile) -> None:
        nonlocal done
        arcname, finish, size = pending.popleft()
        new_name, data, compress_type = finish()
        zf.writestr(new_info(new_name or arcname, compress_type), data)
        done += size
        report.report(1, done, "Resizing")

    try:
        with zipfile.ZipFile(tmp_output, "w", zipfile.ZIP_DEFLATED) as zf:
            for full_path, arcname in files:
                report.check()
                size = os.path.getsize(full_path)
                if resizer is not None and _is_image(arcname):
                    with open(full_path, "rb") as src:
                        original = src.read()
                    pending.append((arcname, resizer.submit(arcname, original), size))
                    while len(pending) >= resizer.window:
                        write_resized(zf)
                        report.check()
                    continue
                info = new_info(arcname)
                with open(full_path, "rb") as src, zf.open(info, "w", force_zip64=size >= 0x3FFFFFFF) as dst:
                    while True:
                        report.check()
                        chunk = src.read(_CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
                        done += len(chunk)
                        report.report(1, done)
                if resizer is not None:
                    resizer.passed_through(size)
            while pending:
                report.check()
                write_resized(zf)
    finally:
        if resizer is not None:
            resizer.close()
    return resizer.summary if resizer is not None else None


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


def _verify_cbz(
    path: str, expected_pages: Optional[int], report: Optional["_Progress"] = None, stage: int = 2
) -> None:
    """The ZIP at `path` opens, its entries pass CRC checks, and (when
    `expected_pages` is given) it holds exactly that many page images."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = None
            done = 0
            for info in zf.infolist():
                if report is not None:
                    report.check()
                try:
                    with zf.open(info) as member:
                        while True:
                            chunk = member.read(_CHUNK)
                            if not chunk:
                                break
                            done += len(chunk)
                            if report is not None:
                                report.report(stage, done, "Checking")
                except zipfile.BadZipFile:
                    bad = info.filename
                    break
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


def _tree_bytes(directory: str) -> int:
    """Bytes of every file under `directory` (what extraction has written so far)."""
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _run_extraction(container: str, path: str, dest_dir: str, report: "_Progress") -> None:
    """Extracts `path` into `dest_dir` once. With a progress callback or cancel
    check, the extraction runs on a helper thread while this one reports how
    many bytes have landed in `dest_dir` (the libraries only offer one blocking
    call), so a huge archive shows live progress instead of silence."""
    if container == CONTAINER_RAR:
        extract = _extract_cbr
    elif container == CONTAINER_TAR:
        extract = _extract_cbt
    else:  # CONTAINER_7Z
        extract = _extract_cb7

    if not report.active:
        extract(path, dest_dir)
        return
    report.check()
    outcome: dict = {}

    def work() -> None:
        try:
            extract(path, dest_dir, report)
        except BaseException as exc:  # re-raised below, on the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(target=work, name="convert-extract", daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(_REPORT_INTERVAL)
        report.report(0, _tree_bytes(dest_dir))
    if "error" in outcome:
        raise outcome["error"]


def _extract_cbr(path: str, dest_dir: str, report: Optional["_Progress"] = None) -> None:
    try:
        import rarfile
    except ImportError as exc:
        raise ForeignArchiveConversionError(
            "CBR support needs the optional 'rarfile' package, which "
            "isn't installed. Run: pip install rarfile"
        ) from exc
    try:
        with rarfile.RarFile(path) as rf:
            if report is not None:
                try:
                    report.expected = sum(i.file_size for i in rf.infolist() if not i.is_dir())
                except Exception:
                    report.expected = 0
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


def _extract_cbt(path: str, dest_dir: str, report: Optional["_Progress"] = None) -> None:
    try:
        with tarfile.open(path, "r:*") as tf:
            if report is not None:
                report.expected = _file_size(path)  # exact for a plain tar, an estimate if compressed
            # "data" filter (Python 3.12+) rejects path-traversal
            # members (e.g. "../../evil"), absolute paths, symlinks
            # escaping dest_dir, and device files -- exactly what a
            # hostile .cbt could otherwise use to write outside
            # dest_dir. Falls back to no filter on Python 3.10/3.11,
            # which never had this option; the app still requires an
            # OS-level unpack anyway, same residual risk any tar
            # extraction on those versions already carries.
            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest_dir, members=_checked_members(tf, report), filter="data")
            else:
                _extract_tar_safely(tf, dest_dir, report)
    except ConversionCancelled:
        raise
    except tarfile.TarError as exc:
        raise ForeignArchiveConversionError(f"Could not read CBT file: {exc}") from exc
    except Exception as exc:
        raise ForeignArchiveConversionError(f"Could not extract CBT file: {_describe(exc)}") from exc


def _checked_members(tf: tarfile.TarFile, report: Optional["_Progress"]):
    """The archive's members one at a time, so Cancel is honoured between files."""
    for member in tf:
        if report is not None:
            report.check()
        yield member


def _extract_tar_safely(tf: tarfile.TarFile, dest_dir: str, report: Optional["_Progress"] = None) -> None:
    """Stand-in for extractall(filter="data") on Python < 3.12: only
    regular files and folders whose resolved path stays under dest_dir
    are extracted; links, devices and "../" or absolute names are
    skipped (a hostile .cbt could otherwise write anywhere)."""
    root = os.path.realpath(dest_dir)

    def safe_members():
        for member in _checked_members(tf, report):
            if not (member.isfile() or member.isdir()):
                continue
            target = os.path.realpath(os.path.join(root, member.name))
            if target != root and not target.startswith(root + os.sep):
                continue
            yield member

    tf.extractall(dest_dir, members=safe_members())


def _extract_cb7(path: str, dest_dir: str, report: Optional["_Progress"] = None) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise ForeignArchiveConversionError(
            "CB7 support needs the optional 'py7zr' package, which "
            "isn't installed. Run: pip install py7zr"
        ) from exc
    try:
        with py7zr.SevenZipFile(path, "r") as zf:
            if report is not None:
                try:
                    report.expected = sum(int(f.uncompressed or 0) for f in zf.list() if not f.is_directory)
                except Exception:
                    report.expected = 0
            zf.extractall(path=dest_dir)
    except ConversionCancelled:
        raise
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
