"""
gui/api_keys_dialog.py

Tools > API Keys...: every online-source credential in one dialog, the
pattern the other Redactor apps use. Two sections: the Comic Vine API key
and the optional Grand Comics Database account. The secrets live in
redactor_common's secret store (OS credential store, or an opt-in
unencrypted file), never in the settings file; the GCD username is kept in
the settings file (gui/app_settings.py).

The single-purpose dialogs (gui/comicvine_key_dialog.py, which the Comic
Vine lookup still opens when no key is stored yet, and
gui/gcd_account_dialog.py) are unchanged; this one has the same fields
and the same save rules, so the two ways in agree.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.gcd_lookup import GcdAuthError, GcdLookupError, check_login
from gui import app_settings
from gui.gcd_account_dialog import GCD_SITE_URL
from gui.secret_prompts import save_with_fallback_prompt
from redactor_common.gui.secret_field import SecretField


class ApiKeysDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Keys")
        self.setMinimumWidth(480)
        outer = QVBoxLayout(self)

        # --- Comic Vine -------------------------------------------------
        comicvine = QGroupBox("Comic Vine")
        cv_layout = QVBoxLayout(comicvine)
        cv_intro = QLabel(
            "Your Comic Vine API key (free -- register at comicvine.gamespot.com/api/). "
            "Leave the box empty to keep the stored key."
        )
        cv_intro.setWordWrap(True)
        cv_layout.addWidget(cv_intro)
        cv_form = QFormLayout()
        self.key_field = SecretField(app_settings.SECRET_APP, app_settings.COMICVINE_SECRET)
        cv_form.addRow("API key:", self.key_field)
        cv_layout.addLayout(cv_form)
        outer.addWidget(comicvine)

        # --- Grand Comics Database --------------------------------------
        gcd = QGroupBox("Grand Comics Database (optional)")
        gcd_layout = QVBoxLayout(gcd)
        gcd_intro = QLabel(
            "Without an account, GCD lookups run anonymously, which GCD limits to a smaller "
            "number of requests per hour. A free account raises that limit (and GCD has said "
            f'anonymous access may be switched off eventually). Register at <a href="{GCD_SITE_URL}">comics.org</a>.'
        )
        gcd_intro.setWordWrap(True)
        gcd_intro.setOpenExternalLinks(True)
        gcd_intro.setTextFormat(Qt.TextFormat.RichText)
        gcd_layout.addWidget(gcd_intro)
        username = app_settings.load_gcd_username()
        gcd_form = QFormLayout()
        self.username_edit = QLineEdit(username)
        self.password_field = SecretField(app_settings.SECRET_APP, app_settings.GCD_SECRET)
        gcd_form.addRow("Username:", self.username_edit)
        gcd_form.addRow("Password:", self.password_field)
        gcd_layout.addLayout(gcd_form)
        self.test_button = QPushButton("Test Login")
        self.test_button.clicked.connect(self._test_login)
        gcd_layout.addWidget(self.test_button)
        self.remove_account_button = QPushButton("Remove Account")
        self.remove_account_button.setEnabled(bool(username) or app_settings.load_gcd_password() != "")
        self.remove_account_button.clicked.connect(self._remove_account)
        gcd_layout.addWidget(self.remove_account_button)
        outer.addWidget(gcd)

        note = QLabel(
            "Keys and passwords are kept in your computer's secure credential store, "
            "not in this app's settings file."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        outer.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
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

    def _remove_account(self) -> None:
        app_settings.save_gcd_account("", "")
        self.username_edit.clear()
        self.password_field.refresh()
        self.remove_account_button.setEnabled(False)

    def _save(self, allow: bool | None) -> None:
        # Comic Vine key: replaced or removed -> a leftover pre-migration
        # ini copy must not linger (or come back as the "legacy" fallback).
        if self.key_field.apply(allow):
            app_settings.clear_legacy_comicvine_api_key()
        # GCD account: a blank username means no account.
        username = self.username_edit.text().strip()
        if not username:
            app_settings.save_gcd_account("", "")
            return
        if self.password_field.apply(allow):
            app_settings.clear_legacy_gcd_password()
        app_settings.save_gcd_username(username)

    def accept(self) -> None:
        if not save_with_fallback_prompt(self, self._save):
            return  # declined: stay open, the typed values are kept
        super().accept()
