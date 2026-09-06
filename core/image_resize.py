"""
core/image_resize.py

Pure-logic page-image resizing, for shrinking extremely large CBZ
files down to a target maximum width. Uses Pillow -- a new dependency
added specifically for this feature (see requirements.txt); nothing
else in this project needed image *pixel* decoding before now, only
byte-for-byte copying (core/cbz_file.py's save()).

Double-page spread detection: some scans/exports store a two-page
spread as a single wide image. Virtually every real single comic/manga
page is portrait (taller than wide); a spread is the opposite --
landscape or square. Detecting this by orientation alone (rather than,
say, comparing each page's aspect ratio against the rest of the book)
is deliberately the simplest thing that actually works: it needs no
first pass over the archive, and lets every page get resized
independently, matching how the rest of this module already processes
one page at a time. A detected spread gets DOUBLE the requested target
width, so each half keeps the same effective per-page resolution a
normal single page would get at that target -- instead of being
crushed down to half the detail.

Only ever shrinks, never upscales: a page already at or under its
(possibly-doubled) target width is returned completely unchanged, byte
for byte.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

from PIL import Image, UnidentifiedImageError


@dataclass
class ResizeResult:
    data: bytes  # the (possibly unchanged) image bytes to write back
    resized: bool  # True if this page was actually re-encoded
    was_spread: bool = False
    original_size: Optional[tuple[int, int]] = None  # (width, height); None if unreadable
    new_size: Optional[tuple[int, int]] = None
    error: str = ""  # set (non-fatal) if Pillow couldn't decode this page at all


def is_double_page_spread(width: int, height: int) -> bool:
    """Landscape or square orientation (width >= height) -- true for a
    two-page spread scanned/exported as one image, false for virtually
    any real single comic/manga page, which is portrait."""
    return width >= height


def resize_page(data: bytes, max_width: int, jpeg_quality: int = 90) -> ResizeResult:
    """Resizes one page's raw image bytes down to `max_width` (doubled
    first if the page looks like a double-page spread -- see module
    docstring), preserving aspect ratio and the original file format.

    A page Pillow can't decode at all (a corrupt entry, or a format it
    doesn't understand) is left completely untouched, with `error` set
    -- this isn't fatal to a batch resize, it just means that one page
    couldn't be checked/resized.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()  # force the full decode now, not lazily at save time
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return ResizeResult(data=data, resized=False, error=str(exc))

    width, height = image.size
    spread = is_double_page_spread(width, height)
    effective_max_width = max_width * 2 if spread else max_width

    if width <= effective_max_width:
        # Already small enough -- return the ORIGINAL bytes unchanged,
        # not a re-encoded copy, so a page that didn't need touching
        # doesn't silently lose quality (or gain file size) to a
        # pointless re-save at the same dimensions.
        return ResizeResult(
            data=data, resized=False, was_spread=spread,
            original_size=(width, height), new_size=(width, height),
        )

    new_height = max(1, round(height * (effective_max_width / width)))
    resized_image = image.resize((effective_max_width, new_height), Image.LANCZOS)

    save_format = image.format or "JPEG"
    save_kwargs: dict = {}
    if save_format == "JPEG":
        # Pillow refuses to save an RGBA/palette image as JPEG (no
        # alpha channel in that format) -- flatten explicitly first
        # rather than letting a rare RGBA/P-mode JPEG source crash the
        # whole batch.
        if resized_image.mode in ("RGBA", "P", "LA"):
            resized_image = resized_image.convert("RGB")
        save_kwargs["quality"] = jpeg_quality
        save_kwargs["optimize"] = True
    elif save_format == "PNG":
        save_kwargs["optimize"] = True

    out = io.BytesIO()
    resized_image.save(out, format=save_format, **save_kwargs)

    return ResizeResult(
        data=out.getvalue(), resized=True, was_spread=spread,
        original_size=(width, height), new_size=(effective_max_width, new_height),
    )
