"""Tests for core/image_resize.py -- built against real Pillow-generated
images (not fake byte strings), since decoding/re-encoding pixels is
the whole point of this module."""

import io

import pytest
from PIL import Image

from core.image_resize import is_double_page_spread, resize_page


def _make_image_bytes(width: int, height: int, fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    image = Image.new(mode, (width, height), color=(120, 130, 140) if mode == "RGB" else 255)
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def _dimensions(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


# ------------------------------------------------------------------
# is_double_page_spread()
# ------------------------------------------------------------------

def test_portrait_page_is_not_a_spread():
    assert is_double_page_spread(800, 1200) is False


def test_landscape_page_is_a_spread():
    assert is_double_page_spread(1600, 1200) is True


def test_square_page_counts_as_a_spread():
    # width == height: treated as a spread (>=, not >) -- the rare
    # ambiguous case errs toward NOT crushing detail rather than
    # silently under-sizing a borderline spread.
    assert is_double_page_spread(1000, 1000) is True


# ------------------------------------------------------------------
# resize_page()
# ------------------------------------------------------------------

def test_small_page_is_left_completely_unchanged():
    data = _make_image_bytes(800, 1200)
    result = resize_page(data, max_width=1600)
    assert result.resized is False
    assert result.data == data  # byte-for-byte, not just same dimensions
    assert result.original_size == (800, 1200)


def test_oversized_single_page_is_shrunk_to_max_width():
    data = _make_image_bytes(3000, 4500)  # portrait, 2:3 ratio
    result = resize_page(data, max_width=1500)
    assert result.resized is True
    assert result.was_spread is False
    new_width, new_height = _dimensions(result.data)
    assert new_width == 1500
    assert new_height == 2250  # aspect ratio preserved exactly (2:3)


def test_double_page_spread_gets_double_the_target_width():
    """The core 'smart' behavior: a spread isn't crushed down to the
    single-page target -- it gets 2x that budget, so each half keeps
    the same effective per-page resolution a single page would."""
    data = _make_image_bytes(4000, 3000)  # landscape -- a spread
    result = resize_page(data, max_width=1500)
    assert result.resized is True
    assert result.was_spread is True
    new_width, new_height = _dimensions(result.data)
    assert new_width == 3000  # 2x max_width, not max_width
    assert new_height == 2250  # aspect ratio preserved (4:3)


def test_spread_under_doubled_width_is_left_unchanged():
    # 2400 wide is over a plain 1500 target, but under the doubled
    # 3000 a detected spread gets -- must NOT be resized.
    data = _make_image_bytes(2400, 1800)
    result = resize_page(data, max_width=1500)
    assert result.resized is False
    assert result.was_spread is True


def test_never_upscales():
    data = _make_image_bytes(400, 600)
    result = resize_page(data, max_width=4000)
    assert result.resized is False
    assert _dimensions(result.data) == (400, 600)


def test_preserves_original_format():
    data = _make_image_bytes(3000, 4000, fmt="PNG")
    result = resize_page(data, max_width=1000)
    assert result.resized is True
    assert Image.open(io.BytesIO(result.data)).format == "PNG"


def test_rgba_source_flattened_before_jpeg_save(monkeypatch):
    """Pillow refuses to save an RGBA/palette image as JPEG outright (no
    alpha channel in that format) -- resize_page() must flatten to RGB
    first rather than let Image.save() raise. A real JPEG stream never
    actually decodes to RGBA (JPEG has no alpha channel to decode), so
    this is simulated via a narrow monkeypatch of Image.open() that
    hands back a real RGBA image with its format forced to "JPEG" --
    the exact shape resize_page() would see if a mis-saved file ever
    produced one, while still driving the real function end-to-end."""
    from PIL import Image as PILImage

    real_open = PILImage.open

    def _fake_open(*args, **kwargs):
        image = real_open(*args, **kwargs)
        image.format = "JPEG"
        return image

    monkeypatch.setattr("core.image_resize.Image.open", _fake_open)

    data = _make_image_bytes(3000, 4000, fmt="PNG", mode="RGBA")  # a real RGBA source
    result = resize_page(data, max_width=1000, jpeg_quality=85)

    assert result.resized is True
    reopened = Image.open(io.BytesIO(result.data))
    assert reopened.format == "JPEG"
    assert reopened.mode == "RGB"  # flattened, not still RGBA


def test_corrupt_image_is_left_untouched_with_error_set():
    result = resize_page(b"not actually an image", max_width=1000)
    assert result.resized is False
    assert result.error
    assert result.data == b"not actually an image"
