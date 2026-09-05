"""
core/crash_log.py

Installs a global exception hook that catches any otherwise-unhandled
exception and appends a full timestamped traceback to a log file next
to the app (see core.app_paths.base_dir()), rather than letting it
vanish or bring the whole app down with nothing to go on afterward.

This matters more for a PyQt app than a plain script: an exception
raised inside a signal/slot doesn't propagate back through Qt's C++
event loop the normal way -- sys.excepthook is the one place
guaranteed to see it regardless of platform/PyQt version behavior.

Ported from epubredactor's core/crash_log.py; identical logic, just
this project's own log filename.
"""

from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import traceback

from core.app_paths import base_dir

LOG_FILENAME = "cbzredactor_crash.log"

# A single log file, appended to (not overwritten), so a pattern across
# multiple crashes is still visible afterward. Capped so a machine that
# crashes repeatedly doesn't grow this unboundedly.
MAX_LOG_BYTES = 2_000_000


def log_path() -> str:
    return os.path.join(base_dir(), LOG_FILENAME)


def format_crash_entry(exc_type, exc_value, exc_tb) -> str:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    return f"\n{'=' * 70}\n{timestamp}\n{tb_text}"


def _trim_if_oversized(path: str) -> None:
    """Drops entries from the front (oldest first) once the file
    exceeds MAX_LOG_BYTES, keeping the most recent activity -- what's
    actually useful when diagnosing a fresh crash."""
    try:
        if os.path.getsize(path) <= MAX_LOG_BYTES:
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        trimmed = content[-MAX_LOG_BYTES:]
        marker = "=" * 70
        idx = trimmed.find(marker)
        if idx > 0:
            trimmed = trimmed[idx:]
        with open(path, "w", encoding="utf-8") as f:
            f.write(trimmed)
    except OSError:
        pass  # trimming is a nice-to-have; never let it block logging itself


def write_crash_entry(exc_type, exc_value, exc_tb, path: str | None = None) -> None:
    """Appends one crash entry to the log file. Never raises -- a
    failure here must not prevent whatever else the caller does with
    the exception. `path` defaults to log_path() but is injectable for
    testing."""
    if path is None:
        path = log_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(format_crash_entry(exc_type, exc_value, exc_tb))
        _trim_if_oversized(path)
    except OSError:
        pass


def install(also_call=None, faulthandler_path: str | None = None) -> None:
    """Installs the global hook. `also_call(exc_type, exc_value, exc_tb)`,
    if given, runs after logging (e.g. to show a dialog).

    Also enables Python's own faulthandler for the same log file (or
    `faulthandler_path`, injectable for testing): a genuine native-level
    crash inside Qt's own C++ code isn't a Python exception at all and
    sys.excepthook never sees it -- faulthandler's signal handlers can
    still dump a minimal traceback of what was executing. Best-effort:
    if the log location isn't writable, the app should still launch."""
    try:
        faulthandler.enable(file=open(faulthandler_path or log_path(), "a", encoding="utf-8"))
    except OSError:
        pass

    previous_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        write_crash_entry(exc_type, exc_value, exc_tb)
        if also_call is not None:
            try:
                also_call(exc_type, exc_value, exc_tb)
            except Exception:  # noqa: BLE001 - the crash handler itself must never crash
                pass
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
