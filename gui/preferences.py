"""
gui/preferences.py

Tools > Preferences... (Ctrl+,): redactor_common's shared Preferences dialog
over this app's existing storage.

The pages are the shared Filenames section plus our own
(Conversion, Resize defaults, Lookups, Paths, History). Every value is read and written
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

import os

from redactor_common.core.preferences import (
    KEY_ASCII_FILENAMES,
    KEY_AUTO_NUMBER_PADDING,
    KEY_ZERO_PAD_NUMBERS,
    KEY_ZERO_PAD_WIDTH,
    CallbackBackend,
    PrefAction,
    PrefSection,
    PrefSpec,
    filenames_section,
)

from core.image_resize import DEFAULT_MAX_WIDTH
from gui import app_settings

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
KEY_SAVE_AFTER_LOOKUP = "save_after_lookup"
KEY_HISTORY_ENABLED = "history_enabled"
DELETE_HISTORY_LABEL = "Delete History…"

# Same ceiling the Export/Import adapter accepts for the padding settings.
MAX_PAD_WIDTH = 10


def preference_sections() -> list[PrefSection]:
    """The dialog's pages, in order."""
    conversion = PrefSection(
        "conversion", "Conversion",
        (
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
        "What happens when a file is not a real CBZ (a CBR, CBT or CB7, or an archive with the wrong extension).",
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
    lookups = PrefSection(
        "lookups", "Lookups",
        (
            PrefSpec(
                KEY_SAVE_AFTER_LOOKUP, "Save the files right after a lookup applies metadata", "bool", True,
                help="Comic Vine, GCD, Bedetheque and the other lookups write the files they changed "
                     "as soon as you accept the results. Off: the files stay marked as changed until "
                     "you Save All. Undo works either way.",
            ),
        ),
        "What happens after a lookup has filled in metadata.",
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
    history_section = PrefSection(
        "history", "History",
        (
            PrefSpec(
                KEY_HISTORY_ENABLED, "Keep a history of tag changes (lets you undo a Save or Redact later)", "bool",
                False,
                help=_history_help(),
            ),
        ),
        "Off unless you turn it on. Tools > History... shows what was recorded.",
        actions=(
            PrefAction(
                DELETE_HISTORY_LABEL, _delete_history,
                help="Sends the history file (the recorded tag changes) to the Recycle Bin at once, after asking. "
                     "The setting is not changed.",
            ),
        ),
    )
    return [filenames, conversion, resize, lookups, paths, history_section]


def _history_help() -> str:
    from core.history import journal_file_path

    return (
        "Stores the old and new text of every ComicInfo field a Save, Redact or the command line changes, "
        "never page images. Keeps the last 90 days, at most 50,000 changes, in "
        f"{journal_file_path()}. You can switch it off again at any time; Delete History sends the file to the Recycle Bin."
    )


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
    KEY_RESIZE_MAX_WIDTH: app_settings.load_resize_max_width,
    KEY_RESIZE_MAX_HEIGHT: app_settings.load_resize_max_height,
    KEY_RESIZE_JPEG_QUALITY: app_settings.load_resize_jpeg_quality,
    KEY_RESIZE_OUTPUT_FORMAT: app_settings.load_resize_output_format,
    KEY_RESIZE_IN_PLACE: app_settings.load_resize_in_place,
    KEY_SAVE_AFTER_LOOKUP: app_settings.load_save_after_lookup,
    KEY_RESIZE_RECYCLE_ORIGINAL: app_settings.load_resize_recycle_original,
    KEY_RESIZE_OVERSIZED_ONLY: app_settings.load_resize_oversized_only,
    KEY_RESIZE_EXPORT_FOLDER: app_settings.load_resize_export_folder,
    KEY_RESIZE_ON_CONVERT: app_settings.load_resize_on_convert,
    KEY_GCD_LOCAL_DB: app_settings.load_gcd_local_database,
    KEY_COMICRACK_DB: app_settings.load_comicrack_database,
    KEY_LIBRARY_ROOT: app_settings.load_library_root,
    KEY_HISTORY_ENABLED: app_settings.load_history_enabled,
}

_WRITERS = {
    KEY_ASCII_FILENAMES: app_settings.save_ascii_filenames,
    KEY_ZERO_PAD_NUMBERS: lambda value: _write_zero_pad(enabled=value),
    KEY_ZERO_PAD_WIDTH: lambda value: _write_zero_pad(width=value),
    KEY_AUTO_NUMBER_PADDING: app_settings.save_auto_number_padding,
    KEY_RESIZE_MAX_WIDTH: app_settings.save_resize_max_width,
    KEY_RESIZE_MAX_HEIGHT: app_settings.save_resize_max_height,
    KEY_RESIZE_JPEG_QUALITY: app_settings.save_resize_jpeg_quality,
    KEY_RESIZE_OUTPUT_FORMAT: app_settings.save_resize_output_format,
    KEY_RESIZE_IN_PLACE: app_settings.save_resize_in_place,
    KEY_SAVE_AFTER_LOOKUP: app_settings.save_save_after_lookup,
    KEY_RESIZE_RECYCLE_ORIGINAL: app_settings.save_resize_recycle_original,
    KEY_RESIZE_OVERSIZED_ONLY: app_settings.save_resize_oversized_only,
    KEY_RESIZE_EXPORT_FOLDER: app_settings.save_resize_export_folder,
    KEY_RESIZE_ON_CONVERT: app_settings.save_resize_on_convert,
    KEY_GCD_LOCAL_DB: app_settings.save_gcd_local_database,
    KEY_COMICRACK_DB: app_settings.save_comicrack_database,
    KEY_LIBRARY_ROOT: app_settings.save_library_root,
    KEY_HISTORY_ENABLED: app_settings.save_history_enabled,
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

    dialog = PreferencesDialog(preference_sections(), make_backend(), parent)
    _add_conversion_policy(dialog)
    # The History page's Delete History... (a PrefAction): only usable while there is a file to delete.
    dialog.delete_history_button = delete_history_button(dialog)
    _refresh_delete_history(dialog)
    return dialog


NEVER_TEXT = "the files are listed as read-only rows (right-click > Convert to CBZ converts them later)"


def _add_conversion_policy(dialog) -> None:
    """The shared conversion-policy widget (redactor_common) on top of the Conversion page. It is saved on
    OK / Apply (only when it was changed, so an untouched page never pins the old settings) and dropped on Cancel,
    like the other pages; `dialog.policy_widget` is the widget."""
    from PyQt6.QtWidgets import QAbstractButton, QLineEdit, QSpinBox

    from redactor_common.gui.conversion_policy_widget import ConversionPolicyWidget

    baseline = app_settings.load_conversion_policy()
    widget = ConversionPolicyWidget(baseline, never_text=NEVER_TEXT)
    dialog.policy_widget = widget
    index = dialog.page_titles().index("Conversion")
    page = dialog._tabs.widget(index) if dialog._tabs is not None else dialog._stack.widget(index)
    page.layout().insertWidget(1, widget)  # under the description, above the app's own options

    state = {"baseline": baseline}

    def changed() -> bool:
        return widget.policy() != state["baseline"]

    def save() -> None:
        if changed():
            state["baseline"] = widget.policy()
            app_settings.save_conversion_policy(state["baseline"])
        dialog._refresh_state()

    refresh = dialog._refresh_state

    def refresh_with_policy(*args) -> None:
        refresh()
        if changed():
            dialog.apply_button.setEnabled(True)

    dialog._refresh_state = refresh_with_policy
    for button in widget.findChildren(QAbstractButton):
        button.toggled.connect(refresh_with_policy)
    for edit in widget.findChildren(QLineEdit):
        edit.textChanged.connect(refresh_with_policy)
    for spin in widget.findChildren(QSpinBox):
        spin.valueChanged.connect(refresh_with_policy)
    dialog.apply_button.clicked.connect(save)
    dialog.accepted.connect(save)


def delete_history_button(dialog):
    """The dialog's Delete History... push button (one of its action_buttons), or None."""
    return next((b for b in getattr(dialog, "action_buttons", []) if b.text() == DELETE_HISTORY_LABEL), None)


def _refresh_delete_history(dialog) -> None:
    from core import history

    button = getattr(dialog, "delete_history_button", None)
    if button is not None:
        button.setEnabled(os.path.exists(history.journal_file_path()))


def _delete_history(dialog) -> None:
    """Delete History...: acts at once (after a confirmation), not on OK/Apply. The file goes to the
    Recycle Bin, never deleted for good; the setting is not changed."""
    from PyQt6.QtWidgets import QMessageBox

    from core import history
    from redactor_common.core.trash import TrashError

    answer = QMessageBox.question(
        dialog, "Delete History",
        f"Send every recorded tag change to the Recycle Bin?\n{history.journal_file_path()}\n\nOld values can then "
        "no longer be restored from Tools > History. The files themselves are not touched.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    try:
        history.delete_history()
    except (OSError, TrashError) as exc:
        QMessageBox.warning(dialog, "Delete History", f"The history could not be moved to the Recycle Bin: {exc}")
    _refresh_delete_history(dialog)
