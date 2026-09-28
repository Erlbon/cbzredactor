"""
core/scene_tags.py

Classifies the bracketed tags in comic filenames -- "(Zone-Empire)",
"(Digital)", "(c2c)", "(TPB)", "(2 covers)", "(Chapter 01)" -- so the
filename parser (core/scene_name.py) knows where each one belongs:

- SCAN    scan group, source or quality tag ("Zone-Empire", "Digital",
          "webrip", "c2c", "1920px") -> ComicInfo ScanInformation
- FORMAT  edition format ("TPB", "One Shot", "FCBD") -> ComicInfo
          Format, as a canonical value (FORMAT_CANONICAL)
- NOTE    completeness / cover notes ("missing ifc", "2 covers",
          "variant cover only") -> ComicInfo Notes
- ORDER   reading-order markers ("Chapter 01", "cronology 00247") --
          a hint, not stored
- HINT    real metadata that isn't a scan tag (creator or publisher
          names, "black and white") -- never treated as a scan tag

Two layers, checked in order:
1. The phrase list in core/scene_tag_list.py -- exact (normalized)
   phrases, including the irregular ones no pattern could guess. Seeded
   from the maintainer's own ZenCBR training list, cleaned up.
2. Pattern rules below -- the regular shapes ("<name>-Empire",
   "<name>-DCP", "Minutemen-<name>", "<N> covers", "<N>px") that also
   recognise a brand-new group the first time it's seen.

Matching is always against a WHOLE bracketed phrase, never words inside
a title: the list contains entries like "empire", "madness", "maxx" or
"wizard", which are tags in brackets but real words in titles (The
Maxx is a comic). Same rule ZenCBR used.

Idea credit: ZenCBR (Shannon Larratt, 2012) -- its group/ignore lists
and bracket-phrase approach. No ZenCBR code is used.
"""

from __future__ import annotations

import re
from typing import Optional

SCAN = "scan"
FORMAT = "format"
NOTE = "note"
ORDER = "order"
HINT = "hint"

# Normalized phrase -> the value written to ComicInfo's Format field.
FORMAT_CANONICAL = {
    "tpb": "TPB",
    "digital tpb": "TPB",
    "deluxe tpb": "TPB",
    "gn": "Graphic Novel",
    "gn-1600": "Graphic Novel",
    "ogn": "Graphic Novel",
    "hc gn": "Graphic Novel",
    "digital gn": "Graphic Novel",
    "digital ogn": "Graphic Novel",
    "original gn": "Graphic Novel",
    "graphic novel": "Graphic Novel",
    "hc": "Hardcover",
    "hard cover": "Hardcover",
    "hardcover": "Hardcover",
    "one shot": "One-Shot",
    "one-shot": "One-Shot",
    "oneshot": "One-Shot",
    "limited series": "Limited Series",
    "fcbd": "FCBD",
    "annual": "Annual",
    "omnibus": "Omnibus",
    "anthology": "Anthology",
}

_ORDER_PATTERNS = [
    re.compile(r"^(chapter|chap|ch)\.?\s*\d+(\.\d+)?$"),
    re.compile(r"^cronolog(y|ia)\s*\d+$"),
]

_NOTE_PATTERNS = [
    re.compile(r"^(\d+|two|three|four|five|six|seven|eight|nine|ten|both|all)\s*covers?\b"),
    re.compile(r"\bvariant\b"),
    re.compile(r"\b(missing|damaged)\b"),
    re.compile(r"^no ifc\b"),
    re.compile(r"\bcover (only|b only)\b|\bcovers only\b"),
    re.compile(r"\bpreview\b"),
    re.compile(r"^page order fixed$"),
]

# Scan groups and source/quality tags.
_SCAN_PATTERNS = [
    # "<name>-Empire", "Empire-<name>", "-DCP", "-Novus(-HD)", and the
    # other long-running release-group suffixes.
    re.compile(r"(^|[\s\-_.])(empire|emipre|dcp|novus|cps|ocd|ocdcp|swa|hacsa|dregs|scc|nwg|cca|mms|italia-dcp)(-hd|-ft|-ot)?$"),
    re.compile(r"^(empire|dcp|novus|gca)[\s\-]"),
    re.compile(r"-(empire|dcp|novus|resin)[\s\-+&]"),
    re.compile(r"^minute?m[ae]n\b|^mintuemen\b|^minuemen\b"),
    # "Digital" and its many spellings/variants: "digital-hd", "digital
    # rip", "digtial", "digita", "digial", "digital+", "digital-1680".
    re.compile(r"^(digital|digtal|digtial|digita|digial|digitale|digi)(\b|[\s\-+,'])"),
    re.compile(r"^(digital|digtal|digtial|digita|digial|digi)$"),
    re.compile(r"\bwebrip\b|^web-?rip\b|^pdf ?rip$|^rip[\s\-_]"),
    re.compile(r"^\d{3,4}\s?px\b|\b\d{3,4}\s?px$|^\d{3,4}x\d{3,4}$|^\d{2,4}\s?ppi$|^d\d{4}$"),
    re.compile(r"^\d+\s?(p|pg|pgs|pages)(\s+c2c)?$|^c2c\s+\d+\s?(p|pg|pgs)$"),
    re.compile(r"(^|[\s.\-;])c2c([\s.\-;]|$)|^noc2c\b|^no c2c$"),
    re.compile(r"^(hd|hi-res|hybrid|digi-hybrid|rescan|re-?edit|re-scanned\b.*|fixed|noads|no ads|scanlation\b.*|hd-upscaled?|webp|jpg|resized|shrunk|de-bloated)$"),
    re.compile(r"\bscan(s|ned)?\b|\bscanner\b|\bcbr'd by\b|\bcbz'ed by\b|\bedit by\b"),
]


def normalize(phrase: str) -> str:
    """Case-folded, underscores and HTML "&amp;" undone, whitespace
    collapsed -- the form both the phrase list and the patterns use."""
    text = (phrase or "").replace("&amp;", "&").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def classify_by_pattern(phrase: str) -> Optional[str]:
    """Category from the pattern rules alone, or None."""
    text = normalize(phrase)
    if not text:
        return None
    if text in FORMAT_CANONICAL:
        return FORMAT
    for pattern in _ORDER_PATTERNS:
        if pattern.search(text):
            return ORDER
    for pattern in _NOTE_PATTERNS:
        if pattern.search(text):
            return NOTE
    for pattern in _SCAN_PATTERNS:
        if pattern.search(text):
            return SCAN
    return None


def classify(phrase: str, extra: Optional[dict[str, str]] = None) -> Optional[str]:
    """Category for one bracketed phrase: `extra` (a user's own
    additions, which win), then the built-in list, then the pattern
    rules. None = unknown."""
    text = normalize(phrase)
    if extra and text in extra:
        return extra[text]
    from core.scene_tag_list import SCENE_TAGS

    if text in SCENE_TAGS:
        return SCENE_TAGS[text]
    return classify_by_pattern(text)


def canonical_format(phrase: str) -> str:
    """The Format-field value for a FORMAT phrase ("digital tpb" ->
    "TPB"); the phrase itself, title-cased, if it has no mapping."""
    text = normalize(phrase)
    return FORMAT_CANONICAL.get(text, phrase.strip().title())
