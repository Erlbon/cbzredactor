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
- **Quick single-file rename**: double-click a Filename cell, or
  right-click a single selected file > Rename File..., to fix a typo
  directly without the pattern-based tool above --
  `redactor_common.gui.rename_single_file.rename_single_file()`.
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
- A file's whole row is tinted (amber = unsaved change or page-count
  mismatch, red = failed to load) so a problem or a pending edit is
  visible at a glance without reading the Status column text --
  matching the other three Redactor apps' own row-tinting.
- Saving rewrites only `ComicInfo.xml`; every page image is copied
  byte-for-byte into a fresh archive, so pixel data is never
  re-encoded or reordered -- **except** via the explicit Resize
  Images... action described below, the one deliberate exception.
- **Ext column**: each file's extension, plus its real format when the
  two disagree -- `CBR → ZIP` for a ZIP someone renamed to .cbr, `CBZ →
  RAR` for the reverse. Sort by it to group what still needs converting.
- **Import > Read Filename Tags** fills ComicInfo from scene-style
  filenames with no pattern to type: series, issue number, "3 of 12"
  counts, `v01` volumes, "001 - Title" issue titles, and years (a year
  before the issue number, as in `Batman (2016) 045`, is the series'
  start year, stored in Volume as ComicRack does). Bracketed tags are
  sorted: scan groups and sources (`Zone-Empire`, `Digital`, `c2c`) go
  to **ScanInformation**, editions (`TPB`, `One Shot`, `FCBD`) to
  **Format**, completeness notes (`missing ifc`, `2 covers`) are
  appended to **Notes**. Only whole bracketed phrases count as tags, so
  a title word like "Empire" is never stripped. Tags it doesn't
  recognise are listed afterwards, not guessed at. Handles the
  `.webp.cbz` ending CbxConverter adds. Goes through the usual
  per-field review; undoable. The same parser now seeds every online
  lookup, which previously found no issue number at all for
  `.webp.cbz` names.
- `Import > Convert to CBZ` converts CBR/CBT/CB7 and mislabeled files
  (see "Foreign archive formats" below).
- **Size column**: each book's typical page width, colored **yellow**
  (low-res, under 1000px), **green** (acceptable, 1000-1599px) or
  **orange** (oversized, 1600px+). Based on the median width of the
  single pages, so double-page spreads and one oversized cover don't
  skew it; hover for the width breakdown. Measured from image headers
  only, in the background. A **File Size** column is available too.
- **Scanner credit pages** (the "scanned by <group>" tag page scene
  releases add): right-click a file > **Credit Pages...** shows its
  first 2 and last 4 pages as thumbnails. Tick the credit page and
  Remove: it's removed from the file and **remembered**, like
  epubredactor's Junk Cover flag. From then on the **Credit Pages**
  column flags every file containing that page, and **Operations >
  Remove Credit Pages...** removes them all after a thumbnail review.
  Matching is visual (a small image fingerprint), so the same tag page
  is recognised after resizing or re-encoding -- CbxConverter's
  renamed, resized WebP pages included. Unlearned candidates get hints
  (a "zzzz_..." name, a scan group in the name, an odd page shape) but
  are never pre-ticked. Blank pages are never learned (they'd match
  every blank page). ComicInfo's PageCount and per-page entries are
  corrected; the original file goes to the Recycle Bin. Learned pages:
  **Settings > Known Credit Pages...**, with Forget.
- **Operations > Find Duplicates...** finds the same comic loaded more
  than once -- another release, resolution or format -- and suggests
  the copy to keep: highest page resolution, then most pages (credit
  pages not counted), then the most filled-in ComicInfo, then the
  biggest file. Matching is visual, on the STORY pages (a few pages
  around a third and two thirds in, compared with some slack either
  way, since ads and credit pages shift page numbers between
  releases), so a trade paperback reusing issue #1's cover is not a
  duplicate, while a variant cover of the same issue is (and is
  labelled as such). Files sharing a Comic Vine/GCD link in Web are
  grouped too. Review with covers side by side; ticked copies go to
  the Recycle Bin and leave the list.
- **Operations > Tag Low-Res Scans** adds a `Low-res scan` entry to the
  ComicInfo **Tags** field of every low-res (yellow) book, so they can
  be found and replaced with better copies later (your comic server can
  filter on it too). Running it again removes the tag from books that
  are no longer low-res. Other tags are kept, `ScanInformation` (the
  scan group's credit) is never touched; undoable, written on Save.
  There's deliberately no upscaling: plain resampling can't add detail.
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
  Following [GCD's API wiki](https://github.com/GrandComicsDatabase/gcd-django/wiki/API):
  searches also send the **year** (GCD's own year filter -- usually one
  request instead of several pages), try a scene name's " - " as the
  ":" it stands for ("G.I. Joe - A Real American Hero" finds "G.I.
  Joe: A Real American Hero"), and record the issue's GCD page in
  **Web**. GCD limits anonymous use per hour; an optional free GCD
  account (**Settings > GCD Account...**) raises that limit, and the
  batch stops cleanly with a clear message when the limit is reached
  instead of failing every remaining file.
- `Import > Look Up via GCD (Local Database)...` -- the same lookup
  against **your own downloaded copy** of the Grand Comics Database
  (its SQLite data dump, `current.zip`, free for registered comics.org
  users -- see **Settings > GCD Local Database...** for step-by-step
  instructions; the app never bundles or downloads it). Milliseconds
  per file instead of seconds, works offline, no hourly limit, and
  structured credits. Matching is word-based and ignores punctuation,
  so scene names find GCD's fuller titles ("Mangaverse - Ghostlocke" ->
  "Marvel Mangaverse: Ghostlocke"); a series start year in the filename
  ("Batman (2016) 045") picks the right same-named series; English
  editions rank ahead of translated reprints, which are offered under
  Other Matches. No cover images (the dump has none). Never writes to
  the database file.
- `Import > Look Up via ComicRack Library...` -- the same local lookup
  against **your own ComicRack library**: **Settings > ComicRack
  Library Database... > Build from ComicRack Library...** converts
  ComicRack's `ComicDb.xml` (plain or zipped) into a database with the
  GCD dump's table layout -- about 30-40 s for a 234k-book library,
  cancellable. Duplicate files of one issue become one entry (the best
  tagged copy); credits, characters, summaries and the Comic Vine link
  come along, file paths and reading history never do. A blank issue
  number now also finds a #1 (one-shots are #1 on Comic Vine), in this
  lookup and the GCD one.
- `Import > Look Up via Bedetheque...` -- same Series + Number
  search and review-then-Apply flow, against [Bedetheque](https://www.bedetheque.com/),
  the reference database for French-language "bande dessinée" (BD).
  Best for francophone comics Comic Vine and GCD tend to have thin or
  no coverage of. Bedetheque needs both Series AND Number, like GCD.
  The site sits behind Cloudflare, so this needs the optional
  `cloudscraper` package installed (`pip install cloudscraper`) --
  you'll get a clear message if it's missing rather than a crash.
- **All three lookup dialogs show the file's own "Current" cover side
  by side with the "Found" one** (not a cramped in-table icon) for
  whichever row is currently selected -- so you can actually see
  whether the match is the same comic before trusting it, instead of
  finding out after Apply. Below that, an **editable Series/Number
  correction form** pre-filled with whatever was actually searched
  (the filename guess, by default). If the guess was wrong -- or GCD/
  Bedetheque need a number the filename didn't have -- correct it and
  click "Search This Item" to re-run just that row, without
  restarting the whole batch.
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
  resized copies to a folder (originals untouched); a quality setting
  controls re-save quality for JPEG/WebP pages (PNG pages stay
  lossless). Width presets: **Standard 1280px**, **Wide 1440px** (the
  default, which lands a book in the Size column's green band), **HD
  1920px** and **UHD 2560px**, or any custom width. Options modeled on
  [CbxConverter](https://github.com/tomek-o/CbxConverter): an optional
  **height limit** (for tall manga/webtoon pages), an optional
  **output format** (JPEG or WebP -- every page is re-encoded and
  renamed to the new extension, keeping reading order), and **"only
  files marked Oversized"** to fix a whole list in one go. Pages are
  processed in parallel. This is the one operation with **no
  Undo** -- unlike a metadata edit, there's no in-memory original to
  restore once pixels are actually re-encoded and written to disk, so
  it only ever runs when you explicitly ask for it, never as a side
  effect of Save.
- **Every metadata-writing path that could overwrite existing data
  (both lookups, and Parse Filename) shows a per-file, per-field
  review before anything is written** -- every field the change would
  touch, side by side with what's there now, with its own Apply
  checkbox. A field that's currently blank starts ticked (nothing to
  lose); a field that would actually replace a different, non-blank
  value starts unticked, so accepting it takes a deliberate per-field
  choice rather than one all-or-nothing decision for the whole file.
  You can accept some fields from an import and reject others on the
  very same file. A batch with nothing to overwrite skips the review
  entirely -- there's nothing to confirm.

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

## Foreign archive formats (CBR, CBT, CB7)

CBZ (a plain ZIP) is the only format this tool ever writes to. CBR
(RAR), CBT (tar), and CB7 (7-Zip) are all read-only, converted to a
real `.cbz` first, and every edit from then on happens on that CBZ --
none of them is ever edited in place, and there are no plans to change
that (RAR5/7z compression is proprietary and can't be safely
round-tripped the way ZIP can; tar *could* technically be rewritten,
but every comic reader and ComicInfo.xml tool expects a ZIP-based
container regardless, so there's no reason to special-case it as
writable).

A file's real format is read from its first bytes, not trusted from
its extension. A `.cbr` that's really a ZIP converts by a plain copy
(no unpacking); a `.cbz` that's really a RAR/7z/tar is first renamed to
its true extension, then converted normally.

What **Load Files**/**Load Folder** does with files that need
converting is set in **Settings > Converting to CBZ...**:

- **Add to the list unconverted** (the default) -- listed as greyed,
  read-only rows (Status "Needs conversion"). Sort by Ext, then select
  and right-click > **Convert to CBZ** (or use **Import > Convert to
  CBZ**, which converts the selected unconverted rows, or all of them).
  Each row is replaced by its converted .cbz in place.
- **Convert automatically** -- converted while loading, no prompt.
- **Ask each time** -- one prompt per load: **Convert Now**, **Add
  Unconverted**, or **Skip**, with a "Remember my choice" box that
  saves Convert Now / Add Unconverted as the setting.

The same dialog has **Move originals to the Recycle Bin after a
successful conversion** (off by default), used by every conversion.

Every conversion is checked before anything else happens: the new
.cbz must open, pass its CRC checks, and hold as many page images as
were extracted. An existing .cbz is never overwritten. Originals are
kept unless you choose to remove them, and then they go to the
**Recycle Bin** (via `send2trash`), never deleted permanently -- and
never after a failed conversion.

Per format:

- **CBR** needs the optional `rarfile` package, which itself shells
  out to a real `unrar`/`unar`/`bsdtar`-compatible binary -- **you need
  one of those on PATH**, e.g. [7-Zip](https://www.7-zip.org/) or
  [WinRAR](https://www.win-rar.com/). Bundling a portable Windows
  binary automatically (the same approach the
  [keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)
  repo takes for FFmpeg) is a likely follow-up once this is needed
  without a manual install step -- not done yet.
- **CBT** needs nothing extra -- Python's stdlib `tarfile` handles it.
- **CB7** needs the optional `py7zr` package -- unlike CBR, this is a
  normal pip-installable dependency with no separate binary to install.

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

Tests are plain pytest, no Qt required for the `core/` modules. The
GUI layer isn't exhaustively unit-tested (same convention as the
sibling tools), but real (offscreen) `QApplication`-based tests cover
the spots where a regression would be easy to miss visually and costly
if it shipped anyway -- the overwrite-review logic in `gui/main_window.py`/`redactor_common.
gui.overwrite_review_dialog` (data safety across every metadata-writing
path at once), column/panel-visibility
sync, the side-panel collapse toggle, column sorting, and more:

```bash
pip install pytest
pytest
```

## Releasing a new version

Run `python bump_version.py`, add a `CHANGELOG.md` entry, commit, and push.

## License

Licensed under the [GNU General Public License v3.0 or later](LICENSE).
The GUI is built on PyQt6, which Riverbank Computing licenses under GPL
v3 (or a paid commercial license) -- this project ships under
GPL-compatible terms to match.
