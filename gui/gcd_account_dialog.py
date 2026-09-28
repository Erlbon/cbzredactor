"""
gui/gcd_account_dialog.py

Settings > GCD Account... -- an optional Grand Comics Database login.
GCD's API allows anonymous use with an hourly limit; a (free) logged-in
account gets a higher one, and GCD has said anonymous access will
likely be switched off eventually. Without an account, GCD lookups
simply run anonymously, as before.

Stored per install in the settings file (gui/app_settings.py,
password scrambled -- see there for why it can't be hashed).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.gcd_lookup import GcdAuthError, GcdLookupError, check_login
from gui import app_settings

GCD_SITE_URL = "https://www.comics.org/"


class GcdAccountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GCD Account")
        self.setMinimumWidth(440)
        outer = QVBoxLayout(self)

        intro = QLabel(
            "Optional. Without an account, Grand Comics Database lookups run "
            "anonymously, which GCD limits to a smaller number of requests per "
            "hour. A free account raises that limit (and GCD has said anonymous "
            f'access may be switched off eventually). Register at <a href="{GCD_SITE_URL}">comics.org</a>.'
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(intro)

        username, password = app_settings.load_gcd_account()
        form = QFormLayout()
        self.username_edit = QLineEdit(username)
        self.password_edit = QLineEdit(password)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username:", self.username_edit)
        form.addRow("Password:", self.password_edit)
        show = QCheckBox("Show password")
        show.toggled.connect(
            lambda on: self.password_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        form.addRow("", show)
        outer.addLayout(form)

        note = QLabel(
            "Saved in this app's settings file on this computer, scrambled "
            "(not encrypted) so it isn't readable at a glance."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        outer.addWidget(note)

        self.test_button = QPushButton("Test Login")
        self.test_button.clicked.connect(self._test_login)
        outer.addWidget(self.test_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.remove_button = buttons.addButton("Remove Account", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.remove_button.setEnabled(bool(username))
        self.remove_button.clicked.connect(self._remove)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _test_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            QMessageBox.information(self, "Test Login", "Enter a username and password first.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            check_login(username, password)
        except GcdAuthError:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Test Login", "GCD didn't accept that username/password.")
            return
        except GcdLookupError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Test Login", f"Couldn't check the login: {exc}")
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Test Login", "Login accepted.")

    def _remove(self) -> None:
        app_settings.save_gcd_account("", "")
        self.username_edit.clear()
        self.password_edit.clear()
        self.remove_button.setEnabled(False)
        super().accept()

    def accept(self) -> None:
        app_settings.save_gcd_account(self.username_edit.text(), self.password_edit.text())
        super().accept()
