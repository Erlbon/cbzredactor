"""
gui/settings_adapter.py

File > Export Settings... / Import Settings...: the SettingsAdapter
(redactor_common.core.settings_bundle) over gui/app_settings.py.

What travels (portable sections, ticked by default): Redact recipe,
rename/parse pattern history, rename defaults (ASCII, zero-pad),
column layout, genre/language lists, conversion settings, resize
defaults. Machine-specific (unticked, "this computer only"): the GCD and
ComicRack database paths, the library root and the last-used folder.

Deliberately NOT exported, ever:
  * the Comic Vine key and GCD password (the credential store; the
    bundle's own guard would drop them anyway),
  * the GCD username -- it names the account the password belongs to,
    and a username without its password is only confusing,
  * the "allow unencrypted secret file" choice -- a security setting,
  * Known Credit Pages -- its own JSON file of learned page thumbnails,
    not plain settings data.
Window geometry and sort order are not persisted by this app at all.

Every read coerces to plain JSON types (QSettings hands back strings for
lists/bools/ints); every write validates and skips an unusable value
rather than storing it. Keys not named here are never touched.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from gui import app_settings
from redactor_common.core import settings_bundle as sb

_ON_LOAD_CHOICES = (
    app_settings.FOREIGN_LOAD_UNCONVERTED,
    app_settings.FOREIGN_LOAD_CONVERT,
    app_settings.FOREIGN_LOAD_ASK,
)
_OUTPUT_FORMATS = ("", "JPEG", "WEBP")


def _str_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(value)


def _pair_list(value: Any) -> list[tuple[str, str]] | None:
    if not isinstance(value, list):
        return None
    pairs = []
    for item in value:
        if not (isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, str) for x in item)):
            return None
        pairs.append((item[0], item[1]))
    return pairs


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _int(low: int, high: int) -> Callable[[Any], int | None]:
    def coerce(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return None
        return value
    return coerce


def _choice(choices: Iterable[str]) -> Callable[[Any], str | None]:
    allowed = tuple(choices)
    return lambda value: value if isinstance(value, str) and value in allowed else None


def _path(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _width_map(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    out = {}
    for k, v in value.items():
        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 10000:
            return None
        out[k] = v
    return out


def _recipe(value: Any) -> str | None:
    """The recipe travels as a JSON object (readable in the file), "" for
    the default; stored back as the JSON text the app already keeps."""
    if value == "":
        return ""
    return json.dumps(value) if isinstance(value, (dict, list)) else None


class CbzSettingsAdapter(sb.SettingsAdapter):
    app_slug = "cbzredactor"

    def __init__(self, app_version: str = "", default_hidden_columns: Iterable[str] = ()):
        self.app_version = app_version
        # The first-run hidden set (MainWindow.DEFAULT_HIDDEN_COLUMNS), reported
        # until the user has saved a choice of their own.
        self._default_hidden = sorted(default_hidden_columns)
        # section -> key -> (reader, writer, coerce); one table drives both directions.
        self._table: dict[str, dict[str, tuple[Callable[[], Any], Callable[[Any], None], Callable[[Any], Any]]]] = {
            "recipe": {
                "recipe": (self._read_recipe, app_settings.save_redact_recipe, _recipe),
            },
            "patterns": {
                "history": (app_settings.load_pattern_history, app_settings.save_pattern_history, _str_list),
            },
            "rename_defaults": {
                "ascii_only": (app_settings.load_ascii_filenames, app_settings.save_ascii_filenames, _bool),
                "zero_pad_enabled": (
                    lambda: app_settings.load_rename_zero_pad()[0],
                    lambda v: app_settings.save_rename_zero_pad(v, app_settings.load_rename_zero_pad()[1]),
                    _bool,
                ),
                "zero_pad_width": (
                    lambda: app_settings.load_rename_zero_pad()[1],
                    lambda v: app_settings.save_rename_zero_pad(app_settings.load_rename_zero_pad()[0], v),
                    _int(1, 10),
                ),
                "auto_number_padding": (
                    app_settings.load_auto_number_padding, app_settings.save_auto_number_padding, _int(1, 10),
                ),
            },
            "columns": {
                "order": (app_settings.load_column_order, app_settings.save_column_order, _str_list),
                "hidden": (self._read_hidden, self._write_hidden, _str_list),
                "widths": (app_settings.load_column_widths, app_settings.save_column_widths, _width_map),
            },
            "lists": {
                "custom_genres": (app_settings.load_custom_genres, app_settings.save_custom_genres, _str_list),
                "hidden_default_genres": (
                    app_settings.load_hidden_default_genres, app_settings.save_hidden_default_genres, _str_list,
                ),
                "custom_languages": (
                    lambda: [list(p) for p in app_settings.load_custom_languages()],
                    app_settings.save_custom_languages,
                    _pair_list,
                ),
                "hidden_default_languages": (
                    app_settings.load_hidden_default_language_codes,
                    app_settings.save_hidden_default_language_codes,
                    _str_list,
                ),
            },
            "conversion": {
                "on_load": (
                    app_settings.load_foreign_load_behavior, app_settings.save_foreign_load_behavior,
                    _choice(_ON_LOAD_CHOICES),
                ),
                "recycle_originals": (
                    app_settings.load_recycle_originals, app_settings.save_recycle_originals, _bool,
                ),
            },
            "resize": {
                "max_width": (app_settings.load_resize_max_width, app_settings.save_resize_max_width, _int(1, 20000)),
                "max_height": (
                    app_settings.load_resize_max_height, app_settings.save_resize_max_height, _int(0, 20000),
                ),
                "jpeg_quality": (
                    app_settings.load_resize_jpeg_quality, app_settings.save_resize_jpeg_quality, _int(1, 100),
                ),
                "output_format": (
                    app_settings.load_resize_output_format, app_settings.save_resize_output_format,
                    _choice(_OUTPUT_FORMATS),
                ),
            },
            "paths": {
                "gcd_local_database": (
                    app_settings.load_gcd_local_database, app_settings.save_gcd_local_database, _path,
                ),
                "comicrack_database": (
                    app_settings.load_comicrack_database, app_settings.save_comicrack_database, _path,
                ),
                "library_root": (app_settings.load_library_root, app_settings.save_library_root, _path),
                "last_directory": (app_settings.load_last_directory, self._write_last_directory, _path),
            },
        }

    def sections(self) -> list[sb.SectionSpec]:
        return [
            sb.SectionSpec("recipe", "Redact recipe"),
            sb.SectionSpec("patterns", "Rename / Parse Filename pattern history"),
            sb.SectionSpec("rename_defaults", "Rename and numbering defaults"),
            sb.SectionSpec("columns", "Column layout (order, visibility, widths)"),
            sb.SectionSpec("lists", "Genre and Language lists"),
            sb.SectionSpec("conversion", "Converting to CBZ"),
            sb.SectionSpec("resize", "Resize Images defaults"),
            sb.SectionSpec("paths", "Database paths and last-used folders (this computer only)", portable=False),
        ]

    def read_section(self, key: str) -> dict[str, Any]:
        return {name: reader() for name, (reader, _w, _c) in self._table[key].items()}

    def write_section(self, key: str, values: dict[str, Any]) -> None:
        entries = self._table.get(key)
        if entries is None:
            return
        for name, value in values.items():
            if name not in entries:
                continue
            _reader, writer, coerce = entries[name]
            clean = coerce(value)
            if clean is None:
                continue  # unusable value: keep the current setting
            writer(clean)

    # -- keys that aren't a straight load/save pair --------------------

    @staticmethod
    def _read_recipe() -> Any:
        text = app_settings.load_redact_recipe()
        if not text:
            return ""
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return ""
        return data if isinstance(data, (dict, list)) else ""

    def _read_hidden(self) -> list[str]:
        if app_settings.has_hidden_columns_preference():
            return sorted(app_settings.load_hidden_columns())
        return list(self._default_hidden)

    @staticmethod
    def _write_hidden(hidden: list[str]) -> None:
        app_settings.save_hidden_columns(set(hidden))

    @staticmethod
    def _write_last_directory(path: str) -> None:
        # save_last_directory() refuses a folder that doesn't exist here;
        # the load side already ignores one, so store what the file says.
        app_settings._settings().setValue(app_settings._LAST_DIR_KEY, path)
