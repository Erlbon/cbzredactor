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

Two optional extras, modeled on CbxConverter's resize options:
- `max_height` caps page height too (useful for tall manga/webtoon
  strips). It isn't doubled for spreads -- a spread is two pages side
  by side, so it's the same height as one page.
- `output_format` ("JPEG" or "WEBP") re-encodes every page into that
  format, resized or not. The caller renames the archive entry to
  match (see ResizeResult.extension).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

from PIL import Image, UnidentifiedImageError

# Output formats the Resize dialog offers, and the entry extension each
# one gets. Both are already in core/cbz_file.py's IMAGE_EXTENSIONS, so
# a converted page still counts as a page.
OUTPUT_FORMAT_EXTENSIONS = {"JPEG": ".jpg", "WEBP": ".webp"}


@dataclass
class ResizeResult:
    data: bytes  # the (possibly unchanged) image bytes to write back
    resized: bool  # True if this page was actually re-encoded
    was_spread: bool = False
    original_size: Optional[tuple[int, int]] = None  # (width, height); None if unreadable
    new_size: Optional[tuple[int, int]] = None
    error: str = ""  # set (non-fatal) if Pillow couldn't decode this page at all
    # Set when the page was converted to a different format: the entry
    # extension it should now have (".webp", ".jpg"). None = keep name.
    extension: Optional[str] = None


def is_double_page_spread(width: int, height: int) -> bool:
    """Landscape or square orientation (width >= height) -- true for a
    two-page spread scanned/exported as one image, false for virtually
    any real single comic/manga page, which is portrait."""
    return width >= height


def target_size(
    width: int, height: int, max_width: int, max_height: Optional[int] = None
) -> tuple[int, int]:
    """The size a width x height page should shrink to (unchanged if it
    already fits). Spreads get double `max_width`; `max_height` applies
    as-is to every page. Aspect ratio is always preserved."""
    effective_max_width = max_width * 2 if is_double_page_spread(width, height) else max_width
    scale = min(1.0, effective_max_width / width)
    if max_height:
        scale = min(scale, max_height / height)
    if scale >= 1.0:
        return width, height
    return max(1, round(width * scale)), max(1, round(height * scale))


def resize_page(
    data: bytes,
    max_width: int,
    jpeg_quality: int = 90,
    max_height: Optional[int] = None,
    output_format: Optional[str] = None,
) -> ResizeResult:
    """Resizes one page's raw image bytes down to `max_width` (doubled
    first if the page looks like a double-page spread -- see module
    docstring) and, if given, `max_height`, preserving aspect ratio.
    Keeps the original file format unless `output_format` ("JPEG" or
    "WEBP") asks for a conversion; `jpeg_quality` is used for both
    lossy formats.

    A page Pillow can't decode at all (a corrupt entry, or a format it
    doesn't understand) is left completely untouched, with `error` set
    -- this isn't fatal to a batch resize, it just means that one page
    couldn't be checked/resized.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()  # force the full decode now, not lazily at save time
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        return ResizeResult(data=data, resized=False, error=str(exc))
    except Exception as exc:  # a decoder bug on one page must not sink the batch
        return ResizeResult(data=data, resized=False, error=f"{type(exc).__name__}: {exc}")

    width, height = image.size
    spread = is_double_page_spread(width, height)
    new_size = target_size(width, height, max_width, max_height)
    source_format = image.format or "JPEG"
    converting = bool(output_format) and output_format != source_format

    if new_size == (width, height) and not converting:
        # Already small enough -- return the ORIGINAL bytes unchanged,
        # not a re-encoded copy, so a page that didn't need touching
        # doesn't silently lose quality (or gain file size) to a
        # pointless re-save at the same dimensions.
        return ResizeResult(
            data=data, resized=False, was_spread=spread,
            original_size=(width, height), new_size=(width, height),
        )

    out_image = image if new_size == (width, height) else image.resize(new_size, Image.LANCZOS)
    save_format = output_format if converting else source_format

    save_kwargs: dict = {}
    if save_format == "JPEG":
        # Pillow refuses to save an RGBA/palette image as JPEG (no
        # alpha channel in that format) -- flatten explicitly first
        # rather than letting a rare RGBA/P-mode JPEG source crash the
        # whole batch.
        if out_image.mode not in ("RGB", "L", "CMYK"):
            out_image = out_image.convert("RGB")
        save_kwargs["quality"] = jpeg_quality
        save_kwargs["optimize"] = True
    elif save_format == "WEBP":
        if out_image.mode not in ("RGB", "RGBA"):
            has_alpha = out_image.mode in ("RGBA", "LA", "PA") or "transparency" in out_image.info
            out_image = out_image.convert("RGBA" if has_alpha else "RGB")
        save_kwargs["quality"] = jpeg_quality
        save_kwargs["method"] = 4
    elif save_format == "PNG":
        save_kwargs["optimize"] = True

    out = io.BytesIO()
    out_image.save(out, format=save_format, **save_kwargs)

    return ResizeResult(
        data=out.getvalue(), resized=True, was_spread=spread,
        original_size=(width, height), new_size=new_size,
        extension=OUTPUT_FORMAT_EXTENSIONS[save_format] if converting else None,
    )
