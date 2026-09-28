"""
core/scan_quality_tag.py

Marks low-resolution scans in ComicInfo.xml, so they can be found and
replaced with better copies later (a comic server such as Komga or
Kavita can filter on it, and so can this app's Tags column).

Stored as one entry in the Tags field (comma-separated, the same
multi-value convention as Genre/Characters -- see core/comicinfo.py),
not in ScanInformation: that field conventionally holds the scan
group's own credit ("Digital-Empire"), which marking must never
overwrite. Adding/removing the tag leaves every other tag untouched.
"""

from __future__ import annotations

LOW_RES_TAG = "Low-res scan"


def _split(tags: str) -> list[str]:
    return [t.strip() for t in (tags or "").split(",") if t.strip()]


def has_tag(tags: str, tag: str = LOW_RES_TAG) -> bool:
    return tag.casefold() in (t.casefold() for t in _split(tags))


def add_tag(tags: str, tag: str = LOW_RES_TAG) -> str:
    if has_tag(tags, tag):
        return tags
    return ", ".join(_split(tags) + [tag])


def remove_tag(tags: str, tag: str = LOW_RES_TAG) -> str:
    if not has_tag(tags, tag):
        return tags
    return ", ".join(t for t in _split(tags) if t.casefold() != tag.casefold())
