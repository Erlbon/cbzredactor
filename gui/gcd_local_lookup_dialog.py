"""
gui/gcd_local_lookup_dialog.py

Import > Look Up via GCD (Local Database)... -- the same review-then-
Apply dialog as the online lookups (redactor_common's LookupDialogBase),
backed by the user's own downloaded GCD dump (core/gcd_local.py)
instead of the network: milliseconds per file, no hourly limit.

The query is built with core/scene_name.py's parser: series, issue
number, the issue's year, and a series start year when the filename
has one ("Batman (2016) 045" -> the 2016 series). Other matching
issues -- another series of the same name, a reprint -- are offered
under "Other Matches"; picking one is instant.

No covers: the dump holds none, so the "Found" side stays empty.
Compare the credits/date instead.
"""

from __future__ import annotations

import os

from redactor_common.gui.lookup_dialog import LookupAlternative, LookupDialogBase, LookupResult

from core.cbz_file import CbzBook
from core.gcd_local import GcdLocalDatabase, GcdLocalError
from core.scene_name import parse_filename


class GcdLocalLookupDialog(LookupDialogBase):
    def __init__(self, books: list[CbzBook], database: GcdLocalDatabase, parent=None):
        self._db = database
        super().__init__(
            books,
            parent,
            window_title="Look Up via GCD (Local Database)",
            info_text=(
                f"Searching your local copy of the Grand Comics Database for {len(books)} "
                "file(s), by Series + Number (+ year), from ComicInfo or the filename. "
                "Other matching issues are listed under Other Matches. The local database "
                "has no cover images -- check the credits and date before trusting a match. "
                "Untick anything you don't trust, then Apply."
            ),
            search_label="Searching the local GCD database…",
            item_label=lambda book: os.path.basename(book.path),
            search_one=self._search_one_book,
            query_fields=[("series", "Series"), ("number", "Number"), ("year", "Year"), ("series_year", "Series start year")],
            get_local_cover=lambda book: book.read_first_page_bytes(),
            resolve_alternative=self._resolve,
        )

    def _default_query(self, book: CbzBook) -> dict:
        meta = book.metadata
        parsed = parse_filename(book.path)
        number = meta.number or parsed.number or (parsed.volume if len(parsed.volume) < 4 else "")
        series_year = meta.volume if len(meta.volume or "") == 4 else (parsed.volume if len(parsed.volume) == 4 else "")
        return {
            "series": meta.series or parsed.series,
            "number": number,
            "year": meta.year or parsed.year,
            "series_year": series_year,
        }

    def _search_one_book(self, book: CbzBook, query_override: dict) -> LookupResult:
        query = self._default_query(book)
        if query_override:
            query = {key: query_override.get(key, "") for key in query}
        if not query["series"]:
            return LookupResult(error="needs a Series", used_query=query)
        try:
            candidates = self._db.search(query["series"], query["number"], query["year"], query["series_year"])
            if not candidates:
                return LookupResult(used_query=query)
            fields = self._db.details(candidates[0].issue_id).as_dict()
        except GcdLocalError as exc:
            return LookupResult(error=str(exc), used_query=query)
        alternatives = [LookupAlternative(label=c.display_label(), data=c.issue_id) for c in candidates[1:]]
        return LookupResult(fields=fields, used_query=query, alternatives=alternatives)

    def _resolve(self, _book: CbzBook, issue_id) -> LookupResult:
        try:
            return LookupResult(fields=self._db.details(issue_id).as_dict())
        except GcdLocalError as exc:
            return LookupResult(error=str(exc))
