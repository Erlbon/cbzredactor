"""
gui/gcd_local_settings_dialog.py

Settings > GCD Local Database... -- points the app at the user's own
downloaded copy of the Grand Comics Database (core/gcd_local.py). The
app never bundles or downloads it: GCD offers the dump to registered
users, and this dialog explains how to get it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.gcd_local import GCD_DOWNLOAD_URL, GcdLocalError, GcdLocalDatabase
from gui import app_settings

INSTRUCTIONS = (
    "Look up comics in your own copy of the Grand Comics Database -- fast, "
    "offline, and with no hourly limit.<br><br>"
    "<b>Getting the database:</b><ol>"
    "<li>Create a free account at <a href='https://www.comics.org/'>comics.org</a> "
    "and log in.</li>"
    f"<li>Go to <a href='{GCD_DOWNLOAD_URL}'>{GCD_DOWNLOAD_URL}</a> and download the "
    "<b>SQLite</b> version of the data dump -- a file called <code>current.zip</code>, about 2 GB.</li>"
    "<li>Unzip it somewhere with room to spare: it holds one <code>.db</code> file of about 7 GB, named after the dump date (e.g. <code>2026-09-15.db</code>).</li>"
    "<li>Choose the unzipped <code>.db</code> file below.</li></ol>"
    "Download a newer dump now and then to pick up GCD's latest additions. The "
    "GCD data is the work of GCD's volunteers; see comics.org for its licence."
)


class GcdLocalSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GCD Local Database")
        self.setMinimumWidth(540)
        outer = QVBoxLayout(self)

        intro = QLabel(INSTRUCTIONS)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setOpenExternalLinks(True)
        outer.addWidget(intro)

        row = QHBoxLayout()
        self.path_edit = QLineEdit(app_settings.load_gcd_local_database())
        self.path_edit.setPlaceholderText("Path to the unzipped GCD .db file")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        outer.addLayout(row)

        check = QPushButton("Check File")
        check.clicked.connect(self._check)
        outer.addWidget(check)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the GCD database", self.path_edit.text(), "SQLite database (*.db *.sqlite);;All files (*)"
        )
        if path:
            self.path_edit.setText(path)

    def _check(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            db = GcdLocalDatabase(self.path_edit.text().strip())
            info = db.summary()
            db.close()
        except GcdLocalError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Check File", str(exc))
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(
            self, "Check File",
            f"Looks good: {info['series']:,} series and {info['issues']:,} issues, "
            f"last updated {info['newest'] or 'unknown'}.",
        )

    def accept(self) -> None:
        app_settings.save_gcd_local_database(self.path_edit.text().strip())
        super().accept()
