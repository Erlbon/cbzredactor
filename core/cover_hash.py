"""
core/cover_hash.py

A small perceptual hash (a "difference hash") for comparing a book's own
first page with a Comic Vine cover, so the Comic Vine browser can PRESELECT
the volume/issue whose cover looks like the file's. It only ever preselects:
nothing is applied without the user pressing the button (see
gui/comicvine_browse_dialog.py).

Method: shrink the cover to 9x8 greyscale and set one bit per pixel pair
(is the left pixel brighter than its right neighbour). Two scans of the
same cover differ in a few bits; different covers differ in about half of
the 64. Idea after the ComicRack "Comic Vine Scraper" plugin's cover
matching (cbanack/comic-vine-scraper, Apache 2.0); the code is our own.
"""

from __future__ import annotations

import io
from typing import Optional

from PIL import Image

HASH_BITS = 64
# Similarity at or above which two covers count as "the same cover" for
# preselecting. 0.85 = at most 9 of 64 bits differ.
MATCH_THRESHOLD = 0.85

# A page wider than this many times its height is taken to be a wraparound
# cover (back + front side by side): only the right half (the front) is hashed.
_WRAPAROUND_RATIO = 1.2


def cover_hash(data: Optional[bytes]) -> Optional[int]:
    """The 64-bit hash of an image's bytes, or None if they aren't a readable image."""
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image = image.convert("L")
            width, height = image.size
            if width <= 0 or height <= 0:
                return None
            if width > height * _WRAPAROUND_RATIO:
                image = image.crop((width // 2, 0, width, height))
            small = image.resize((9, 8), Image.Resampling.LANCZOS)
            pixels = small.tobytes()  # one byte per pixel in mode "L"
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else 0)
    return bits


def similarity(a: Optional[int], b: Optional[int]) -> float:
    """0.0 (nothing alike) to 1.0 (identical hash); 0.0 when either hash is missing."""
    if a is None or b is None:
        return 0.0
    return 1.0 - bin(a ^ b).count("1") / HASH_BITS


def is_same_cover(a: Optional[int], b: Optional[int]) -> bool:
    return similarity(a, b) >= MATCH_THRESHOLD
