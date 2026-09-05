# Changelog

## 2026-09-06#02 -- v2.1 draft fields, every field as a column, ComicRack/ComicTagger comparison

Prompted by a real ComicRack column-chooser screenshot: most of those
columns turned out to be fields already editable in the side panel but
never exposed as table columns, not a genre-list problem specifically.
Also researched a related open question directly against source rather
than guessing:

- **ComicRack vs. ComicTagger**: confirmed against ComicTagger's own
  `comicapi/tags/comicrack.py` that its comma-separated multi-value
  convention (Genre/Characters/Teams/Locations/StoryArc/credits)
  matches what this app already does. Real differences found: Web is
  space-separated multiple URLs for ComicTagger (harmless here, this
  app doesn't enforce any separator); ScanInformation gets a
  ComicTagger-specific file-hash convention (`sum:{hash}`) this app
  doesn't replicate, just edits as plain text like anything else.
- **Four v2.1 draft fields added** (confirmed against the Anansi
  Project's actual v2.1 draft XSD): Translator, Tags, StoryArcNumber,
  GTIN. Not finalized, but already understood by ComicTagger and
  Kavita, and purely additive -- a v2.0-only reader just ignores what
  it doesn't recognize, same tolerance this app already extends to any
  other unrecognized element.
- **ScanInformation exposed in the form** -- it already round-tripped
  correctly on save, but had no editable field at all until now.
- **Every ComicInfo field is now an available table column** (45 total,
  up from 6), not just Filename/Title/Series/Number/Pages/Status --
  matching ComicRack's own "everything is an optional column"
  philosophy. A brand-new install still only shows the original 6 by
  default (DEFAULT_HIDDEN_COLUMNS); once you've saved any column
  visibility choice at all (including "show everything"), your own
  choice always wins over that default.

2 new tests for the v2.1 fields (round-trip, correct schema-order
placement).

## 2026-09-06#01 -- Undo, Search/Replace, Case Conversion, list management, correct toolbar

Still finishing the walk through redactor_common's full toolkit from
the previous pass -- Search/Replace, Case Conversion, and Undo were
still missing, and the toolbar didn't match the family's actual shape.
Bumped redactor_common pin to 2026-09-06-01 (adds `core/undo.py`,
generalized from epub's original `EpubBook`-specific version).

- **Undo** (Ctrl+Z / toolbar / Operations menu): a bounded 5-deep
  in-memory undo stack covering bulk edit, lookup-apply, Parse
  Filename, Search/Replace, and Case Conversion. Excludes physical
  file operations by design (see redactor_common's own module
  docstring for why).
- **Search/Replace...** and **Case Conversion...** (Operations menu):
  built on `redactor_common.gui.search_replace_dialog`/
  `case_conversion_dialog`, working across the full ComicInfo field
  set (Search/Replace can also target the filename itself, performing
  a real on-disk rename on accept).
- **Remove Files** (Delete), **Refresh List** (F5/Ctrl+R), **Clear
  List** (File menu): list-management actions that were entirely
  missing. Found and fixed a real bug while building `remove_selected`:
  `CbzBook` is a plain `@dataclass` (value-based `__eq__`, therefore
  unhashable) -- an initial set-membership-based removal crashed
  outright with a real book, and would have silently removed multiple
  identical-looking books with a less lucky test case. Fixed to
  exclude by row index instead; regression-tested.
- **Toolbar corrected** to match epubredactor's actual shape: Load
  Files, Load Folder, Save, Apply (bulk edit -- now a shared QAction
  with the Operations menu, dynamic "Apply to N Selected File(s)"
  label, not a button embedded in the panel), Undo, then a Panel
  toggle and table-font zoom control (`redactor_common.gui.
  zoom_toolbar.TableZoomController`) pushed to the far right.

7 new tests cover the unhashable-book fix, clear-list confirmation
(prompts when dirty, skips the prompt when nothing is, respects both
Yes/No), and undo restoring metadata + the dirty flag together.

## 2026-09-05#08 -- Column management, Rename/Parse Filename, Genre/Language lists, cover layout fix

The rest of the shared Redactor-family toolkit this project had
skipped in its initial build -- bumped redactor_common pin to
2026-09-05-05 (adds `manage_list_dialog.py`, promoted from epub):

- **Columns**: drag a header to reorder, right-click for a show/hide
  checklist or "Add/Remove Columns...". Order/visibility/widths all
  persist across restarts, keyed by column name (not index) via
  `redactor_common.core.table_settings`. Right-click a row for "Open
  Containing Folder"/"Copy Path".
- **Rename / Export Files...** (File, F2) and **Parse Filename...**
  (Import, F3): the mp3tag-style `%field%` pattern engine
  (`redactor_common.gui.rename_pattern_dialog`/`parse_filename_dialog`),
  wired to a curated subset of ComicInfo fields (series, number, title,
  volume, year, publisher, writer). Parse Filename's results go through
  the same overwrite-conflict protection as a lookup. Both share one
  pattern history.
- **Genre/Language quick-pick + management**: a "+" button next to the
  Genre and Language (ISO) fields opens a curated default list (new
  `core/comic_genres.py`/`core/comic_languages.py`) plus your own
  custom entries; Settings > Add/Remove Genres.../Add/Remove
  Languages... manages them (hide a default without deleting it,
  restore all hidden defaults, add/remove custom ones) via the newly-
  promoted `ManageListDialog`.
- **Cover layout fixed**: was above the field form in a fixed-height
  box; now below it, in a resizable splitter -- matching epubredactor's
  own "Bulk Edit Tags above, Cover Image below, draggable divider
  between them" convention, which this had inverted for no reason.

All of the above -- table_settings, rename/parse-filename, and the
genre/language management pattern -- were already fully generic pieces
of `redactor_common` (or, for `manage_list_dialog.py`, a genuinely
generic piece of epub that had just never been promoted) that this
project's first build simply hadn't wired up yet.

## 2026-09-05#07 -- Toolbar, multi-select bulk editing, lookup query correction

Four real usability gaps reported by the user in one go:

- **Missing top toolbar**: added one (Load Files / Save / Save All
  Changed) above the table, reusing the same QActions the menus use.
- **Only one file selectable at a time**: the table is now multi-select
  (ctrl/shift-click). Selecting 2+ files switches the metadata panel
  into **bulk edit** mode -- every field starts blank, and only the
  ones you actually fill in get applied to every selected file at once
  via a new "Apply to N Selected Files" button (blank = unchanged, not
  cleared). Save, and both lookup dialogs, now operate on the whole
  selection instead of just one file.
- **GCD lookups failing**: traced to `core/filename_guess.py` not
  handling real-world scene-release filenames at all -- anything with
  trailing metadata like `Batman 001 (2016) (Digital) (Empire).cbz`
  extracted no number whatsoever (the pattern required the number to
  be the literal last thing in the filename), and GCD's search requires
  both series AND number to search at all. Fixed to strip trailing
  bracketed/parenthesized groups before looking for the number.
- **No way to correct a bad guess, and the Comic Vine cover preview
  was tiny**: both fixed at the shared `redactor_common.gui.
  lookup_dialog.LookupDialogBase` level (see that repo's own changelog,
  pinned to tag `2026-09-05-04`) -- a large per-row cover preview
  replaces the old in-table icon, and an editable Series/Number
  correction form lets you fix a wrong guess and re-search just that
  row via "Search This Item", without restarting the whole batch. This
  is what actually makes GCD usable even when the filename guess still
  isn't perfect after the parsing fix above.

## 2026-09-05#06 -- Side panel moved to the left; fixed a stuck collapse toggle

- Moved the metadata side panel (cover thumbnail + ComicInfo.xml form)
  from the right side of the window to the left, with the file table on
  the right -- matches epubredactor's and videoredactor's own layout,
  which this had inverted for no real reason.
- Found and fixed a real bug while re-verifying the panel's collapse
  toggle after the move: it visibly shrank on click but never actually
  reached its target width, so the toggle button got stuck and
  couldn't expand the panel back open. Root cause was in
  `redactor_common.gui.collapsible_splitter.CollapseToggleButton`
  itself (missing `setMinimumWidth()` -- see that repo's own changelog
  for detail) -- fixed there (pinned to tag `2026-09-05-03`) so every
  consuming project's collapse toggle benefits, not just this one.
- Also bumped `PANEL_COLLAPSED_WIDTH` from 32 to 70: the cover
  thumbnail's own 60px minimum width means 32 was never actually
  reachable regardless of the button fix -- see the constant's own
  comment in `gui/main_window.py`.

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
