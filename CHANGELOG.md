# Changelog

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
