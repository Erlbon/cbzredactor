"""
core/archive_sniff.py

Identifies what kind of archive a comic file really is from its first
bytes, regardless of its extension. Mislabeled comics are common in
the wild: plenty of ".cbr" files are really ZIPs (someone renamed
.zip -> .cbr), and now and then a ".cbz" is really a RAR. The table's
Ext column shows both ("CBR → ZIP"), and conversion uses the real
container rather than trusting the name -- a mislabeled ZIP only needs
renaming, not unpacking and repacking.
"""

from __future__ import annotations

import os

CONTAINER_ZIP = "zip"
CONTAINER_RAR = "rar"
CONTAINER_7Z = "7z"
CONTAINER_TAR = "tar"
CONTAINER_UNKNOWN = "unknown"

# What each comic extension is supposed to contain.
EXPECTED_CONTAINER = {
    ".cbz": CONTAINER_ZIP,
    ".cbr": CONTAINER_RAR,
    ".cb7": CONTAINER_7Z,
    ".cbt": CONTAINER_TAR,
}

CONTAINER_LABELS = {
    CONTAINER_ZIP: "ZIP",
    CONTAINER_RAR: "RAR",
    CONTAINER_7Z: "7Z",
    CONTAINER_TAR: "TAR",
}

_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_RAR_SIGNATURE = b"Rar!\x1a\x07"  # RAR 4 ("...\x00") and RAR 5 ("...\x01\x00")
_7Z_SIGNATURE = b"7z\xbc\xaf\x27\x1c"
_TAR_MAGIC_OFFSET = 257  # "ustar" in a POSIX/GNU tar header


def detect_container(path: str) -> str:
    """One of the CONTAINER_* constants. CONTAINER_UNKNOWN for anything
    unrecognized or unreadable -- callers fall back to the extension."""
    try:
        with open(path, "rb") as f:
            head = f.read(_TAR_MAGIC_OFFSET + 8)
    except OSError:
        return CONTAINER_UNKNOWN
    if head.startswith(_ZIP_SIGNATURES):
        return CONTAINER_ZIP
    if head.startswith(_RAR_SIGNATURE):
        return CONTAINER_RAR
    if head.startswith(_7Z_SIGNATURE):
        return CONTAINER_7Z
    if head[_TAR_MAGIC_OFFSET:_TAR_MAGIC_OFFSET + 5] == b"ustar":
        return CONTAINER_TAR
    return CONTAINER_UNKNOWN


def extension_label(path: str, container: str) -> str:
    """The Ext column's text: "CBZ", or "CBR → ZIP" when the real
    container isn't what the extension promises."""
    ext = os.path.splitext(path)[1].lower()
    label = ext.lstrip(".").upper() or "?"
    expected = EXPECTED_CONTAINER.get(ext)
    if container != CONTAINER_UNKNOWN and container != expected:
        return f"{label} → {CONTAINER_LABELS[container]}"
    return label
