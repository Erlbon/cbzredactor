# Changelog

## 2026-09-10#03 -- Look Up via Bedetheque

New lookup source for francophone comics: `Import > Look Up via
Bedetheque...` searches [Bedetheque](https://www.bedetheque.com/), the
reference database for French-language "bande dessinée," by Series +
Number (like GCD, unlike Comic Vine's free-text search) -- best for
BD titles Comic Vine and GCD tend to have thin or no coverage of.

Analyzed three community scraper projects first (givka/bedetheque-scraper,
vsoeiro/bedetheque, maforget/Bedetheque-Scrapper-2), then verified
everything directly against the live site rather than trusting them --
two are years old, one archived, and the site's been redesigned since:
all three documented an individual-issue URL scheme that no longer
exists. What's still accurate, cross-validated by all three and
confirmed live, is the underlying "Label : Value" field structure a
real issue page uses.

Bedetheque sits behind Cloudflare -- a plain HTTP request gets 403 on
every page. Uses the optional `cloudscraper` package (mimics a real
browser's TLS fingerprint and solves the JS challenge programmatically)
rather than bundling a real headless browser -- lighter dependency, no
browser binary to ship in the PyInstaller build. You'll get a clear
"install cloudscraper" message if it's missing, not a crash. Same
review-then-Apply flow as the other two lookups, including the
side-by-side Current/Found cover comparison and the editable Series/
Number correction form.

11 new tests in `test_bedetheque_lookup.py`, using response shapes
captured directly from the live site.

## 2026-09-10#02 -- Per-file, per-field review before any overwrite

Replaced the old all-or-nothing "Overwrite All / Keep Existing /
Cancel" choice (shown whenever a lookup or Parse Filename would
overwrite an existing value) with a real per-file, per-field review:
"we need per field. There could be instances where we want some
fields, but not all." This is now the standard confirmation step for
every metadata-writing path that could clobber existing data -- not an
opt-in extra.

- New **Review Changes** dialog (`gui/overwrite_review_dialog.py`):
  every field a change would touch, for every affected file, shown
  side by side with its current value, each with its own Apply
  checkbox -- grouped by file so a file with several changed fields is
  reviewed as a whole. A field that's currently blank starts ticked
  (nothing to lose); a field that would actually replace a different,
  non-blank value starts unticked, requiring a deliberate per-field
  opt-in. You can accept some fields from an import and reject others
  on the very same file.
- A batch with nothing to overwrite still skips the dialog entirely --
  there's nothing to review when nothing would be clobbered, same as
  before.
- Built on `redactor_common.gui.preview_table.PreviewTableController`
  (the same "before/after + Apply checkbox" shape Search/Replace and
  Case Conversion already use), extended there with an optional
  grouping column and a per-row default-checked state specifically for
  this. Bumps the `redactor_common` pin to `2026-09-10-02`.

12 new tests across `test_overwrite_review_dialog.py` and a rewritten
`test_main_window_overwrite.py` (the old QMessageBox-button-clicking
tests no longer apply -- replaced with tests that exercise the real
dialog's default-checked logic and per-field selection, not a mocked
stand-in for it).

## 2026-09-10#01 -- quick single-file rename

Double-click a Filename cell (or right-click a single selected file >
Rename File...) to fix a typo in one filename directly, without going
through the pattern-based "Rename / Export Files..." batch tool.
epubredactor already had this (its own local copy); mp3redactor added
it too and promoted the reusable part to `redactor_common`
(`gui/rename_single_file.py`, tag `2026-09-10-01`, bumped here) --
cbzredactor consumes that shared function rather than writing another
local copy.

- Renames on disk immediately (extension kept automatically, current
  name pre-filled), refuses illegal characters/reserved Windows names/
  an already-existing filename with a clear message rather than a raw
  exception or a silent overwrite. A physical file operation -- not
  pushed onto the undo stack, same as Save/the batch rename tool.
- Right-click menu only offers it when exactly one book is genuinely
  selected (checked via `self._selected_rows`, not `_target_books()`'s
  own "select one, some, or fall back to everything loaded" fallback
  used elsewhere in this menu) and never for a book that failed to load.

Verified end-to-end against a real `.cbz` file on disk (not mocked): a
real double-click dispatch, a real rename, correct no-op on every
other column, and correct context-menu gating for a real single
selection vs. no selection. Full suite: 139 passed. No new permanent
test file -- this project's GUI layer has no automated test coverage;
the underlying `rename_file_on_disk()`/`validate_filename_stem()` logic
is already covered by `redactor_common`'s own test suite.

## 2026-09-07#01 -- redactor_common adoption fixes: row tinting, path-too-long handling

A cross-repo review of `redactor_common` adoption across all four
Redactor apps found this project was the only one without row color-
tinting or path-too-long protection, and still had an inline duplicate
of the shared QMessageBox fix. Fixed:

- **Row color-tinting** -- a file's whole row now tints amber (unsaved
  change, or a page-count mismatch that'll be corrected on save) or
  red (failed to load), via `redactor_common.gui.colors`, matching
  epub/mp3/video. Previously the Status column was plain text only.
  Also adopted `TABLE_SELECTION_STYLESHEET` for its current-cell focus
  outline (safe now -- see the `redactor_common` fix below).
- **Path-too-long protection** -- `CbzBook.save()`/`resize_images()`'s
  failure messages now route through `redactor_common.core.
  save_errors.describe_save_error()`, explaining Windows' 260-
  character path limit clearly instead of a raw exception string.
  Also fixed a related gap while in this code: the final `shutil.move()`
  rename step used to sit OUTSIDE the try/except entirely, so a
  failure there (a real possibility -- the temp file's `.tmp_write`/
  `.tmp_resize` suffix is longer than the final path) would propagate
  raw and uncaught instead of setting `save_error` at all.
- Inlined QMessageBox max-width stylesheet -> the shared
  `redactor_common.gui.qmessagebox_style.apply_message_box_style()`.
- Bumped the `redactor_common` pin to `2026-09-07-01`, which also
  fixes a real bug at the source: `colors.py`'s
  `TABLE_SELECTION_STYLESHEET` used to hardcode a selected row's own
  background/text color, silently overriding `apply_theme()`'s
  WCAG-verified, light/dark-aware selection colors -- moot for this
  app specifically (it never imported `colors.py` before now), but
  the fix is what makes adopting the stylesheet here safe today.

6 new tests across `test_row_status_color.py` and `test_cbz_file.py`.

## 2026-09-06#08 -- Fixed: side panel collapse button getting stuck

"button to collapse left field does not include cover. Pushing the
button afterwards does nothing. leftbar is stuck."

Root cause: the cover box sat directly in the panel's own splitter as
a plain `QGroupBox`, not wrapped in a `QScrollArea` the way the field
groups already are. A `QGroupBox`'s `minimumSizeHint()` is inflated by
its own title text width -- "Cover (First Page)" alone forced roughly
a 254px floor, and neither `setMinimumWidth()` nor `setMinimumSize()`
can override that (confirmed directly; `minimumSizeHint()` is a
separate, un-overridable computation `QSplitter.setSizes()` clamps
against). So collapsing looked like it worked -- the field list
visibly shrank via its own scroll area's much smaller floor -- while
the cover silently held the whole panel open at ~262px, nowhere near
the 70px target. Since the resulting width was still bigger than
`collapsed_width`, `is_collapsed()` incorrectly reported `False` right
after "collapsing", so the next click tried to collapse again instead
of restoring -- the stuck button.

Fixed by wrapping the cover box in a `QScrollArea` too (a
`QScrollArea`'s own `minimumSizeHint()` stays small regardless of its
content's) -- purely a minimum-size trick, the cover still renders at
full size normally, only shrinking with a scrollbar if the panel is
dragged narrower than the cover's own natural width.

4 new tests in `test_main_window_panel_collapse.py`, verified to
actually fail against the old code before the fix.

## 2026-09-06#07 -- Remember last directory for Load Files/Folder

Both dialogs previously always opened wherever Qt/Windows defaulted to
-- now they start from the last directory actually used, persisted
across restarts (`gui/app_settings.py`'s `load_last_directory()`/
`save_last_directory()`, same shape as the epub tool's equivalent).
Reported alongside the same gap in mp3redactor, which got its own
fix using its plain-configparser settings instead of QSettings.

## 2026-09-06#06 -- Lookup dialogs: side-by-side cover comparison

"The scrapers need more usability... show the existing cover page of
the comic ... and the one from the scraper ... so that we can see that
they are the same comic." Both `Import > Look Up via Comic Vine...` and
`Import > Look Up via Grand Comics Database...` now show the selected
row's file's own **Current** cover right next to the source's
**Found** one, so a wrong match (different series, wrong issue) is
obvious at a glance instead of only surfacing after Apply.

Promoted to `redactor_common.gui.lookup_dialog.LookupDialogBase` as a
new optional `get_local_cover(item) -> bytes | None` parameter (tag
`2026-09-06-05`) -- backward compatible for any future consumer that
doesn't supply one (that side just reads "No local cover"). Both
cbzredactor dialogs pass `book.read_first_page_bytes()`. Cover preview
size halved per slot (220x320 -> 170x250) to fit two side by side; the
dialog's own default size grew (1020x600 -> 1150x640) to keep both
comfortably readable.

3 new tests in `test_lookup_dialog_covers.py`.

## 2026-09-06#05 -- Genre picker redesign, uniform selection theme, sortable columns, panel scroll fix

Four separate real-use complaints, fixed together:

- **Genre/Language quick-pick redesign**: "the genre list gets too
  long to see the apply button." The flat `QMenu` behind the "+"
  button had no search and no real scroll affordance once enough
  custom genres piled up (Settings > Add/Remove Genres...), so it
  could overflow the screen entirely. Replaced with
  `redactor_common.gui.quick_pick_dialog.QuickPickDialog` (new,
  promoted): a fixed-size dialog with a filter box and a genuinely
  scrolling list, so OK/Cancel stay visible no matter how long the
  list gets. Genre stays multi-select (pick several, appended at
  once); Language stays single-select (replaces the field).
- **Uniform, dark-mode-safe selection** ("Selection is shit, can't see
  what is selected in dark mode... want uniform behaviour across the
  apps"): none of the four Redactor apps ever set an explicit style or
  palette, so each just inherited whatever the native platform style's
  own dark-mode approximation happened to render -- often low-contrast
  for a selected row. New `redactor_common.gui.theme.apply_theme(app)`
  switches to the Fusion style + an explicit palette (light or dark,
  auto-detected from the OS), with a Highlight/HighlightedText pair
  verified against the real WCAG contrast formula rather than
  eyeballed. Wired into all four apps' `main.py` (one line each) since
  "uniform" was the explicit ask, not just a cbzredactor fix. Found and
  fixed a genuine landmine along the way: epub/mp3/video each still had
  a stale, untracked `redactor_common/` leftover (pure `__pycache__`
  droppings from before their migration to the pip dependency) sitting
  in their project root, silently shadowing the real installed package
  for any newly-added module whenever Python resolved imports with
  that project's own directory on `sys.path` -- exactly how `main.py`
  normally runs. Deleted in all three (zero `.py` sources, zero
  git-tracked files -- safe to remove outright).
- **Click a column header to sort by it** -- click again to reverse.
  Deliberately does NOT use `QTableWidget.setSortingEnabled(True)`
  (Qt's own item-based sort would silently break every "table row N is
  `self.books[N]`" assumption throughout `gui/main_window.py` -- save,
  remove, bulk edit, lookups, and more all index into `self.books` by
  row). Instead sorts `self.books` itself and rebuilds the table from
  it, the same pattern `_rebuild_table()` already uses for Refresh/
  Clear/Remove -- so that invariant never breaks. Numeric-natured
  columns (Number/Count/Volume/Year/Month/Day/Pages/Community Rating)
  sort numerically, not lexicographically ("9" before "10"). A sort
  stays applied across a subsequent Load/Refresh (new arrivals get
  folded back into it, not just tacked onto the end).
- **Field panel now actually scrolls with the mouse wheel**: the
  `QScrollArea` itself was never broken (verified directly -- it always
  computed a correct scroll range), but the Age Rating/Manga/Black &
  White/Community Rating combo/spin boxes silently consumed the wheel
  event to change their own value instead of letting it reach the
  scroll area, the moment the cursor happened to be hovering over one
  of them while scrolling -- a well-known Qt gotcha for any
  `QScrollArea` containing a combo/spin box. Fixed with
  `_ScrollSafeComboBox`/`_ScrollSafeDoubleSpinBox`, which ignore the
  wheel unless they actually have focus.

17 new tests across `test_main_window_sort.py`,
`test_metadata_panel_scroll.py`, and
`test_metadata_panel_quick_pick.py`; 7 more in `redactor_common`'s own
`tests/test_theme.py` (the WCAG contrast checks).

## 2026-09-06#04 -- Resize Images..., with double-page-spread detection

New feature, not a fix: shrinking an extremely large CBZ's page images
down to a target max width, requested with the explicit constraint
that it be "smart enough" not to crush a double-page spread down to
single-page detail.

- **Operations > Resize Images...** -- target max width + JPEG quality,
  resize in place or export copies to a folder (originals untouched).
  Uses [Pillow](https://python-pillow.org/) (new dependency), not an
  external ImageMagick install -- a deliberate choice: this project
  already has one "you need an external tool on PATH" pain point (CBR
  conversion's unrar requirement, still on the backlog to fix by
  bundling a portable binary) and a pure-Python library avoids adding
  a second one, bundling straight into the existing PyInstaller build.
- **Double-page spread detection**: a page wider than it is tall
  (landscape or square) is treated as a spread -- true for a two-page
  spread scanned/exported as one image, false for virtually any real
  single comic/manga page. A detected spread gets **double** the
  target width rather than being squeezed to the single-page target,
  so each half keeps the same effective per-page resolution a normal
  page would.
- Only ever shrinks, never upscales -- a page already at or under its
  (possibly-doubled) target is returned completely byte-for-byte
  unchanged, not just "same dimensions after a pointless re-save".
- No preview-before-commit table (unlike Rename/Export, Parse
  Filename, etc.): accurately estimating file-size savings means
  decoding/re-encoding every page once already, which is exactly the
  expensive extra pass this feature exists to help avoid paying twice
  on the huge files it's meant for. Instead: an upfront warning in the
  dialog, and a real (not estimated) pages-resized/skipped/failed +
  total-size-before/after report once it's actually run.
- No Undo: unlike every other Operations entry, this rewrites pixel
  bytes to disk -- there's no in-memory original left to restore from,
  unlike a metadata edit. Deliberately not wired into undo_manager.
- New `core/image_resize.py` (pure per-page logic, Pillow-based) and
  `CbzBook.resize_images()` (core/cbz_file.py) -- the one deliberate
  exception to this project's "images are always copied byte-for-byte"
  guarantee, and only ever runs when explicitly asked for, never as a
  side effect of Save.

17 new tests across `test_image_resize.py` and `test_cbz_resize.py`.

## 2026-09-06#03 -- Column visibility now drives the side panel too

A "sanity check" question from the user surfaced three real gaps left
over from the previous pass's "every field is a column" work:

- **Hiding a column now hides that field's edit row in the side panel
  too**, and un-hiding it brings the row straight back --
  `ComicInfoPanel.set_visible_fields()` (new), wired from
  `MainWindow._sync_panel_visible_fields()` on construction and every
  time column visibility changes (header right-click, Settings >
  Add/Remove Columns...). This is epub's own long-standing
  `tag_panel.set_visible_fields()` pattern, ported here for the first
  time -- it was never in `redactor_common`, and still isn't (see
  below); cbzredactor's own version deliberately hides/shows widgets
  rather than rebuilding rows from scratch like epub's does, since this
  panel never needs epub's per-field bulk-edit checkboxes and the
  simpler approach has zero risk of losing an in-progress edit mid-hide.
- **No data loss from hiding a field**: a hidden field still exists in
  the data model and is still written to by a lookup apply, Parse
  Filename, Search/Replace, or Case Conversion -- only the on-screen
  row disappears. `load_metadata()`/`apply_to_metadata()`/
  `bulk_changed_fields()` were never filtered by visibility to begin
  with; the new tests in `test_panel_column_visibility.py` pin this
  down explicitly instead of leaving it merely implied.
- **Every field with real metadata can now be used as a filename
  %placeholder%** in Rename/Export and Parse Filename, not just the
  old curated 7-field subset (Series/Number/Title/Volume/Year/
  Publisher/Writer) -- `FILENAME_PLACEHOLDERS` is now built from the
  same `_FIELD_LABELS` dict the columns and panel already share, so a
  field like Genre or Story Arc can be exported into a filename.
- Direct answer to "is the column/show edit field logic part of
  redactor_common?": **no** -- confirmed by reading epub's actual
  `tag_panel.set_visible_fields()`, which is epub-local and never
  promoted. Not promoted from here either yet: cbzredactor's simpler
  hide-don't-rebuild mechanic and epub's rebuild-and-reorder one solve
  the same problem differently enough that generalizing both into one
  shared implementation isn't a clean fit yet -- deferred until a third
  Redactor app needs this and the actual common shape becomes clearer.

4 new tests in `test_panel_column_visibility.py`.

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
