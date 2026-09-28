"""Tests for core/duplicates.py -- synthetic releases of the same comic
must group together (despite resizing, WebP, an inserted ad page and an
added credit page), while a TPB sharing issue #1's cover and two
different issues of one series must not."""

import io
import random
import zipfile

from PIL import Image, ImageDraw

from core.duplicates import BookFacts, find_duplicates, fingerprint_book, same_story


def _page(seed, w=1200, h=1800):
    """A busy, varied page (panels of random tones), so distinct seeds
    fingerprint as distinct pages -- the way real comic art does."""
    rng = random.Random(seed)
    image = Image.new("RGB", (w, h), tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(image)
    for _ in range(40):
        x, y = rng.randrange(w), rng.randrange(h)
        draw.rectangle((x, y, x + rng.randrange(60, 500), y + rng.randrange(60, 500)),
                       fill=tuple(rng.randrange(256) for _ in range(3)))
    return image


def _bytes(image, fmt="JPEG", **kw):
    out = io.BytesIO()
    image.save(out, fmt, **kw)
    return out.getvalue()


def _cbz(path, images, fmt="JPEG", size=None):
    names = []
    with zipfile.ZipFile(path, "w") as zf:
        for i, image in enumerate(images):
            if size:
                image = image.resize(size)
            name = f"{i:03}.{'webp' if fmt == 'WEBP' else 'jpg'}"
            zf.writestr(name, _bytes(image, fmt))
            names.append(name)
    return str(path), names


def _facts(path, names, **kw):
    return BookFacts(fingerprint=fingerprint_book(path, names), pages=len(names), **kw)


def _issue(first_seed, count=24):
    return [_page(first_seed + i) for i in range(count)]


def test_two_releases_of_one_comic_group_and_the_better_one_is_kept(tmp_path):
    story = _issue(100)
    a = _cbz(tmp_path / "a.cbz", story)
    # Another release: resized WebP, an ad inserted early, a credit page at the end.
    other = story[:3] + [_page(999)] + story[3:] + [_page(777)]
    b = _cbz(tmp_path / "b.cbz", other, fmt="WEBP", size=(900, 1350))
    fa, fb = _facts(*a, width=1200), _facts(*b, width=900)
    assert same_story(fa.fingerprint, fb.fingerprint)
    groups = find_duplicates([fa, fb])
    assert len(groups) == 1 and groups[0].members == [0, 1]
    assert groups[0].keep == 0  # higher resolution wins
    assert not groups[0].different_cover


def test_variant_cover_is_a_duplicate_with_a_different_cover(tmp_path):
    story = _issue(200)
    a = _cbz(tmp_path / "a.cbz", story)
    b = _cbz(tmp_path / "b.cbz", [_page(555)] + story[1:])
    groups = find_duplicates([_facts(*a), _facts(*b)])
    assert len(groups) == 1 and groups[0].different_cover


def test_tpb_sharing_issue_ones_cover_is_not_a_duplicate(tmp_path):
    issue_one = _issue(300)
    tpb = [issue_one[0]] + _issue(400, 60)[1:]
    groups = find_duplicates([_facts(*_cbz(tmp_path / "i1.cbz", issue_one)), _facts(*_cbz(tmp_path / "tpb.cbz", tpb))])
    assert groups == []


def test_different_issues_of_a_series_are_not_duplicates(tmp_path):
    books = [_facts(*_cbz(tmp_path / f"{n}.cbz", _issue(500 + n * 50))) for n in range(3)]
    assert find_duplicates(books) == []


def test_same_web_link_groups_regardless_of_pixels(tmp_path):
    a = _facts(*_cbz(tmp_path / "a.cbz", _issue(600)), web="https://comicvine.gamespot.com/x/4000-1/")
    b = _facts(*_cbz(tmp_path / "b.cbz", _issue(700)), web="https://comicvine.gamespot.com/x/4000-1")
    c = _facts(*_cbz(tmp_path / "c.cbz", _issue(800)), web="https://comicvine.gamespot.com/x/4000-2/")
    groups = find_duplicates([a, b, c])
    assert len(groups) == 1 and groups[0].members == [0, 1] and groups[0].by_link


def test_ranking_prefers_resolution_then_complete_then_tagged():
    from core.duplicates import BookFingerprint, DuplicateGroup  # noqa: F401

    same = BookFingerprint(page_count=20, cover=1, samples={1 / 3: [5], 2 / 3: [9]})
    books = [
        BookFacts(fingerprint=same, width=1440, pages=20, metadata_fields=3),
        BookFacts(fingerprint=same, width=1440, pages=22, metadata_fields=1),  # more pages wins the tie
        BookFacts(fingerprint=same, width=1000, pages=30, metadata_fields=20),
    ]
    assert find_duplicates(books)[0].keep == 1
