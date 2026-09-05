# Changelog

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
