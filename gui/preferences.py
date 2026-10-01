"""
gui/preferences.py

Tools > Preferences... (Ctrl+,): redactor_common's shared Preferences dialog
over this app's existing storage.

The pages are the shared Filenames section plus three of our own
(Conversion, Resize defaults, Paths). Every value is read and written
through the same gui/app_settings.py load_*/save_* functions the rest of the
app uses, so the ini keys do not change and Export/Import Settings
(gui/settings_adapter.py) keeps working untouched.

Secrets (the Comic Vine key, the GCD password) are deliberately NOT here:
they stay in Tools > API Keys..., which owns the credential-store handling.
The GCD / ComicRack database paths can be edited here, but the dialogs that
check or build those files (Tools > GCD Local Database... / ComicRack
Library Database...) stay as they are.
"""

from __future__ import annotations

from redactor_common.core.preferences import (
    KEY_ASCII_FILENAMES,
    KEY_AUTO_NUMBER_PADDING,
    KEY_ZERO_PAD_NUMBERS,
    KEY_ZERO_PAD_WIDTH,
    CallbackBackend,
    PrefSection,
    PrefSpec,
    filenames_section,
)

from core.image_resize import DEFAULT_MAX_WIDTH
from gui import app_settings

KEY_ON_LOAD = "conversion_on_load"
KEY_RECYCLE_ORIGINALS = "conversion_recycle_originals"
KEY_RESIZE_MAX_WIDTH = "resize_max_width"
KEY_RESIZE_MAX_HEIGHT = "resize_max_height"
KEY_RESIZE_JPEG_QUALITY = "resize_jpeg_quality"
KEY_RESIZE_OUTPUT_FORMAT = "resize_output_format"
KEY_RESIZE_IN_PLACE = "resize_in_place"
KEY_RESIZE_RECYCLE_ORIGINAL = "resize_recycle_original"
KEY_RESIZE_OVERSIZED_ONLY = "resize_oversized_only"
KEY_RESIZE_EXPORT_FOLDER = "resize_export_folder"
KEY_RESIZE_ON_CONVERT = "conversion_resize_on_convert"
KEY_GCD_LOCAL_DB = "gcd_local_database"
KEY_COMICRACK_DB = "comicrack_database"
KEY_LIBRARY_ROOT = "library_root"

# Same ceiling the Export/Import adapter accepts for the padding settings.
MAX_PAD_WIDTH = 10


def preference_sections() -> list[PrefSection]:
    """The dialog's pages, in order."""
    conversion = PrefSection(
        "conversion", "Conversion",
        (
            PrefSpec(
                KEY_ON_LOAD, "When loading CBR/CBT/CB7 (or mislabeled) files", "choice",
                app_settings.FOREIGN_LOAD_UNCONVERTED,
                help="Add to the list unconverted: nothing is written until you ask. "
                     "Convert automatically: converted while loading. "
                     "Ask each time: one prompt per load.",
                choices=(
                    (app_settings.FOREIGN_LOAD_UNCONVERTED, "Add to the list unconverted (recommended)"),
                    (app_settings.FOREIGN_LOAD_CONVERT, "Convert automatically"),
                    (app_settings.FOREIGN_LOAD_ASK, "Ask each time"),
                ),
            ),
            PrefSpec(
                KEY_RECYCLE_ORIGINALS, "Move originals to the Recycle Bin after a conversion", "bool", False,
                help="An original is only moved after its new .cbz opened cleanly with the "
                     "same number of pages: never deleted permanently, never after a failure.",
            ),
            PrefSpec(
                KEY_RESIZE_ON_CONVERT, "Resize pages while converting", "choice",
                app_settings.RESIZE_ON_CONVERT_ASK,
                help="Converting a CBR/CBT/CB7 can shrink its pages in the same step, with the "
                     "Resize defaults below (one pass, not a conversion and then a rewrite). "
                     "Ask: one question per batch. Redact never asks: it resizes only on Always.",
                choices=(
                    (app_settings.RESIZE_ON_CONVERT_ASK, "Ask once per batch"),
                    (app_settings.RESIZE_ON_CONVERT_YES, "Always resize"),
                    (app_settings.RESIZE_ON_CONVERT_NO, "Never resize"),
                ),
            ),
        ),
        "What happens when a file is not a real CBZ.",
    )
    resize = PrefSection(
        "resize", "Resize defaults",
        (
            PrefSpec(
                KEY_RESIZE_MAX_WIDTH, "Max width (single page, px)", "int", DEFAULT_MAX_WIDTH,
                help="The Resize Images dialog starts with this width. A double-page spread "
                     "gets twice as much.",
                minimum=200, maximum=10000,
            ),
            PrefSpec(
                KEY_RESIZE_MAX_HEIGHT, "Max height (px, 0 = no limit)", "int", 0,
                help="Also limit page height; useful for very tall pages. 0 leaves height alone.",
                minimum=0, maximum=30000,
            ),
            PrefSpec(
                KEY_RESIZE_JPEG_QUALITY, "JPEG / WebP quality", "int", 90,
                help="Used for JPEG and WebP pages; PNG pages stay lossless.",
                minimum=50, maximum=100,
            ),
            PrefSpec(
                KEY_RESIZE_OUTPUT_FORMAT, "Output format", "choice", "",
                help="Converting re-encodes every page, not only the oversized ones.",
                choices=(
                    ("", "Keep original format"),
                    ("JPEG", "JPEG"),
                    ("WEBP", "WebP (smaller files; most modern readers support it)"),
                ),
            ),
            PrefSpec(
                KEY_RESIZE_IN_PLACE, "Replace the files in place (instead of exporting copies)", "bool", False,
                help="Resize Images starts on 'Resize files in place'. Off: it exports resized "
                     "copies to a folder and leaves the originals alone.",
            ),
            PrefSpec(
                KEY_RESIZE_RECYCLE_ORIGINAL, "Send the original to the Recycle Bin when replacing in place",
                "bool", True,
                help="The resized file replaces the original only after it was written and checked; "
                     "the original goes to the Recycle Bin, never deleted for good.",
            ),
            PrefSpec(
                KEY_RESIZE_OVERSIZED_ONLY, "Only files marked Oversized", "bool", False,
                help="Resize Images starts with 'Only files marked Oversized' chosen.",
            ),
            PrefSpec(
                KEY_RESIZE_EXPORT_FOLDER, "Export folder", "path", "",
                help="Where Resize Images exports copies to.",
                path_mode="folder",
            ),
        ),
        "What the Resize Images dialog starts with (and remembers from the last run). "
        "You can still change them there each time.",
    )
    paths = PrefSection(
        "paths", "Paths",
        (
            PrefSpec(
                KEY_GCD_LOCAL_DB, "GCD local database", "path", "",
                help="Your own downloaded copy of the Grand Comics Database (a .db file). "
                     "Tools > GCD Local Database... can also check the file.",
                path_mode="file",
            ),
            PrefSpec(
                KEY_COMICRACK_DB, "ComicRack library database", "path", "",
                help="Your ComicRack library converted to GCD's layout. "
                     "Tools > ComicRack Library Database... can build and check it.",
                path_mode="file",
            ),
            PrefSpec(
                KEY_LIBRARY_ROOT, "Library root folder", "path", "",
                help="Where Rename / Export / Move... puts files when moving them into folders.",
                path_mode="folder",
            ),
        ),
        "Where the app finds your databases and library.",
    )
    filenames = filenames_section(
        width_max=MAX_PAD_WIDTH,
        description="Habits for the names the app builds when it renames, exports or moves files. "
                    "The Rename / Export and Auto-Numbering dialogs start from these.",
    )
    return [filenames, conversion, resize, paths]


# --- storage: key -> (read, write) over app_settings ---------------------------


def _write_zero_pad(enabled=None, width=None) -> None:
    current_enabled, current_width = app_settings.load_rename_zero_pad()
    app_settings.save_rename_zero_pad(
        current_enabled if enabled is None else bool(enabled),
        current_width if width is None else int(width),
    )


_READERS = {
    KEY_ASCII_FILENAMES: app_settings.load_ascii_filenames,
    KEY_ZERO_PAD_NUMBERS: lambda: app_settings.load_rename_zero_pad()[0],
    KEY_ZERO_PAD_WIDTH: lambda: app_settings.load_rename_zero_pad()[1],
    KEY_AUTO_NUMBER_PADDING: app_settings.load_auto_number_padding,
    KEY_ON_LOAD: app_settings.load_foreign_load_behavior,
    KEY_RECYCLE_ORIGINALS: app_settings.load_recycle_originals,
    KEY_RESIZE_MAX_WIDTH: app_settings.load_resize_max_width,
    KEY_RESIZE_MAX_HEIGHT: app_settings.load_resize_max_height,
    KEY_RESIZE_JPEG_QUALITY: app_settings.load_resize_jpeg_quality,
    KEY_RESIZE_OUTPUT_FORMAT: app_settings.load_resize_output_format,
    KEY_RESIZE_IN_PLACE: app_settings.load_resize_in_place,
    KEY_RESIZE_RECYCLE_ORIGINAL: app_settings.load_resize_recycle_original,
    KEY_RESIZE_OVERSIZED_ONLY: app_settings.load_resize_oversized_only,
    KEY_RESIZE_EXPORT_FOLDER: app_settings.load_resize_export_folder,
    KEY_RESIZE_ON_CONVERT: app_settings.load_resize_on_convert,
    KEY_GCD_LOCAL_DB: app_settings.load_gcd_local_database,
    KEY_COMICRACK_DB: app_settings.load_comicrack_database,
    KEY_LIBRARY_ROOT: app_settings.load_library_root,
}

_WRITERS = {
    KEY_ASCII_FILENAMES: app_settings.save_ascii_filenames,
    KEY_ZERO_PAD_NUMBERS: lambda value: _write_zero_pad(enabled=value),
    KEY_ZERO_PAD_WIDTH: lambda value: _write_zero_pad(width=value),
    KEY_AUTO_NUMBER_PADDING: app_settings.save_auto_number_padding,
    KEY_ON_LOAD: app_settings.save_foreign_load_behavior,
    KEY_RECYCLE_ORIGINALS: app_settings.save_recycle_originals,
    KEY_RESIZE_MAX_WIDTH: app_settings.save_resize_max_width,
    KEY_RESIZE_MAX_HEIGHT: app_settings.save_resize_max_height,
    KEY_RESIZE_JPEG_QUALITY: app_settings.save_resize_jpeg_quality,
    KEY_RESIZE_OUTPUT_FORMAT: app_settings.save_resize_output_format,
    KEY_RESIZE_IN_PLACE: app_settings.save_resize_in_place,
    KEY_RESIZE_RECYCLE_ORIGINAL: app_settings.save_resize_recycle_original,
    KEY_RESIZE_OVERSIZED_ONLY: app_settings.save_resize_oversized_only,
    KEY_RESIZE_EXPORT_FOLDER: app_settings.save_resize_export_folder,
    KEY_RESIZE_ON_CONVERT: app_settings.save_resize_on_convert,
    KEY_GCD_LOCAL_DB: app_settings.save_gcd_local_database,
    KEY_COMICRACK_DB: app_settings.save_comicrack_database,
    KEY_LIBRARY_ROOT: app_settings.save_library_root,
}


def _get(key: str) -> object:
    try:
        return _READERS[key]()
    except (TypeError, ValueError):
        return None  # a hand-edited ini: the dialog shows the default


def _set(values: dict[str, object]) -> None:
    for key, value in values.items():
        _WRITERS[key](value)


def make_backend() -> CallbackBackend:
    return CallbackBackend(_get, _set)


def make_dialog(parent=None):
    """The Preferences dialog, ready to exec()."""
    from redactor_common.gui.preferences_dialog import PreferencesDialog

    return PreferencesDialog(preference_sections(), make_backend(), parent)
