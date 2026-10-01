"""
gui/conversion_progress.py

Progress for CBR/CBT/CB7 -> CBZ conversion. Converting is one long blocking
job per file (extract the whole archive, pack it into a zip, check the zip),
which used to run on the GUI thread: the window froze, with no progress and
no way to cancel, for as long as a big archive took.

ConversionRun runs each conversion on a worker thread while the GUI thread
keeps repainting a single progress dialog that shows the file's name, "n of
m" for a batch, a bar driven by the bytes processed (a busy bar when the
archive's size can't be learned up front) and a Cancel button. Cancel never
leaves a half-written file: core/foreign_archive_convert.py only writes the
.cbz under a temporary name and renames it into place once it is complete and
verified.

Use it as a context manager:

    with ConversionRun(window, len(paths)) as run:
        run.process(paths, step, label_for=...)   # step() calls run.convert(...)
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Iterable, TypeVar

from PyQt6.QtWidgets import QApplication, QWidget

from redactor_common.gui.progress import ProgressReporter

T = TypeVar("T")

_SCALE = 1000  # bar steps per file
_POLL_SECONDS = 0.05


class ConversionRun:
    def __init__(self, parent: QWidget, count: int, label: str = "Converting to CBZ...", threshold: int = 1):
        self._parent = parent
        self.count = max(count, 1)
        self._cancel = threading.Event()
        self._latest: tuple[int, int, str] | None = None
        self._base = 0
        self._text = label
        self._busy = False
        # The reporter's threshold is in bar steps, one file = _SCALE of them.
        self._reporter = ProgressReporter(
            parent, self.count * _SCALE, label, threshold=max(threshold, 1) * _SCALE, cancellable=True,
            title="Converting to CBZ",
        )
        dialog = self._reporter.dialog
        if dialog is not None:
            # Qt's own Cancel handling hides the dialog at once -- but a RAR
            # extraction can't be interrupted, so the wait after a Cancel click
            # must stay visible (and modal). Handle the click ourselves.
            try:
                dialog.canceled.disconnect()
            except TypeError:
                pass
            dialog.canceled.connect(self._on_cancel)

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> "ConversionRun":
        return self

    def __exit__(self, *_exc) -> None:
        self._reporter.close()
        self._status("")

    # -- state ----------------------------------------------------------------

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _on_cancel(self) -> None:
        self._cancel.set()
        self._reporter.set_label("Cancelling... finishing the current step, nothing half-written is kept.")
        dialog = self._reporter.dialog
        if dialog is not None:
            dialog.setCancelButton(None)
        self._status("Cancelling conversion...")

    def _status(self, text: str) -> None:
        bar = getattr(self._parent, "statusBar", None)
        if callable(bar):
            if text:
                bar().showMessage(text)
            else:
                bar().clearMessage()

    # -- driving the dialog -----------------------------------------------------

    def begin(self, index: int, name: str) -> None:
        """Starts file `index` (0-based): label, base position of the bar."""
        self._base = index * _SCALE
        suffix = f"  ({index + 1} of {self.count})" if self.count > 1 else ""
        self._text = f"{name}{suffix}"
        self._latest = None
        self._set_busy(False)
        self._reporter.set_label(self._text)
        self._reporter.set_value(self._base)
        self._status(self._text)

    def _set_busy(self, busy: bool) -> None:
        dialog = self._reporter.dialog
        if dialog is None or busy == self._busy:
            return
        self._busy = busy
        if busy:
            dialog.setRange(0, 0)
        else:
            dialog.setRange(0, self.count * _SCALE)

    def _on_progress(self, done: int, total: int, stage: str) -> None:
        # Worker thread: just remember the newest value; the GUI thread reads it.
        self._latest = (done, total, stage)

    def _refresh(self) -> None:
        latest = self._latest
        if latest is None or self._cancel.is_set():
            return
        done, total, stage = latest
        if total <= 0:
            self._set_busy(True)
            line = f"{stage}: {self._text}"
        else:
            self._set_busy(False)
            fraction = min(done / total, 1.0)
            self._reporter.set_value(self._base + int(fraction * (_SCALE - 1)), pump=False)
            line = f"{stage} ({int(fraction * 100)}%): {self._text}"
        self._reporter.set_label(line)
        self._status(line)

    def convert(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Runs fn(*args, progress=..., should_cancel=..., **kwargs) -- normally
        convert_to_cbz -- on a worker thread, keeping the dialog alive until it
        returns. Returns its result; an exception it raised (including
        ConversionCancelled) is re-raised here, on the GUI thread."""
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                outcome["result"] = fn(*args, progress=self._on_progress, should_cancel=self._cancel.is_set, **kwargs)
            except BaseException as exc:  # re-raised below
                outcome["error"] = exc

        thread = threading.Thread(target=work, name="convert-to-cbz", daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(_POLL_SECONDS)
            self._refresh()
            QApplication.processEvents()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

    def process(
        self, items: Iterable[T], step: Callable[[T, int], None], label_for: Callable[[T], str] | None = None
    ) -> bool:
        """run_with_progress() for conversions: step(item, index) per item under
        this run's dialog. Returns False if Cancel stopped it before the end."""
        items = list(items)
        for index, item in enumerate(items):
            if self.cancelled:
                return False
            self.begin(index, label_for(item) if label_for is not None else str(item))
            step(item, index)
            if self.cancelled:
                return False
            self._set_busy(False)
            self._reporter.set_value(self._base + _SCALE)
        return True


def file_label(verb: str, path: str) -> str:
    return f"{verb}: {os.path.basename(path)}"
