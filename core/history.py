"""
core/history.py

The operations journal for this app (redactor_common.core.journal): every change the app makes to a
CBZ's ComicInfo.xml TAGS (Save, Save All, Save As over a file, Redact, the command line, Restore) is
recorded with the old and new text of each changed field, so an unwanted Save can be undone after the
app was closed (Tools > History...).

OPT-IN: nothing is recorded unless the user turned history on (Tools > Preferences > History, setting
history/enabled, default off). While it is off no line is written and no journal file is created; the
command line obeys the same setting.

Only the ComicInfo text fields are kept. Page images are never stored: a Redact that removed or resized
pages records just a `cover_changed` flag (its original is in the Recycle Bin). The scan stamp and
PageCount are technical values the app writes itself, so they are left out (a save that only records a
scan stamp records nothing).

The journal file sits next to the settings ini (core/app_paths.base_dir()). A journal problem never
blocks or fails a save: it is printed to stderr once and kept for the window's status bar
(take_warning()).

Qt-free; apply_restore() runs on a worker thread (the History dialog's progress), so it touches no
widgets.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Mapping

from redactor_common.core.journal import (
    KIND_REDACT,
    KIND_RESTORE,
    KIND_SAVE,
    Journal,
    default_journal_path,
    diff_fields,
)

from core.app_paths import APP_SLUG, base_dir
from core.comicinfo import TAG_TO_ATTR

__all__ = [
    "KIND_REDACT", "KIND_RESTORE", "KIND_SAVE", "JOURNALED_FIELDS", "apply_restore", "begin", "delete_history",
    "diff_fields", "fields_text", "finish", "get_journal", "history_enabled", "journal_path", "set_history_enabled",
    "take_warning", "use_journal",
]

# Technical, written by the app itself rather than edited: never journaled, never restored.
_TECHNICAL_FIELDS = frozenset({"PageCount"})
JOURNALED_FIELDS = tuple(tag for tag in TAG_TO_ATTR if tag not in _TECHNICAL_FIELDS)

_lock = threading.Lock()
_journal: Journal | None = None
_warned = False
_warning = ""


def _read_enabled_setting() -> bool:
    # The setting lives in the app's ini (gui/app_settings.py, key history/enabled), read the way the
    # command line reads its settings; imported lazily so this module stays importable without Qt.
    from gui import app_settings

    return app_settings.load_history_enabled()


def history_enabled() -> bool:
    """Whether tag changes are recorded. OPT-IN (owner decision 2026-10-14): off unless the user turned
    it on in Tools > Preferences > History (or answered Yes in Tools > History); an unreadable setting
    is off. While off, nothing is written and no journal file is created."""
    try:
        return bool(_read_enabled_setting())
    except Exception:  # noqa: BLE001 -- a broken settings file means "off", never a failed save
        return False


def set_history_enabled(on: bool) -> None:
    from gui import app_settings

    app_settings.save_history_enabled(on)


def delete_history() -> bool:
    """Removes the journal file (Tools > Preferences > History > Delete History...). Returns whether a
    file was removed. Raises OSError when it can't be."""
    path = get_journal().path
    with _lock:
        removed = False
        for candidate in (path, path + ".tmp"):
            if os.path.exists(candidate):
                os.remove(candidate)
                removed = removed or candidate == path
        return removed


def journal_file_path() -> str:
    """Where the journal in use writes (journal_path() unless a test replaced the journal)."""
    return get_journal().path


def journal_path() -> str:
    """`<settings folder>/cbzredactor_journal.jsonl`."""
    return default_journal_path(APP_SLUG, base_dir())


def get_journal() -> Journal:
    """The process-wide journal, created on first use."""
    global _journal
    with _lock:
        if _journal is None:
            _journal = Journal(journal_path())
        return _journal


def use_journal(journal: Journal | None) -> None:
    """Replaces the process-wide journal (tests point it at tmp_path); None: the default one again."""
    global _journal, _warned, _warning
    with _lock:
        _journal = journal
        _warned = False
        _warning = ""


def fields_text(metadata) -> dict[str, str]:
    """The ComicInfo fields as plain text, keyed by their XML tag names (Title, Series, ...): no scan
    stamp, no PageCount, no <Pages> block."""
    return {tag: _text(getattr(metadata, TAG_TO_ATTR[tag], "")) for tag in JOURNALED_FIELDS}


def _text(value) -> str:
    return "" if value is None else str(value).strip()


# -- never-failing wrappers ---------------------------------------------------------------------


def _report(exc: BaseException) -> None:
    global _warned, _warning
    message = f"The history (Tools > History) could not record a change: {exc}"
    with _lock:
        if _warned:
            return
        _warned = True
        _warning = message
    print(message, file=sys.stderr)


def take_warning() -> str:
    """The first journal problem not shown yet ("" if none); shown once, then cleared."""
    global _warning
    with _lock:
        message, _warning = _warning, ""
        return message


def begin(path: str, kind: str, changes: Mapping[str, tuple[str, str]], **kwargs) -> str | None:
    """Journal.begin() that never raises (a journal problem must not stop a save). Does nothing (None)
    while history is off."""
    if not changes and not kwargs.get("cover_changed"):
        return None
    if not history_enabled():
        return None
    try:
        return get_journal().begin(os.path.abspath(path), kind, changes, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- any journal failure is reported, never raised
        _report(exc)
        return None


def finish(entry_id: str | None, ok: bool = True, error: str = "") -> None:
    """Journal.finish() that never raises."""
    if entry_id is None:
        return
    try:
        get_journal().finish(entry_id, ok, error)
    except Exception as exc:  # noqa: BLE001
        _report(exc)


# -- restoring ------------------------------------------------------------------------------------


def apply_restore(path: str, values: Mapping[str, str]) -> str:
    """Writes {field: text} onto the CBZ at `path`: reads its CURRENT ComicInfo with the app's reader,
    sets the fields, saves through CbzBook.save() (pages copied byte for byte), then reads the file back
    and checks the values stuck. Returns "" or a plain-language problem. Writes no journal entry (the
    History dialog records the restore itself). Runs on a worker thread: no widgets."""
    from core.cbz_file import CbzBook, CbzError

    unknown = [name for name in values if name not in JOURNALED_FIELDS]
    if unknown:
        return f"this program does not know the field(s) {', '.join(unknown)}"
    if not os.path.isfile(path):
        return "the file is not there any more"
    book = CbzBook(path)
    if book.load_error:
        return f"the file could not be read ({book.load_error})"
    if book.needs_conversion:
        return "the file is not a CBZ any more"
    for name, text in values.items():
        setattr(book.metadata, TAG_TO_ATTR[name], _text(text))
    try:
        book.save(journal_kind=None)
    except CbzError as exc:
        return str(exc)
    check = CbzBook(path)
    if check.load_error:
        return f"the file could not be read back ({check.load_error})"
    now = fields_text(check.metadata)
    wrong = [name for name, text in values.items() if now.get(name, "") != _text(text)]
    if wrong:
        return f"the file was written but {', '.join(wrong)} did not read back as restored"
    return ""
