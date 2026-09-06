"""
gui/comicvine_lookup_dialog.py

Look Up via Comic Vine: for each target book, searches Comic Vine using
that book's own Series + Number (falling back to the archive's
filename, parsed loosely, when Series is blank), shows the best match
-- series, issue number/title, credits, characters, publisher, and its
found cover next to the book's own existing cover for a side-by-side
visual confirmation (see redactor_common.gui.lookup_dialog's
`get_local_cover`) -- and lets the user apply it.

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

from redactor_common.gui.lookup_dialog import LookupDialogBase, LookupResult

from core.cbz_file import CbzBook
from core.comicvine_lookup import (
    ComicVineLookupError,
    download_cover_image,
    fetch_issue_details,
    fetch_publisher,
    search_comicvine,
)
from core.filename_guess import guess_series_and_number
from gui import app_settings


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
                "confirmation only -- they are never written into the archive."
            ),
            search_label="Searching Comic Vine…",
            item_label=lambda book: os.path.basename(book.path),
            search_one=self._search_one_book,
            query_fields=[("series", "Series"), ("number", "Number")],
            get_local_cover=lambda book: book.read_first_page_bytes(),
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
        used_query = {"series": series, "number": number}

        if not self._api_key:
            return LookupResult(
                error='No Comic Vine API key set -- click "Change API Key…" to add one.',
                used_query=used_query,
            )

        try:
            candidates = search_comicvine(self._api_key, series, number)
        except ComicVineLookupError as exc:
            return LookupResult(error=str(exc), used_query=used_query)
        if not candidates:
            return LookupResult(used_query=used_query)

        best = candidates[0]
        try:
            details = fetch_issue_details(self._api_key, best.detail_url)
            details.publisher = fetch_publisher(self._api_key, best.volume_detail_url)
            fields = details.as_dict()
        except ComicVineLookupError as exc:
            return LookupResult(error=str(exc), used_query=used_query)

        cover_bytes = None
        if best.image_url:
            try:
                cover_bytes = download_cover_image(best)
            except ComicVineLookupError:
                pass  # cover is a nice-to-have preview only; never worth failing the row over

        return LookupResult(fields=fields, cover_bytes=cover_bytes, used_query=used_query)
