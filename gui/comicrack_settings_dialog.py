"""
gui/comicrack_settings_dialog.py

Settings > ComicRack Library Database... -- points the app at a
ComicRack library converted to GCD's table layout, and builds that file:
Build from ComicRack Library... asks for ComicRack's ComicDb.xml (plain
or zipped) and converts it (core/comicrack_import.py) behind a
cancellable progress dialog -- about 40 s for a 234k-book library.

The dialog is redactor_common's LocalDatabaseSettingsDialog with its
Build button; the lookup is the local GCD one (Import > Look Up via
ComicRack Library...).
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget
from redactor_common.core.dump_import import DumpImportError
from redactor_common.core.local_db import forget_cached
from redactor_common.gui.dump_import_runner import run_dump_import
from redactor_common.gui.local_db_settings_dialog import LocalDatabaseSettingsDialog

from core.comicrack_import import build_comicrack_database
from core.gcd_local import GcdLocalDatabase, GcdLocalError
from gui import app_settings

INSTRUCTIONS = (
    "Look up comics in your own <b>ComicRack</b> library -- everything you've "
    "already tagged there, searched the same way as the local GCD database.<br><br>"
    "<b>Building it:</b><ol>"
    "<li>On the machine with ComicRack, close ComicRack and copy its library file "
    "<code>ComicDb.xml</code> from <code>%APPDATA%\\cYo\\ComicRack Community Edition\\</code> "
    "(zip it first if it has to travel -- a zipped file can be read directly).</li>"
    "<li>Click <b>Build from ComicRack Library...</b>, choose that file, then where to "
    "save the converted database.</li></ol>"
    "Only descriptions of comics are copied (series, issues, credits, characters, "
    "summaries, Comic Vine links) -- never file paths or reading history. Build again "
    "whenever the ComicRack library has changed."
)


def _check(path: str) -> str:
    db = GcdLocalDatabase(path)
    try:
        info = db.summary()
        if not db.is_comicrack:
            raise GcdLocalError(
                "This is a GCD dump, not a converted ComicRack library -- set it under "
                "Settings > GCD Local Database... instead."
            )
    finally:
        db.close()
    return f"Looks good: {info['series']:,} series and {info['issues']:,} issues, built {info['newest'][:10] or 'unknown'}."


def _comicrack_folder() -> str:
    folder = os.path.join(os.environ.get("APPDATA", ""), "cYo", "ComicRack Community Edition")
    return folder if os.path.isdir(folder) else ""


def build_from_comicrack(parent: QWidget, current_path: str = "") -> Optional[str]:
    """Asks for ComicDb.xml and a destination, converts, and returns the
    new database's path (None if abandoned, cancelled or failed)."""
    source, _ = QFileDialog.getOpenFileName(
        parent, "Choose ComicRack's library file", _comicrack_folder(),
        "ComicRack library (*.xml *.zip *.gz);;All files (*)",
    )
    if not source:
        return None
    dest, _ = QFileDialog.getSaveFileName(
        parent, "Save the converted library as",
        current_path or app_settings.default_comicrack_database_path(), "SQLite database (*.db)",
    )
    if not dest:
        return None
    forget_cached(dest)  # a lookup may still hold the old build open
    try:
        summary = run_dump_import(
            parent, "Build ComicRack Library Database", "Converting the ComicRack library…",
            lambda progress, cancelled: build_comicrack_database(source, dest, progress, cancelled),
        )
    except DumpImportError as exc:
        QMessageBox.warning(parent, "Build ComicRack Library Database", str(exc))
        return None
    if summary is None:
        return None
    QMessageBox.information(parent, "Build ComicRack Library Database", summary.describe())
    return dest


class ComicRackSettingsDialog(LocalDatabaseSettingsDialog):
    def __init__(self, parent=None):
        super().__init__(
            title="ComicRack Library Database",
            instructions_html=INSTRUCTIONS,
            path=app_settings.load_comicrack_database(),
            check=_check,
            save=app_settings.save_comicrack_database,
            error_types=(GcdLocalError,),
            build=lambda dialog: build_from_comicrack(dialog, dialog.path_edit.text().strip()),
            build_label="Build from ComicRack Library…",
            parent=parent,
        )
