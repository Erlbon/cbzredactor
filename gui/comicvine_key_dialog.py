"""
gui/comicvine_key_dialog.py

Settings > Comic Vine API Key... (also shown by the Comic Vine lookup
when no key is stored yet). The key lives in the secret store, never in
the settings file.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from gui import app_settings
from gui.secret_prompts import save_with_fallback_prompt
from redactor_common.gui.secret_field import SecretField


class ComicVineKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Comic Vine API Key")
        self.setMinimumWidth(440)
        outer = QVBoxLayout(self)
        intro = QLabel(
            "Enter your Comic Vine API key (free -- register at "
            "comicvine.gamespot.com/api/). Leave the box empty to keep the stored key."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        self.key_field = SecretField(app_settings.SECRET_APP, app_settings.COMICVINE_SECRET)
        form.addRow("API key:", self.key_field)
        outer.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _save(self, allow: bool | None) -> None:
        if self.key_field.apply(allow):
            # Replaced or removed: a leftover pre-migration ini copy must
            # not linger (or come back as the "legacy" fallback).
            app_settings.clear_legacy_comicvine_api_key()

    def accept(self) -> None:
        if not save_with_fallback_prompt(self, self._save):
            return  # declined: stay open, the typed value is kept
        super().accept()
