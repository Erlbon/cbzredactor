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

## Features

- Load one or many `.cbz` files (or a whole folder) and browse them in
  a table.
- Edit the full set of common ComicInfo.xml fields, grouped as
  Identity/Sequence, Story, Credits, Publication, Classification, and
  free-text Summary/Notes/Review.
- Sidebar shows the first page (by filename sort order) as a cover
  thumbnail.
- `PageCount` is always recomputed from the archive's actual image
  count at save time -- never hand-edited -- with a mismatch against
  whatever was previously stored flagged in the file list beforehand.
- Saving rewrites only `ComicInfo.xml`; every page image is copied
  byte-for-byte into a fresh archive, so pixel data is never
  re-encoded or reordered.
- `Import > Convert CBR to CBZ` for RAR-based archives (see below).

### Deferred (not in this version)

Per-page `<Pages>` tagging (marking specific pages as FrontCover,
Story, BackCover, etc.) isn't yet exposed in the UI. If a file already
has `<Pages>` data, it's preserved untouched through load/edit/save --
just not editable yet.

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

Tests are plain pytest, no Qt required for the `core/` modules (the
GUI layer isn't unit-tested, same convention as the sibling tools):

```bash
pip install pytest
pytest
```

## Releasing a new version

Run `python bump_version.py`, add a `CHANGELOG.md` entry, commit, and push.
