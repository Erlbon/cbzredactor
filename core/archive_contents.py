"""
core/archive_contents.py

Operations > Clean Up Archive Contents... -- tidies the names INSIDE a
CBZ, the second round of scene-name cleanup (the first, core/scene_name.py,
handles the archive's own filename). Release groups put their name into
page and folder names ("Zone-Empire/Batman 045 (2018) (Zone-Empire)
p01.jpg"), and nested folders plus long names can push paths past what
some tools and file systems accept once extracted.

Three independent actions (CleanupOptions), all on by default for the
manual command:
- remove_junk: operating-system leftovers (Thumbs.db, desktop.ini,
  .DS_Store, __MACOSX/, "._" resource forks) and scene-release extras
  (.nfo, .sfv, .url, .diz, .md5, .txt, .htm/.html -- release notes and
  group adverts, not comic content) are dropped;
- flatten_folders: pages are moved out of folders, and ComicInfo.xml is
  kept at the top level under its standard name. A page keeps its own file
  name where that is safe; where two pages would end up with the same name
  (or the names would no longer sort in reading order) a short sequence
  number is put in front -- "003 - p01.jpg" -- so page order never changes;
- rename_pages: pages renamed to plain zero-padded numbers in reading
  order -- "001.webp", "002.webp", ... (at least three digits, more for
  longer books). Alone, a page stays in its folder ("Folder/001.webp");
  if keeping the folders would change the reading order, the pages are
  moved to the top level instead (the plan says so).

Reading order is the app's numeric-aware page order
(cbz_file.page_sort_key), so an archive numbered "1.webp, 2.webp, 10.webp"
keeps its real order, and every reader agrees on it afterwards, even one
that sorts names as text. ComicInfo.xml's per-page entries stay valid since
the order never changes. Anything else is kept under its own name.

Nothing is written here; CbzBook.clean_contents() applies a plan.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from core.cbz_file import page_sort_key
from core.scene_tags import SCAN, classify

COMICINFO_NAME = "ComicInfo.xml"
MIN_DIGITS = 3
LONG_PATH = 100  # characters: an entry path this long is worth mentioning

_OS_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}
_EXTRA_EXTENSIONS = {".nfo", ".sfv", ".url", ".diz", ".md5", ".txt", ".htm", ".html"}
_BRACKET_RE = re.compile(r"[\(\[]([^\(\)\[\]]+)[\)\]]")


@dataclass(frozen=True)
class CleanupOptions:
    """Which of the three actions to take (see the module docstring). The
    defaults are what the manual Clean Up command has always done."""

    remove_junk: bool = True
    flatten_folders: bool = True
    rename_pages: bool = True

    @property
    def any(self) -> bool:
        return self.remove_junk or self.flatten_folders or self.rename_pages


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
    numbered: bool = False  # pages become plain numbers (rename_pages)
    notes: list[str] = field(default_factory=list)  # why the plan differs from what the options alone say

    @property
    def needed(self) -> bool:
        return bool(self.renames or self.removals)

    def summary(self) -> str:
        parts = []
        pages = sum(1 for old, new in self.renames.items() if new != COMICINFO_NAME)
        if pages:
            detail = []
            if self.scene_tags:
                detail.append("names had " + ", ".join(f"({t})" for t in self.scene_tags[:3]))
            if self.longest_path >= LONG_PATH:
                detail.append(f"longest path {self.longest_path} characters")
            if self.numbered:
                if self.folders:
                    detail.insert(0, f"out of {self.folders} folder(s)")
                text = f"rename {pages} page(s)"
            elif self.folders:
                text = f"move {pages} page(s) out of {self.folders} folder(s)"
            else:
                text = f"rename {pages} page(s)"
            parts.append(text + (f" ({'; '.join(detail)})" if detail else ""))
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


def _in_reading_order(names: list[str]) -> bool:
    """The names sort, as a reader sorts them, in exactly this order."""
    keys = [page_sort_key(n) for n in names]
    return all(a < b for a, b in zip(keys, keys[1:]))


def _numbered_names(page_names: list[str], keep_folders: bool) -> list[str]:
    width = max(MIN_DIGITS, len(str(len(page_names))))
    result = []
    for index, old in enumerate(page_names, start=1):
        number = f"{index:0{width}d}{posixpath.splitext(old)[1].lower()}"
        folder = posixpath.dirname(old) if keep_folders else ""
        result.append(f"{folder}/{number}" if folder else number)
    return result


def _flattened_names(page_names: list[str], other_root_names: set[str]) -> list[str]:
    """Each page's own file name, out of its folder. A page whose name is
    shared (case-insensitively, as on Windows) with another page or with a
    kept top-level entry gets its sequence number in front; if the result
    still wouldn't sort in reading order, every page does."""
    width = max(MIN_DIGITS, len(str(len(page_names))))
    bases = [posixpath.basename(p) for p in page_names]
    counts: dict[str, int] = {}
    for base in bases:
        counts[base.lower()] = counts.get(base.lower(), 0) + 1

    def numbered(index: int) -> str:
        return f"{index + 1:0{width}d} - {bases[index]}"

    names = [
        numbered(i) if counts[b.lower()] > 1 or b.lower() in other_root_names else b
        for i, b in enumerate(bases)
    ]
    if len({n.lower() for n in names}) != len(names) or not _in_reading_order(names):
        names = [numbered(i) for i in range(len(bases))]
    return names


def plan_cleanup(
    entry_names: list[str],
    page_names: list[str],
    comicinfo_name: str | None,
    options: CleanupOptions = CleanupOptions(),
) -> CleanupPlan:
    """What cleaning would change. `page_names` in reading order (as
    CbzBook.page_names), `entry_names` every entry in the archive."""
    plan = CleanupPlan(numbered=options.rename_pages)
    pages = set(page_names)

    removable = [
        name for name in entry_names
        if options.remove_junk and name not in pages and name != comicinfo_name
        and not name.endswith("/") and is_junk(name)  # folder entries simply aren't written again
    ]
    plan.removals = removable

    flattened = False
    if page_names and (options.rename_pages or options.flatten_folders):
        if options.rename_pages:
            new_names = _numbered_names(page_names, keep_folders=not options.flatten_folders)
            flattened = options.flatten_folders
            if not options.flatten_folders and not _in_reading_order(new_names):
                # Folders that don't line up with the reading order: numbering inside them would
                # reorder the pages, so they go to the top level (numbers alone keep the order).
                new_names = _numbered_names(page_names, keep_folders=False)
                flattened = True
                plan.notes.append("pages moved out of their folders to keep the reading order")
        else:
            kept_root = {
                n.lower() for n in entry_names
                if "/" not in n and n not in pages and n not in removable
            } | {COMICINFO_NAME.lower()}
            new_names = _flattened_names(page_names, kept_root)
            flattened = True
        for old, new in zip(page_names, new_names):
            if new != old:
                plan.renames[old] = new
    if options.flatten_folders and comicinfo_name and comicinfo_name != COMICINFO_NAME:
        plan.renames[comicinfo_name] = COMICINFO_NAME

    # Folder entries ("Zone-Empire/") vanish with the flattening; count
    # the folders pages actually came out of.
    if flattened:
        plan.folders = len({posixpath.dirname(p) for p in page_names if "/" in p})
    folder_entries = [n for n in entry_names if n.endswith("/")]
    plan.drop_folder_entries = bool((flattened or options.flatten_folders) and plan.renames and folder_entries)
    plan.scene_tags = _scene_tags_in(page_names + folder_entries)
    plan.longest_path = max((len(n) for n in entry_names), default=0)
    return plan
