# Changelog

## 2026-09-05#05 -- Migrate lookups onto redactor_common's shared template

The Comic Vine and GCD lookups had independently arrived at the same
shape epubredactor's own Google Books/Calibre/Open Library lookups
already used -- promoted the shared parts into redactor_common
(pinned to tag `2026-09-05-02`) rather than letting a third/fourth/
fifth copy of the same boilerplate accumulate:

- `core/comicvine_lookup.py` and `core/gcd_lookup.py` now build their
  network calls on `redactor_common.core.lookup_client`'s
  `fetch_json()`/`fetch_bytes()`/`make_default_fetch()` instead of
  each keeping its own copy of the HTTPError/URLError/JSON-decode
  translation (one copy for API calls, a second for cover-image
  downloads). No behavior change -- all existing tests pass unchanged.
- `gui/comicvine_lookup_dialog.py` and `gui/gcd_lookup_dialog.py` are
  now thin `redactor_common.gui.lookup_dialog.LookupDialogBase`
  subclasses supplying only `search_one()` -- the shared table/
  progress-dialog/checkbox/Apply plumbing moved out entirely. Combined
  line count for both dialogs dropped from ~460 to ~195.
- epubredactor's three lookup dialogs are the same shape this was
  generalized from but are NOT migrated -- deliberately scoped to
  cbzredactor only, to avoid regression risk in a repo developed
  elsewhere until this usage proves the abstraction out.

## 2026-09-05#04 -- Warn before a lookup overwrites existing metadata

- Applying any lookup's results (Comic Vine, GCD, and any future
  source -- enforced once in MainWindow._resolve_overwrite_conflicts(),
  not per-dialog) now checks whether it would overwrite a field that
  already has a different, non-blank value. If so, asks once: Overwrite
  All, Keep Existing (fill blanks only), or Cancel -- instead of
  silently clobbering hand-typed or previously-looked-up data.

## 2026-09-05#03 -- Grand Comics Database lookup

- `Import > Look Up via Grand Comics Database...`: same review-then-
  Apply flow as Comic Vine, against comics.org's open API (no API key
  needed). Aggregates credits/genre/characters from an issue's "comic
  story" entries only (covers, text stories, and credits pages are
  real GCD data but not what a ComicInfo.xml field is meant to hold),
  strips GCD's own "(credited)"/"(uncredited)"/translation annotations
  down to bare names, and reads publisher directly from the issue
  response (no second request needed, unlike Comic Vine).
- GCD's search results come back alphabetically, not by relevance --
  confirmed live that searching "Watchmen" #1 buries the real 1986
  series behind dozens of "Before Watchmen: ..." spin-offs purely
  because of sort order. search_gcd() now follows pagination (capped)
  and promotes an exact series-name match to the top when found on any
  page, rather than trusting page 1 alone.
- Refactored the shared "guess series/number from a book's metadata or
  filename" logic out of the Comic Vine dialog into
  core/filename_guess.py so both lookup dialogs (and any future one)
  use the same tested implementation instead of two copies.

## 2026-09-05#02 -- Comic Vine lookup, rebrand, app icon

- Rebranded as "The ƆBZ Redactor" (Ɔ = LATIN CAPITAL LETTER OPEN O, a
  "reversed C" -- same reversed-letter mark as epubredactor's Ǝ).
- Added a real app icon (assets/icon.png master, assets/icon.ico
  multi-resolution) and wired it into the window/taskbar icon and the
  PyInstaller build.
- `Import > Look Up via Comic Vine...`: searches Comic Vine by
  Series + Number (or a filename guess), fetches full issue credits
  and a best-effort publisher name, shows a cover thumbnail for visual
  confirmation only, and applies chosen fields on Apply -- same
  review-before-apply table pattern as epubredactor's lookup dialogs.
  Needs a free API key, entered via Settings > Comic Vine API Key...
  and stored locally.
- Scoped (not yet built): Metron, Grand Comics Database, and MangaDex
  as additional lookup sources -- see README's "Other metadata
  sources" section.

## 2026-09-05#01 -- Initial scaffold

- First working version: load one or more `.cbz` files, view/edit
  ComicInfo.xml fields (identity, story, credits, publication,
  classification, summary/notes/review), and save back into the
  archive without touching the page images.
- Sidebar shows the first page as a cover thumbnail.
- PageCount is always recomputed from the archive's actual image count
  on save; a mismatch against the stored value is flagged in the file
  list before saving.
- `Import > Convert CBR to CBZ` converts RAR-based archives to CBZ
  ahead of editing (requires an external unrar/unar/bsdtar tool on
  PATH -- not bundled yet, see README's "CBR support" section).
- Deferred to a later version: per-page `<Pages>` tagging
  (FrontCover/Story/BackCover/...) -- existing `<Pages>` data is
  preserved untouched on save, just not yet exposed for editing.
