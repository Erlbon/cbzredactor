"""
gui/comicvine_lookup_dialog.py

Look Up via Comic Vine: for each target book, searches Comic Vine using
that book's own Series + Number (falling back to the archive's
filename, parsed loosely, when Series is blank), shows the best match
-- series, issue number/title, credits, characters, publisher, and its
found cover next to the book's own existing cover for a side-by-side
visual confirmation (see redactor_common.gui.lookup_dialog's
`get_local_cover`) -- and lets the user apply it.

Comic Vine's /search/ endpoint matches loosely against issue/volume
text, so the "best" result is rarely a sure thing -- core/
comicvine_lookup.py re-ranks candidates itself (word-overlap series
match, issue-number sanity check, a year hint from ComicInfo.xml or
the filename) before this module ever sees them, but ranking alone
still can't always pick the one true release. The next few
lower-ranked candidates are surfaced as a pickable "Other Matches
Found" list in the detail panel (see redactor_common.gui.lookup_dialog's
`resolve_alternative`), so a wrong top pick is a click away instead of
requiring the Series/Number text to be re-typed and re-searched.

When a series name has been published as several different volumes
(reboots, imprints, a same-named unrelated series), Series/Number
alone can't disambiguate them -- Publisher and Series Year (the
volume's own start year, not one issue's own cover date) are offered
as two more, purely opt-in, query fields for exactly that case (see
core/comicvine_lookup.py's `filter_candidates_by_series()`).

Built on redactor_common's gui/lookup_dialog.py (LookupDialogBase) --
this class supplies only what's Comic-Vine-specific: the API-key
prompt/storage and search_one()'s actual API calls. Everything else
(the table, progress dialog, checkboxes, accepted_metadata()) lives in
the shared base, same as gui/gcd_lookup_dialog.py.

Prompts for a Comic Vine API key (stored via gui/app_settings.py) the
first time none is set, the same "ask once, remember it" pattern as
epubredactor's Calibre-location prompt.
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import QInputDialog, QLineEdit, QPushButton

from redactor_common.gui.lookup_dialog import LookupAlternative, LookupDialogBase, LookupResult

from core.cbz_file import CbzBook
from core.comicvine_lookup import (
    ComicVineCandidate,
    ComicVineLookupError,
    download_cover_image,
    fetch_issue_details,
    fetch_publisher,
    filter_candidates_by_series,
    search_comicvine,
)
from core.filename_guess import guess_series_and_number, guess_year
from gui import app_settings

# How many lower-ranked candidates (beyond the top pick already shown
# as the row's default "Found" result) are offered as alternatives.
# Comic Vine's own search `limit` is bumped a bit past this so there's
# real headroom left to rank -- see _search_one_book().
MAX_ALTERNATIVES = 5


class ComicVineLookupDialog(LookupDialogBase):
    def __init__(self, books: list[CbzBook], parent=None):
        self._api_key = app_settings.load_comicvine_api_key()
        if not self._api_key:
            self._api_key = self._prompt_for_key_value(parent, self._api_key)

        super().__init__(
            books,
            parent,
            window_title="Look Up via Comic Vine",
            info_text=(
                f"Searching Comic Vine for {len(books)} file(s) by Series + Number "
                "(guessed from the filename when Series is blank). Compare the file's own "
                "cover against the one found for each row before trusting a match. Untick "
                "anything you don't trust, then Apply. Cover images are shown for "
                "confirmation only -- they are never written into the archive. If a common "
                "series name matches several different volumes, select a row and fill in "
                "Publisher and/or Series Year (the volume's own start year, not this issue's "
                "cover date) to narrow it down, then Search This Item."
            ),
            search_label="Searching Comic Vine…",
            item_label=lambda book: os.path.basename(book.path),
            search_one=self._search_one_book,
            query_fields=[
                ("series", "Series"),
                ("number", "Number"),
                ("publisher", "Publisher"),
                ("series_year", "Series Year"),
            ],
            get_local_cover=lambda book: book.read_first_page_bytes(),
            resolve_alternative=self._resolve_alternative,
        )

        change_key_btn = QPushButton("Change API Key…")
        change_key_btn.clicked.connect(self._change_key)
        self.add_toolbar_button(change_key_btn)

    # ------------------------------------------------------------------
    # API key

    @staticmethod
    def _prompt_for_key_value(parent, current: str) -> str:
        text, ok = QInputDialog.getText(
            parent,
            "Comic Vine API Key",
            "Enter your Comic Vine API key (free -- register at comicvine.gamespot.com/api/):",
            QLineEdit.EchoMode.Normal,
            current,
        )
        if ok:
            app_settings.save_comicvine_api_key(text)
            return text.strip()
        return current

    def _change_key(self) -> None:
        self._api_key = self._prompt_for_key_value(self, self._api_key)
        self._run_search()

    # ------------------------------------------------------------------
    # Searching one book

    def _search_one_book(self, book: CbzBook, query_override: dict) -> LookupResult:
        guessed_series, guessed_number = guess_series_and_number(
            book.path, book.metadata.series, book.metadata.number
        )
        series = query_override.get("series") or guessed_series
        number = query_override.get("number") or guessed_number
        # Publisher/Series Year have no filename/ComicInfo guess of
        # their own -- unlike Series/Number they start blank on every
        # row, purely opt-in for narrowing down an ambiguous match
        # (see filter_candidates_by_series()).
        publisher = query_override.get("publisher", "")
        series_year = query_override.get("series_year", "")
        used_query = {
            "series": series, "number": number, "publisher": publisher, "series_year": series_year,
        }

        if not self._api_key:
            return LookupResult(
                error='No Comic Vine API key set -- click "Change API Key…" to add one.',
                used_query=used_query,
            )

        year_hint = guess_year(book.path, book.metadata.year)
        try:
            # A few more than MAX_ALTERNATIVES+1 are fetched so ranking
            # has real candidates to work with beyond just whatever
            # Comic Vine's own relevance order put first.
            candidates = search_comicvine(
                self._api_key,
                series,
                number,
                year_hint=year_hint,
                max_results=MAX_ALTERNATIVES + 5,
            )
            if publisher or series_year:
                candidates = filter_candidates_by_series(
                    candidates, publisher, series_year, self._api_key
                )
        except ComicVineLookupError as exc:
            return LookupResult(error=str(exc), used_query=used_query)
        if not candidates:
            return LookupResult(used_query=used_query)

        best = candidates[0]
        try:
            fields, cover_bytes = self._resolve_candidate(best)
        except ComicVineLookupError as exc:
            return LookupResult(error=str(exc), used_query=used_query)

        alternatives = [
            LookupAlternative(label=candidate.display_label(), data=candidate)
            for candidate in candidates[1 : MAX_ALTERNATIVES + 1]
        ]

        return LookupResult(
            fields=fields,
            cover_bytes=cover_bytes,
            used_query=used_query,
            alternatives=alternatives,
        )

    def _resolve_candidate(self, candidate: ComicVineCandidate) -> tuple[dict, bytes | None]:
        """Fetches full credits + publisher + cover for one candidate --
        the same per-candidate work whether it's a row's initial top
        pick (above) or an alternative the user picks afterwards (via
        _resolve_alternative(), the callback LookupDialogBase invokes
        for the "Other Matches Found" list)."""
        details = fetch_issue_details(self._api_key, candidate.detail_url)
        details.publisher = fetch_publisher(self._api_key, candidate.volume_detail_url)
        fields = details.as_dict()

        cover_bytes = None
        if candidate.image_url:
            try:
                cover_bytes = download_cover_image(candidate)
            except ComicVineLookupError:
                pass  # cover is a nice-to-have preview only; never worth failing the row over

        return fields, cover_bytes

    def _resolve_alternative(self, book: CbzBook, candidate: ComicVineCandidate) -> LookupResult:
        try:
            fields, cover_bytes = self._resolve_candidate(candidate)
        except ComicVineLookupError as exc:
            return LookupResult(error=str(exc))
        return LookupResult(fields=fields, cover_bytes=cover_bytes)
