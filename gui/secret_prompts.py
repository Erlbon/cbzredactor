"""
gui/secret_prompts.py

Shared by the Comic Vine key and GCD account dialogs: run a save that
writes to redactor_common's secret store and, when there is no secure
store on this machine, ask the user once whether an UNENCRYPTED file
may be used instead (remembering a yes in the settings).
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import QMessageBox

from gui import app_settings
from redactor_common.core import secret_store


def ask_allow_unencrypted_fallback(parent) -> bool:
    answer = QMessageBox.question(
        parent,
        "No Secure Storage Available",
        "This computer has no usable secure credential store (such as Windows "
        "Credential Manager), so the key or password can't be stored safely.\n\n"
        "It can instead be saved in a plain, UNENCRYPTED file in your user "
        "profile folder. Anyone who can read your files could read it.\n\n"
        "Save it in an unencrypted file, and remember that choice?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def save_with_fallback_prompt(parent, save: Callable[[bool | None], object]) -> bool:
    """Call save(None); on SecretStoreUnavailable ask the user and, if they
    agree, remember it and call save(True). True when the save happened."""
    try:
        save(None)
        return True
    except secret_store.SecretStoreUnavailable:
        pass
    if not ask_allow_unencrypted_fallback(parent):
        return False
    app_settings.save_allow_unencrypted_fallback(True)
    secret_store.set_allow_unencrypted_fallback(True)
    save(True)
    return True
