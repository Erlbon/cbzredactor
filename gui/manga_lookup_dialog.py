"""
gui/manga_lookup_dialog.py

Metadata > Look Up > Manga (AniList + MangaUpdates)... -- the same review-then-Apply dialog as the other
lookups (redactor_common's LookupDialogBase), backed by core/manga_lookup.py.

Both sources are SERIES databases: they describe the series, not one volume. So every volume of a series gets
the same answer (one request pair for the whole batch: the client remembers a series it has already asked
about), and the volume number a file has is never touched. What comes back: the series title, summary, start
date, genres and tags, the credits (author and artist), the publisher (the English one when MangaUpdates has
it), the volume count of a finished series, the community rating, Manga = YesAndRightToLeft for Japanese
manga and an adults-only rating where AniList flags it. Other series that match the title are offered under
Other Matches; the found cover is shown for confirmation only and is never written into the archive.
"""

from __future__ import annotations

import os

from redactor_common.gui.lookup_dialog import LookupAlternative, LookupDialogBase, LookupResult

from core.cbz_file import CbzBook
from core.filename_guess import guess_series_and_number, guess_year
from core.manga_lookup import MangaClient, MangaLookupError, MangaMatch


class MangaLookupDialog(LookupDialogBase):
    def __init__(self, books: list[CbzBook], parent=None, client: MangaClient | None = None):
        self._client = client or MangaClient()
        super().__init__(
            books,
            parent,
            window_title="Look Up Manga (AniList + MangaUpdates)",
            info_text=(
                f"Searching AniList and MangaUpdates for {len(books)} file(s) by Series (from ComicInfo, or guessed "
                "from the filename). Both describe the whole SERIES, so every volume of it gets the same answer "
                "and the volume number is never changed. If the series is not the right one, correct the name and "
                "Search This Item, or pick another under Other Matches. Compare the cover, then untick anything "
                "you do not trust and Apply. Covers are shown for confirmation only. Data from AniList and "
                "MangaUpdates."
            ),
            search_label="Searching AniList and MangaUpdates…",
            item_label=lambda book: os.path.basename(book.path),
            search_one=self._search_one_book,
            query_fields=[("series", "Series")],
            get_local_cover=lambda book: book.read_first_page_bytes(),
            resolve_alternative=self._resolve,
        )

    def _search_one_book(self, book: CbzBook, query_override: dict) -> LookupResult:
        series = query_override.get("series") or guess_series_and_number(
            book.path, book.metadata.series, book.metadata.number
        )[0]
        query = {"series": series}
        if not series:
            return LookupResult(error="needs a Series", used_query=query)
        try:
            matches = self._client.search(series, guess_year(book.path, book.metadata.year))
        except MangaLookupError as exc:
            return LookupResult(error=str(exc), used_query=query)
        if not matches:
            return LookupResult(used_query=query)
        result = self._result_for(matches[0])
        result.used_query = query
        result.alternatives = [LookupAlternative(label=m.display_label(), data=m) for m in matches[1:]]
        return result

    def _result_for(self, match: MangaMatch) -> LookupResult:
        details = self._client.details(match)
        return LookupResult(fields=details.as_dict(), cover_bytes=self._client.cover(match))

    def _resolve(self, _book: CbzBook, match: MangaMatch) -> LookupResult:
        try:
            return self._result_for(match)
        except MangaLookupError as exc:
            return LookupResult(error=str(exc))
