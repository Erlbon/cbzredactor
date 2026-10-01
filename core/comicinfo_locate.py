"""
core/comicinfo_locate.py

Which entry of a CBZ is its ComicInfo.xml -- ONE rule for everything that
looks (the editor's load, the collection scan and its report, the scan
fingerprint, the cleanup plan), so the report can never call a file tagged
that the editor shows blank.

- A ComicInfo.xml at the archive root (any letter case: some writers use
  comicinfo.xml) always wins.
- With no root one, a nested `sub/ComicInfo.xml` (any depth) is used for
  READING -- some packagers wrap everything in a folder. With several, the
  shallowest wins, then alphabetical order; the others are reported as
  ambiguous so the editor can say so.
- Saving always writes ComicInfo.xml at the root and drops the nested copy it
  was read from (CbzBook.save), so an archive never ends up with two.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Iterable, Optional

COMICINFO_NAME = "ComicInfo.xml"


@dataclass(frozen=True)
class ComicInfoLocation:
    name: str  # the entry that is read
    nested: bool  # it is not at the archive root
    others: tuple = field(default_factory=tuple)  # further nested copies that were passed over


def _is_candidate(name: str) -> bool:
    """A real ComicInfo.xml entry: right file name, not a folder entry, not
    inside macOS's __MACOSX resource-fork folder."""
    if name.endswith("/") or posixpath.basename(name).lower() != COMICINFO_NAME.lower():
        return False
    lowered = name.lower()
    return not (lowered.startswith("__macosx/") or "/__macosx/" in lowered)


def locate_comicinfo(names: Iterable[str]) -> Optional[ComicInfoLocation]:
    nested = []
    for name in names:
        if not _is_candidate(name):
            continue
        if "/" not in name:
            return ComicInfoLocation(name, False)
        nested.append(name)
    if not nested:
        return None
    nested.sort(key=lambda n: (n.count("/"), n.casefold(), n))
    return ComicInfoLocation(nested[0], True, tuple(nested[1:]))


def find_comicinfo_entry(names: Iterable[str]) -> Optional[str]:
    """The name of the ComicInfo.xml entry to read (see the module
    docstring), or None."""
    found = locate_comicinfo(names)
    return found.name if found else None
