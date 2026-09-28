"""
core/credit_pages.py

Finds scanner credit pages -- the "scanned by <group>" tag page scene
releases add, usually as the last page -- so they can be removed.

Works like epubredactor's Junk Cover flag: the user marks a credit page
once, the app remembers its fingerprint, and every page matching it is
found from then on. The difference: epub matches covers byte for byte,
but the same group's tag page shows up re-encoded and resized from one
release to the next (CbxConverter re-encodes every page to WebP and
resizes it), so this uses a *visual* fingerprint -- a 64-bit difference
hash ("dHash") of a 9x8 greyscale thumbnail -- matched within a small
Hamming distance.

Only the first FIRST_PAGES and last LAST_PAGES pages of a book are
checked: that's where credit pages go, and decoding every page of a
300-page book would be far slower.

Besides known fingerprints, cheap hints suggest candidates the user
hasn't marked yet (shown, never pre-ticked): a page name like
"zzzz_Empire.jpg", a name containing a known scan group
(core/scene_tags.py), or a page whose size differs from the book's
normal pages. Converted files lose the first two (pages are renamed).

Known fingerprints live in their own JSON file next to the settings,
each with a small thumbnail so the user can see what they'd be
forgetting (Settings > Known Credit Pages...).
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import os
import posixpath
import re
import statistics
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, UnidentifiedImageError

from core import scene_tags

FIRST_PAGES = 2
LAST_PAGES = 4
# dHash bits allowed to differ for "the same picture" -- resizing and
# lossy re-encoding typically flip a handful; unrelated pages differ by
# ~32 on average.
MATCH_DISTANCE = 8
_THUMB_SIZE = (96, 144)


def candidate_indices(page_count: int) -> list[int]:
    """Page positions to check: the first FIRST_PAGES and last
    LAST_PAGES, without duplicates, in page order."""
    wanted = list(range(min(FIRST_PAGES, page_count))) + list(range(max(0, page_count - LAST_PAGES), page_count))
    return sorted(set(wanted))


def dhash(image: Image.Image) -> int:
    """64-bit difference hash: each bit says whether a pixel of a 9x8
    greyscale thumbnail is brighter than its right-hand neighbour."""
    small = image.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = small.tobytes()  # one byte per pixel in "L" mode
    value = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            value = (value << 1) | (1 if left > right else 0)
    return value


# Below this greyscale standard deviation a page is too plain to
# fingerprint: blank and near-blank pages all hash alike, so a plain
# page is never learned or matched (it would match every blank page).
PLAIN_STDDEV = 10.0


def is_plain(image: Image.Image) -> bool:
    small = image.convert("L").resize((32, 32))
    pixels = list(small.tobytes())
    return statistics.pstdev(pixels) < PLAIN_STDDEV


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def open_page_image(data: bytes) -> Image.Image:
    """Decodes a page, cheaply where the format allows (JPEG's draft
    mode decodes straight to a small size)."""
    image = Image.open(io.BytesIO(data))
    try:
        image.draft("RGB", (256, 256))
    except (AttributeError, ValueError):
        pass
    image.load()
    return image


def thumbnail_png(image: Image.Image) -> bytes:
    thumb = image.convert("RGB")
    thumb.thumbnail(_THUMB_SIZE)
    out = io.BytesIO()
    thumb.save(out, format="PNG", optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Hints for pages nobody has marked yet
# ---------------------------------------------------------------------------

_Z_PREFIX_RE = re.compile(r"^z{2,}", re.IGNORECASE)
_WORD_HINT_RE = re.compile(r"\b(tag|credits?|scann?(ed|er)?|banner|logo)\b", re.IGNORECASE)


def name_hints(name: str) -> list[str]:
    stem = posixpath.splitext(posixpath.basename(name))[0]
    hints = []
    if _Z_PREFIX_RE.match(stem):
        hints.append('name starts with "zz" (sorts last on purpose)')
    readable = re.sub(r"[_.]+", " ", stem)
    if _WORD_HINT_RE.search(readable):
        hints.append("name mentions tag/credits/scan")
    for piece in re.split(r"[\s_()\[\]]+", readable) + [readable]:
        piece = piece.strip(" -z")
        if len(piece) > 3 and scene_tags.classify(piece) == scene_tags.SCAN:
            hints.append(f'name contains scan group "{piece}"')
            break
    return hints


# ---------------------------------------------------------------------------
# Known credit pages (the learned fingerprints)
# ---------------------------------------------------------------------------

@dataclass
class KnownCreditPage:
    hash: int
    thumbnail: bytes = b""  # PNG, for display only
    added: str = ""
    source: str = ""  # the page name it was learned from, for display

    def to_json(self) -> dict:
        return {
            "hash": f"{self.hash:016x}",
            "thumbnail": base64.b64encode(self.thumbnail).decode("ascii"),
            "added": self.added,
            "source": self.source,
        }

    @staticmethod
    def from_json(data: dict) -> Optional["KnownCreditPage"]:
        try:
            return KnownCreditPage(
                hash=int(data["hash"], 16),
                thumbnail=base64.b64decode(data.get("thumbnail", "")),
                added=str(data.get("added", "")),
                source=str(data.get("source", "")),
            )
        except (KeyError, ValueError, TypeError):
            return None


class KnownCreditPages:
    """The learned fingerprints, stored as JSON at `path`."""

    def __init__(self, path: str):
        self.path = path
        self.pages: list[KnownCreditPage] = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = []
        self.pages = [p for p in (KnownCreditPage.from_json(d) for d in raw if isinstance(d, dict)) if p]

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([p.to_json() for p in self.pages], f, indent=1)
        os.replace(tmp, self.path)

    def match(self, page_hash: int) -> Optional[KnownCreditPage]:
        best = min(self.pages, key=lambda p: hamming(p.hash, page_hash), default=None)
        if best is not None and hamming(best.hash, page_hash) <= MATCH_DISTANCE:
            return best
        return None

    def add(self, page_hash: int, thumbnail: bytes, source: str) -> bool:
        """Learns a page and saves at once (like epub's junk-cover flag);
        False if it (or a near-identical one) is already known."""
        if self.match(page_hash) is not None:
            return False
        self.pages.append(KnownCreditPage(
            hash=page_hash, thumbnail=thumbnail,
            added=datetime.date.today().isoformat(), source=posixpath.basename(source),
        ))
        self.save()
        return True

    def forget(self, page_hash: int) -> None:
        self.pages = [p for p in self.pages if p.hash != page_hash]
        self.save()


# ---------------------------------------------------------------------------
# Scanning a book
# ---------------------------------------------------------------------------

@dataclass
class PageCandidate:
    """One checked page (see candidate_indices()). `hash` is None when
    the page couldn't be decoded."""

    index: int
    name: str
    hash: Optional[int] = None
    thumbnail: bytes = b""
    size: Optional[tuple[int, int]] = None
    hints: list[str] = field(default_factory=list)
    plain: bool = False  # too plain to fingerprint -- see is_plain()


def scan_book(path: str, page_names: list[str], with_thumbnails: bool = False) -> list[PageCandidate]:
    """Fingerprints the pages worth checking (first/last few) and
    collects hints. Matching against known pages is separate
    (KnownCreditPages.match), so a scan stays valid when the user
    learns or forgets a page."""
    indices = candidate_indices(len(page_names))
    candidates = [PageCandidate(index=i, name=page_names[i], hints=name_hints(page_names[i])) for i in indices]
    try:
        with zipfile.ZipFile(path) as zf:
            for candidate in candidates:
                try:
                    image = open_page_image(zf.read(candidate.name))
                except (KeyError, OSError, UnidentifiedImageError, zipfile.BadZipFile, zlib.error, ValueError):
                    continue
                candidate.size = image.size
                candidate.plain = is_plain(image)
                if not candidate.plain:
                    candidate.hash = dhash(image)
                if with_thumbnails:
                    candidate.thumbnail = thumbnail_png(image)
    except (OSError, zipfile.BadZipFile):
        return candidates
    _add_size_hints(candidates)
    return candidates


def _add_size_hints(candidates: list[PageCandidate]) -> None:
    """A checked page whose proportions differ clearly from the other
    checked pages (a landscape tag page among portrait story pages, or
    a small banner). Draft-mode decoding makes absolute sizes
    unreliable, so this compares aspect ratios only."""
    ratios = [c.size[0] / c.size[1] for c in candidates if c.size and c.size[1]]
    if len(ratios) < 3:
        return
    typical = statistics.median(ratios)
    for c in candidates:
        if c.size and c.size[1] and abs(c.size[0] / c.size[1] - typical) > 0.25 * typical:
            c.hints.append("different shape from the other pages")


def credit_matches(candidates: list[PageCandidate], known: KnownCreditPages) -> list[PageCandidate]:
    return [c for c in candidates if c.hash is not None and known.match(c.hash) is not None]
