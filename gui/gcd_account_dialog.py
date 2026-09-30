"""
gui/gcd_account_dialog.py

Settings > GCD Account... -- an optional Grand Comics Database login.
GCD's API allows anonymous use with an hourly limit; a (free) logged-in
account gets a higher one, and GCD has said anonymous access will
likely be switched off eventually. Without an account, GCD lookups
simply run anonymously, as before.

The username is kept in the settings file (gui/app_settings.py); the
password is kept in the secret store (OS credential store, or an
opt-in unencrypted file), never in the settings file.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
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
from gui.secret_prompts import save_with_fallback_prompt
from redactor_common.gui.secret_field import SecretField

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

        username = app_settings.load_gcd_username()
        form = QFormLayout()
        self.username_edit = QLineEdit(username)
        self.password_field = SecretField(app_settings.SECRET_APP, app_settings.GCD_SECRET)
        form.addRow("Username:", self.username_edit)
        form.addRow("Password:", self.password_field)
        outer.addLayout(form)

        note = QLabel(
            "The password is kept in your computer's secure credential store, "
            "not in this app's settings file. Leave the password box empty to "
            "keep the stored one."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        outer.addWidget(note)

        self.test_button = QPushButton("Test Login")
        self.test_button.clicked.connect(self._test_login)
        outer.addWidget(self.test_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.remove_button = buttons.addButton("Remove Account", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.remove_button.setEnabled(bool(username) or app_settings.load_gcd_password() != "")
        self.remove_button.clicked.connect(self._remove)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _test_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_field.new_value() or app_settings.load_gcd_password()
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
        self.password_field.refresh()
        self.remove_button.setEnabled(False)
        super().accept()

    def _save(self, allow: bool | None) -> None:
        username = self.username_edit.text().strip()
        field = self.password_field
        if not username:
            app_settings.save_gcd_account("", "")  # blank username = no account
            return
        if field.apply(allow):
            # Replaced or removed: drop any pre-migration scrambled copy.
            app_settings.clear_legacy_gcd_password()
        app_settings.save_gcd_username(username)

    def accept(self) -> None:
        if not save_with_fallback_prompt(self, self._save):
            return  # declined: stay open, the typed password is kept
        super().accept()
