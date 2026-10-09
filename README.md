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
- **Operations > Validate / Fix Issues...** (selected files, or all)
  checks each ComicInfo.xml and proposes a fix per problem: PageCount
  out of step with the archive, impossible dates, scene tags or file
  extensions left in Series/Title ("Batman (Zone-Empire).webp"), GCD's
  "[nn]" marker or "#007" in Number, stray spaces, "EN"/"en-US" in
  LanguageISO, and Black & White / Manga suggestions from the tags.
  Count lower than Number is reported. Reviewed per finding; the ticked
  fixes are ordinary edits -- one Undo step, written on Save.
- **Operations > Clean Up Archive Contents...** (selected files, or
  all) tidies the names *inside* each archive -- where release groups
  put their name too ("Zone-Empire/Batman 045 (2018) (Zone-Empire)
  p1.jpg"), and nested folders make paths too long once extracted.
  Three independent checkboxes, all on by default and remembered:
  pages are renamed to plain numbers in reading order (001.webp,
  002.webp, ...), pages are taken out of folders (keeping their names
  where that is safe, a short number in front where two would clash),
  and junk is removed (Thumbs.db, .DS_Store, __MACOSX, and scene
  extras like .nfo, .sfv, .url, .txt). Page bytes, order and
  ComicInfo.xml are unchanged. Reviewed first, per file; originals go
  to the Recycle Bin. The Redact step offers the same three options
  (page renaming is off there unless the recipe turns it on).
- **Collection > Scan Collection Folder...** -- a snapshot of your
  whole organised collection, taken only when you press it (nothing is
  watched; cbzredactor stays a tool for incoming files). Every comic
  under the chosen folder becomes one row of a CSV -- path, size, date,
  format, pages and the key ComicInfo fields -- saved zipped as
  `collection_scan.zip` next to the app (a big collection packs to a
  few MB; the CSV inside opens in Excel). Only each archive's table of
  contents and ComicInfo.xml are read, never the pages; paths over 260
  characters are read too. Rescanning re-reads only changed files, and
  a stopped scan keeps what it read and carries on next time. Run it on
  the collection's own computer and carry the zip wherever you like.
- **Collection > Collection Report...** reads that scan (never the
  files) and learns the folder layout from the collection itself: a
  series is its name plus its volume ("vN" in the file or folder
  name), TPBs sit with their series, spin-offs in their parent's folder
  are left alone. Tabs: **Moves** (a file away from the folder named
  for its series and volume -- the year picks between volumes when the
  name has none -- or away from where most of the series lives; several
  candidates are asked about, never guessed), **Split series**, **File
  names** that break their folder's pattern (number width, the
  "(Publisher, year-month)" bracket, extra brackets, the series spelled
  differently, stray spaces -- with the Library Organizer name
  `{series} {number3} ({publisher}, {year}-{month2})` suggested from
  ComicInfo), **Duplicates**, **Name vs ComicInfo** disagreements, and
  **Formats & missing** (not really a ZIP, PDF, no or unreadable
  ComicInfo, no pages, PageCount out of step, paths over 260
  characters). Nothing moves unless you tick it; moves and renames never
  overwrite, only run where the scanned folder exists, and File > Undo
  Last Rename takes them back. **Save List...** writes every finding to
  a CSV.
- **Page order is numeric**, as comic readers do it: an archive whose
  pages are numbered 1, 2, ... 10 reads 1, 2, ... 10 (plain text
  sorting put 10 before 2 -- found in real archives), and macOS
  "__MACOSX/._name.jpg" leftovers are no longer counted as pages.
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
- `Import > Look Up via Comic Vine...` goes through the files one at a
  time and you decide while looking at the covers, in two steps, after the
  ComicRack "Comic Vine Scraper" plugin. **1. Series:** the matching
  [Comic Vine](https://comicvine.gamespot.com/api/) volumes, best fit
  first (name words, an issue count that can hold the number, a start year
  not after the file's year), your file's cover beside the selected volume's.
  **2. Issue:** that volume's issues in number order with your number
  selected, or a note of which numbers it does have. A "Cover Match"
  figure compares your first page with Comic Vine's cover and preselects the
  look-alike; nothing is applied until you press Use This Issue. The series
  you choose is remembered for the next file of the same series. Fills in
  series, title, summary, date, credits, characters/teams/locations and
  publisher (then the usual overwrite review). Needs a free Comic Vine API
  key (Settings > Comic Vine API Key...).
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
- `Import > Compare ComicRack Library with GCD...` -- compares that
  converted library with the local GCD database and saves the
  difference (a database plus CSV spreadsheets): series not found in
  GCD, issues GCD doesn't have, and GCD issues missing credits,
  characters or a summary your library has -- each with the library's
  own data, ready to offer to GCD. Series match by name, start year,
  publisher and shared issue numbers (checked against publication
  years), including Comic Vine volumes that GCD keeps inside an older
  series and runs GCD splits over several series; every match records
  how it was made. GCD's dump holds no cover images, so covers aren't
  compared.
- `Import > Look Up via Bedetheque...` -- same Series + Number
  search and review-then-Apply flow, against [Bedetheque](https://www.bedetheque.com/),
  the reference database for French-language "bande dessinée" (BD).
  Best for francophone comics Comic Vine and GCD tend to have thin or
  no coverage of. Bedetheque needs both Series AND Number, like GCD.
  The site sits behind Cloudflare, so this needs the optional
  `cloudscraper` package installed (`pip install cloudscraper`) --
  you'll get a clear message if it's missing rather than a crash.
  The album's Bedetheque page is recorded in **Web**, as the Comic
  Vine and GCD lookups record theirs.
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

## Command line

The one exe (`cbzredactor.exe`, or `python main.py` from source) is also the command line. When its first
argument is a command name, it runs that command and the window never opens; with no command, or with a file
or folder to open, the window starts as usual. `cbzredactor --help` lists the commands and
`cbzredactor COMMAND --help` lists the options of one.

```
cbzredactor info     PATH...  [--fields LIST | --all]
cbzredactor set      PATH...  -s FIELD=VALUE ... [--clear FIELD ...] [-n]
cbzredactor convert  PATH...  [--resize] [--trash-original] [-n]
cbzredactor rename   PATH...  [-p PATTERN] [--zero-pad N] [--ascii] [-n]
cbzredactor move     PATH...  -p PATTERN [--root FOLDER] [--copy] [--zero-pad N] [--ascii] [-n]
cbzredactor redact   [PATH...] [--recipe FILE] [--enable STEP] [--disable STEP] [--threshold N]
                              [--trash-dir FOLDER] [--list-steps]
```

The command line uses the same code as the window, so the results are the same. It reads the same settings file
(`cbzredactor_settings.ini` next to the exe: Tools > Preferences, the saved Redact recipe, the library root, the
database paths) and the same secret store for the API keys. Not every window function is available from the
command line; the commands above are what is.

### Options every command has

| Option | Meaning |
| --- | --- |
| `PATH...` | One or more comic files, folders or wildcards (`D:\Comics\Saga*.cbz`). A folder is searched recursively for `.cbz`, `.cbr`, `.cb7` and `.cbt`. A file you name is always used. A path that matches nothing is reported, and if nothing at all matches the command stops with exit code 2. A name containing `[` or `]` is taken literally, a wildcard's matches are filtered by extension like a folder's files, and a folder inside a folder that is a link or junction is not followed. |
| `-R`, `--no-recurse` | For a folder, look only at the files directly in it. |
| `--json` | Print one JSON document on stdout instead of text (see "JSON output"). Nothing else goes to stdout. |
| `-q`, `--quiet` | No progress lines and no warnings on stderr (errors are still shown). |
| `-o FILE`, `--output FILE` | Write the result (the text, or with `--json` the JSON document) to FILE instead of stdout. The file is complete when the program exits. This is the reliable way for a script to read a result. |
| `-n`, `--dry-run` | Only on the commands that change files (`set`, `convert`, `rename`, `move`): show what would happen and change nothing. |
| `-h`, `--help` | Help for the program or for one command. |
| `--version` | The version (top level only). |

Progress lines (`[3/20] name.cbz`) go to stderr when more than one file is processed.

### info

`cbzredactor info PATH... [--fields LIST | --all]`

Shows what each comic is: its real format (`zip`, `rar`, ...), the number of pages, whether it is ready to edit
(`ok`), needs converting (`needs conversion`) or could not be read (the reason), and its ComicInfo fields.

| Option | Meaning |
| --- | --- |
| `--fields LIST` | Comma-separated fields to show, e.g. `--fields series,number,year`. Default: `series, number, title, year, publisher`. |
| `--all` | Show every field that has a value. |

Only fields with a value are listed. Exit code 1 if a file could not be read; a CBR/CB7/CBT that merely needs
converting is not a failure.

### set

`cbzredactor set PATH... -s FIELD=VALUE [-s ...] [--clear FIELD ...] [-n]`

Sets or empties ComicInfo fields and saves each comic in place (the same save as the window's Save All; the
page count is recomputed). A CBR/CB7/CBT must be converted first. Fields and values are checked before any file
is touched; a bad one stops the command with exit code 2.

| Option | Meaning |
| --- | --- |
| `-s FIELD=VALUE`, `--set FIELD=VALUE` | Set a field (repeat for several). `VALUE` may be empty to clear it. |
| `--clear FIELD` | Empty a field (repeat for several). |
| `-n`, `--dry-run` | Show the old and new value of each field, save nothing. |

Field names are case-insensitive and accept the ComicInfo spelling or the plain one (`CoverArtist`,
`cover_artist`, `coverartist`). The settable fields are: Title, Series, Number, Count, Volume, AlternateSeries,
AlternateNumber, AlternateCount, Summary, Notes, Year, Month, Day, Writer, Penciller, Inker, Colorist, Letterer,
CoverArtist, Editor, Translator, Publisher, Imprint, Genre, Tags, Web, LanguageISO, Format, BlackAndWhite, Manga,
Characters, Teams, Locations, ScanInformation, StoryArc, StoryArcNumber, SeriesGroup, AgeRating, CommunityRating,
MainCharacterOrTeam, Review, GTIN. (PageCount is always taken from the archive.)

Checks: Count, Volume, AlternateCount, Year, Month and Day must be whole numbers (Month 1-12, Day 1-31);
CommunityRating is a number from 0 to 5; BlackAndWhite, Manga and AgeRating must be one of the schema's values
(any capitalisation is accepted and corrected).

Each file's result is `changed`, `unchanged` (nothing differed), `planned` (dry run) or `failed`.

### convert

`cbzredactor convert PATH... [--resize] [--trash-original] [-n]`

Converts CBR, CB7 and CBT files, and `.cbz` files that are really another format, to real `.cbz` files beside
them. The original is left in place unless you ask otherwise. Never overwrites: if the `.cbz` already exists the
file is `skipped` and nothing is touched. A file that already is a real CBZ is `skipped` too.

| Option | Meaning |
| --- | --- |
| `--resize` | Shrink the pages in the same pass, with the saved Resize defaults (Tools > Preferences > Resize defaults). |
| `--trash-original` | After the new `.cbz` is made, send the original to the Recycle Bin (never deleted for good; if the Recycle Bin refuses, the original is kept and a warning says so). |
| `-n`, `--dry-run` | Show what would be converted, change nothing. |

Results: `converted`, `skipped`, `planned`, `failed`. The new path is in `new_path`.

### rename

`cbzredactor rename PATH... [-p PATTERN] [--zero-pad N] [--ascii] [-n]`

Renames each comic from its ComicInfo fields, in its own folder, like Rename / Export / Move > Rename files in
place. Never overwrites: a name that is taken gets `(2)`, `(3)`, ... A change of letter case alone (`song` to `Song`) counts as a rename.

| Option | Meaning |
| --- | --- |
| `-p PATTERN`, `--pattern PATTERN` | The new name (without the extension), with `%field%` tokens, e.g. `"%series% %number% - %title%"`. Default: `%series% %number% - %title%`. Quote it so the shell leaves the `%` signs alone. |
| `--zero-pad N` | Pad the number to N digits (`--zero-pad 3` gives `001`). The month is always two digits. |
| `--ascii` | ASCII-safe names (é becomes e, æ becomes ae, other symbols are dropped). |
| `-n`, `--dry-run` | Show the new names, rename nothing. |

Tokens are the ComicInfo field names in lower case (`%series%`, `%number%`, `%title%`, `%year%`, `%publisher%`,
`%volume%`, `%writer%` and so on). A file the pattern gives no name for (all its fields are empty) is `skipped`,
not renamed to "untitled". A file that already has the name is `unchanged`. There is no undo for the command line: preview with `--dry-run`.

### move

`cbzredactor move PATH... -p PATTERN [--root FOLDER] [--copy] [--zero-pad N] [--ascii] [-n]`

Moves (or copies) each comic into a folder tree under a library folder, like Rename / Export / Move > Move into
folders. The pattern may contain `/` to make sub-folders: `"%publisher%/%series%/%series% %number%"`. Missing
folders are created; nothing is overwritten (a taken name gets `(2)`); a destination outside the library folder
or too long is refused.

| Option | Meaning |
| --- | --- |
| `-p PATTERN`, `--pattern PATTERN` | Required. The path under the library folder, with `%field%` tokens. |
| `--root FOLDER` | The library folder. Default: the one saved in the app (Rename / Export / Move window). The folder must exist. |
| `--copy` | Copy instead of move, leaving the originals. |
| `--zero-pad N`, `--ascii` | As for `rename`. |
| `-n`, `--dry-run` | Show where each file would go, change nothing. |

Across volumes a move is a verified copy followed by sending the original to the Recycle Bin. A file the pattern
has no name for is `skipped`. There is no undo for a move either: preview with `--dry-run`.

### redact

`cbzredactor redact [PATH...] [--recipe FILE] [--enable STEP] [--disable STEP] [--threshold N] [--trash-dir FOLDER] [--list-steps]`

Runs the Redact recipe on the comics, the same steps as Operations > Redact: convert, clean up archive contents,
remove credit pages, resize, fill from the path and the filename, database lookups, fix ComicInfo issues, tag
low-res scans, rename, move into folders. Each file is saved in place and its original goes to the Recycle Bin
(or `--trash-dir`). Guesses below the confidence threshold are listed under "needs review" and not applied. There
is no `--dry-run`: use `info` first, and `--disable` for the steps you do not want.

| Option | Meaning |
| --- | --- |
| `--recipe FILE` | Use this recipe (a JSON file in the format the app stores) instead of the one saved in the app. |
| `--enable STEP` | Turn a step on for this run (repeatable). |
| `--disable STEP` | Turn a step off for this run (repeatable). |
| `--threshold N` | Confidence needed to apply a guess, `0`-`1` or a percentage (`0.9`, `90` or `90%`); a plain number from 1 to 5 such as `1.5` is refused as ambiguous. |
| `--trash-dir FOLDER` | Move originals into this folder (created if needed) instead of the Recycle Bin, for a machine or a task that has none. |
| `--list-steps` | Show the steps and whether the recipe has each on, then stop (no `PATH` needed). |

Steps: `convert_to_cbz`, `clean_contents`, `remove_credit_pages`, `resize_images`, `path_tags`, `filename_tags`,
`lookup`, `validate_fix`, `tag_low_res`, `rename`, `move_into_folders`. Without `--recipe` the recipe saved in the
app is used (the defaults if none was saved). The Comic Vine key comes from the `COMICVINE_API_KEY` environment
variable if it is set, else from the app's saved key; the local GCD / ComicRack databases and the library root
come from the app's settings. A file with unsaved edits does not exist on the command line, so nothing is skipped
for that. Exit code 1 if any file failed; files that need review are not failures.

### JSON output

`--json` prints one document: `{"results": [...], <summary fields>, "warnings": [...], "errors": [...]}`. It is ASCII-only (a non-ASCII character in a path is a `\uXXXX` escape, which any JSON reader decodes). If a command fails or is interrupted after it started, the document is still printed, with what was done so far and an `error` entry, so a script reading `--output FILE` never finds an empty or half-written file.

| Command | Each entry in `results` | Summary fields |
| --- | --- | --- |
| `info` | `path`, `format`, `pages`, `status`, `has_comicinfo`, `fields` (name to value) | `files`, `failed` |
| `set` | `path`, `status`, `changes` (field to `{old, new}`), `message` | `files`, `failed`, `dry_run` |
| `convert` | `path`, `status`, `new_path`, `message` | `files`, `failed`, `dry_run` |
| `rename` | `path`, `status`, `new_path`, `message` | `files`, `failed`, `dry_run`, `pattern` |
| `move` | `path`, `status`, `new_path`, `message` | `files`, `failed`, `dry_run`, `root` |
| `redact` | `file`, `path`, `status`, `applied`, `needs_review` (step, value, confidence, reason), `failures`, `notes`, `skipped`, `not_saved` | `files`, `failed`, `needs_review`, `cancelled`, `confidence_threshold`, `run_notes` |
| `redact --list-steps` | `step`, `label`, `enabled` | `confidence_threshold` |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done (files that were skipped or unchanged are not failures). |
| 1 | The command ran but some files failed. |
| 2 | Bad arguments, an unknown field or step, or no files found. The reason is on stderr. |
| 70 | An internal error (a bug); the traceback is on stderr. |
| 130 | Interrupted with Ctrl+C. |

### Using it from scripts and scheduled tasks (Windows)

`cbzredactor.exe` is a windowed program, and Windows shells treat those differently from console programs:
typed by hand in a terminal its output appears there and `>` / `|` redirection works, but an interactive shell
does not wait for it (the prompt can come back before the output), and a script cannot read a windowed
program's output unless it is redirected. So for automation: ask for the result in a file with `--output`, wait
for the process, and read the exit code.

```
:: batch file (cmd waits for the program in a batch file; %errorlevel% is the exit code)
cbzredactor.exe info "D:\Comics" --json --output "%TEMP%\comics.json"
if errorlevel 1 echo some files failed

:: interactive cmd: start /wait waits and keeps the exit code
start /wait cbzredactor.exe redact "D:\Incoming" --quiet --trash-dir "D:\Trash"

# PowerShell: wait with Start-Process, read .ExitCode
$p = Start-Process cbzredactor.exe -ArgumentList 'info','D:\Comics','--json','-o','C:\Temp\comics.json' -Wait -PassThru
$p.ExitCode
(Get-Content C:\Temp\comics.json -Raw | ConvertFrom-Json).results | Where-Object status -ne 'ok'

# PowerShell: piping to Out-Null also waits
cbzredactor.exe convert "D:\Incoming" --trash-original | Out-Null; $LASTEXITCODE
```

Task Scheduler waits for the program and records its exit code as it is. On Linux and macOS there is no such
distinction: the output goes to the terminal and pipes as usual.

### Examples

```
cbzredactor info "D:\Comics\Saga" --all                          what is in a folder
cbzredactor set "D:\Comics\Saga" -s Publisher="Image Comics" -n  preview a bulk edit, then run it without -n
cbzredactor set book.cbz --clear Notes --clear Review
cbzredactor convert "D:\Incoming" --resize --trash-original      convert, shrink the pages, recycle the originals
cbzredactor rename "D:\Comics\Saga" -p "%series% %number% (%year%)" --zero-pad 3 -n
cbzredactor move "D:\Incoming" -p "%publisher%/%series%/%series% %number%" --root "D:\Library"
cbzredactor redact "D:\Incoming" --disable lookup --disable move_into_folders --trash-dir "D:\Trash"
cbzredactor redact --list-steps --json
```

What the commands will not do: overwrite a file, delete anything for good, or ask a question. Everything that
could be a prompt in the window is a flag here or a skipped file in the report.

## Running from source

```bash
pip install -r requirements.txt
python main.py
```

## Linux

Each release also has a Linux download,
`cbzredactor-linux-x86_64.tar.gz`: one self-contained program (Python
and Qt inside) for 64-bit desktop Linux with glibc 2.35 or newer
(Ubuntu 22.04+, Debian 12+, Fedora 36+, Mint 21+). Unpack and run
`./cbzredactor/cbzredactor`. On Linux the settings live in
`~/.config/cbzredactor/` instead of next to the program.

It's built in Docker with the family's shared toolchain,
[redactor-build-tools](https://github.com/Erlbon/redactor-build-tools):

```bash
./linux-build/build.sh /path/to/cbzredactor cbzredactor.spec --test
./linux-build/package.sh /path/to/cbzredactor cbzredactor "The ƆBZ Redactor"
```

The release workflow does the same on GitHub Actions after the Windows
build.

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
