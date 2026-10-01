"""
core/zip_rewrite.py

The ONE way this app rewrites a ZIP archive entry by entry: Save,
Remove Pages, Clean Up Archive Contents and Resize Images all go through
rewrite_archive(). They used to carry four near-identical loops that
drifted apart (a resize that lost the ZIP comment, temp files leaking on
some failures, entries losing their zip metadata), so everything that must
hold for every rewrite now lives here, once:

- the source is never written to: the new archive goes to a temp file
  beside the destination, is checked, and only then replaces the
  destination (os.replace, retried briefly when another reader holds the
  file open);
- every entry's ZIP metadata is carried over (timestamp, compression,
  attributes, creating system, entry comment, and the extra fields that
  stay valid), and so is the archive comment unless the caller replaces it;
- the temp file is removed on EVERY failure (a bad archive, an encrypted or
  unsupported-compression entry, a full disk, a caller's own exception, a
  cancel) -- except when the original was already sent to the Recycle Bin
  and the replace then failed: the temp is then the only full copy, so it
  is kept and the error names it;
- the zip library's many failure types all surface as CbzError.

The caller describes the rewrite with a RewritePlan: a per-entry decision
(keep / drop / rename / replace the bytes), entries to add before or after
the copied ones, an optional reordering and optional checks on the result.
Nothing here knows about comics.
"""

from __future__ import annotations

import os
import struct
import time
import zipfile
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from redactor_common.core.save_errors import describe_save_error

from core.zip_names import RawNameInfo, legacy_raw_name, open_zip, raw_names_supported

try:  # a Python built without lzma has no LZMAError to catch
    from lzma import LZMAError as _LZMAError
except ImportError:  # pragma: no cover
    _LZMAError = OSError

# What zipfile/zlib/lzma/bz2 raise for a bad archive or entry. RuntimeError is
# an encrypted entry, NotImplementedError a compression method zipfile has no
# codec for, ValueError a malformed header/name, EOFError/zlib.error/LZMAError
# a truncated or damaged compressed stream, KeyError a missing entry (bz2
# raises OSError). None but OSError is an OSError, so all of them used to
# escape past the CbzError the GUI catches.
ZIP_ERRORS = (
    zipfile.BadZipFile, KeyError, OSError, zlib.error, RuntimeError, NotImplementedError, ValueError, EOFError,
    _LZMAError,
)


class CbzError(Exception):
    """Raised for a problem reading or writing a CBZ file."""


class RewriteError(CbzError):
    """A rewrite failed (the source is untouched, the temp file is gone).
    `detail` is the reason without the "Could not ..." prefix."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail or message


class RewriteCancelled(CbzError):
    """`should_cancel` fired; the temp file was removed and the source left
    untouched."""


# --- committing the temp file ------------------------------------------------

_REPLACE_ATTEMPTS = 5
_REPLACE_DELAY = 0.1  # seconds between attempts


def _replace(tmp_path: str, path: str) -> None:
    """os.replace, retried briefly on PermissionError: on Windows another
    reader holding the file open for a moment (the app's own cover and
    page-size scans, a virus scanner) fails the rename, where the old
    shutil.move silently fell back to copying over it."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY)


class _ReplaceFailedAfterDispose(OSError):
    """The original was already sent to the Recycle Bin when the rewritten
    file failed to replace it; the rewrite is still at `tmp_path`."""

    def __init__(self, tmp_path: str, cause: BaseException):
        super().__init__(
            f"the original is in the Recycle Bin and the rewritten copy was kept at "
            f"{tmp_path} ({describe_save_error(cause)})"
        )
        self.tmp_path = tmp_path


def _dispose_then_replace(dispose_original, tmp_path: str, path: str) -> None:
    """`dispose_original(path)` (Recycle Bin) then os.replace(tmp, path).
    If the replace fails after the original is gone, the temp file is the
    only full copy: it is kept, and the error names it."""
    if dispose_original is not None:
        dispose_original(path)
    try:
        _replace(tmp_path, path)
    except OSError as exc:
        if dispose_original is not None and not os.path.exists(path):
            raise _ReplaceFailedAfterDispose(tmp_path, exc) from exc
        raise


def _discard_temp(exc: BaseException, tmp_path: str) -> None:
    """Removes a rewriter's temp file after a failure -- except when it
    holds the only remaining copy (see _ReplaceFailedAfterDispose)."""
    if isinstance(exc, _ReplaceFailedAfterDispose):
        return
    try:
        os.remove(tmp_path)
    except OSError:
        pass


# --- the plan ----------------------------------------------------------------


@dataclass
class Entry:
    """A source entry as a decision sees it."""

    name: str
    info: zipfile.ZipInfo

    @property
    def is_dir(self) -> bool:
        return self.name.endswith("/")


@dataclass
class Action:
    """What to do with one source entry. The default keeps it as it is.
    `drop` leaves it out; `rename` writes it under another name; `data`
    writes these bytes instead of its own (its other metadata is kept;
    `date_time` stamps a replaced entry that is really new content;
    `compress_type` overrides how it is stored -- Resize stores re-encoded
    pages instead of deflating a JPEG a second time)."""

    drop: bool = False
    rename: Optional[str] = None
    data: Optional[bytes] = None
    date_time: Optional[tuple] = None
    compress_type: Optional[int] = None


@dataclass
class NewEntry:
    """An entry that isn't in the source. `date_time` None means now."""

    name: str
    data: bytes
    compress_type: int = zipfile.ZIP_STORED
    date_time: Optional[tuple] = None


# decide(entry, read) -> Action | None (keep) | a zero-argument callable that
# returns the Action later (see RewritePlan.window). read() gives the entry's
# uncompressed bytes (read once, then remembered).
Decision = Union[Action, None, Callable[[], Optional[Action]]]


@dataclass
class RewritePlan:
    decide: Optional[Callable[[Entry, Callable[[], bytes]], Decision]] = None  # None: keep everything
    before: list = field(default_factory=list)  # NewEntry written ahead of the copied entries
    after: list = field(default_factory=list)  # NewEntry written after them
    comment: Optional[bytes] = None  # None keeps the source's archive comment; b"" clears it
    order: Optional[Callable[[list], list]] = None  # entries (source order) -> the order to write them
    begin: Optional[Callable[[list], None]] = None  # called once with every Entry, before any decision
    # Up to `window` entries are decided ahead of the one being written (a sliding
    # window), and they are written in order. A decision that returns a callable
    # lets the caller start slow work for those entries
    # (Resize runs its page encoders on a thread pool) and finish it when the
    # entry's turn to be written comes.
    window: int = 1
    verify_crc: bool = False  # re-read the written archive and check every entry's CRC
    verify: Optional[Callable[[zipfile.ZipFile], Optional[str]]] = None  # a problem message, or None if fine


@dataclass
class RewriteResult:
    kept: int = 0  # copied unchanged
    dropped: list = field(default_factory=list)  # names left out
    renamed: dict = field(default_factory=dict)  # old name -> new name
    replaced: list = field(default_factory=list)  # names whose bytes were replaced
    added: list = field(default_factory=list)  # names of NewEntry items
    bytes_in: int = 0  # uncompressed bytes read from the source
    bytes_out: int = 0  # uncompressed bytes written
    entries_written: int = 0


# --- copying entry metadata --------------------------------------------------

# Extra-field ids that must not be copied: zip64 (zipfile writes its own, and
# a copy would be duplicated) and the Info-ZIP unicode path/comment fields
# (they carry a checksum of the old name, wrong once an entry is renamed).
_DROP_EXTRA_IDS = {0x0001, 0x7075, 0x7063}


def safe_extra(extra: bytes) -> bytes:
    """`extra` without the fields that go stale when an entry is copied. A
    malformed field list is dropped whole, never half-copied."""
    kept = []
    pos = 0
    while pos < len(extra):
        if pos + 4 > len(extra):
            return b""
        header_id, size = struct.unpack("<HH", extra[pos:pos + 4])
        if pos + 4 + size > len(extra):
            return b""
        if header_id not in _DROP_EXTRA_IDS:
            kept.append(extra[pos:pos + 4 + size])
        pos += 4 + size
    return b"".join(kept)


def _make_info(name: str, source: Optional[zipfile.ZipInfo], date_time: Optional[tuple] = None) -> zipfile.ZipInfo:
    """A fresh ZipInfo for the output, with `source`'s metadata."""
    when = date_time or (source.date_time if source else _now())
    raw = legacy_raw_name(source) if source is not None and name == source.filename else None
    if raw is not None:
        # A legacy-encoded name kept under its own name: its bytes go back as they were.
        if not raw_names_supported():
            raise RewriteError(
                "Cannot rewrite the archive: its entry names use an old encoding that this Python "
                "version can't keep exactly"
            )
        info = RawNameInfo(name, when, raw)
    else:
        info = zipfile.ZipInfo(name, date_time=when)
    if source is None:
        info.external_attr = 0o600 << 16  # what ZipFile.writestr(name, data) gives a new file
    else:
        info.compress_type = source.compress_type
        info.external_attr = source.external_attr
        info.internal_attr = source.internal_attr
        info.create_system = source.create_system
        info.comment = source.comment
        info.extra = safe_extra(source.extra)
    return info


def _now() -> tuple:
    return time.localtime()[:6]


def now_date_time() -> tuple:
    """A ZIP timestamp for 'this moment' (an entry whose content is new)."""
    return _now()


# --- the rewrite -------------------------------------------------------------


def rewrite_archive(
    src_path: str,
    dst_path: str,
    plan: RewritePlan,
    *,
    dispose_original: Optional[Callable[[str], None]] = None,
    temp_suffix: str = ".tmp_write",
    error_prefix: str = "Could not rewrite the archive",
    progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> RewriteResult:
    """Writes `src_path` rewritten as `plan` says to `dst_path` (the same
    path for an in-place rewrite) and returns what was done.

    The output is built at `dst_path + temp_suffix`, checked (entry count,
    plus whatever the plan asks for), and only then moved over `dst_path`.
    `dispose_original(path)` runs on the destination just before that move
    (the GUI passes "move to the Recycle Bin"); if it raises, nothing is
    replaced. `progress(done, total)` follows each source entry;
    `should_cancel()` is polled before each entry is written.

    Raises RewriteError (a CbzError, message starting with `error_prefix`)
    for any zip-level failure, RewriteCancelled on cancel; any other
    exception propagates -- the temp file is removed in every case."""
    tmp_path = dst_path + temp_suffix
    try:
        result = _write_temp(src_path, tmp_path, plan, progress, should_cancel)
        _check_written(tmp_path, plan, result)
        _dispose_then_replace(dispose_original, tmp_path, dst_path)
    except BaseException as exc:
        _discard_temp(exc, tmp_path)
        if isinstance(exc, ZIP_ERRORS):
            detail = describe_save_error(exc)
            raise RewriteError(f"{error_prefix}: {detail}", detail) from exc
        raise
    return result


def _write_temp(src_path, tmp_path, plan: RewritePlan, progress, should_cancel) -> RewriteResult:
    result = RewriteResult()
    written: dict[str, bool] = {}  # output name -> came straight from a source entry

    def put(info: zipfile.ZipInfo, data: bytes, from_source: bool, dst) -> None:
        previous = written.get(info.filename)
        if previous is not None and not (previous and from_source):
            raise RewriteError(f"Cannot rewrite the archive: two entries would be named {info.filename!r}")
        written[info.filename] = from_source
        dst.writestr(info, data)
        result.entries_written += 1
        result.bytes_out += len(data)

    with open_zip(src_path) as src, zipfile.ZipFile(tmp_path, "w") as dst:
        dst.comment = src.comment if plan.comment is None else plan.comment
        entries = [Entry(info.filename, info) for info in src.infolist()]
        if plan.begin is not None:
            plan.begin(entries)
        if plan.order is not None:
            entries = plan.order(entries)
        for new in plan.before:
            put(_make_info(new.name, None, new.date_time), new.data, False, dst)
            result.added.append(new.name)

        window = max(1, plan.window)
        done = 0
        # A sliding window: up to `window` entries are decided ahead of the one being
        # written, and a new one is decided as soon as one is written, so the caller's
        # workers never sit idle waiting for a whole window to drain.
        pending: deque = deque()
        upcoming = iter(entries)
        while True:
            if should_cancel is not None and should_cancel():
                raise RewriteCancelled("Cancelled")
            while len(pending) < window:
                entry = next(upcoming, None)
                if entry is None:
                    break
                reader = _Reader(src, entry.info)
                pending.append((entry, reader, plan.decide(entry, reader) if plan.decide else None))
            if not pending:
                break
            entry, reader, decision = pending.popleft()
            action = decision() if callable(decision) else decision
            action = action or Action()
            done += 1
            if action.drop:
                result.dropped.append(entry.name)
            else:
                new_name = action.rename or entry.name
                data = reader() if action.data is None else action.data
                info = _make_info(new_name, entry.info, action.date_time)
                if action.compress_type is not None:
                    info.compress_type = action.compress_type
                put(info, data, new_name == entry.name, dst)
                renamed = new_name != entry.name
                replaced = action.data is not None and action.data != reader.peek()
                if renamed:
                    result.renamed[entry.name] = new_name
                if replaced:
                    result.replaced.append(entry.name)
                if not renamed and not replaced:
                    result.kept += 1
            result.bytes_in += reader.size_read
            if progress is not None:
                progress(done, len(entries))

        for new in plan.after:
            put(_make_info(new.name, None, new.date_time), new.data, False, dst)
            result.added.append(new.name)
    return result


class _Reader:
    """Reads one source entry's bytes at most once."""

    def __init__(self, src: zipfile.ZipFile, info: zipfile.ZipInfo):
        self._src, self._info = src, info
        self._data: Optional[bytes] = None

    def __call__(self) -> bytes:
        if self._data is None:
            self._data = self._src.read(self._info)
        return self._data

    def peek(self) -> Optional[bytes]:
        return self._data

    @property
    def size_read(self) -> int:
        return len(self._data) if self._data is not None else 0


def _check_written(tmp_path: str, plan: RewritePlan, result: RewriteResult) -> None:
    """The temp archive reads back with the entries that were written (and,
    when the plan asks, intact CRCs and its own checks)."""
    with zipfile.ZipFile(tmp_path, "r") as zf:
        if len(zf.infolist()) != result.entries_written:
            raise RewriteError(
                f"The rewritten archive has {len(zf.infolist())} entries, expected {result.entries_written}"
            )
        if plan.verify_crc:
            bad = zf.testzip()
            if bad is not None:
                raise RewriteError(f"The rewritten archive failed its checksum at {bad!r}")
        if plan.verify is not None:
            problem = plan.verify(zf)
            if problem:
                raise RewriteError(f"The rewritten archive failed verification: {problem}")
