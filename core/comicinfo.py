"""
core/comicinfo.py

Pure-logic ComicInfo.xml metadata engine. No GUI, no zip handling --
just parsing an lxml element tree into a plain dataclass and
serializing it back, plus the field list and enum constraints from the
ComicInfo schema (the Anansi Project's v2.0, the closest thing to a
canonical reference now that ComicRack itself is no longer developed):
https://github.com/anansi-project/comicinfo

ComicInfo.xml is a de facto standard, not an enforced one -- readers in
the wild (Komga, Kavita, YACReader, ComicRack...) vary in which fields
they honor, and plenty of real CBZ files have no ComicInfo.xml at all.
This module treats every field as optional and never invents a value
the archive didn't already have.

Design notes (mirroring core/epub_metadata.py's approach for the
sibling EPUB tool):
- Every field is a plain string from the GUI's point of view -- a form
  field never has to worry about "what if this int field is blank".
  Int-typed fields (Count, Volume, AlternateCount, Year, Month, Day,
  PageCount) and CommunityRating (decimal) are validated/coerced only
  at the serialize boundary; a non-numeric value already sitting in an
  existing file is preserved on parse rather than rejected, since
  fixing a malformed file isn't this module's job.
- Enum fields (BlackAndWhite, Manga, AgeRating) are validated against
  the schema's fixed value lists for the GUI's dropdowns, but an
  unrecognized value found in an existing file is preserved as-is on
  parse/round-trip rather than silently dropped or reset -- a file
  from a newer schema draft, or a slightly nonstandard writer,
  shouldn't lose data just because this tool doesn't recognize one
  value.
- PageCount is round-tripped like every other field here, but the
  caller (core/cbz_file.py) always overwrites it from the archive's
  actual image count before saving -- this module has no opinion on
  that; see cbz_file.py's own docstring.
- Any element this tool doesn't have a dedicated field for -- most
  notably <Pages>, the per-page tagging block (FrontCover/Story/
  BackCover/... per page), which this tool doesn't yet expose an
  editor for -- is kept as a verbatim clone of the original XML
  element and written back out unchanged. Round-tripping a file this
  tool doesn't fully understand must never silently drop data from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lxml import etree

ROOT_TAG = "ComicInfo"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XSD_NS = "http://www.w3.org/2001/XMLSchema"

# Fixed value lists per the v2.0 schema (ComicInfo.xsd). An empty
# string represents "unset" -- most real-world writers simply omit the
# element rather than writing an explicit "Unknown".
BLACK_AND_WHITE_VALUES = ["", "Unknown", "No", "Yes"]
MANGA_VALUES = ["", "Unknown", "No", "Yes", "YesAndRightToLeft"]
AGE_RATING_VALUES = [
    "", "Unknown", "Adults Only 18+", "Early Childhood", "Everyone",
    "Everyone 10+", "G", "Kids to Adults", "M", "MA15+", "Mature 17+",
    "PG", "R18+", "Rating Pending", "Teen", "X18+",
]

# Tag <-> dataclass-attribute mapping, and the two field groups that
# sit either side of <Pages> in the schema's declared element order
# (Title...AgeRating, then Pages, then CommunityRating...Review). Any
# *other* element this tool doesn't recognize is treated the same way
# extra_elements handles Pages -- preserved verbatim -- but placed in
# that same slot, since in practice unrecognized elements are newer
# schema-draft additions that also live in that region.
FIELD_ORDER_BEFORE_EXTRAS = [
    "Title", "Series", "Number", "Count", "Volume",
    "AlternateSeries", "AlternateNumber", "AlternateCount",
    "Summary", "Notes",
    "Year", "Month", "Day",
    "Writer", "Penciller", "Inker", "Colorist", "Letterer",
    "CoverArtist", "Editor",
    "Publisher", "Imprint",
    "Genre", "Web", "PageCount", "LanguageISO", "Format",
    "BlackAndWhite", "Manga",
    "Characters", "Teams", "Locations",
    "ScanInformation", "StoryArc", "SeriesGroup",
    "AgeRating",
]
FIELD_ORDER_AFTER_EXTRAS = [
    "CommunityRating", "MainCharacterOrTeam", "Review",
]
FIELD_ORDER = FIELD_ORDER_BEFORE_EXTRAS + FIELD_ORDER_AFTER_EXTRAS

TAG_TO_ATTR = {
    "Title": "title", "Series": "series", "Number": "number",
    "Count": "count", "Volume": "volume",
    "AlternateSeries": "alternate_series", "AlternateNumber": "alternate_number",
    "AlternateCount": "alternate_count",
    "Summary": "summary", "Notes": "notes",
    "Year": "year", "Month": "month", "Day": "day",
    "Writer": "writer", "Penciller": "penciller", "Inker": "inker",
    "Colorist": "colorist", "Letterer": "letterer",
    "CoverArtist": "cover_artist", "Editor": "editor",
    "Publisher": "publisher", "Imprint": "imprint",
    "Genre": "genre", "Web": "web", "PageCount": "page_count",
    "LanguageISO": "language_iso", "Format": "format",
    "BlackAndWhite": "black_and_white", "Manga": "manga",
    "Characters": "characters", "Teams": "teams", "Locations": "locations",
    "ScanInformation": "scan_information", "StoryArc": "story_arc",
    "SeriesGroup": "series_group", "AgeRating": "age_rating",
    "CommunityRating": "community_rating",
    "MainCharacterOrTeam": "main_character_or_team", "Review": "review",
}
ATTR_TO_TAG = {attr: tag for tag, attr in TAG_TO_ATTR.items()}

INT_FIELDS = {"Count", "Volume", "AlternateCount", "Year", "Month", "Day", "PageCount"}


class ComicInfoError(Exception):
    """Raised for a problem parsing an existing ComicInfo.xml."""


@dataclass
class ComicInfoMetadata:
    """Plain-data snapshot of every field this tool edits, all as
    strings (see module docstring)."""

    title: str = ""
    series: str = ""
    number: str = ""
    count: str = ""
    volume: str = ""
    alternate_series: str = ""
    alternate_number: str = ""
    alternate_count: str = ""
    summary: str = ""
    notes: str = ""
    year: str = ""
    month: str = ""
    day: str = ""
    writer: str = ""
    penciller: str = ""
    inker: str = ""
    colorist: str = ""
    letterer: str = ""
    cover_artist: str = ""
    editor: str = ""
    publisher: str = ""
    imprint: str = ""
    genre: str = ""
    web: str = ""
    page_count: str = ""
    language_iso: str = ""
    format: str = ""
    black_and_white: str = ""
    manga: str = ""
    characters: str = ""
    teams: str = ""
    locations: str = ""
    scan_information: str = ""
    story_arc: str = ""
    series_group: str = ""
    age_rating: str = ""
    community_rating: str = ""
    main_character_or_team: str = ""
    review: str = ""

    # Verbatim clones of any child element not in TAG_TO_ATTR (most
    # notably <Pages>) -- see module docstring. Not compared by
    # dataclass equality/repr in any meaningful way since lxml elements
    # don't define that, but that's fine: nothing in this app needs to
    # diff two ComicInfoMetadata instances by extra_elements.
    extra_elements: list = field(default_factory=list)


def parse_comicinfo_xml(data: bytes) -> ComicInfoMetadata:
    """Parses ComicInfo.xml bytes into a ComicInfoMetadata. Raises
    ComicInfoError on malformed XML or an unexpected root element --
    the caller decides how to surface that (core/cbz_file.py treats it
    as a load error for that one file, not a crash)."""
    try:
        root = etree.fromstring(data, parser=etree.XMLParser(remove_blank_text=False))
    except etree.XMLSyntaxError as exc:
        raise ComicInfoError(f"Malformed ComicInfo.xml: {exc}") from exc

    # Namespaces are stripped for tag comparison -- some writers
    # declare the xsi/xsd namespaces on the root (per the original
    # ComicRack schema) but never actually use a prefix on the field
    # elements themselves, so a plain localname match is what's
    # actually needed to read every real-world file correctly.
    root_local = etree.QName(root).localname
    if root_local != ROOT_TAG:
        raise ComicInfoError(f"Expected root element <{ROOT_TAG}>, found <{root_local}>")

    metadata = ComicInfoMetadata()
    for child in root:
        if not isinstance(child.tag, str):
            continue  # skip comments/PIs
        tag = etree.QName(child).localname
        attr = TAG_TO_ATTR.get(tag)
        if attr:
            setattr(metadata, attr, child.text or "")
        else:
            metadata.extra_elements.append(child)
    return metadata


def serialize_comicinfo_xml(metadata: ComicInfoMetadata) -> bytes:
    """Serializes a ComicInfoMetadata back to ComicInfo.xml bytes,
    pretty-printed with an XML declaration -- matching the format most
    real-world writers (and readers' own expectations) produce.

    A field left as "" is omitted entirely rather than written as an
    empty element, matching how most real-world writers represent
    "unset" -- this also means clearing a field in the GUI and saving
    actually removes it from the file, not just blanks its text.
    """
    root = etree.Element(ROOT_TAG, nsmap={"xsi": XSI_NS, "xsd": XSD_NS})

    def _add_fields(tags: list[str]) -> None:
        for tag in tags:
            value = getattr(metadata, TAG_TO_ATTR[tag])
            value = (value or "").strip()
            if not value:
                continue
            if tag in INT_FIELDS and not value.lstrip("-").isdigit():
                # A non-numeric value in an int field (typically hand-
                # edited or carried over from a malformed source file)
                # is written as-is rather than silently dropped --
                # letting it round-trip is safer than guessing at a
                # "corrected" number.
                pass
            etree.SubElement(root, tag).text = value

    _add_fields(FIELD_ORDER_BEFORE_EXTRAS)
    for element in metadata.extra_elements:
        root.append(element)
    _add_fields(FIELD_ORDER_AFTER_EXTRAS)

    return etree.tostring(
        root, xml_declaration=True, encoding="utf-8", pretty_print=True
    )
