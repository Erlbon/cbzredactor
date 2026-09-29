"""
core/cover_stamp.py

A cover fingerprint kept INSIDE a CBZ, as its ZIP comment -- the text in
the archive's end record. No extra file in the archive or the folder, no
change to the pages or ComicInfo.xml, invisible to other comic tools, and
it travels with the file when it is moved or renamed.

The stamp is JSON: {"cbzredactor": 1, "cover": "<16 hex digits>", "key":
"<crc32>-<size>"}. `key` is the first page entry's CRC32 and size, so a
stamp whose cover page has since been replaced, or deleted, is ignored.
A comment that isn't ours is never overwritten.

Written by patching only the archive's last bytes in place (the end
record), not by rewriting the archive.
"""

from __future__ import annotations

import json
import os
import struct
from typing import Optional

TAG = "cbzredactor"
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_MAX_COMMENT = 0xFFFF


def make_stamp(cover: str, key: str) -> bytes:
    return json.dumps({TAG: 1, "cover": cover, "key": key}, separators=(",", ":")).encode("ascii")


def read_stamp(comment: bytes) -> Optional[tuple[str, str]]:
    """(cover, key) from an archive comment that is one of our stamps."""
    if not comment.startswith(b'{"' + TAG.encode()):
        return None
    try:
        data = json.loads(comment.decode("ascii"))
        cover, key = data["cover"], data["key"]
    except (ValueError, KeyError, UnicodeDecodeError, TypeError):
        return None
    return (cover, key) if isinstance(cover, str) and isinstance(key, str) else None


def write_comment(path: str, comment: bytes) -> None:
    """Sets the ZIP comment of the archive at `path`, changing only its end
    record. Raises OSError when the file isn't a plain ZIP whose end record
    can be found and checked; nothing is written then."""
    if len(comment) > _MAX_COMMENT:
        raise OSError("comment too long")
    with open(path, "r+b") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        tail_start = max(0, size - _EOCD_SIZE - _MAX_COMMENT)
        handle.seek(tail_start)
        tail = handle.read()
        position = tail.rfind(_EOCD_SIGNATURE)
        while position != -1:
            record = tail[position:position + _EOCD_SIZE]
            if len(record) == _EOCD_SIZE:
                (comment_length,) = struct.unpack("<H", record[20:22])
                if position + _EOCD_SIZE + comment_length == len(tail):
                    break
            position = tail.rfind(_EOCD_SIGNATURE, 0, position)
        else:
            raise OSError("no ZIP end record found")
        if position == -1:
            raise OSError("no ZIP end record found")
        new_record = record[:20] + struct.pack("<H", len(comment)) + comment
        handle.seek(tail_start + position)
        handle.write(new_record)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
