"""
gui/bedetheque_lookup_dialog.py

Look Up via Bedetheque: for each target book, searches Bedetheque
(https://www.bedetheque.com) using that book's own Series + Number
(falling back to a filename guess, same as the Comic Vine/GCD dialogs)
and shows the match -- credits, publisher, publication year/month,
summary, and its found cover next to the book's own existing cover for
a side-by-side visual confirmation (see redactor_common.gui.
lookup_dialog's `get_local_cover`; core/bedetheque_lookup.py's module
docstring covers why a fetched cover is never written into the
archive).

Built on redactor_common's gui/lookup_dialog.py (LookupDialogBase) --
this class supplies only what's Bedetheque-specific: search_one()'s
actual scraping calls. Everything else (the table, progress dialog,
checkboxes, accepted_metadata()) lives in the shared base, same as
gui/comicvine_lookup_dialog.py and gui/gcd_lookup_dialog.py.

Unlike Comic Vine (needs an API key) and GCD (no key, open API),
Bedetheque sits behind Cloudflare (see core/bedetheque_lookup.py's own
module docstring) and needs a `cloudscraper` session to get past it.
That session is created ONCE here, in __init__, and reused for every
book in the batch and any "Search This Item" retry -- solving
Cloudflare's challenge fresh on every single request would be slow and
needlessly conspicuous. `cloudscraper` is an optional dependency (see
requirements.txt); its own import is lazy (inside core/bedetheque_lookup.
make_bedetheque_fetch()), so importing THIS module doesn't require it
either -- only actually opening this dialog does, and a missing
dependency surfaces here as a normal, catchable error rather than an
import-time crash of the whole app.

Best-effort source, same caveat GCD's own module carries: verified
directly against the live site (2026-09-10) rather than trusted from
any of the three community scraper projects analyzed beforehand, all
of which turned out to document a URL scheme the site no longer uses.
"""

from __future__ import annotations

import os

from redactor_common.gui.lookup_dialog import LookupDialogBase, LookupResult

from core.bedetheque_lookup import (
    BedethequeLookupError,
    download_cover_image,
    fetch_issue_details,
    make_bedetheque_fetch,
    search_bedetheque,
)
from core.cbz_file import CbzBook
from core.filename_guess import guess_series_and_number


class BedethequeLookupDialog(LookupDialogBase):
    def __init__(self, books: list[CbzBook], parent=None):
        # Raises ImportError if cloudscraper isn't installed -- the
        # caller (MainWindow.open_bedetheque_lookup_dialog()) checks
        # for that BEFORE ever constructing this dialog, with its own
        # clear message, so this should never actually raise in
        # practice; not re-checked here to avoid two dialogs for the
        # same problem.
        self._fetch = make_bedetheque_fetch()

        super().__init__(
            books,
            parent,
            window_title="Look Up via Bedetheque",
            info_text=(
                f"Searching Bedetheque (bedetheque.com) for {len(books)} file(s) by Series + "
                "Number (guessed from the filename when Series is blank; Bedetheque needs both "
                "to search, like GCD). Best for French-language \"bande dessinée\" -- Comic Vine "
                "and GCD tend to have thin or no coverage of these. Compare the file's own cover "
                "against the one found for each row before trusting a match. Untick anything you "
                "don't trust, then Apply. Cover images are shown for confirmation only -- they "
                "are never written into the archive."
            ),
            search_label="Searching Bedetheque…",
            item_label=lambda book: os.path.basename(book.path),
            search_one=self._search_one_book,
            query_fields=[("series", "Series"), ("number", "Number")],
            get_local_cover=lambda book: book.read_first_page_bytes(),
        )

    def _search_one_book(self, book: CbzBook, query_override: dict) -> LookupResult:
        guessed_series, guessed_number = guess_series_and_number(
            book.path, book.metadata.series, book.metadata.number
        )
        series = query_override.get("series") or guessed_series
        number = query_override.get("number") or guessed_number
        used_query = {"series": series, "number": number}

        if not series or not number:
            return LookupResult(error="needs Series + Number", used_query=used_query)

        try:
            candidates = search_bedetheque(series, number, fetch=self._fetch)
        except BedethequeLookupError as exc:
            return LookupResult(error=str(exc), used_query=used_query)
        if not candidates:
            return LookupResult(used_query=used_query)

        best = candidates[0]
        try:
            details = fetch_issue_details(best.detail_url, fetch=self._fetch)
        except BedethequeLookupError as exc:
            return LookupResult(error=str(exc), used_query=used_query)
        details.issue_number = best.issue_number  # not on the issue page itself -- see fetch_issue_details()
        fields = details.as_dict()

        cover_bytes = None
        if details.cover_image_url:
            try:
                cover_bytes = download_cover_image(details, fetch=self._fetch)
            except BedethequeLookupError:
                pass  # cover is a nice-to-have preview only

        return LookupResult(fields=fields, cover_bytes=cover_bytes, used_query=used_query)
