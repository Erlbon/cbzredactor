"""
gui/gcd_lookup_dialog.py

Look Up via Grand Comics Database: for each target book, searches GCD
using that book's own Series + Number (falling back to a filename
guess, same as the Comic Vine dialog) and shows the match -- series,
story title, credits, genre, characters, publisher, and a cover
thumbnail for visual confirmation only (see core/gcd_lookup.py's
module docstring for why it's never written into the archive).

Built on redactor_common's gui/lookup_dialog.py (LookupDialogBase) --
this class supplies only what's GCD-specific: search_one()'s actual
API calls. Everything else (the table, progress dialog, checkboxes,
accepted_metadata()) lives in the shared base, same as
gui/comicvine_lookup_dialog.py.

No API key needed, unlike Comic Vine -- GCD's API is open. But GCD's
own endpoint shape requires BOTH a series name and an issue number
(no free-text search like Comic Vine's), so a book with a blank
Series and a filename that doesn't parse into "name + number" simply
can't be searched here; that row is marked "(needs Series + Number)"
rather than silently guessing something wrong.
"""

from __future__ import annotations

import os

from redactor_common.gui.lookup_dialog import LookupDialogBase, LookupResult

from core.cbz_file import CbzBook
from core.filename_guess import guess_series_and_number
from core.gcd_lookup import GcdLookupError, download_cover_image, fetch_issue_details, search_gcd


class GcdLookupDialog(LookupDialogBase):
    def __init__(self, books: list[CbzBook], parent=None):
        super().__init__(
            books,
            parent,
            window_title="Look Up via Grand Comics Database",
            info_text=(
                f"Searching the Grand Comics Database for {len(books)} file(s) by Series + "
                "Number (guessed from the filename when Series is blank; GCD needs both to "
                "search, unlike Comic Vine). Untick anything you don't trust, then Apply. Cover "
                "images are shown for confirmation only -- they are never written into the archive."
            ),
            search_label="Searching the Grand Comics Database…",
            item_label=lambda book: os.path.basename(book.path),
            search_one=self._search_one_book,
        )

    def _search_one_book(self, book: CbzBook) -> LookupResult:
        series, number = guess_series_and_number(book.path, book.metadata.series, book.metadata.number)
        if not series or not number:
            return LookupResult(error="needs Series + Number")

        try:
            candidates = search_gcd(series, number)
        except GcdLookupError as exc:
            return LookupResult(error=str(exc))
        if not candidates:
            return LookupResult()

        best = candidates[0]
        try:
            details = fetch_issue_details(best.detail_url)
            fields = details.as_dict()
        except GcdLookupError as exc:
            return LookupResult(error=str(exc))

        cover_bytes = None
        if details.cover_image_url:
            try:
                cover_bytes = download_cover_image(details)
            except GcdLookupError:
                pass  # cover is a nice-to-have preview only

        return LookupResult(fields=fields, cover_bytes=cover_bytes)
