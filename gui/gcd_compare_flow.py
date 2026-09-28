"""
gui/gcd_compare_flow.py

Import > Compare ComicRack Library with GCD... -- runs core/gcd_compare.py
on the two local databases set in Settings (the converted ComicRack
library and the GCD dump), behind a cancellable progress dialog (about
20-40 s for a 234k-book library), then writes the CSV files next to the
result and offers to open that folder.
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget
from redactor_common.core.dump_import import DumpImportError
from redactor_common.core.local_db import LocalDatabaseError, forget_cached
from redactor_common.gui.dump_import_runner import run_dump_import

from core.gcd_compare import compare_with_gcd, export_csv
from gui import app_settings

TITLE = "Compare ComicRack Library with GCD"


def compare_library_with_gcd(parent: Optional[QWidget]) -> Optional[str]:
    """The whole flow; returns the CSV folder, or None if it didn't run."""
    library, gcd = app_settings.load_comicrack_database(), app_settings.load_gcd_local_database()
    missing = [name for name, path in (("ComicRack Library Database", library), ("GCD Local Database", gcd))
               if not path or not os.path.isfile(path)]
    if missing:
        QMessageBox.information(
            parent, TITLE,
            "This compares your converted ComicRack library with your local copy of the GCD, "
            f"so both need setting up first -- see Settings > {' and Settings > '.join(missing)}.",
        )
        return None
    suggested = os.path.join(os.path.dirname(library), "comicrack_vs_gcd.db")
    dest, _ = QFileDialog.getSaveFileName(parent, "Save the comparison as", suggested, "SQLite database (*.db)")
    if not dest:
        return None
    forget_cached(dest)
    try:
        summary = run_dump_import(
            parent, TITLE, "Comparing your library with the GCD…",
            lambda progress, cancelled: compare_with_gcd(library, gcd, dest, progress, cancelled),
        )
        if summary is None:
            return None
        folder = os.path.splitext(dest)[0] + " csv"
        export_csv(dest, folder)
    except (DumpImportError, LocalDatabaseError, OSError) as exc:
        QMessageBox.warning(parent, TITLE, str(exc))
        return None
    box = QMessageBox(QMessageBox.Icon.Information, TITLE, summary.describe(), parent=parent)
    box.setInformativeText(
        f"Saved to {os.path.basename(dest)}, with spreadsheets (CSV) in \"{os.path.basename(folder)}\": "
        "series and issues not in GCD, and GCD issues missing credits, characters or a summary "
        "that your library has."
    )
    open_button = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    if box.clickedButton() is open_button:
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
    return folder
