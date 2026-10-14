# The ƆBZ Redactor

A desktop tool for viewing and editing the [ComicInfo.xml](https://github.com/anansi-project/comicinfo)
metadata embedded in CBZ comic book archives -- title, series, credits,
publication info, and more. Saving metadata never re-encodes a page
image; only Resize Images (or resizing while converting, when you ask
for it) changes the pixels.

Part of the "Redactor" family of metadata tools, alongside the EPUB,
MP3, and video Redactors, sharing common UI/core code via
[redactor_common](https://github.com/Erlbon/redactor_common).

CBR, CB7 and CBT archives are never edited in place. By default they are
added to the list unconverted (read-only rows) and converted to CBZ only
when you ask (right-click > Convert to CBZ, or Repair > Convert to
CBZ...); Tools > Preferences > Conversion can make loading convert them
automatically or ask each time -- see README.md's "Foreign archive
formats" section.

History of tag changes is opt-in, off by default. Turned on (Tools >
Preferences > History), every tag change the app makes (Save, Redact, and
the command line, which obeys the same setting) is recorded with the old
and new values in `cbzredactor_journal.jsonl` next to the settings file:
Tools > History... shows it and can restore the old values, even after the
app was closed. Page images are not kept there (replaced files go to the
Recycle Bin). While it is off nothing is recorded and no file is created.
The history follows a file that is renamed, moved or converted to CBZ,
Delete History sends the file to the Recycle Bin, and the on/off setting
travels with Export / Import Settings.
