"""Tests for core/comicinfo.py -- pure XML parse/serialize logic, no
zip or Qt involved."""

import pytest
from lxml import etree

from core.comicinfo import (
    ComicInfoError,
    ComicInfoMetadata,
    parse_comicinfo_xml,
    serialize_comicinfo_xml,
)

SAMPLE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <Title>The Amazing Test</Title>
  <Series>Test Comics</Series>
  <Number>1</Number>
  <Writer>Jane Doe</Writer>
  <Publisher>Test Publisher</Publisher>
  <AgeRating>Teen</AgeRating>
  <PageCount>22</PageCount>
</ComicInfo>
"""


def test_parse_reads_known_fields():
    metadata = parse_comicinfo_xml(SAMPLE_XML)
    assert metadata.title == "The Amazing Test"
    assert metadata.series == "Test Comics"
    assert metadata.number == "1"
    assert metadata.writer == "Jane Doe"
    assert metadata.publisher == "Test Publisher"
    assert metadata.age_rating == "Teen"
    assert metadata.page_count == "22"


def test_parse_rejects_wrong_root():
    with pytest.raises(ComicInfoError):
        parse_comicinfo_xml(b"<NotComicInfo></NotComicInfo>")


def test_parse_rejects_malformed_xml():
    with pytest.raises(ComicInfoError):
        parse_comicinfo_xml(b"<ComicInfo><Title>unclosed</ComicInfo>")


def test_roundtrip_preserves_values():
    metadata = parse_comicinfo_xml(SAMPLE_XML)
    serialized = serialize_comicinfo_xml(metadata)
    reparsed = parse_comicinfo_xml(serialized)
    assert reparsed.title == metadata.title
    assert reparsed.writer == metadata.writer
    assert reparsed.age_rating == metadata.age_rating


def test_serialize_omits_empty_fields():
    metadata = ComicInfoMetadata(title="Only Title Set")
    serialized = serialize_comicinfo_xml(metadata)
    root = etree.fromstring(serialized)
    tags = [etree.QName(child).localname for child in root]
    assert tags == ["Title"]


def test_unknown_pages_element_is_preserved_on_roundtrip():
    xml_with_pages = b"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Title>Has Pages</Title>
  <Pages>
    <Page Image="0" Type="FrontCover" />
    <Page Image="1" />
  </Pages>
  <CommunityRating>4.5</CommunityRating>
</ComicInfo>
"""
    metadata = parse_comicinfo_xml(xml_with_pages)
    assert metadata.title == "Has Pages"
    assert metadata.community_rating == "4.5"
    assert len(metadata.extra_elements) == 1
    assert etree.QName(metadata.extra_elements[0]).localname == "Pages"

    serialized = serialize_comicinfo_xml(metadata)
    root = etree.fromstring(serialized)
    pages_el = root.find("Pages")
    assert pages_el is not None
    assert len(pages_el.findall("Page")) == 2
    # Pages must sit between Title...AgeRating and CommunityRating,
    # matching the schema's declared element order.
    tags = [etree.QName(child).localname for child in root]
    assert tags.index("Pages") < tags.index("CommunityRating")


def test_unrecognized_enum_value_is_preserved_not_rejected():
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo><AgeRating>SomeFutureRating</AgeRating></ComicInfo>
"""
    metadata = parse_comicinfo_xml(xml)
    assert metadata.age_rating == "SomeFutureRating"
    serialized = serialize_comicinfo_xml(metadata)
    assert b"SomeFutureRating" in serialized


# v2.1 draft fields (Translator, Tags, StoryArcNumber, GTIN) -- not yet
# finalized, but already understood by ComicTagger and Kavita; see
# module docstring for the schema source.

def test_v21_draft_fields_roundtrip():
    metadata = ComicInfoMetadata(
        title="Draft Fields", translator="Jane Translator", tags="cool, indie",
        story_arc="Arc One, Arc Two", story_arc_number="1, 3", gtin="9780000000000",
    )
    serialized = serialize_comicinfo_xml(metadata)
    reparsed = parse_comicinfo_xml(serialized)
    assert reparsed.translator == "Jane Translator"
    assert reparsed.tags == "cool, indie"
    assert reparsed.story_arc_number == "1, 3"
    assert reparsed.gtin == "9780000000000"


def test_v21_draft_fields_sit_at_correct_schema_positions():
    metadata = ComicInfoMetadata(
        editor="Ed", translator="Trans", publisher="Pub", genre="Action", tags="Tag1",
        story_arc="Arc", story_arc_number="1", series_group="Group",
        review="A review", gtin="123",
    )
    root = etree.fromstring(serialize_comicinfo_xml(metadata))
    order = [etree.QName(child).localname for child in root]
    assert order.index("Editor") < order.index("Translator") < order.index("Publisher")
    assert order.index("Translator") < order.index("Genre") < order.index("Tags")
    assert order.index("StoryArc") < order.index("StoryArcNumber") < order.index("SeriesGroup")
    assert order.index("Review") < order.index("GTIN")  # GTIN is always last
