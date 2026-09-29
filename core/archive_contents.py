"""
core/archive_contents.py

Operations > Clean Up Archive Contents... -- tidies the names INSIDE a
CBZ, the second round of scene-name cleanup (the first, core/scene_name.py,
handles the archive's own filename). Release groups put their name into
page and folder names ("Zone-Empire/Batman 045 (2018) (Zone-Empire)
p01.jpg"), and nested folders plus long names can push paths past what
some tools and file systems accept once extracted.

The plan (plan_cleanup):
- pages renamed to plain zero-padded numbers in reading order --
  "001.webp", "002.webp", ... (at least three digits, more for longer
  books) -- and moved out of any folders. Reading order is the app's
  numeric-aware page order (cbz_file.page_sort_key), so an archive
  numbered "1.webp, 2.webp, 10.webp" keeps its real order, and every
  reader agrees on it afterwards, even one that sorts names as text;
- ComicInfo.xml kept, at the top level under its standard name; its
  per-page entries stay valid since the order doesn't change;
- junk dropped: operating-system leftovers (Thumbs.db, desktop.ini,
  .DS_Store, __MACOSX/, "._" resource forks) and scene-release extras
  (.nfo, .sfv, .url, .diz, .md5, .txt, .htm/.html -- release notes and
  group adverts, not comic content);
- anything else kept under its own name.

Nothing is written here; CbzBook.clean_contents() applies a plan.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from core.scene_tags import SCAN, classify

COMICINFO_NAME = "ComicInfo.xml"
MIN_DIGITS = 3
LONG_PATH = 100  # characters: an entry path this long is worth mentioning

_OS_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}
_EXTRA_EXTENSIONS = {".nfo", ".sfv", ".url", ".diz", ".md5", ".txt", ".htm", ".html"}
_BRACKET_RE = re.compile(r"[\(\[]([^\(\)\[\]]+)[\)\]]")


def is_junk(name: str) -> bool:
    """OS leftovers and scene-release extras (see the module docstring)."""
    lowered = name.replace("\\", "/").lower()
    if lowered.startswith("__macosx/") or "/__macosx/" in lowered:
        return True
    base = posixpath.basename(lowered.rstrip("/"))
    if base in _OS_JUNK_NAMES or base.startswith("._"):
        return True
    return posixpath.splitext(base)[1] in _EXTRA_EXTENSIONS


@dataclass
class CleanupPlan:
    renames: dict[str, str] = field(default_factory=dict)  # old entry name -> new name (pages, ComicInfo.xml)
    removals: list[str] = field(default_factory=list)  # junk entries dropped
    scene_tags: list[str] = field(default_factory=list)  # scan-group tags seen in entry names
    folders: int = 0  # page folders flattened
    drop_folder_entries: bool = False  # the folders' own (empty) entries go too
    longest_path: int = 0  # longest entry path before cleaning

    @property
    def needed(self) -> bool:
        return bool(self.renames or self.removals)

    def summary(self) -> str:
        parts = []
        pages = sum(1 for old, new in self.renames.items() if new != COMICINFO_NAME)
        if pages:
            detail = []
            if self.folders:
                detail.append(f"out of {self.folders} folder(s)")
            if self.scene_tags:
                detail.append("names had " + ", ".join(f"({t})" for t in self.scene_tags[:3]))
            if self.longest_path >= LONG_PATH:
                detail.append(f"longest path {self.longest_path} characters")
            parts.append(f"rename {pages} page(s)" + (f" ({'; '.join(detail)})" if detail else ""))
        if any(new == COMICINFO_NAME for new in self.renames.values()):
            parts.append("move ComicInfo.xml to the top level")
        if self.removals:
            shown = ", ".join(posixpath.basename(r.rstrip("/")) or r for r in self.removals[:3])
            more = f" +{len(self.removals) - 3} more" if len(self.removals) > 3 else ""
            parts.append(f"remove {len(self.removals)} junk file(s): {shown}{more}")
        return "; ".join(parts) or "already clean"


def _scene_tags_in(names: list[str]) -> list[str]:
    found: dict[str, None] = {}
    for name in names:
        for phrase in _BRACKET_RE.findall(name):
            if classify(phrase.strip()) == SCAN:
                found.setdefault(phrase.strip(), None)
    return list(found)


def plan_cleanup(entry_names: list[str], page_names: list[str], comicinfo_name: str | None) -> CleanupPlan:
    """What cleaning would change. `page_names` in reading order (as
    CbzBook.page_names), `entry_names` every entry in the archive."""
    plan = CleanupPlan()
    width = max(MIN_DIGITS, len(str(len(page_names))))
    for index, old in enumerate(page_names, start=1):
        extension = posixpath.splitext(old)[1].lower()
        new = f"{index:0{width}d}{extension}"
        if new != old:
            plan.renames[old] = new
    if comicinfo_name and comicinfo_name != COMICINFO_NAME:
        plan.renames[comicinfo_name] = COMICINFO_NAME

    pages = set(page_names)
    for name in entry_names:
        if name in pages or name == comicinfo_name or name.endswith("/"):
            continue  # folder entries simply aren't written again
        if is_junk(name):
            plan.removals.append(name)
    # Folder entries ("Zone-Empire/") vanish with the flattening; count
    # the folders pages actually came out of.
    plan.folders = len({posixpath.dirname(p) for p in page_names if "/" in p})
    folder_entries = [n for n in entry_names if n.endswith("/")]
    plan.drop_folder_entries = bool(plan.renames and folder_entries)
    plan.scene_tags = _scene_tags_in(page_names + folder_entries)
    plan.longest_path = max((len(n) for n in entry_names), default=0)
    return plan
