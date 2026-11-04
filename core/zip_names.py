"""
core/zip_names.py

Legacy ZIP entry names: now in redactor_common (core/zip_names.py, promoted
from here on 2026-11-04). This module only keeps the names the app imports.
"""

from redactor_common.core.zip_names import (  # noqa: F401 -- re-exported
    ArchiveReader,
    RawNameInfo,
    decode_zip_name,
    legacy_raw_name,
    open_zip,
    raw_names_supported,
)
