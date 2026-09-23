"""
gui/app_settings.py

Thin wrapper around QSettings for the things this app needs to
remember across runs: the user's Comic Vine API key, table column
order/widths/visibility, rename/parse-filename pattern history, and
custom Genre/Language quick-pick entries. Stored in a plain .ini file
next to the executable (or, in dev mode, at the project root) -- not
the Windows registry, since a plain file is easier to back up, copy to
a new machine, or inspect directly. Same approach as epubredactor's
gui/app_settings.py, and the genre/language management functions below
mirror its exact shape (hideable defaults + custom entries) -- ISO
language codes and comic genre labels are plain facts/conventions, not
something worth inventing a different persistence scheme for here.
"""

from __future__ import annotations

import json
import os

from core.app_paths import base_dir
from core.comic_genres import COMMON_COMIC_GENRES
from core.comic_languages import DEFAULT_LANGUAGES
from redactor_common.core import managed_list, pattern_history

_SETTINGS_FILENAME = "cbzredactor_settings.ini"
_COMICVINE_API_KEY = "comicvine/api_key"
_LAST_DIR_KEY = "files/last_directory"

_PATTERN_HISTORY_KEY = "patterns/history"
_MAX_PATTERN_HISTORY = 15

_COLUMN_ORDER_KEY = "table/column_order"
_COLUMN_WIDTHS_KEY = "table/column_widths"
_HIDDEN_COLUMNS_KEY = "table/hidden_columns"

_CUSTOM_GENRES_KEY = "genres/custom"
_HIDDEN_DEFAULT_GENRES_KEY = "genres/hidden_defaults"
_CUSTOM_LANGUAGES_KEY = "languages/custom"
_HIDDEN_DEFAULT_LANGUAGES_KEY = "languages/hidden_defaults"

_RESIZE_MAX_WIDTH_KEY = "resize/max_width"
_RESIZE_JPEG_QUALITY_KEY = "resize/jpeg_quality"
_DEFAULT_RESIZE_MAX_WIDTH = 1600
_DEFAULT_RESIZE_JPEG_QUALITY = 90


def _settings_ini_path() -> str:
    return os.path.join(base_dir(), _SETTINGS_FILENAME)


def _settings():
    # Imported lazily so this module doesn't force a Qt platform
    # backend to exist just to be imported (e.g. from a test).
    from PyQt6.QtCore import QSettings

    return QSettings(_settings_ini_path(), QSettings.Format.IniFormat)


def load_comicvine_api_key() -> str:
    return str(_settings().value(_COMICVINE_API_KEY, ""))


def save_comicvine_api_key(api_key: str) -> None:
    settings = _settings()
    settings.setValue(_COMICVINE_API_KEY, api_key.strip())
    settings.sync()


def load_last_directory() -> str:
    """Returns "" if nothing's been remembered yet, or the remembered
    directory no longer exists (e.g. a removable drive that's since
    been unplugged) -- callers should treat "" as "let Qt use its own
    default". Same shape as epubredactor's gui/app_settings.py."""
    path = _settings().value(_LAST_DIR_KEY, "", type=str)
    return path if path and os.path.isdir(path) else ""


def save_last_directory(path: str) -> None:
    """Remember the directory containing `path` (a file or folder that
    was just loaded) as the starting point for the next file dialog."""
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    if directory and os.path.isdir(directory):
        _settings().setValue(_LAST_DIR_KEY, directory)


def load_resize_max_width() -> int:
    return int(_settings().value(_RESIZE_MAX_WIDTH_KEY, _DEFAULT_RESIZE_MAX_WIDTH))


def save_resize_max_width(max_width: int) -> None:
    _settings().setValue(_RESIZE_MAX_WIDTH_KEY, int(max_width))


def load_resize_jpeg_quality() -> int:
    return int(_settings().value(_RESIZE_JPEG_QUALITY_KEY, _DEFAULT_RESIZE_JPEG_QUALITY))


def save_resize_jpeg_quality(quality: int) -> None:
    _settings().setValue(_RESIZE_JPEG_QUALITY_KEY, int(quality))


# ------------------------------------------------------------------
# Rename / Parse Filename pattern history -- shared between both
# dialogs (see gui/main_window.py's open_rename_dialog()/
# open_parse_filename_dialog()), same as epubredactor's own.
# ------------------------------------------------------------------

def _dedupe_and_trim(history: list[str], new_pattern: str, max_history: int = _MAX_PATTERN_HISTORY) -> list[str]:
    """Move new_pattern to the front of history, deduped, trimmed --
    redactor_common.core.pattern_history's rule, shared by every app."""
    return pattern_history.dedupe_and_trim(history, new_pattern, max_history)


def load_pattern_history() -> list[str]:
    return pattern_history.decode_history(_settings().value(_PATTERN_HISTORY_KEY, "", type=str))


def save_pattern_used(pattern: str) -> None:
    history = _dedupe_and_trim(load_pattern_history(), pattern)
    _settings().setValue(_PATTERN_HISTORY_KEY, pattern_history.encode_history(history))


# ------------------------------------------------------------------
# Table columns -- field-key based (not index-based), so a persisted
# preference survives a column being added/removed/reordered in code
# without silently pointing at the wrong field. See
# redactor_common.core.table_settings's own docstring for why.
# ------------------------------------------------------------------

def load_column_order() -> list[str]:
    raw = _settings().value(_COLUMN_ORDER_KEY, "", type=str)
    if not raw:
        return []
    try:
        return [k for k in json.loads(raw) if isinstance(k, str)]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def save_column_order(order: list[str]) -> None:
    _settings().setValue(_COLUMN_ORDER_KEY, json.dumps(order))


def load_column_widths() -> dict[str, int]:
    raw = _settings().value(_COLUMN_WIDTHS_KEY, "", type=str)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def save_column_widths(widths: dict[str, int]) -> None:
    _settings().setValue(_COLUMN_WIDTHS_KEY, json.dumps(widths))


def load_hidden_columns() -> set[str]:
    raw = _settings().value(_HIDDEN_COLUMNS_KEY, "", type=str)
    if not raw:
        return set()
    try:
        return {k for k in json.loads(raw) if isinstance(k, str)}
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()


def save_hidden_columns(hidden: set[str]) -> None:
    _settings().setValue(_HIDDEN_COLUMNS_KEY, json.dumps(sorted(hidden)))


def has_hidden_columns_preference() -> bool:
    """True once the user has ever saved a hidden-columns choice --
    including an explicit "show everything" (an empty set is still a
    saved choice). Distinct from load_hidden_columns() returning an
    empty set, which is ambiguous between "never configured" and
    "deliberately show everything" on its own; callers that need to
    apply a first-run default (see MainWindow.DEFAULT_HIDDEN_COLUMNS)
    check this first."""
    return _settings().contains(_HIDDEN_COLUMNS_KEY)


# ------------------------------------------------------------------
# Genres: built-in defaults (individually hideable/restorable) plus any
# custom genres added via the Genre field's "+" menu or Settings >
# Add/Remove Genres. Same shape as the languages section below. The
# merge/hide/add/remove rules are redactor_common.core.managed_list
# (shared with epub and mp3); only the storage keys live here.
# ------------------------------------------------------------------

_merge_genres = managed_list.merge_names
_exclude_hidden_genres = managed_list.exclude_hidden_names


def _load_names(key: str) -> list[str]:
    return managed_list.decode_names(_settings().value(key, "", type=str))


def _save_names(key: str, names: list[str]) -> None:
    _settings().setValue(key, managed_list.encode_names(names))


def load_hidden_default_genres() -> list[str]:
    return _load_names(_HIDDEN_DEFAULT_GENRES_KEY)


def hide_default_genre(genre: str) -> None:
    _save_names(_HIDDEN_DEFAULT_GENRES_KEY, managed_list.add_name(load_hidden_default_genres(), genre))


def restore_default_genres() -> None:
    _save_names(_HIDDEN_DEFAULT_GENRES_KEY, [])


def load_visible_default_genres() -> list[str]:
    return _exclude_hidden_genres(COMMON_COMIC_GENRES, load_hidden_default_genres())


def load_genres() -> list[str]:
    """Visible (non-hidden) default genres plus any custom ones added
    previously -- what the quick-pick "+" menu shows."""
    return _merge_genres(load_visible_default_genres(), load_custom_genres())


def load_custom_genres() -> list[str]:
    return _load_names(_CUSTOM_GENRES_KEY)


def add_custom_genre(genre: str) -> None:
    _save_names(_CUSTOM_GENRES_KEY, managed_list.add_name(load_custom_genres(), genre))


def remove_custom_genre(genre: str) -> None:
    _save_names(_CUSTOM_GENRES_KEY, managed_list.remove_name(load_custom_genres(), genre))


# ------------------------------------------------------------------
# Languages: same hideable-defaults-plus-custom shape as genres above.
# ------------------------------------------------------------------

_merge_languages = managed_list.merge_pairs
_exclude_hidden_languages = managed_list.exclude_hidden_codes


def load_hidden_default_language_codes() -> list[str]:
    return _load_names(_HIDDEN_DEFAULT_LANGUAGES_KEY)


def hide_default_language(code: str) -> None:
    _save_names(_HIDDEN_DEFAULT_LANGUAGES_KEY, managed_list.add_code(load_hidden_default_language_codes(), code))


def restore_default_languages() -> None:
    _save_names(_HIDDEN_DEFAULT_LANGUAGES_KEY, [])


def load_visible_default_languages() -> list[tuple[str, str]]:
    return _exclude_hidden_languages(DEFAULT_LANGUAGES, load_hidden_default_language_codes())


def load_languages() -> list[tuple[str, str]]:
    """Visible (non-hidden) default languages plus any custom ones
    added previously -- what the quick-pick "+" menu shows."""
    return _merge_languages(load_visible_default_languages(), load_custom_languages())


def load_custom_languages() -> list[tuple[str, str]]:
    return managed_list.decode_pairs(_settings().value(_CUSTOM_LANGUAGES_KEY, "", type=str))


def add_custom_language(code: str, name: str) -> None:
    pairs = managed_list.add_pair(load_custom_languages(), code, name)
    _settings().setValue(_CUSTOM_LANGUAGES_KEY, managed_list.encode_pairs(pairs))


def remove_custom_language(code: str) -> None:
    pairs = managed_list.remove_pair(load_custom_languages(), code)
    _settings().setValue(_CUSTOM_LANGUAGES_KEY, managed_list.encode_pairs(pairs))
