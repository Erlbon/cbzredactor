"""
core/app_paths.py

Where this app's persistent, non-bundled files live (settings ini, crash
log), via redactor_common.core.app_paths (2026-09-23) -- the frozen-vs-
dev walk every Redactor app used to reimplement. This project's own root
is passed in, since the shared package lives in site-packages.

Pure logic, no Qt dependency -- importable before QApplication exists.
"""

from __future__ import annotations

import os

from redactor_common.core import app_paths as _shared

APP_SLUG = "cbzredactor"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def base_dir() -> str:
    return str(_shared.base_dir(PROJECT_ROOT))


def asset_path(*parts: str) -> str:
    """A bundled read-only asset (icon, README) -- sys._MEIPASS when frozen."""
    return str(_shared.asset_path(os.path.join(*parts), PROJECT_ROOT))
