"""
gui/page_size_scanner.py

Background measuring for the table's Size column: runs
core.page_dimensions.scan_page_sizes() for a book on the shared
QThreadPool and caches the result per book (by identity, like the
cover icons -- see redactor_common.gui.async_icon_cache), keyed on a
(path, mtime) version so a resized/replaced file gets re-measured.

Same lazy shape as the cover thumbnails: MainWindow only requests rows
that are on screen. Anything that needs EVERY book's size up front
(sorting by Size, "only Oversized files" in the Resize dialog) calls
scan_now() for the rest instead, under a progress dialog.
"""

from __future__ import annotations

import zipfile
import zlib
from typing import Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from core.page_dimensions import PageSizeStats, scan_page_sizes
from redactor_common.gui.async_icon_cache import IdentityWeakDict


def _scan(path: str, page_names: list[str]) -> PageSizeStats:
    try:
        return scan_page_sizes(path, page_names)
    except (OSError, zipfile.BadZipFile, zlib.error):
        return PageSizeStats(unreadable=len(page_names))


class _Signals(QObject):
    done = pyqtSignal(object, object, object)  # book, source, stats


class _ScanTask(QRunnable):
    def __init__(self, scan, book, source, path: str, page_names: list[str], signals: _Signals):
        super().__init__()
        self._scan = scan
        self._book = book
        self._source = source
        self._path = path
        self._page_names = page_names
        self._signals = signals

    def run(self) -> None:
        self._signals.done.emit(self._book, self._source, self._scan(self._path, self._page_names))


class PageSizeScanner(QObject):
    """`stats_ready(book, result)` fires on the main thread whenever a
    background scan finishes. `scan(path, page_names)` is the per-book
    work -- page sizes by default; the Credit Pages column passes
    core.credit_pages.scan_book instead (same lazy, cached machinery)."""

    stats_ready = pyqtSignal(object, object)

    def __init__(self, parent=None, scan=None):
        super().__init__(parent)
        self._scan_fn = scan or _scan
        self._cache = IdentityWeakDict()  # book -> (source, stats)
        self._in_flight = IdentityWeakDict()  # book -> source
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)

    def get_cached(self, book, source) -> Optional[PageSizeStats]:
        entry = self._cache.get(book)
        if entry is not None and entry[0] == source:
            return entry[1]
        return None

    def request(self, book, source) -> None:
        if self.get_cached(book, source) is not None or self._in_flight.get(book) == source:
            return
        self._in_flight[book] = source
        QThreadPool.globalInstance().start(
            _ScanTask(self._scan_fn, book, source, book.path, list(book.page_names), self._signals)
        )

    def scan_now(self, book, source) -> PageSizeStats:
        """Synchronous version, for callers that need the answer before
        continuing (already under their own progress dialog)."""
        cached = self.get_cached(book, source)
        if cached is not None:
            return cached
        stats = self._scan_fn(book.path, list(book.page_names))
        self._cache[book] = (source, stats)
        return stats

    def _on_done(self, book, source, stats) -> None:
        if self._in_flight.get(book) == source:
            self._in_flight.pop(book)
        self._cache[book] = (source, stats)
        self.stats_ready.emit(book, stats)
