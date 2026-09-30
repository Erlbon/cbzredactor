"""
core/page_dimensions.py

Measures the page images inside a CBZ without decoding them, for the
table's Size column: Pillow's Image.open() only parses an image's
header (the pixel data isn't read until .load()), and each entry is
opened as a stream straight out of the ZIP, so a whole book costs a
few KB of reads per page rather than its full size.

A book's "representative width" is the median width of its single
(portrait) pages. Double-page spreads (see core/image_resize.py's
is_double_page_spread) and the odd oversized cover would otherwise
drag a book into "Oversized" when every real page is fine; a median of
the single pages ignores both. Only if a book has no portrait pages at
all (a landscape-format album) does it fall back to every page.

The size bands are the ones agreed for this column:
- low-res     below 1000 px
- acceptable  1000-1599 px
- oversized   1600 px and up
"""

from __future__ import annotations

import statistics
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, UnidentifiedImageError

from core.image_resize import is_double_page_spread

LOW_RES_BELOW = 1000
OVERSIZED_FROM = 1600

SIZE_LOW = "low"
SIZE_OK = "ok"
SIZE_OVERSIZED = "oversized"

SIZE_CATEGORY_LABELS = {
    SIZE_LOW: "Low-res",
    SIZE_OK: "Acceptable",
    SIZE_OVERSIZED: "Oversized",
}


def classify_width(width: int) -> str:
    if width < LOW_RES_BELOW:
        return SIZE_LOW
    if width < OVERSIZED_FROM:
        return SIZE_OK
    return SIZE_OVERSIZED


@dataclass
class PageSizeStats:
    """Measured page dimensions for one book. `sizes` holds one
    (width, height) per page that could be read; `unreadable` counts the
    rest (corrupt entry, or a format Pillow doesn't know)."""

    sizes: list[tuple[int, int]] = field(default_factory=list)
    unreadable: int = 0

    @property
    def single_page_widths(self) -> list[int]:
        return [w for w, h in self.sizes if not is_double_page_spread(w, h)]

    @property
    def spread_count(self) -> int:
        return sum(1 for w, h in self.sizes if is_double_page_spread(w, h))

    @property
    def representative_width(self) -> Optional[int]:
        widths = self.single_page_widths or [w for w, _h in self.sizes]
        if not widths:
            return None
        return int(statistics.median_low(widths))

    @property
    def category(self) -> Optional[str]:
        width = self.representative_width
        return None if width is None else classify_width(width)

    def describe(self) -> str:
        """Multi-line summary for the Size cell's tooltip."""
        width = self.representative_width
        if width is None:
            return "No readable page images."
        lines = [f"{SIZE_CATEGORY_LABELS[classify_width(width)]}: typical page width {width}px"]
        common = Counter(self.single_page_widths or [w for w, _h in self.sizes]).most_common(3)
        lines.append("Most common widths: " + ", ".join(f"{w}px ({n})" for w, n in common))
        heights = [h for w, h in self.sizes if not is_double_page_spread(w, h)]
        if heights:
            lines.append(f"Typical page height: {int(statistics.median_low(heights))}px")
        if self.spread_count:
            lines.append(f"Double-page spreads: {self.spread_count}")
        if self.unreadable:
            lines.append(f"Unreadable pages: {self.unreadable}")
        return "\n".join(lines)


def read_image_size(stream) -> Optional[tuple[int, int]]:
    """(width, height) from an image's header, or None if it can't be
    identified. Never decodes pixel data."""
    try:
        with Image.open(stream) as image:
            return image.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def scan_page_sizes(path: str, page_names: list[str]) -> PageSizeStats:
    """Measures every page in `page_names` inside the CBZ at `path`.
    Raises OSError/zipfile.BadZipFile if the archive itself can't be
    opened; a single bad page only counts as unreadable."""
    stats = PageSizeStats()
    with zipfile.ZipFile(path, "r") as zf:
        for name in page_names:
            try:
                with zf.open(name) as stream:
                    size = read_image_size(stream)
            except (KeyError, OSError, zipfile.BadZipFile, zlib.error):
                size = None
            if size is None:
                stats.unreadable += 1
            else:
                stats.sizes.append(size)
    return stats
