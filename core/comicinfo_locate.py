"""
core/comicinfo_locate.py

Which entry of a CBZ is its ComicInfo.xml -- ONE rule for everything that
looks (the editor's load, the collection scan and its report, the scan
fingerprint, the cleanup plan, and Dossier), so the report can never call a
file tagged that the editor shows blank. The rule lives in redactor_common
(core/comicinfo_write.locate_comicinfo, promoted from here on 2026-11-04):

- A ComicInfo.xml at the archive root (any letter case: some writers use
  comicinfo.xml) always wins.
- With no root one, a nested `sub/ComicInfo.xml` (any depth) is used for
  READING -- some packagers wrap everything in a folder. With several, the
  shallowest wins, then alphabetical order; the others are reported as
  ambiguous so the editor can say so.
- Saving always writes ComicInfo.xml at the root and drops the nested copy it
  was read from (CbzBook.save), so an archive never ends up with two.
"""

from redactor_common.core.comicinfo_write import (  # noqa: F401 -- re-exported
    COMICINFO_NAME,
    ComicInfoLocation,
    locate_comicinfo,
)
from redactor_common.core.comicinfo_write import find_comicinfo_member as find_comicinfo_entry  # noqa: F401
