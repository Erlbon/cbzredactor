"""
core/comic_languages.py

Default languages for the quick-pick "+" menu next to the Language
(ISO) field, as (ISO 639-1 code, display name). ComicInfo.xml's
LanguageISO is meant to hold a valid language code, so picking from
this menu sets the code; the field stays free text, so any other code
can still be typed directly, and more can be added via Settings >
Add/Remove Languages (persisted for future sessions).

Same role and same list as epubredactor's core/languages.py -- ISO
language codes/names are plain facts, not something worth maintaining
two independent copies of across sibling projects.
"""

from redactor_common.core.languages import language_pairs

# (ISO 639-1 code, English name) from redactor_common's shared ISO 639
# table (2026-09-23) -- the "two independent copies" this docstring
# warned about are now one table for the whole family.
DEFAULT_LANGUAGES: list[tuple[str, str]] = language_pairs(
    ["en", "de", "fr", "es", "nl", "no", "it", "sv", "da", "ja", "ko", "pt"], "alpha2",
)
