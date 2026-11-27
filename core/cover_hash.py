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


# A page with more pixels than this is not decoded at all (Pillow itself refuses about 179 million).
MAX_HASH_PIXELS = 150_000_000
# A JPEG is decoded at a reduced scale (1/2, 1/4, 1/8) while it stays at least this big: the hash only needs a 9x8 picture.
_DRAFT_SIZE = (1280, 1280)
PREVIEW_MAX_SIDE = 640


def _load_reduced(data: bytes) -> Image.Image:
    """The image, decoded as cheaply as it allows. Raises OSError/ValueError/Image.DecompressionBombError/
    SyntaxError for anything unreadable or too big."""
    image = Image.open(io.BytesIO(data))
    try:
        if image.width * image.height > MAX_HASH_PIXELS:
            raise ValueError("the picture is too large to hash")
        image.draft(None, _DRAFT_SIZE)  # JPEG only; other formats ignore it
        image.load()
    except BaseException:
        image.close()
        raise
    return image


def _difference_hash(image: Image.Image) -> Optional[int]:
    image = image.convert("L")
    width, height = image.size
    if width <= 0 or height <= 0:
        return None
    if width > height * _WRAPAROUND_RATIO:
        image = image.crop((width // 2, 0, width, height))
    small = image.resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.tobytes()  # one byte per pixel in mode "L"
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else 0)
    return bits


_READ_ERRORS = (OSError, ValueError, SyntaxError, MemoryError, Image.DecompressionBombError)


def cover_hash(data: Optional[bytes]) -> Optional[int]:
    """The 64-bit hash of an image's bytes, or None if they aren't a readable image."""
    if not data:
        return None
    try:
        with _load_reduced(data) as image:
            return _difference_hash(image)
    except _READ_ERRORS:
        return None


def preview_and_hash(data: Optional[bytes], max_side: int = PREVIEW_MAX_SIDE) -> tuple[Optional[bytes], Optional[int]]:
    """(a small JPEG of the picture to show, its hash) from one decode: for a page that may be huge, so a window
    never has to decode the original. (None, None) when the bytes aren't a readable image."""
    if not data:
        return None, None
    try:
        with _load_reduced(data) as image:
            digest = _difference_hash(image)
            preview = image.convert("RGB")
            preview.thumbnail((max_side, max_side))
            out = io.BytesIO()
            preview.save(out, "JPEG", quality=90)
            return out.getvalue(), digest
    except _READ_ERRORS:
        return None, None


def similarity(a: Optional[int], b: Optional[int]) -> float:
    """0.0 (nothing alike) to 1.0 (identical hash); 0.0 when either hash is missing."""
    if a is None or b is None:
        return 0.0
    return 1.0 - bin(a ^ b).count("1") / HASH_BITS


def is_same_cover(a: Optional[int], b: Optional[int]) -> bool:
    return similarity(a, b) >= MATCH_THRESHOLD
