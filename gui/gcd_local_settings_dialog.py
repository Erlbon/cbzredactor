"""
gui/gcd_local_settings_dialog.py

Settings > GCD Local Database... -- points the app at the user's own
downloaded copy of the Grand Comics Database (core/gcd_local.py). The
app never bundles or downloads it: GCD offers the dump to registered
users, and this dialog explains how to get it.

The dialog itself is redactor_common's LocalDatabaseSettingsDialog
(path, Browse..., Check File, Save); only GCD's instructions and the
check live here.
"""

from __future__ import annotations

from redactor_common.gui.local_db_settings_dialog import LocalDatabaseSettingsDialog

from core.gcd_local import GCD_DOWNLOAD_URL, GcdLocalDatabase, GcdLocalError
from gui import app_settings

INSTRUCTIONS = (
    "Look up comics in your own copy of the Grand Comics Database -- fast, "
    "offline, and with no hourly limit.<br><br>"
    "<b>Getting the database:</b><ol>"
    "<li>Create a free account at <a href='https://www.comics.org/'>comics.org</a> "
    "and log in.</li>"
    f"<li>Go to <a href='{GCD_DOWNLOAD_URL}'>{GCD_DOWNLOAD_URL}</a> and download the "
    "<b>SQLite</b> version of the data dump -- a file called <code>current.zip</code>, about 2 GB.</li>"
    "<li>Unzip it somewhere with room to spare: it holds one <code>.db</code> file of about 7 GB, "
    "named after the dump date (e.g. <code>2026-09-15.db</code>).</li>"
    "<li>Choose the unzipped <code>.db</code> file below.</li></ol>"
    "Download a newer dump now and then to pick up GCD's latest additions. The "
    "GCD data is the work of GCD's volunteers; see comics.org for its licence."
)


def _check(path: str) -> str:
    db = GcdLocalDatabase(path)
    try:
        info = db.summary()
    finally:
        db.close()
    return (
        f"Looks good: {info['series']:,} series and {info['issues']:,} issues, "
        f"last updated {info['newest'] or 'unknown'}."
    )


class GcdLocalSettingsDialog(LocalDatabaseSettingsDialog):
    def __init__(self, parent=None):
        super().__init__(
            title="GCD Local Database",
            instructions_html=INSTRUCTIONS,
            path=app_settings.load_gcd_local_database(),
            check=_check,
            save=app_settings.save_gcd_local_database,
            error_types=(GcdLocalError,),
            parent=parent,
        )
