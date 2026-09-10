"""
core/version.py

Single source of truth for the version info shown in the window title
and the About dialog. See redactor_common/core/version.py's own
docstring for the "YYYY-MM-DD#NN" convention this mirrors -- bump via
bump_version.py immediately before each build, not by hand.
"""

from __future__ import annotations

APP_NAME = "The ƆBZ Redactor"  # "Ɔ" = Ɔ, LATIN CAPITAL LETTER OPEN O ("reversed C") -- same reversed-letter mark epubredactor uses ("Ǝ" = Ǝ)
RELEASE_LABEL = "v0.1 Initial Scaffold"
APP_VERSION = "2026-09-10#05"
APP_REPO_URL = "https://github.com/Erlbon/cbzredactor"
