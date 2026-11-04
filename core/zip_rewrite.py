"""
core/zip_rewrite.py

The archive rewrite now lives in redactor_common (core/zip_rewrite.py, promoted
from here on 2026-11-04 so Dossier and the Redactor apps rewrite a zip the same
way; its tests moved there too). This module only keeps the names the app
imports. test_shared_writers.py fails if a zip is written anywhere else.
"""

from redactor_common.core.zip_rewrite import (  # noqa: F401 -- re-exported
    ZIP_ERRORS,
    Action,
    Entry,
    NewEntry,
    RewriteCancelled,
    RewriteError,
    RewritePlan,
    RewriteResult,
    ZipRewriteError,
    backup_name,
    now_date_time,
    rewrite_archive,
    safe_extra,
    swap_in,
    write_new_archive,
)

# The app's name for any problem reading or writing a CBZ.
CbzError = ZipRewriteError
