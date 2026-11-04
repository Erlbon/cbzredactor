"""
core/comicinfo.py

Pure-logic ComicInfo.xml metadata engine. No GUI, no zip handling --
just turning ComicInfo.xml into a plain dataclass and back, plus the field list and enum constraints from the
ComicInfo schema (the Anansi Project's v2.0, the closest thing to a
canonical reference now that ComicRack itself is no longer developed):
https://github.com/anansi-project/comicinfo

ComicInfo.xml is a de facto standard, not an enforced one -- readers in
the wild (Komga, Kavita, YACReader, ComicRack...) vary in which fields
they honor, and plenty of real CBZ files have no ComicInfo.xml at all.
This module treats every field as optional and never invents a value
the archive didn't already have.

The v2.0 field set is supplemented here with four fields from the
Anansi Project's v2.1 DRAFT schema (not yet finalized, but already
understood by ComicTagger and by readers like Kavita):
https://github.com/anansi-project/comicinfo/blob/main/drafts/v2.1/ComicInfo.xsd
Translator, Tags, StoryArcNumber, and GTIN. Purely additive and safe
either way -- a reader that only knows v2.0 simply ignores elements it
doesn't recognize, the same tolerance this module itself extends to
any *other* schema draft's fields via `extra_elements` below.

Confirmed directly against ComicTagger's own source
(comicapi/tags/comicrack.py) that its multi-value convention for
Genre/Characters/Teams/Locations/StoryArc/credits is comma-separated,
matching what this app's own GUI already uses (see gui/metadata_panel.py
and core/comic_genres.py) -- one notable difference found: ComicTagger
treats Web as space-separated multiple URLs, not comma-separated; this
module has no opinion on that (Web is just a plain string here), so
nothing to reconcile, just worth knowing when comparing files.

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

Since 2026-11-04 the XML itself is read and written by redactor_common's
core/comicinfo_write.py, the one ComicInfo writer the apps and Dossier share
(standard-library ElementTree, so extra_elements are ElementTree elements):
schema order, xsi/xsd declared, root attributes kept, empty elements dropped,
values stripped. This module only maps the elements to the dataclass.
test_shared_writers.py fails if ComicInfo.xml is serialized anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import xml.etree.ElementTree as ET

from redactor_common.core import comicinfo_write as shared
from redactor_common.core.comicinfo_write import ComicInfoError  # noqa: F401 -- re-exported

ROOT_TAG = shared.ROOT_TAG
XSI_NS = shared.XSI_NS
XSD_NS = shared.XSD_NS

# Fixed value lists per the v2.0 schema (ComicInfo.xsd). An empty
# string represents "unset" (the GUI's dropdowns start with it) -- most
# real-world writers simply omit the element rather than writing an
# explicit "Unknown".
BLACK_AND_WHITE_VALUES = ["", *shared.BLACK_AND_WHITE_VALUES]
MANGA_VALUES = ["", *shared.MANGA_VALUES]
AGE_RATING_VALUES = ["", *shared.AGE_RATING_VALUES]

# Tag <-> dataclass-attribute mapping, and the two field groups that
# sit either side of <Pages> in the schema's declared element order
# (Title...AgeRating, then Pages, then CommunityRating...Review). Any
# *other* element this tool doesn't recognize is treated the same way
# extra_elements handles Pages -- preserved verbatim -- but placed in
# that same slot, since in practice unrecognized elements are newer
# schema-draft additions that also live in that region.
FIELD_ORDER_BEFORE_EXTRAS = list(shared.FIELDS_BEFORE_EXTRAS)
FIELD_ORDER_AFTER_EXTRAS = list(shared.FIELDS_AFTER_EXTRAS)
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
    "CoverArtist": "cover_artist", "Editor": "editor", "Translator": "translator",
    "Publisher": "publisher", "Imprint": "imprint",
    "Genre": "genre", "Tags": "tags", "Web": "web", "PageCount": "page_count",
    "LanguageISO": "language_iso", "Format": "format",
    "BlackAndWhite": "black_and_white", "Manga": "manga",
    "Characters": "characters", "Teams": "teams", "Locations": "locations",
    "ScanInformation": "scan_information", "StoryArc": "story_arc",
    "StoryArcNumber": "story_arc_number",
    "SeriesGroup": "series_group", "AgeRating": "age_rating",
    "CommunityRating": "community_rating",
    "MainCharacterOrTeam": "main_character_or_team", "Review": "review",
    "GTIN": "gtin",
}
ATTR_TO_TAG = {attr: tag for tag, attr in TAG_TO_ATTR.items()}

# The persisted validation-scan stamp (core/scan_stamp wire text, see
# CbzBook.record_scan) lives here, as a plain child of <ComicInfo>. Not in
# TAG_TO_ATTR on purpose: it isn't a field the user edits, so it must stay
# out of columns, filename patterns and the Redact fill logic. Readers that
# don't know the element skip it, like any other unrecognized one.
SCAN_STAMP_TAG = shared.SCAN_STAMP_TAG

INT_FIELDS = set(shared.INT_FIELDS)


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
    translator: str = ""  # v2.1 draft
    publisher: str = ""
    imprint: str = ""
    genre: str = ""
    tags: str = ""  # v2.1 draft
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
    story_arc_number: str = ""  # v2.1 draft
    series_group: str = ""
    age_rating: str = ""
    community_rating: str = ""
    main_character_or_team: str = ""
    review: str = ""
    gtin: str = ""  # v2.1 draft -- ISBN/ISSN/EAN/JAN/etc, whichever the publisher used

    # Verbatim clones of any child element not in TAG_TO_ATTR (most
    # notably <Pages>) -- see module docstring. Not compared by
    # dataclass equality/repr in any meaningful way since ElementTree elements
    # don't define that, but that's fine: nothing in this app needs to
    # diff two ComicInfoMetadata instances by extra_elements.
    extra_elements: list = field(default_factory=list)

    # Raw text of the <RedactorScan> element ("" = none); parsed by
    # CbzBook.stamp. Kept verbatim so a value this version can't parse
    # still round-trips.
    scan_stamp: str = ""

    # The root element's attributes as read (xsi:noNamespaceSchemaLocation...),
    # written back as they were.
    root_attributes: dict = field(default_factory=dict)


def parse_comicinfo_xml(data: bytes) -> ComicInfoMetadata:
    """Parses ComicInfo.xml bytes into a ComicInfoMetadata. Raises
    ComicInfoError on malformed XML or an unexpected root element --
    the caller decides how to surface that (core/cbz_file.py treats it
    as a load error for that one file, not a crash). Namespaces are
    ignored for tag comparison (some writers declare them on the root)."""
    root = shared.parse_comicinfo(data)
    metadata = ComicInfoMetadata(root_attributes=dict(root.attrib))
    for child in root:
        tag = shared.local_name(child.tag)
        if not tag:
            continue  # skip comments/PIs
        attr = TAG_TO_ATTR.get(tag)
        if tag == SCAN_STAMP_TAG:
            metadata.scan_stamp = (child.text or "").strip()
        elif attr and len(child) == 0:
            setattr(metadata, attr, child.text or "")
        else:
            metadata.extra_elements.append(child)
    return metadata


def serialize_comicinfo_xml(metadata: ComicInfoMetadata) -> bytes:
    """Serializes a ComicInfoMetadata back to ComicInfo.xml bytes through
    the shared writer (see the module docstring). A field left as "" is
    omitted entirely, so clearing a field in the GUI and saving removes
    it from the file. A non-numeric value in an int field is written
    as-is rather than dropped -- letting it round-trip is safer than
    guessing at a "corrected" number."""
    root = ET.Element(ROOT_TAG, metadata.root_attributes)
    for tag in FIELD_ORDER:
        value = (getattr(metadata, TAG_TO_ATTR[tag]) or "").strip()
        if value:
            ET.SubElement(root, tag).text = value
    for element in metadata.extra_elements:
        root.append(element)
    if (metadata.scan_stamp or "").strip():
        ET.SubElement(root, SCAN_STAMP_TAG).text = metadata.scan_stamp.strip()
    return shared.serialize_comicinfo(root)
