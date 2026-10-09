"""
cbzcli/fields.py

ComicInfo field names for the command line: any spelling the user is likely to type ("Series",
"series", "cover_artist", "CoverArtist", "coverartist") finds the field, and a value is checked the
way the GUI's controls would constrain it before anything is written.
"""

from __future__ import annotations

from core.comicinfo import (
    AGE_RATING_VALUES, ATTR_TO_TAG, BLACK_AND_WHITE_VALUES, INT_FIELDS, MANGA_VALUES, TAG_TO_ATTR,
)
from redactor_common.cli import CliError
from redactor_common.cli.values import check_text, is_ascii_number

# PageCount is always recomputed from the archive when saving; it is not a field to set.
SETTABLE = {tag: attr for tag, attr in TAG_TO_ATTR.items() if tag != "PageCount"}

# Fields that may hold several lines; every other field is a single line.
MULTILINE_TAGS = {"Summary", "Notes", "Review"}

_ENUMS = {
    "BlackAndWhite": BLACK_AND_WHITE_VALUES,
    "Manga": MANGA_VALUES,
    "AgeRating": AGE_RATING_VALUES,
}


def _spellings(tag: str, attr: str) -> set[str]:
    return {tag.lower(), attr.lower(), attr.replace("_", "").lower(), attr.replace("_", "-").lower()}


_LOOKUP: dict[str, str] = {}
for _tag, _attr in TAG_TO_ATTR.items():
    for _spelling in _spellings(_tag, _attr):
        _LOOKUP[_spelling] = _tag


def resolve_field(name: str, settable: bool = False) -> tuple[str, str]:
    """(ComicInfo tag, metadata attribute) for what the user typed. Raises CliError listing the valid names."""
    tag = _LOOKUP.get(name.strip().lower())
    if tag is None or (settable and tag not in SETTABLE):
        valid = ", ".join(sorted(attr for t, attr in TAG_TO_ATTR.items() if not settable or t in SETTABLE))
        raise CliError(f"unknown field {name!r}. Fields: {valid}")
    return tag, TAG_TO_ATTR[tag]


def check_value(tag: str, value: str) -> str:
    """The value to store (stripped), or a CliError when the field would not accept it. "" clears the field."""
    value = check_text(tag, value, multiline=tag in MULTILINE_TAGS)
    if not value:
        return ""
    if tag in INT_FIELDS:
        if not is_ascii_number(value):
            raise CliError(f"{tag} must be a whole number written with the digits 0-9, not {value!r}")
        number = int(value)
        value = str(number)  # "007" is stored, and compared, as 7
        if tag == "Month" and not 1 <= number <= 12:
            raise CliError("Month must be 1 to 12")
        if tag == "Day" and not 1 <= number <= 31:
            raise CliError("Day must be 1 to 31")
    if tag == "CommunityRating":
        try:
            if not value.isascii():
                raise ValueError(value)
            rating = float(value)
        except ValueError:
            raise CliError(f"CommunityRating must be a number from 0 to 5, not {value!r}") from None
        if not 0 <= rating <= 5:
            raise CliError("CommunityRating must be from 0 to 5")
    options = _ENUMS.get(tag)
    if options:
        match = next((o for o in options if o and o.lower() == value.lower()), None)
        if match is None:
            raise CliError(f"{tag} must be one of: {', '.join(o for o in options if o)}")
        return match
    return value


def parse_assignment(text: str) -> tuple[str, str, str]:
    """"Series=Saga" -> (tag, attr, checked value). Splits on the first "="."""
    if "=" not in text:
        raise CliError(f"expected FIELD=VALUE, got {text!r}")
    name, _, value = text.partition("=")
    tag, attr = resolve_field(name, settable=True)
    return tag, attr, check_value(tag, value)


# What `info` shows when no --fields or --all is given.
DEFAULT_INFO_FIELDS = ["series", "number", "title", "year", "publisher"]


def attr_for(name: str) -> str:
    return TAG_TO_ATTR[resolve_field(name)[0]]


def tag_for_attr(attr: str) -> str:
    return ATTR_TO_TAG[attr]
