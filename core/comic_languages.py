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

DEFAULT_LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"),
    ("de", "German"),
    ("fr", "French"),
    ("es", "Spanish"),
    ("nl", "Dutch"),
    ("no", "Norwegian"),
    ("it", "Italian"),
    ("sv", "Swedish"),
    ("da", "Danish"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("pt", "Portuguese"),
]
