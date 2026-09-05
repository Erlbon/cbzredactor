"""
core/comic_genres.py

A curated list of common comic-book genre labels, for the quick-pick
"+" menu next to the free-text Genre field (see gui/metadata_panel.py).
This is a convenience shortlist, not a controlled vocabulary -- the
field always stays free text, so anything not on this list can still
be typed directly. Drawn from genre terms actually seen in the wild
across ComicInfo.xml files and the Grand Comics Database's own story-
level genre field (see core/gcd_lookup.py), not any third-party
classification system.

Same role as epubredactor's core/genres.py, but comics-specific --
"Superhero" and "Funny Animal" are comics-industry terms without a
direct prose-fiction equivalent, and this list skips several of
epub's own book-trade categories (e.g. "Self-Help", "Cooking") that
don't apply here.
"""

COMMON_COMIC_GENRES: list[str] = sorted([
    "Superhero",
    "Action",
    "Adventure",
    "Fantasy",
    "Science Fiction",
    "Horror-Suspense",
    "Horror",
    "Crime",
    "Mystery",
    "Western",
    "War",
    "Romance",
    "Humor",
    "Funny Animal",
    "Satire",
    "Drama",
    "Slice of Life",
    "Biography",
    "Historical",
    "Anthology",
    "Children's",
    "Teen",
    "Manga",
    "Erotica",
])


def add_genre(current: str, genre: str) -> str:
    """Append `genre` to a comma-separated list `current`, without
    duplicating it (case-insensitively) if it's already present. Pure
    string logic, no GUI -- used by the quick-pick menu so a value
    picked from the list is added alongside whatever's already typed,
    never replacing it. Comma-separated, not semicolon -- matches this
    project's own ComicInfo.xml field convention (see
    gui/metadata_panel.py's field docstrings)."""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    if not any(p.lower() == genre.lower() for p in parts):
        parts.append(genre)
    return ", ".join(parts)
