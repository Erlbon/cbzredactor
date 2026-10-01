"""
gui/app_settings.py

Thin wrapper around QSettings for the things this app needs to
remember across runs: table column
order/widths/visibility, rename/parse-filename pattern history, and
custom Genre/Language quick-pick entries. (Secrets -- the Comic Vine
key, the GCD password -- are NOT kept here; see the secret store
section below.) Stored in a plain .ini file
next to the executable (or, in dev mode, at the project root) -- not
the Windows registry, since a plain file is easier to back up, copy to
a new machine, or inspect directly. Same approach as epubredactor's
gui/app_settings.py, and the genre/language management functions below
mirror its exact shape (hideable defaults + custom entries) -- ISO
language codes and comic genre labels are plain facts/conventions, not
something worth inventing a different persistence scheme for here.
"""

from __future__ import annotations

import base64
import json
import os

from core.app_paths import base_dir
from core.comic_genres import COMMON_COMIC_GENRES
from core.comic_languages import DEFAULT_LANGUAGES
from core.image_resize import DEFAULT_MAX_WIDTH
from redactor_common.core import managed_list, pattern_history, secret_store

_SETTINGS_FILENAME = "cbzredactor_settings.ini"
_COMICVINE_API_KEY = "comicvine/api_key"  # legacy plaintext; read for migration only

# Secret store names are PERMANENT (they are the OS credential entries).
SECRET_APP = "cbzredactor"
COMICVINE_SECRET = "comicvine_api_key"
GCD_SECRET = "gcd_password"
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
_RESIZE_MAX_HEIGHT_KEY = "resize/max_height"  # 0 = no height limit
_RESIZE_OUTPUT_FORMAT_KEY = "resize/output_format"  # "" = keep each page's format
_DEFAULT_RESIZE_MAX_WIDTH = DEFAULT_MAX_WIDTH  # see core/image_resize.py
_DEFAULT_RESIZE_JPEG_QUALITY = 90


def _settings_ini_path() -> str:
    return os.path.join(base_dir(), _SETTINGS_FILENAME)


def _settings():
    # Imported lazily so this module doesn't force a Qt platform
    # backend to exist just to be imported (e.g. from a test).
    from PyQt6.QtCore import QSettings

    return QSettings(_settings_ini_path(), QSettings.Format.IniFormat)


def _legacy_comicvine_api_key() -> str:
    return str(_settings().value(_COMICVINE_API_KEY, ""))


def clear_legacy_comicvine_api_key() -> None:
    settings = _settings()
    settings.remove(_COMICVINE_API_KEY)
    settings.sync()


def load_comicvine_api_key() -> str:
    # `legacy` keeps an install that hasn't migrated yet (keyring
    # unavailable, or migrate_legacy_secrets() not run) working.
    return secret_store.get_secret(
        SECRET_APP, COMICVINE_SECRET, legacy=_legacy_comicvine_api_key
    ).strip()


def save_comicvine_api_key(api_key: str, allow_unencrypted_fallback: bool | None = None) -> None:
    """Raises SecretStoreUnavailable when there is no secure store and
    the unencrypted fallback isn't allowed; the old ini value is then
    left alone. A blank key removes it."""
    api_key = api_key.strip()
    if api_key:
        secret_store.set_secret(SECRET_APP, COMICVINE_SECRET, api_key, allow_unencrypted_fallback)
    else:
        secret_store.delete_secret(SECRET_APP, COMICVINE_SECRET)
    clear_legacy_comicvine_api_key()


# ------------------------------------------------------------------
# GCD account (Settings > GCD Account...) -- optional; GCD's API gives
# a logged-in account a higher hourly limit than anonymous access.
#
# The username is not a secret and stays in the ini. The password lives
# in redactor_common's secret store (OS credential store, or the opt-in
# unencrypted file). The old XOR-scrambled ini value is only ever READ,
# to migrate it (see migrate_legacy_secrets()).
# ------------------------------------------------------------------

_GCD_USERNAME_KEY = "gcd/username"
_GCD_PASSWORD_KEY = "gcd/password"
_SCRAMBLE_PREFIX = "s1:"
_SCRAMBLE_KEY = b"cbzredactor-gcd"


def unscramble(stored: str) -> str:
    """Decode the old scrambled ini password; legacy migration only."""
    if not stored.startswith(_SCRAMBLE_PREFIX):
        return ""
    try:
        mixed = base64.urlsafe_b64decode(stored[len(_SCRAMBLE_PREFIX):].encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return ""
    data = bytes(b ^ _SCRAMBLE_KEY[i % len(_SCRAMBLE_KEY)] for i, b in enumerate(mixed))
    return data.decode("utf-8", errors="replace")


def _legacy_gcd_password() -> str:
    return unscramble(str(_settings().value(_GCD_PASSWORD_KEY, "")))


def clear_legacy_gcd_password() -> None:
    settings = _settings()
    settings.remove(_GCD_PASSWORD_KEY)
    settings.sync()


def load_gcd_username() -> str:
    return str(_settings().value(_GCD_USERNAME_KEY, "")).strip()


def load_gcd_password() -> str:
    return secret_store.get_secret(SECRET_APP, GCD_SECRET, legacy=_legacy_gcd_password)


def load_gcd_account() -> tuple[str, str]:
    """(username, password); ("", "") when none is set."""
    username = load_gcd_username()
    password = load_gcd_password()
    return (username, password) if username and password else ("", "")


def save_gcd_username(username: str) -> None:
    settings = _settings()
    username = username.strip()
    if username:
        settings.setValue(_GCD_USERNAME_KEY, username)
    else:
        settings.remove(_GCD_USERNAME_KEY)
    settings.sync()


def save_gcd_password(password: str, allow_unencrypted_fallback: bool | None = None) -> None:
    """Raises SecretStoreUnavailable (see save_comicvine_api_key)."""
    if password:
        secret_store.set_secret(SECRET_APP, GCD_SECRET, password, allow_unencrypted_fallback)
    else:
        secret_store.delete_secret(SECRET_APP, GCD_SECRET)
    clear_legacy_gcd_password()


def save_gcd_account(username: str, password: str, allow_unencrypted_fallback: bool | None = None) -> None:
    """Blank username or password removes the account."""
    username = username.strip()
    if username and password:
        save_gcd_password(password, allow_unencrypted_fallback)
        save_gcd_username(username)
    else:
        save_gcd_password("")
        save_gcd_username("")


# ------------------------------------------------------------------
# Secret store plumbing
# ------------------------------------------------------------------

_ALLOW_UNENCRYPTED_KEY = "secrets/allow_unencrypted_fallback"


def load_allow_unencrypted_fallback() -> bool:
    return bool(_settings().value(_ALLOW_UNENCRYPTED_KEY, False, type=bool))


def save_allow_unencrypted_fallback(enabled: bool) -> None:
    settings = _settings()
    settings.setValue(_ALLOW_UNENCRYPTED_KEY, bool(enabled))
    settings.sync()


def migrate_legacy_secrets() -> None:
    """Startup: apply the remembered fallback choice, then move the old
    ini values into the secret store. A value is cleared from the ini
    only after the store read it back; with no usable store it stays
    (and keeps working through get_secret's `legacy`)."""
    secret_store.set_allow_unencrypted_fallback(load_allow_unencrypted_fallback())
    secret_store.migrate_legacy_secret(
        SECRET_APP, COMICVINE_SECRET, _legacy_comicvine_api_key, clear_legacy_comicvine_api_key
    )
    secret_store.migrate_legacy_secret(
        SECRET_APP, GCD_SECRET, _legacy_gcd_password, clear_legacy_gcd_password
    )


def credit_pages_path() -> str:
    """Learned scanner credit pages (core/credit_pages.py) -- their own
    JSON file next to the settings, since each carries a thumbnail."""
    return os.path.join(base_dir(), "cbzredactor_credit_pages.json")


_GCD_LOCAL_DB_KEY = "gcd/local_database"


def load_gcd_local_database() -> str:
    """Path to the user's own downloaded GCD SQLite dump, or ""."""
    return str(_settings().value(_GCD_LOCAL_DB_KEY, ""))


def save_gcd_local_database(path: str) -> None:
    _settings().setValue(_GCD_LOCAL_DB_KEY, path or "")


_COMICRACK_DB_KEY = "comicrack/library_database"


def load_comicrack_database() -> str:
    """Path to the user's ComicRack library converted to GCD's layout
    (core/comicrack_import.py), or ""."""
    return str(_settings().value(_COMICRACK_DB_KEY, ""))


def save_comicrack_database(path: str) -> None:
    _settings().setValue(_COMICRACK_DB_KEY, path or "")


def default_comicrack_database_path() -> str:
    """Where a new conversion is suggested to go: next to the settings."""
    return os.path.join(base_dir(), "comicrack_library.db")


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


_CLEANUP_KEYS = {
    "remove_junk": "cleanup/remove_junk",
    "flatten_folders": "cleanup/flatten_folders",
    "rename_pages": "cleanup/rename_pages",
}


def load_cleanup_options():
    """Clean Up Archive Contents' three checkboxes, remembered between runs.
    All on until the user changes them (what the command always did)."""
    from core.archive_contents import CleanupOptions

    settings = _settings()
    return CleanupOptions(**{field: bool(settings.value(key, True, type=bool)) for field, key in _CLEANUP_KEYS.items()})


def save_cleanup_options(options) -> None:
    settings = _settings()
    for field, key in _CLEANUP_KEYS.items():
        settings.setValue(key, bool(getattr(options, field)))


def load_resize_max_width() -> int:
    return int(_settings().value(_RESIZE_MAX_WIDTH_KEY, _DEFAULT_RESIZE_MAX_WIDTH))


def save_resize_max_width(max_width: int) -> None:
    _settings().setValue(_RESIZE_MAX_WIDTH_KEY, int(max_width))


def load_resize_jpeg_quality() -> int:
    return int(_settings().value(_RESIZE_JPEG_QUALITY_KEY, _DEFAULT_RESIZE_JPEG_QUALITY))


def save_resize_jpeg_quality(quality: int) -> None:
    _settings().setValue(_RESIZE_JPEG_QUALITY_KEY, int(quality))


# ------------------------------------------------------------------
# Foreign/mislabeled archives on load (Settings > Converting to CBZ...)
# ------------------------------------------------------------------

FOREIGN_LOAD_UNCONVERTED = "unconverted"  # list read-only, convert later from the table
FOREIGN_LOAD_CONVERT = "convert"  # convert during load, no prompt
FOREIGN_LOAD_ASK = "ask"  # prompt once per batch
_FOREIGN_LOAD_BEHAVIORS = (FOREIGN_LOAD_UNCONVERTED, FOREIGN_LOAD_CONVERT, FOREIGN_LOAD_ASK)
_FOREIGN_LOAD_KEY = "conversion/on_load"
_DELETE_ORIGINALS_KEY = "conversion/recycle_originals"


def load_foreign_load_behavior() -> str:
    """Defaults to FOREIGN_LOAD_UNCONVERTED: nothing is written or
    removed until the user explicitly converts."""
    value = str(_settings().value(_FOREIGN_LOAD_KEY, FOREIGN_LOAD_UNCONVERTED))
    return value if value in _FOREIGN_LOAD_BEHAVIORS else FOREIGN_LOAD_UNCONVERTED


def save_foreign_load_behavior(behavior: str) -> None:
    if behavior in _FOREIGN_LOAD_BEHAVIORS:
        _settings().setValue(_FOREIGN_LOAD_KEY, behavior)


def load_recycle_originals() -> bool:
    """Move originals to the Recycle Bin after a verified conversion.
    Off by default."""
    return _settings().value(_DELETE_ORIGINALS_KEY, False, type=bool)


def save_recycle_originals(enabled: bool) -> None:
    _settings().setValue(_DELETE_ORIGINALS_KEY, bool(enabled))


def load_resize_max_height() -> int:
    return int(_settings().value(_RESIZE_MAX_HEIGHT_KEY, 0))


def save_resize_max_height(max_height: int) -> None:
    _settings().setValue(_RESIZE_MAX_HEIGHT_KEY, int(max_height))


def load_resize_output_format() -> str:
    return str(_settings().value(_RESIZE_OUTPUT_FORMAT_KEY, ""))


def save_resize_output_format(output_format: str) -> None:
    _settings().setValue(_RESIZE_OUTPUT_FORMAT_KEY, output_format or "")


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


_LIBRARY_ROOT_KEY = "rename/library_root"
_REDACT_RECIPE_KEY = "redact/recipe"


def load_library_root() -> str:
    """The Rename / Export dialog's "Move into folders" library root, or ""."""
    return str(_settings().value(_LIBRARY_ROOT_KEY, "", type=str))


def save_library_root(path: str) -> None:
    _settings().setValue(_LIBRARY_ROOT_KEY, path or "")


def load_redact_recipe() -> str:
    """The Redact recipe as JSON (core/redact_steps.py), or "" for the
    default. Holds step order, on/off and options -- never a secret."""
    return str(_settings().value(_REDACT_RECIPE_KEY, "", type=str))


def save_redact_recipe(recipe_json: str) -> None:
    _settings().setValue(_REDACT_RECIPE_KEY, recipe_json or "")


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


_ASCII_FILENAMES_KEY = "rename/ascii_only"


def load_ascii_filenames() -> bool:
    """Rename/Export by Pattern's "ASCII-safe filenames" checkbox,
    remembered between runs (redactor_common's RenamePatternDialog)."""
    return bool(_settings().value(_ASCII_FILENAMES_KEY, False, type=bool))


def save_ascii_filenames(enabled: bool) -> None:
    _settings().setValue(_ASCII_FILENAMES_KEY, bool(enabled))


_ZERO_PAD_ENABLED_KEY = "rename/zero_pad_enabled"
_ZERO_PAD_WIDTH_KEY = "rename/zero_pad_width"
_AUTO_NUMBER_PADDING_KEY = "auto_numbering/padding"


def load_rename_zero_pad() -> tuple[bool, int]:
    """Rename/Export by Pattern's zero-pad checkbox and width,
    remembered between runs (redactor_common's RenamePatternDialog)."""
    settings = _settings()
    return (
        bool(settings.value(_ZERO_PAD_ENABLED_KEY, False, type=bool)),
        int(settings.value(_ZERO_PAD_WIDTH_KEY, 2, type=int)),
    )


def save_rename_zero_pad(enabled: bool, width: int) -> None:
    settings = _settings()
    settings.setValue(_ZERO_PAD_ENABLED_KEY, bool(enabled))
    settings.setValue(_ZERO_PAD_WIDTH_KEY, int(width))


def load_auto_number_padding() -> int:
    """Auto-Numbering's "Zero-pad to" width, remembered between runs."""
    return int(_settings().value(_AUTO_NUMBER_PADDING_KEY, 2, type=int))


def save_auto_number_padding(width: int) -> None:
    _settings().setValue(_AUTO_NUMBER_PADDING_KEY, int(width))


# ------------------------------------------------------------------
# Whole-list writers, used by Import Settings (gui/settings_adapter.py):
# the per-item add/remove functions above can't express "replace with
# this list".
# ------------------------------------------------------------------

def save_pattern_history(history: list[str]) -> None:
    _settings().setValue(_PATTERN_HISTORY_KEY, pattern_history.encode_history(history[:_MAX_PATTERN_HISTORY]))


def save_custom_genres(genres: list[str]) -> None:
    _save_names(_CUSTOM_GENRES_KEY, genres)


def save_hidden_default_genres(genres: list[str]) -> None:
    _save_names(_HIDDEN_DEFAULT_GENRES_KEY, genres)


def save_custom_languages(pairs: list[tuple[str, str]]) -> None:
    _settings().setValue(_CUSTOM_LANGUAGES_KEY, managed_list.encode_pairs(pairs))


def save_hidden_default_language_codes(codes: list[str]) -> None:
    _save_names(_HIDDEN_DEFAULT_LANGUAGES_KEY, codes)
