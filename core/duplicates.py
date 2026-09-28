"""
core/duplicates.py

Finds duplicate comics among loaded files -- the same comic from two
releases (different scan group, resolution, format, with or without
ads) -- and suggests which copy to keep.

Uses the same visual fingerprints as core/credit_pages.py (a dHash per
page, robust to resizing and re-encoding), but a matching COVER is not
enough: a TPB reuses issue #1's cover, and a series can reuse a cover
design. Two books are the same comic when their STORY pages match: each
book fingerprints a few pages around 1/3 and 2/3 of the way in, and
two releases rarely line up page for page (ads, credit pages, extra
covers shift everything), so each sample is compared against a window
of the other book's pages.

- same story pages, same cover        -> duplicate
- same story pages, different cover   -> duplicate, "different cover" (a variant)
- same Web link (a Comic Vine or GCD issue page) -> duplicate, whatever the pixels say
- same cover only                     -> NOT a duplicate

Keeping the best copy: higher page resolution first (the Size column's
typical width), then more pages (a complete copy over one missing
pages), then the richer ComicInfo, then the bigger file.
"""

from __future__ import annotations

import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Optional

from PIL import UnidentifiedImageError

from core.credit_pages import MATCH_DISTANCE, dhash, hamming, is_plain, open_page_image

SAMPLE_POSITIONS = (1 / 3, 2 / 3)
WINDOW = 2  # pages either side of each sample position


@dataclass
class BookFingerprint:
    page_count: int = 0
    cover: Optional[int] = None
    # sample position -> fingerprints of the pages in its window (page order)
    samples: dict[float, list[Optional[int]]] = field(default_factory=dict)


def _sample_indices(page_count: int, position: float) -> list[int]:
    center = int(round((page_count - 1) * position))
    return [i for i in range(center - WINDOW, center + WINDOW + 1) if 0 < i < page_count - 1]


def fingerprint_book(path: str, page_names: list[str]) -> BookFingerprint:
    """Cover plus a window of story pages at each SAMPLE_POSITION.
    Plain (near-blank) pages get no fingerprint -- they'd match every
    other blank page."""
    result = BookFingerprint(page_count=len(page_names))
    if not page_names:
        return result
    wanted = {0}
    for position in SAMPLE_POSITIONS:
        wanted.update(_sample_indices(len(page_names), position))
    hashes: dict[int, Optional[int]] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            for index in sorted(wanted):
                try:
                    image = open_page_image(zf.read(page_names[index]))
                except (KeyError, OSError, UnidentifiedImageError, zlib.error, ValueError, zipfile.BadZipFile):
                    hashes[index] = None
                    continue
                hashes[index] = None if is_plain(image) else dhash(image)
    except (OSError, zipfile.BadZipFile):
        return result
    result.cover = hashes.get(0)
    for position in SAMPLE_POSITIONS:
        result.samples[position] = [hashes.get(i) for i in _sample_indices(len(page_names), position)]
    return result


def _close(a: Optional[int], b: Optional[int]) -> bool:
    return a is not None and b is not None and hamming(a, b) <= MATCH_DISTANCE


def same_story(a: BookFingerprint, b: BookFingerprint) -> bool:
    """True when, at every sample position both books have, some story
    page of A matches some page in B's window there. Requiring every
    position (not just one) keeps one shared splash page or recap page
    from making two different issues look alike."""
    compared = 0
    for position in SAMPLE_POSITIONS:
        window_a = [h for h in a.samples.get(position, []) if h is not None]
        window_b = [h for h in b.samples.get(position, []) if h is not None]
        if not window_a or not window_b:
            continue
        compared += 1
        if not any(_close(x, y) for x in window_a for y in window_b):
            return False
    return compared > 0


@dataclass
class DuplicateGroup:
    """Indexes into the caller's list of books; `keep` is the suggested
    copy to keep. `different_cover` if any member's cover differs from
    the kept one's."""

    members: list[int]
    keep: int
    different_cover: bool = False
    by_link: bool = False  # grouped (at least partly) by an identical Web link


@dataclass
class BookFacts:
    """What the caller knows about each book, for grouping and ranking."""

    fingerprint: BookFingerprint
    width: int = 0  # typical page width (core/page_dimensions.py), 0 if unknown
    pages: int = 0
    metadata_fields: int = 0  # how many ComicInfo fields are filled in
    file_size: int = 0
    web: str = ""


def find_duplicates(books: list[BookFacts]) -> list[DuplicateGroup]:
    parent = list(range(len(books)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    linked: set[int] = set()  # books grouped with another by an identical Web link
    by_web: dict[str, int] = {}
    for i, facts in enumerate(books):
        web = facts.web.strip().rstrip("/").casefold()
        if web:
            if web in by_web:
                parent[find(i)] = find(by_web[web])
                linked.update((i, by_web[web]))
            else:
                by_web[web] = i
    for i in range(len(books)):
        for j in range(i + 1, len(books)):
            if find(i) != find(j) and same_story(books[i].fingerprint, books[j].fingerprint):
                parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(len(books)):
        groups.setdefault(find(i), []).append(i)

    result = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = max(members, key=lambda i: (books[i].width, books[i].pages, books[i].metadata_fields, books[i].file_size))
        keep_cover = books[keep].fingerprint.cover
        different = any(
            books[i].fingerprint.cover is not None and keep_cover is not None
            and not _close(books[i].fingerprint.cover, keep_cover)
            for i in members
        )
        by_link = any(i in linked for i in members)
        result.append(DuplicateGroup(members=sorted(members), keep=keep, different_cover=different, by_link=by_link))
    return result
