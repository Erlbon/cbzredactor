# The ƆBZ Redactor

A PyQt6 desktop tool for viewing and editing the `ComicInfo.xml`
metadata embedded in CBZ comic book archives -- title, series,
credits, publisher, classification, and more -- without touching the
actual page images. Part of the "Redactor" family alongside the
[epubredactor](https://github.com/Erlbon/epubredactor),
[mp3redactor](https://github.com/Erlbon/mp3redactor), and
[videoredactor](https://github.com/Erlbon/videoredactor) tools,
sharing UI/core code via
[redactor_common](https://github.com/Erlbon/redactor_common).

## What a CBZ is

A CBZ file is a plain ZIP archive of sequentially-named page images
(usually JPG), optionally with a `ComicInfo.xml` metadata file at its
root -- the de facto standard originating from ComicRack, documented by
the [Anansi Project](https://anansi-project.github.io/docs/comicinfo/intro).
Not every CBZ has one; this tool creates one on save if it's missing,
rather than requiring it up front.

### ComicRack vs. ComicTagger

ComicRack (no longer developed) originated ComicInfo.xml; ComicTagger
is the actively-developed tool most people tagging comics today
actually use. Checked directly against ComicTagger's own source
(`comicapi/tags/comicrack.py`) rather than assumed: its comma-separated
convention for Genre/Characters/Teams/Locations/StoryArc/credits
matches what this app already uses. Two real differences worth
knowing: ComicTagger treats `Web` as **space**-separated multiple URLs
(this app treats it as a plain string either way, so nothing to
reconcile); and ComicTagger auto-embeds a file hash into
`ScanInformation` for its own duplicate detection (this app just
treats it as a plain editable field, same as any other -- see
Features below for the v2.1 draft fields ComicTagger already
understands that this app now supports too).

## Features

- Uses the same explicit light/dark theme as every other Redactor app
  (`redactor_common.gui.theme.apply_theme()`) -- selection is clearly
  visible in dark mode, unlike relying on the OS's native style, whose
  own dark-mode approximation of a selected row's colors is often too
  low-contrast to see at a glance.
- Load one or many `.cbz` files (or a whole folder) and browse them in
  a table. Multi-select (ctrl/shift-click, same as Explorer) works for
  saving, looking up, or bulk-editing several files at once.
- A toolbar sits above the table for one-click access to the most
  common actions -- Load Files, Load Folder, Save, Apply (bulk edit,
  see below), Undo, then a Panel-minimize toggle and a table-font
  zoom control pushed to the far right -- matching epubredactor's own
  toolbar shape exactly, sharing the same `QAction`s as the menus so
  neither drifts out of sync with the other.
- **Undo** (Ctrl+Z, or the toolbar/Operations menu) reverts the last
  in-memory metadata edit -- bulk edit, a lookup apply, Parse Filename,
  Search/Replace, or Case Conversion -- up to 5 deep. Deliberately
  doesn't cover physical file operations (Rename/Export, Save): those
  are already deliberate, confirmed actions of their own.
- **Search/Replace...** and **Case Conversion...** (Operations menu)
  work across any ComicInfo field (Search/Replace can also target the
  filename itself, actually renaming the file on accept) -- both show
  a before/after preview with a per-row Apply checkbox before anything
  is written.
- **Remove Files** (Delete key), **Refresh List** (F5), and **Clear
  List** (File menu) manage the loaded list itself -- Remove/Clear only
  ever touch the in-memory list, never disk; Refresh re-scans the
  folders your loaded files live in for new ones and re-reads
  everything still present, discarding unsaved edits (confirmed first).
- **Columns are drag-to-reorder** (grab a header) **and hideable** (right-
  click a header, or Settings > Add/Remove Columns...) -- both the
  order and which columns are visible persist across restarts, keyed
  by column name so a future column added in code can't silently
  scramble a saved preference. Right-clicking a row offers "Open
  Containing Folder"/"Copy Path" for the selection. Every ComicInfo
  field is available as a column, not just the handful shown by
  default (Filename/Title/Series/Number/Pages/Status) -- matching
  ComicRack's own "everything is an optional column" convention, just
  with a much smaller starting set so a fresh install isn't
  overwhelming. **Hiding a column also hides that field's edit row in
  the side panel** (and un-hiding brings it straight back) -- the field
  still exists and is still written to by a lookup, Parse Filename,
  Search/Replace, or Case Conversion while hidden; only the on-screen
  row disappears.
- **Rename / Export Files...** (File menu, F2) renders a `%series%
  %number% - %title%`-style pattern (any ComicInfo field as a
  placeholder, not just a curated subset) into a new filename for
  every selected file, previewed before you commit -- rename in place
  or export renamed copies to a folder, originals untouched either way.
- **Parse Filename...** (Import menu, F3) is the reverse: extracts
  metadata FROM a filename using the same pattern syntax, auto-
  detecting which of your past patterns fits the current batch best.
  Routed through the same overwrite-conflict protection as a lookup
  (see below) -- it won't silently clobber a field you've already set.
  Both dialogs share one pattern history.
- Edit the full ComicInfo.xml v2.0 field set, plus four fields from the
  v2.1 **draft** schema already understood by ComicTagger and readers
  like Kavita -- Translator, Tags, StoryArcNumber, and GTIN (purely
  additive; a v2.0-only reader just ignores what it doesn't recognize).
  Grouped as Identity/Sequence, Story, Credits, Publication,
  Classification, and free-text Summary/Notes/Review. Selecting more
  than one file switches
  the panel to **bulk edit** mode: every field starts blank, and only
  the ones you actually fill in get applied -- to every selected file
  at once -- via the toolbar/Operations menu's "Apply to N Selected
  Files" action. A field left blank is left untouched on every file,
  not cleared.
- Sidebar shows the first page (by filename sort order) as a cover
  thumbnail (single-file selection only -- bulk mode hides it, since
  there's no one "the" cover across different files) -- **below the
  field form, in a resizable splitter**, matching epubredactor's own
  "Bulk Edit Tags" above / "Cover Image" below convention. Drag the
  divider to give the cover more (or less) room.
- Genre and Language (ISO) each have a "+" quick-pick button next to
  the field -- a curated default list plus your own custom entries,
  managed via Settings > Add/Remove Genres.../Add/Remove Languages...
  (built on the same hideable-defaults-plus-custom-list pattern
  epubredactor uses). Picking a genre adds it alongside whatever's
  already typed; picking a language sets the ISO code. The picker is a
  small searchable dialog (filter box + scrolling list), not a plain
  dropdown menu -- a flat menu stopped being usable once enough custom
  genres piled up (it could overflow the screen with no way to search
  it), a real complaint from actual use.
- **Click a column header to sort** by it -- click again to reverse
  direction. Text columns sort alphabetically (case-insensitive);
  Number/Count/Volume/Year/Month/Day/Pages/Community Rating sort
  numerically ("9" before "10", not after). Not persisted across
  restarts (column order/widths/visibility are; sort order isn't).
- The side panel's field list scrolls properly with the mouse wheel
  now, even when the cursor is resting over the Age Rating/Manga/
  Black & White/Community Rating controls -- those used to swallow the
  wheel and silently change their own value instead of letting the
  scroll continue past them, a classic side effect of putting a combo/
  spin box inside a scrollable area.
- `PageCount` is always recomputed from the archive's actual image
  count at save time -- never hand-edited -- with a mismatch against
  whatever was previously stored flagged in the file list beforehand.
- Saving rewrites only `ComicInfo.xml`; every page image is copied
  byte-for-byte into a fresh archive, so pixel data is never
  re-encoded or reordered -- **except** via the explicit Resize
  Images... action described below, the one deliberate exception.
- `Import > Convert CBR to CBZ` for RAR-based archives (see below).
- `Import > Look Up via Comic Vine...` searches [Comic Vine](https://comicvine.gamespot.com/api/)
  by Series + Number (guessed from the filename when Series is blank)
  and offers to fill in series, issue title, summary, date, full
  creator credits, characters/teams/locations, and publisher -- review
  and untick anything before Apply, same pattern as epubredactor's
  Google Books/Calibre/Open Library lookups. Needs a free Comic Vine
  API key (Settings > Comic Vine API Key...).
- `Import > Look Up via Grand Comics Database...` -- same Series +
  Number search and review-then-Apply flow, against
  [comics.org](https://www.comics.org/)'s open API (no key needed).
  Best for older/obscure/international issues Comic Vine doesn't have.
  GCD's search needs both Series AND Number (no free-text search like
  Comic Vine), and its results come back alphabetically rather than by
  relevance -- for a widely-reused title (e.g. searching "Watchmen"
  turns up dozens of "Before Watchmen: ..." spin-offs), this tool
  follows pagination and promotes an exact series-name match to the
  top automatically, but an unusual or very generic series name may
  still need reviewing the candidate list carefully.
- **Both lookup dialogs show the file's own "Current" cover side by
  side with the "Found" one** (not a cramped in-table icon) for
  whichever row is currently selected -- so you can actually see
  whether the match is the same comic before trusting it, instead of
  finding out after Apply. Below that, an **editable Series/Number
  correction form** pre-filled with whatever was actually searched
  (the filename guess, by default). If the guess was wrong -- or GCD
  needs a number the filename didn't have -- correct it and click
  "Search This Item" to re-run just that row, without restarting the
  whole batch.
- The filename guess used to seed a search strips common trailing
  scene-release annotations first -- `Batman 001 (2016) (Digital)
  (Empire).cbz` guesses series "Batman", number "1", not the whole
  bracketed mess. Still just a best-effort guess for a blank Series
  field; use the correction form above when it's wrong.
- **Resize Images...** (Operations menu) shrinks oversized page images
  down to a target maximum width -- for a library with extremely large
  scans, without needing an external tool like ImageMagick (uses
  [Pillow](https://python-pillow.org/) instead, which bundles straight
  into the standalone .exe). Smart about **double-page spreads**: a
  page detected as a spread (landscape or square -- wider than it is
  tall, unlike virtually every real single comic/manga page) gets
  **double** the target width, so each half keeps the same effective
  per-page resolution a normal single page would, instead of being
  crushed down to half the detail. Only ever shrinks, never upscales --
  a page already under its (possibly-doubled) target is left completely
  byte-for-byte untouched. Choose to resize files in place or export
  resized copies to a folder (originals untouched); a JPEG quality
  slider controls re-save quality for pages actually saved as JPEG
  (PNG pages stay lossless). This is the one operation with **no
  Undo** -- unlike a metadata edit, there's no in-memory original to
  restore once pixels are actually re-encoded and written to disk, so
  it only ever runs when you explicitly ask for it, never as a side
  effect of Save.
- **Both lookups protect existing data the same way**: on Apply, if
  any looked-up field would overwrite a value a file already has (a
  hand-typed one, or from a prior lookup), you're asked once whether
  to overwrite everything, keep the existing values and only fill in
  blanks, or cancel -- rather than either silently clobbering it or
  silently refusing to update it.

### Deferred (not in this version)

- Per-page `<Pages>` tagging (marking specific pages as FrontCover,
  Story, BackCover, etc.) isn't yet exposed in the UI. If a file
  already has `<Pages>` data, it's preserved untouched through
  load/edit/save -- just not editable yet.
- **Other metadata sources**: [Metron](https://metron.cloud/) (whose
  output maps almost 1:1 onto ComicInfo.xml fields) and
  [MangaDex](https://api.mangadex.org/) (for the `Manga` field) were
  scoped as follow-up sources alongside Comic Vine and GCD -- not
  implemented yet. Each would plug into the same
  `core/<source>_lookup.py` + `gui/<source>_lookup_dialog.py` shape as
  `comicvine_lookup.py`/`gcd_lookup.py`.

## CBR support

RAR5's compression is a proprietary format Python can't decode without
shelling out to a real `unrar`/`unar`/`bsdtar`-compatible binary. This
tool never edits a `.cbr` in place -- `Import > Convert CBR to CBZ`
converts it to a real `.cbz` first (via the optional `rarfile` package),
and every edit from then on happens on that CBZ.

**You need an unrar-compatible tool on PATH** for conversion to work --
e.g. [7-Zip](https://www.7-zip.org/) or [WinRAR](https://www.win-rar.com/).
Bundling a portable Windows binary automatically (the same approach the
[keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)
repo takes for FFmpeg) is a likely follow-up once this is needed
without a manual install step -- not done yet.

## Running from source

```bash
pip install -r requirements.txt
python main.py
```

## Building a standalone Windows .exe

You need to do this step **on a Windows machine** (PyInstaller builds
for the OS it runs on).

1. Install Python 3.10+ from python.org (check "Add to PATH" during install).
2. Copy this whole folder to the Windows machine.
3. Double-click `build_exe.bat`, or run it from a command prompt.
4. When it finishes, your standalone app is at `dist\cbzredactor.exe`.

## Development

Tests are plain pytest, no Qt required for the `core/` modules. Most
of the GUI layer isn't unit-tested (same convention as the sibling
tools) -- the one exception is the lookup-overwrite-conflict logic in
`gui/main_window.py`, tested via a real (offscreen) QApplication since
a regression there would affect data safety across every lookup
source at once:

```bash
pip install pytest
pytest
```

## Releasing a new version

Run `python bump_version.py`, add a `CHANGELOG.md` entry, commit, and push.
