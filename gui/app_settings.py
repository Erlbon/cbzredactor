"""
gui/app_settings.py

Thin wrapper around QSettings for the small set of things this app
needs to remember across runs -- currently just the user's own Comic
Vine API key. Stored in a plain .ini file next to the executable (or,
in dev mode, at the project root) -- not the Windows registry, since a
plain file is easier to back up, copy to a new machine, or inspect
directly. Same approach as epubredactor's gui/app_settings.py.
"""

from __future__ import annotations

import os

from core.app_paths import base_dir

_SETTINGS_FILENAME = "cbzredactor_settings.ini"
_COMICVINE_API_KEY = "comicvine/api_key"


def _settings_ini_path() -> str:
    return os.path.join(base_dir(), _SETTINGS_FILENAME)


def _settings():
    # Imported lazily so this module doesn't force a Qt platform
    # backend to exist just to be imported (e.g. from a test).
    from PyQt6.QtCore import QSettings

    return QSettings(_settings_ini_path(), QSettings.Format.IniFormat)


def load_comicvine_api_key() -> str:
    return str(_settings().value(_COMICVINE_API_KEY, ""))


def save_comicvine_api_key(api_key: str) -> None:
    settings = _settings()
    settings.setValue(_COMICVINE_API_KEY, api_key.strip())
    settings.sync()
