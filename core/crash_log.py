"""
core/crash_log.py

Global crash logging via redactor_common.core.crash_log (2026-09-23;
this was a line-for-line port of epub's copy): an excepthook appending
timestamped tracebacks to cbzredactor_crash.log next to the app, plus
faulthandler for native crashes inside Qt.
"""

from __future__ import annotations

import os

from redactor_common.core import crash_log as _shared

from core.app_paths import base_dir

LOG_FILENAME = "cbzredactor_crash.log"
MAX_LOG_BYTES = _shared.MAX_LOG_BYTES
format_crash_entry = _shared.format_crash_entry


def log_path() -> str:
    return os.path.join(base_dir(), LOG_FILENAME)


def write_crash_entry(exc_type, exc_value, exc_tb, path: str | None = None) -> None:
    _shared.write_crash_entry(exc_type, exc_value, exc_tb, path or log_path())


def install(also_call=None, faulthandler_path: str | None = None) -> None:
    """`faulthandler_path` kept for compatibility: when given (tests),
    the log itself goes there too, so nothing touches the real log."""
    _shared.install(faulthandler_path or log_path(), also_call=also_call)
